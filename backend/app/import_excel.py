"""Bounded-memory XLSX reading for the pinned openpyxl 3.1.5 reader.

Read-only openpyxl still materializes all shared strings in RAM. Keep small string
tables in memory and spill larger ones to flat temporary files, without SQLite.
The normal openpyxl cell parser retains formula, number, date and rich-text handling.
"""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import mmap
import struct
import sys
from tempfile import TemporaryDirectory
from xml.parsers.expat import ParserCreate, ExpatError

from defusedxml.ElementTree import iterparse
from defusedxml.common import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from openpyxl.reader.excel import ExcelReader
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet._read_only import ReadOnlyWorksheet
from openpyxl.xml.constants import SHARED_STRINGS, SHEET_MAIN_NS


MEMORY_STRING_BUDGET = 32 * 1024 * 1024


def shared_strings(source):
    # Extract text directly instead of allocating an Element for every si/t/run.
    # Feed bounded blocks so completed strings can be spilled to disk immediately.
    parser = ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    si, run, text = (f"{SHEET_MAIN_NS}}}{name}" for name in ("si", "r", "t"))
    stack, parts, ready = [], [], []
    collecting = False

    def start(name, attributes):
        nonlocal collecting
        stack.append(name)
        collecting = (name == text and (stack[-2:] == [si, text]
                      or stack[-3:] == [si, run, text]))

    def end(name):
        nonlocal collecting
        if name == si:
            ready.append("".join(parts).replace("x005F_", ""))
            parts.clear()
        stack.pop()
        collecting = False

    def data(value):
        if collecting:
            parts.append(value)

    def forbid_dtd(name, system_id, public_id, internal):
        raise DTDForbidden(name, system_id, public_id)

    def forbid_entity(name, parameter, value, base, system_id, public_id, notation):
        raise EntitiesForbidden(name, value, base, system_id, public_id, notation)

    def forbid_external(context, base, system_id, public_id):
        raise ExternalReferenceForbidden(context, base, system_id, public_id)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = data
    parser.StartDoctypeDeclHandler = forbid_dtd
    parser.EntityDeclHandler = forbid_entity
    parser.ExternalEntityRefHandler = forbid_external
    try:
        while block := source.read(64 * 1024):
            parser.Parse(block, False)
            yield from ready
            ready.clear()
        parser.Parse(b"", True)
        yield from ready
    except ExpatError:
        raise ValueError("Invalid Excel shared string XML") from None


class DiskStrings:
    PAGE_SIZE = 256
    OFFSET = struct.Struct("<Q")

    def __init__(self, path):
        self.data = Path(path).with_suffix(".text").open("w+b")
        self.offsets = Path(path).with_suffix(".offsets").open("w+b")
        self.count = 0
        self.size = 0
        self.mapping = None
        self.page = lru_cache(maxsize=8)(self._page)

    def load(self, source):
        batch = []
        for value in shared_strings(source):
            batch.append(value)
            if len(batch) == 1000:
                self.extend(batch)
                batch.clear()
        if batch:
            self.extend(batch)
        self.finish()

    def extend(self, values):
        positions = bytearray()
        content = []
        for value in values:
            encoded = value.encode("utf-8")
            positions.extend(self.OFFSET.pack(self.size))
            content.append(encoded)
            self.size += len(encoded)
            self.count += 1
        self.offsets.write(positions)
        self.data.write(b"".join(content))

    def finish(self):
        self.offsets.flush()
        self.data.flush()
        if self.count:
            self.mapping = mmap.mmap(self.offsets.fileno(), 0, access=mmap.ACCESS_READ)

    def position(self, index):
        return self.OFFSET.unpack_from(self.mapping, index * self.OFFSET.size)[0]

    def _page(self, number):
        start = number * self.PAGE_SIZE
        end = min(start + self.PAGE_SIZE, self.count)
        offset = self.position(start)
        stop = self.position(end) if end < self.count else self.size
        self.data.seek(offset)
        content = self.data.read(stop - offset)
        positions = [self.position(index) - offset for index in range(start, end)] + [stop - offset]
        return tuple(content[left:right].decode("utf-8") for left, right in zip(positions, positions[1:]))

    def __getitem__(self, index):
        if index < 0 or index >= self.count:
            raise IndexError("Invalid shared string reference")
        try:
            return self.page(index // self.PAGE_SIZE)[index % self.PAGE_SIZE]
        except IndexError:
            raise IndexError("Invalid shared string reference") from None

    def close(self):
        self.page.cache_clear()
        if self.mapping is not None:
            self.mapping.close()
        self.offsets.close()
        self.data.close()


class AdaptiveStrings:
    def __init__(self, path, memory_budget=MEMORY_STRING_BUDGET):
        self.path = path
        self.memory_budget = memory_budget
        self.values = []
        self.disk = None

    def load(self, source):
        used = 0
        batch = []
        for value in shared_strings(source):
            if self.disk is None:
                used += sys.getsizeof(value) + 16  # Include list capacity and references.
                if used <= self.memory_budget:
                    self.values.append(value)
                    continue
                self.disk = DiskStrings(self.path)
                for start in range(0, len(self.values), 1000):
                    self.disk.extend(self.values[start:start + 1000])
                self.values.clear()
            batch.append(value)
            if len(batch) == 1000:
                self.disk.extend(batch)
                batch.clear()
        if self.disk is not None:
            if batch:
                self.disk.extend(batch)
            self.disk.finish()

    def __getitem__(self, index):
        if index < 0:
            raise IndexError("Invalid shared string reference")
        return self.disk[index] if self.disk is not None else self.values[index]

    def close(self):
        self.values.clear()
        if self.disk is not None:
            self.disk.close()


class StreamingWorksheet(ReadOnlyWorksheet):
    def _get_size(self):
        with self._get_source() as source:
            for _, node in iterparse(source, events=("start",)):
                if node.tag == f"{{{SHEET_MAIN_NS}}}dimension":
                    dimensions = range_boundaries(node.attrib["ref"])
                    self._min_column, self._min_row, self._max_column, self._max_row = dimensions
                    return
                if node.tag == f"{{{SHEET_MAIN_NS}}}sheetData":
                    return


class StreamingExcelReader(ExcelReader):
    def __init__(self, path, strings):
        super().__init__(path, read_only=True, data_only=False, keep_links=False)
        self.disk_strings = strings

    def read_strings(self):
        part = self.package.find(SHARED_STRINGS)
        if part is not None:
            with self.archive.open(part.PartName[1:]) as source:
                self.disk_strings.load(source)
            self.shared_strings = self.disk_strings

    def read_worksheets(self):
        # This adapter is deliberately read-only; never use it to author workbooks.
        for sheet, relation in self.parser.find_sheets():
            if relation.target not in self.valid_files:
                continue
            if "chartsheet" in relation.Type:
                raise ValueError("Use a worksheet with property records")
            worksheet = StreamingWorksheet(self.wb, sheet.name, relation.target, self.shared_strings)
            worksheet.sheet_state = sheet.state
            self.wb._sheets.append(worksheet)


@contextmanager
def open_import_workbook(path):
    # Keep the index beside the assembled upload; all temporary artifacts are removed
    # on success, failure, shutdown or a closed iterator.
    with TemporaryDirectory(prefix="xlsx-strings-", dir=Path(path).parent) as folder:
        strings = AdaptiveStrings(Path(folder) / "strings")
        reader = None
        try:
            reader = StreamingExcelReader(path, strings)
            reader.read()
            yield reader.wb
        finally:
            if reader is not None:
                reader.archive.close()
            strings.close()
