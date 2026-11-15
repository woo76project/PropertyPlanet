import hashlib
import json
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.auth import require_admin
from app.audit import record_audit
from app.credits import balance_for
from app.core.config import get_settings
from app.property_search import SearchBuilder, builder_clause, quick_search_clause
from app.search_rate_limit import require_preview_search, require_credit_search

router = APIRouter(prefix="/properties", tags=["Properties"])


class PropertyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner_name: str = Field(min_length=1, max_length=200)
    phone_01: str = Field(min_length=1, max_length=30)
    phone_02: str | None = Field(default=None, min_length=1, max_length=30)
    ic_number: str | None = Field(default=None, min_length=1, max_length=30)
    address: str | None = Field(default=None, min_length=1, max_length=1000)
    remark: str | None = Field(default=None, min_length=1, max_length=2000)


class PropertyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone_01: str | None = Field(default=None, min_length=1, max_length=30)
    phone_02: str | None = Field(default=None, min_length=1, max_length=30)
    ic_number: str | None = Field(default=None, min_length=1, max_length=30)
    address: str | None = Field(default=None, min_length=1, max_length=1000)
    remark: str | None = Field(default=None, min_length=1, max_length=2000)


@router.get("/summary")
def property_summary(user: dict = Depends(require_admin)):
    settings = get_settings()
    with psycopg.connect(
        settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
    ) as connection:
        total = connection.execute("SELECT COUNT(*) AS total FROM properties").fetchone()["total"]
    return {"total": total}


@router.get("")
def list_properties(
    search: str | None = Query(default=None, max_length=200),
    filters: str | None = Query(default=None, max_length=6000),
    before_id: int | None = Query(default=None, gt=0), #gt =greater than
    limit: int = Query(default=20, ge=1, le=100), #ge = greater than or equal to, le = less than or equal to, restrict to 1-100
    user: dict = Depends(require_admin),
):
    conditions = []
    values = []
    clause, parameters = builder_clause(filters)
    if clause:
        conditions.append(clause)
        values.extend(parameters)
    clause, parameters = quick_search_clause(search)
    if clause:
        conditions.append(clause)
        values.extend(parameters)
    if before_id is not None:
        conditions.append("id < %s")
        values.append(before_id)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = (
        "SELECT id, owner_name, phone_01, phone_02, ic_number, "
        "address, remark, created_by, updated_by, created_at, updated_at "
        "FROM properties" + where + " ORDER BY id DESC LIMIT %s"
    )
    settings = get_settings()
    with psycopg.connect(
        settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
    ) as connection:
        # One extra row tells us if another page exists without counting all rows.
        rows = connection.execute(query, (*values, limit + 1)).fetchall()

    has_more = len(rows) > limit
    results = rows[:limit]
    return {
        "properties": results,
        "pagination": {
            "limit": limit,
            "has_more": has_more,
            "next_before_id": results[-1]["id"] if has_more else None,
        },
    }


