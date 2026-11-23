"""A small reader for Excel binary workbooks (``.xlsb``), using only the standard library.

Plain-language summary
----------------------
The CDFI Fund publishes its New Markets Tax Credit tract list only as an Excel
binary workbook (``.xlsb``). Python's common spreadsheet libraries either do not
read this format or carry a licence this project cannot use, so this module
reads it directly.

An ``.xlsb`` file is a zip archive, like ``.xlsx``, but its sheets are stored as
a stream of binary **records** instead of XML. Each record starts with its type
and its length, both written as "variable-length integers" (7 bits per byte;
the top bit says another byte follows), followed by the record's bytes. Only a
handful of record types matter for reading cell values:

======  ==================  ============================================
type    name                what it holds
======  ==================  ============================================
0       row header          the row number of the cells that follow
2       RK number           a number packed into 4 bytes
4       boolean             true or false
5       real number         an 8-byte floating-point number
6       inline text         a text value stored in the cell itself
7       shared text         a pointer into the shared-strings list
8, 9    formula results     text or number results of formulas
19      shared string       one entry of ``xl/sharedStrings.bin``
======  ==================  ============================================

Every cell record starts with its column number (4 bytes) and a style
reference (4 bytes); the value comes after. Text is UTF-16 with a 4-byte
character count in front. The format is published by Microsoft as
"[MS-XLSB]: Excel (.xlsb) Binary File Format".

Only the first sheet is read, and members over 400 MB are refused.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
from collections.abc import Iterator
from pathlib import Path

__all__ = ["records", "xlsb_rows"]

_MAX_BYTES = 400_000_000
ROW, BLANK, RK, ERROR, BOOL, REAL, TEXT, SHARED = 0, 1, 2, 3, 4, 5, 6, 7
FORMULA_TEXT, FORMULA_NUM, FORMULA_BOOL, SST_ITEM = 8, 9, 10, 19


def records(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield ``(record type, record bytes)`` for every record of a binary part.

    Raises:
        ValueError: If a record runs past the end of the data.
    """
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        i += 1
        rtype = b & 0x7F
        if b & 0x80:
            rtype |= (data[i] & 0x7F) << 7
            i += 1
        size = 0
        for k in range(4):
            b = data[i]
            i += 1
            size |= (b & 0x7F) << (7 * k)
            if not b & 0x80:
                break
        if i + size > n:
            raise ValueError("xlsb record runs past the end of the part")
        yield rtype, data[i : i + size]
        i += size


def _wide_text(buf: bytes, offset: int) -> str:
    (count,) = struct.unpack_from("<I", buf, offset)
    return buf[offset + 4 : offset + 4 + 2 * count].decode("utf-16-le")


def _rk(value: int) -> float:
    """Unpack an RK number: bit 0 = divide by 100, bit 1 = integer, else top 30 bits of a double."""
    if value & 2:
        number = value >> 2
        if number & (1 << 29):
            number -= 1 << 30
        out = float(number)
    else:
        out = struct.unpack("<d", struct.pack("<Q", (value & 0xFFFFFFFC) << 32))[0]
    return out / 100 if value & 1 else out


def _member(zf: zipfile.ZipFile, name: str) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > _MAX_BYTES:
        raise ValueError(f"{name} is {info.file_size:,} bytes; refusing to parse")
    return zf.read(name)


def _first_sheet(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.bin", n)]
    if not names:
        raise ValueError("workbook has no binary sheets")
    return min(names, key=lambda n: int(re.findall(r"\d+", n)[-1]))


def _cell_value(rtype: int, body: bytes, strings: list[str]) -> str | float | bool | None:
    if rtype == RK:
        return _rk(struct.unpack_from("<I", body, 8)[0])
    if rtype in (REAL, FORMULA_NUM):
        return struct.unpack_from("<d", body, 8)[0]
    if rtype == SHARED:
        return strings[struct.unpack_from("<I", body, 8)[0]]
    if rtype in (TEXT, FORMULA_TEXT):
        return _wide_text(body, 8)
    if rtype in (BOOL, FORMULA_BOOL):
        return bool(body[8])
    return None


def xlsb_rows(source: bytes | str | Path) -> Iterator[list[str | float | bool | None]]:
    """Yield the first sheet's rows as lists of cell values (text, number, bool or ``None``).

    Empty rows are skipped; missing cells inside a row are ``None``.
    """
    data = Path(source).read_bytes() if isinstance(source, str | Path) else bytes(source)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        strings: list[str] = []
        if "xl/sharedStrings.bin" in zf.namelist():
            for rtype, body in records(_member(zf, "xl/sharedStrings.bin")):
                if rtype == SST_ITEM:
                    strings.append(_wide_text(body, 1))
        sheet = _member(zf, _first_sheet(zf))
    cells: dict[int, str | float | bool | None] = {}
    for rtype, body in records(sheet):
        if rtype == ROW:
            if cells:
                yield [cells.get(i) for i in range(max(cells) + 1)]
            cells = {}
        elif rtype in (RK, REAL, SHARED, TEXT, FORMULA_TEXT, FORMULA_NUM, BOOL, FORMULA_BOOL):
            (col,) = struct.unpack_from("<I", body, 0)
            cells[col] = _cell_value(rtype, body, strings)
    if cells:
        yield [cells.get(i) for i in range(max(cells) + 1)]
