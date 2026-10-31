import csv
from io import BytesIO, StringIO
from zipfile import ZipFile

import psycopg
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font
from psycopg.rows import dict_row
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.auth import require_admin
from app.audit import record_audit
from app.core.config import get_settings
from app.properties import PropertyCreate

router = APIRouter(prefix="/properties", tags=["Property files"])

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_ROWS = 10_000
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FIELDS = ("owner_name", "phone_01", "phone_02", "ic_number", "address", "remark")
LABELS = ("Name", "Phone 01", "Phone 02", "IC number", "Address", "Remark")
REQUIRED_FIELDS = {"owner_name", "phone_01"}
TEXT_IDENTIFIERS = {"phone_01", "phone_02", "ic_number"}


def validate_headers(values) -> list[str]:
    headers = ["_".join(str(value or "").lower().split()) for value in values]
    headers = ["owner_name" if value == "name" else value for value in headers]
    if (not headers or len(set(headers)) != len(headers)
            or any(field not in FIELDS for field in headers)
            or not REQUIRED_FIELDS.issubset(headers)):
        raise HTTPException(status_code=422, detail="Invalid column headings; use the downloaded property template")
    return headers


def finish_validation(records: list[PropertyCreate], errors: list[dict]) -> list[PropertyCreate]:
    if errors:
        raise HTTPException(status_code=422, detail={
            "message": "No rows imported. Correct the listed rows and upload again.", "rows": errors,
        })
    if not records:
        raise HTTPException(status_code=422, detail="File contains no property records")
    return records