@router.get("/preview")
def preview_properties(
    search: str | None = Query(default=None, max_length=200),
    filters: str | None = Query(default=None, max_length=6000),
    before_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(require_preview_search),
):
    # Free previews expose owner name and address, never contact/IC/remark data.
    # Apply before LIMIT so blank addresses cannot fill or shorten preview pages.
    conditions = ["address IS NOT NULL", "BTRIM(address) <> ''", "LOWER(BTRIM(address)) <> 'nil'"]
    values = []
    clause, parameters = builder_clause(filters, preview=True)
    if clause:
        conditions.append(clause)
        values.extend(parameters)
    clause, parameters = quick_search_clause(search, preview=True)
    if clause:
        conditions.append(clause)
        values.extend(parameters)
    if before_id is not None:
        conditions.append("id < %s")
        values.append(before_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = ("SELECT id, owner_name, address FROM properties"
             + where + " ORDER BY id DESC LIMIT %s")
    settings = get_settings()
    with psycopg.connect(
        settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15,
    ) as connection:
        rows = connection.execute(query, (*values, limit + 1)).fetchall()
    has_more = len(rows) > limit
    # Explicit allowlist also prevents future SELECT changes leaking private fields.
    fields = ("id", "owner_name", "address")
    results = [{field: row[field] for field in fields} for row in rows[:limit]]
    return {
        "properties": results,
        "pagination": {
            "limit": limit, "has_more": has_more,
            "next_before_id": results[-1]["id"] if has_more else None,
        },
    }


class CreditSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    search: str = Field(default="", max_length=200)
    filters: SearchBuilder | None = None
    before_id: int | None = Field(default=None, gt=0)
    limit: int = Field(default=20, ge=1, le=100)


@router.post("/search")
def search_with_credit(data: CreditSearchRequest, user: dict = Depends(require_credit_search)):
    if not data.search and data.filters is None:
        raise HTTPException(status_code=422, detail="Enter a search clue before using a credit")
    canonical = data.model_dump(mode="json", exclude={"request_id", "before_id", "limit"})
    query_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    conditions, values = [], []
    if data.filters:
        clause, params = builder_clause(data.filters.model_dump_json())
        conditions.append(clause)
        values.extend(params)
    if data.search:
        clause, parameters = quick_search_clause(data.search)
        conditions.append(clause)
        values.extend(parameters)
    with psycopg.connect(get_settings().db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15) as connection:
        # Serialize every credit grant/spend with account suspension/deletion.
        current = connection.execute("SELECT id, role, status FROM users WHERE id = %s FOR UPDATE", (user["id"],)).fetchone()
        if not current or current["status"] != "Active":
            raise HTTPException(status_code=403, detail="Account is not active")
        unlimited = current["role"] == "Admin"
        prior = None if unlimited else connection.execute("SELECT * FROM credit_searches WHERE request_id = %s", (data.request_id,)).fetchone()
        if prior and (prior["user_id"] != user["id"] or prior["query_hash"] != query_hash):
            raise HTTPException(status_code=409, detail="Start a new search when changing your search conditions")
        balance = 0 if unlimited else balance_for(connection, user["id"])
        if not unlimited and not prior and balance < 1:
            raise HTTPException(status_code=402, detail="You need a search credit. Subscribe via WhatsApp to purchase credits.")
        max_id = prior["max_property_id"] if prior else connection.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM properties").fetchone()["max_id"]
        conditions.append("id <= %s")
        values.append(max_id)
        if data.before_id is not None:
            conditions.append("id < %s")
            values.append(data.before_id)
        # A new search always starts on its first page, preventing paid blind skips.
        if not prior and data.before_id is not None and not unlimited:
            raise HTTPException(status_code=409, detail="Start your search from its first page")
        rows = connection.execute(
            "SELECT id, owner_name, phone_01, phone_02, ic_number, "
            "address, remark, created_by, updated_by, created_at, updated_at "
            "FROM properties WHERE " + " AND ".join(conditions) + " ORDER BY id DESC LIMIT %s",
            (*values, data.limit + 1),
        ).fetchall()
        charged = False
        # Only a new search with matches costs a credit. Empty results do not
        # create a paid-search record; query failures also never consume credits.
        if not unlimited and not prior and rows:
            updated = connection.execute(
                "UPDATE users SET credit_balance = credit_balance - 1, updated_at = NOW() "
                "WHERE id = %s AND credit_balance > 0 RETURNING credit_balance AS balance", (user["id"],),
            ).fetchone()
            if not updated:
                raise HTTPException(status_code=402, detail="You need a search credit. Subscribe via WhatsApp to purchase credits.")
            balance = updated["balance"]
            connection.execute(
                "INSERT INTO credit_searches (request_id, user_id, query_hash, max_property_id) VALUES (%s, %s, %s, %s)",
                (data.request_id, user["id"], query_hash, max_id),
            )
            charged = True
    has_more = len(rows) > data.limit
    results = rows[:data.limit]
    return {"properties": results, "pagination": {"limit": data.limit, "has_more": has_more,
            "next_before_id": results[-1]["id"] if has_more else None},
            "balance": balance, "charged": charged, "unlimited": unlimited}


@router.get("/{property_id}")
def get_property(
    property_id: int = Path(gt=0),
    user: dict = Depends(require_admin),
):
    settings = get_settings()
    with psycopg.connect(
        settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
    ) as connection:
        property_record = connection.execute(
            "SELECT id, owner_name, phone_01, phone_02, ic_number, "
            "address, remark, created_by, updated_by, created_at, updated_at "
            "FROM properties WHERE id = %s",
            (property_id,),
        ).fetchone()
    if not property_record:
        raise HTTPException(status_code=404, detail="Property not found")
    return property_record


@router.post("", status_code=201)
def create_property(data: PropertyCreate, admin: dict = Depends(require_admin)):
    settings = get_settings()
    try:
        with psycopg.connect(
            settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
        ) as connection:
            property_record = connection.execute(
                """
                INSERT INTO properties (
                    owner_name, phone_01, phone_02,
                    ic_number, address, remark, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, owner_name, phone_01, phone_02,
                          ic_number, address, remark, created_by,
                          updated_by, created_at, updated_at
                """,
                (data.owner_name, data.phone_01,
                 data.phone_02, data.ic_number, data.address,
                 data.remark, admin["id"]),
            ).fetchone()
            record_audit(connection, admin["id"], "property.created", "property", property_record["id"])
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="An identical property record already exists") from None
    return property_record


@router.delete("/{property_id}", status_code=204)
def delete_property(
    property_id: int = Path(gt=0),
    admin: dict = Depends(require_admin),
):
    settings = get_settings()
    with psycopg.connect(
        settings.db_url.get_secret_value(), connect_timeout=15
    ) as connection:
        deleted = connection.execute(
            "DELETE FROM properties WHERE id = %s RETURNING id",
            (property_id,),
        ).fetchone()
        if not deleted:
            raise HTTPException(status_code=404, detail="Property not found")
        record_audit(connection, admin["id"], "property.deleted", "property", property_id)
    return Response(status_code=204)


@router.patch("/{property_id}")
def update_property(
    data: PropertyUpdate,
    property_id: int = Path(gt=0),
    admin: dict = Depends(require_admin),
):
    # Only fields actually sent in the JSON are changed.
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide at least one field to update")

    for field in ("owner_name", "phone_01"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")

    # Identifier safely quotes column names; values still use placeholders.
    assignments = [
        sql.SQL("{} = %s").format(sql.Identifier(field)) for field in changes
    ]
    assignments.extend([sql.SQL("updated_by = %s"), sql.SQL("updated_at = NOW()")])
    query = sql.SQL(
        "UPDATE properties SET {} WHERE id = %s "
        "RETURNING id, owner_name, phone_01, phone_02, "
        "ic_number, address, remark, created_by, "
        "updated_by, created_at, updated_at"
    ).format(sql.SQL(", ").join(assignments))

    settings = get_settings()
    try:
        with psycopg.connect(
            settings.db_url.get_secret_value(), row_factory=dict_row, connect_timeout=15
        ) as connection:
            property_record = connection.execute(
                query, (*changes.values(), admin["id"], property_id)
            ).fetchone()
            if not property_record:
                raise HTTPException(status_code=404, detail="Property not found")
            record_audit(connection, admin["id"], "property.updated", "property", property_id,
                         {"fields": sorted(changes)})
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="These changes would duplicate another property record") from None
    return property_record
