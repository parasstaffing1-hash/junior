from __future__ import annotations

import codecs
import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Dict

from app.errors import AppError


# Tool 1 accepts large CSV fields without changing the source bytes. The limit is
# raised once because csv.field_size_limit is process-global.
csv.field_size_limit(max(csv.field_size_limit(), 16 * 1024 * 1024))


@dataclass(frozen=True)
class CsvInspection:
    encoding: str
    delimiter: str
    row_count: int
    column_count: int
    header_columns: list[str]
    has_header: bool = True


def detect_encoding(sample: bytes) -> str:
    if not sample:
        raise AppError("EMPTY_FILE", "CSV file is empty.")
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if sample.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if b"\x00" in sample:
        raise AppError("INVALID_ENCODING", "File contains binary data or an unsupported encoding.")
    try:
        sample.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        try:
            sample.decode("cp1252", errors="strict")
            return "cp1252"
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_ENCODING", "CSV bytes are not valid UTF-8 or Windows-1252.") from exc


def detect_delimiter(text: str) -> str:
    candidates = ",;\t|"
    try:
        return csv.Sniffer().sniff(text[:262144], delimiters=candidates).delimiter
    except csv.Error:
        scored: list[tuple[int, int, str]] = []
        lines = [line for line in text.splitlines()[:100] if line.strip()]
        for delimiter in candidates:
            widths: list[int] = []
            try:
                widths = [len(row) for row in csv.reader(lines, delimiter=delimiter, strict=True)]
            except csv.Error:
                continue
            if widths:
                modal, frequency = Counter(widths).most_common(1)[0]
                if modal > 1:
                    scored.append((frequency, modal, delimiter))
        return max(scored, default=(0, 0, ","))[2]


def _validate_headers(header: list[str]) -> None:
    if not header or any(not cell.strip() for cell in header):
        raise AppError("INVALID_HEADER", "CSV header must contain non-empty column names.")
    folded = [cell.strip().casefold() for cell in header]
    if len(folded) != len(set(folded)):
        raise AppError("DUPLICATE_HEADERS", "CSV header contains duplicate column names.")


def inspect_csv(path: Path, *, has_header: bool = True) -> CsvInspection:
    sample = path.read_bytes()[:262144]
    encoding = detect_encoding(sample)
    try:
        text = sample.decode(encoding, errors="strict")
        with path.open("r", encoding=encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=detect_delimiter(text), strict=True)
            first: list[str] | None = None
            rows = 0
            for record_number, row in enumerate(reader, start=1):
                if not row or (len(row) == 1 and row[0] == "" and first is None):
                    continue
                if first is None:
                    first = row
                    if has_header:
                        _validate_headers(first)
                    else:
                        first = [f"column_{i + 1}" for i in range(len(row))]
                        rows += 1
                    continue
                if len(row) != len(first):
                    raise AppError("INCONSISTENT_COLUMNS", f"CSV record {record_number} has {len(row)} columns; expected {len(first)}.")
                rows += 1
            if first is None:
                raise AppError("NO_RECORDS", "CSV contains no records.")
            return CsvInspection(encoding, detect_delimiter(text), rows, len(first), first, has_header)
    except UnicodeDecodeError as exc:
        raise AppError("INVALID_ENCODING", "CSV could not be decoded.", {"offset": exc.start}) from exc
    except csv.Error as exc:
        raise AppError("MALFORMED_CSV", f"Malformed CSV: {exc}.") from exc


from .base import BaseImporter

class CSVImporter(BaseImporter):
    def inspect(self, path: Path, **kwargs) -> Dict[str, Any]:
        try:
            has_header = kwargs.get('has_header', True)
            inspection = inspect_csv(path, has_header=has_header)
            return {
                "file_type": "csv",
                "delimiter": inspection.delimiter,
                "encoding": inspection.encoding,
                "row_count": inspection.row_count,
                "column_count": inspection.column_count,
                "columns": inspection.header_columns
            }
        except AppError:
            raise
        except Exception as e:
            raise AppError("INVALID_CSV", f"Malformed CSV: {str(e)}", status_code=400)
            
    def load_to_dataframe(self, path: Path, **kwargs) -> pd.DataFrame:
        try:
            has_header = kwargs.get('has_header', True)
            inspection = inspect_csv(path, has_header=has_header)
            
            import pandas as pd
            df = pd.read_csv(
                path,
                encoding=inspection.encoding,
                delimiter=inspection.delimiter,
                header=0 if inspection.has_header else None,
                names=inspection.header_columns if not inspection.has_header else None
            )
            return df
        except Exception as e:
            raise AppError("INVALID_CSV", f"Failed to load CSV: {str(e)}", status_code=400)