def excel_response(rows: list[dict], filename: str, template: bool = False) -> Response:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Properties"
    sheet.append(LABELS)
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in zip("ABCDEF", (25, 20, 20, 25, 50, 50)):
        sheet.column_dimensions[column].width = width
        sheet.column_dimensions[column].number_format = "@"
    for row_number, record in enumerate(rows, start=2):
        for column_number, field in enumerate(FIELDS, start=1):
            value = record.get(field)
            if value is not None:
                cell = sheet.cell(row=row_number, column=column_number, value=str(value))
                # Store names beginning with '=' as text, not executable formulas.
                cell.data_type = "s"
                cell.number_format = "@"
    if template:
        for row in sheet.iter_rows(min_row=2, max_row=1001, min_col=2, max_col=4):
            for cell in row:
                cell.number_format = "@"
        for column in ("A", "B"):
            sheet[f"{column}1"].comment = Comment("Required for each property row.", "Property Planets")
        sheet["B1"].comment = Comment("Required. Enter as Text to preserve leading zeros.", "Property Planets")
        sheet["D1"].comment = Comment("Optional. Enter as Text to preserve the original IC number.", "Property Planets")
    sheet.auto_filter.ref = f"A1:F{max(1, len(rows) + 1)}"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return Response(content=output.getvalue(), media_type=EXCEL_MEDIA_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def parse_properties(contents: bytes) -> list[PropertyCreate]:
    if len(contents) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File must be 5 MB or smaller")
    workbook = None
    try:
        with ZipFile(BytesIO(contents)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNPACKED_BYTES:
                raise HTTPException(status_code=413, detail="Excel workbook is too large when unpacked")
        workbook = load_workbook(BytesIO(contents), read_only=True, data_only=False, keep_links=False)
        if len(workbook.sheetnames) != 1:
            raise HTTPException(status_code=422, detail="Use one worksheet with the property data")
        sheet = workbook.worksheets[0]
        if (sheet.max_row or 0) > MAX_ROWS + 1:
            raise HTTPException(status_code=413, detail="Import supports at most 10,000 rows")
        if (sheet.max_column or 0) > len(FIELDS):
            raise HTTPException(status_code=422, detail="Use only the six columns in the property template")
        header_cells = next(sheet.iter_rows(min_row=1, max_row=1), ())
        headers = validate_headers([cell.value for cell in header_cells])
        records = []
        errors = []
        for row_number, cells in enumerate(sheet.iter_rows(min_row=2), start=2):
            if all(cell.value is None or str(cell.value).strip() == "" for cell in cells):
                continue
            data = {}
            row_errors = []
            for field, cell in zip(headers, cells):
                if cell.data_type == "f":
                    row_errors.append({"field": field, "message": "Replace formulas with plain values"})
                elif field in TEXT_IDENTIFIERS and cell.value is not None and not isinstance(cell.value, str):
                    row_errors.append({"field": field, "message": "Enter this value as Text; numeric cells may lose leading zeros"})
                value = str(cell.value).strip() if cell.value is not None else ""
                data[field] = value if value or field in REQUIRED_FIELDS else None
            if not row_errors:
                try:
                    records.append(PropertyCreate.model_validate(data))
                except ValidationError as error:
                    row_errors = [{"field": str(item["loc"][0]), "message": item["msg"]}
                                  for item in error.errors(include_input=False)]
            if row_errors:
                errors.append({"row": row_number, "errors": row_errors})
                if len(errors) >= 50:
                    break
        return finish_validation(records, errors)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=422, detail="Cannot read this workbook; upload a valid .xlsx file") from None
    finally:
        if workbook is not None:
            workbook.close()


def parse_csv_properties(contents: bytes) -> list[PropertyCreate]:
    if len(contents) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File must be 5 MB or smaller")
    try:
        # utf-8-sig accepts normal UTF-8 and Excel's optional UTF-8 BOM.
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="Save the file as CSV UTF-8 and upload again") from None
    try:
        # Regional Excel settings may export ';' rather than ','. Detect the header separator.
        first_line = text.split("\n", 1)[0]
        try:
            delimiter = csv.Sniffer().sniff(first_line, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
        reader = csv.reader(StringIO(text, newline=""), delimiter=delimiter, strict=True)
        headers = validate_headers(next(reader, []))
        records = []
        errors = []
        for row_number, values in enumerate(reader, start=2):
            if row_number > MAX_ROWS + 1:
                raise HTTPException(status_code=413, detail="Import supports at most 10,000 rows")
            if not values or all(not value.strip() for value in values):
                continue
            row_errors = []
            if len(values) != len(headers):
                row_errors.append({"field": "row", "message": "Row must have the same number of columns as the headings"})
            else:
                data = {}
                for field, value in zip(headers, values):
                    value = value.strip()
                    data[field] = value if value or field in REQUIRED_FIELDS else None
                try:
                    records.append(PropertyCreate.model_validate(data))
                except ValidationError as error:
                    row_errors = [{"field": str(item["loc"][0]), "message": item["msg"]}
                                  for item in error.errors(include_input=False)]
            if row_errors:
                errors.append({"row": row_number, "errors": row_errors})
                if len(errors) >= 50:
                    break
        return finish_validation(records, errors)
    except csv.Error:
        raise HTTPException(status_code=422, detail="Cannot read this CSV; check its quoting and separators") from None


def save_properties(records: list[PropertyCreate], admin_id: int) -> dict:
    # Preserve the first instance of each complete row in the file.
    unique = {}
    for record in records:
        key = tuple((getattr(record, field) or "").strip() for field in FIELDS)
        unique.setdefault(key, record)
    duplicates_in_file = len(records) - len(unique)
    imported = 0
    rows = [tuple(getattr(record, field) for field in FIELDS) + (admin_id,)
            for record in unique.values()]
    settings = get_settings()
    try:
        with psycopg.connect(settings.db_url.get_secret_value(), connect_timeout=15) as connection:
            with connection.cursor() as cursor:
                for start in range(0, len(rows), 500):
                    cursor.executemany(
                        "INSERT INTO properties (owner_name, phone_01, phone_02, "
                        "ic_number, address, remark, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT ON CONSTRAINT properties_duplicate_key_unique DO NOTHING",
                        rows[start:start + 500],
                    )
                    imported += cursor.rowcount
            result = {
                "imported": imported,
                "duplicates_skipped": len(records) - imported,
                "duplicates_in_file": duplicates_in_file,
                "already_in_database": len(unique) - imported,
            }
            record_audit(connection, admin_id, "properties.imported", "property",
                         details={"count": imported, "duplicates_skipped": result["duplicates_skipped"]})
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Import failed; no rows were saved. Try again later.") from None
    return result


@router.get("/import-template")
def import_template(admin: dict = Depends(require_admin)):
    return excel_response([], "property_import_template.xlsx", template=True)


@router.post("/import", status_code=201)
async def import_properties(
    file: UploadFile = File(..., description="Excel (.xlsx) or CSV UTF-8 (.csv) property file"),
    admin: dict = Depends(require_admin),
):
    try:
        filename = (file.filename or "").lower()
        if not filename.endswith((".xlsx", ".csv")):
            raise HTTPException(status_code=422, detail="Upload an Excel .xlsx or CSV .csv file")
        contents = await file.read(MAX_FILE_BYTES + 1)
        parser = parse_csv_properties if filename.endswith(".csv") else parse_properties
        records = await run_in_threadpool(parser, contents)
        return await run_in_threadpool(save_properties, records, admin["id"])
    finally:
        await file.close()


@router.get("/export")
def export_properties(
    search: str | None = Query(default=None, max_length=200),
    admin: dict = Depends(require_admin),
):
    conditions = []
    values = []
    if search and search.strip():
        term = search.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        conditions.append(
            "(owner_name ILIKE %s ESCAPE '!' OR remark ILIKE %s ESCAPE '!' "
            "OR address ILIKE %s ESCAPE '!' OR phone_01 ILIKE %s ESCAPE '!' "
            "OR phone_02 ILIKE %s ESCAPE '!' OR ic_number ILIKE %s ESCAPE '!')"
        )
        values.extend([f"%{term}%"] * 6)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    settings = get_settings()
    try:
        with psycopg.connect(settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15) as connection:
            rows = connection.execute(
                "SELECT " + ", ".join(FIELDS) + " FROM properties" + where + " ORDER BY id DESC LIMIT %s",
                (*values, MAX_ROWS + 1),
            ).fetchall()
            if len(rows) > MAX_ROWS:
                raise HTTPException(status_code=413, detail="Export exceeds 10,000 rows; narrow your search")
            response = excel_response(rows, "properties.xlsx")
            record_audit(connection, admin["id"], "properties.exported", "property",
                         details={"count": len(rows), "search_used": bool(search and search.strip())})
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Export is temporarily unavailable") from None
    return response
