from __future__ import annotations

import codecs
import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from charset_normalizer import from_bytes

from app.errors import AppError


@dataclass(frozen=True, slots=True)
class CsvInspection:
    encoding: str
    delimiter: str
    row_count: int
    column_count: int
    header_columns: list[str] | None


class CsvInspector:
    CANDIDATE_DELIMITERS = [",", ";", "\t", "|"]

    def __init__(self, sample_bytes: int = 256 * 1024, max_field_bytes: int = 16 * 1024 * 1024):
        self.sample_bytes = sample_bytes
        self.max_field_bytes = max_field_bytes
        # csv.field_size_limit is process-global. Raise it monotonically once so
        # concurrent imports never race by changing/resetting it per request.
        csv.field_size_limit(max(csv.field_size_limit(), max_field_bytes))

    def inspect(self, path: Path, *, has_header: bool) -> CsvInspection:
        sample = self._read_sample(path)
        encoding = self._detect_encoding(sample)
        text_sample = self._decode_sample(sample, encoding)
        delimiter = self._detect_delimiter(text_sample)
        row_count, column_count, header = self._count_and_validate(
            path,
            encoding=encoding,
            delimiter=delimiter,
            has_header=has_header,
        )
        return CsvInspection(
            encoding=encoding,
            delimiter=delimiter,
            row_count=row_count,
            column_count=column_count,
            header_columns=header,
        )

    def _read_sample(self, path: Path) -> bytes:
        with path.open("rb") as f:
            sample = f.read(self.sample_bytes)
        if not sample:
            raise AppError("empty_file", "CSV file is empty.")
        unicode_bom = sample.startswith((
            codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE
        ))
        if b"\x00" in sample and not unicode_bom:
            raise AppError("binary_file", "File contains NUL bytes and does not look like text CSV.")
        if not unicode_bom:
            control_bytes = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 13))
            if control_bytes / len(sample) > 0.01:
                raise AppError("binary_file", "File contains too many binary control bytes to be a text CSV.")
        return sample

    def _detect_encoding(self, sample: bytes) -> str:
        if sample.startswith(codecs.BOM_UTF8):
            return "utf-8-sig"
        # UTF-32 LE begins with the UTF-16 LE BOM prefix, so test UTF-32 first.
        if sample.startswith(codecs.BOM_UTF32_LE) or sample.startswith(codecs.BOM_UTF32_BE):
            return "utf-32"
        if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
            return "utf-16"

        try:
            sample.decode("utf-8", errors="strict")
            return "utf-8"
        except UnicodeDecodeError:
            pass

        match = from_bytes(sample).best()
        candidates: list[str] = []
        if match is not None and match.encoding:
            candidates.append(match.encoding.lower())
        # Western CSV exports are commonly Windows-1252/Latin-1. Keep these as
        # conservative fallbacks for short samples that statistical detectors can
        # misclassify as a multibyte Asian encoding.
        candidates.extend(["cp1252", "latin-1"])

        seen: set[str] = set()
        for encoding in candidates:
            if encoding in seen:
                continue
            seen.add(encoding)
            try:
                decoded = sample.decode(encoding, errors="strict")
            except (LookupError, UnicodeDecodeError):
                continue
            if self._ascii_structure_preserved(sample, decoded):
                return encoding

        raise AppError("unknown_encoding", "Could not detect a reliable text encoding.")

    @staticmethod
    def _ascii_structure_preserved(sample: bytes, decoded: str) -> bool:
        # CSV structural bytes must remain visible after decoding. This prevents
        # short Western files from being falsely classified as encodings where a
        # delimiter byte can be consumed as part of a multibyte character.
        for char in [",", ";", "\t", "|", "\n", "\r"]:
            if sample.count(char.encode("ascii")) != decoded.count(char):
                return False
        return True

    @staticmethod
    def _decode_sample(sample: bytes, encoding: str) -> str:
        try:
            return sample.decode(encoding, errors="strict")
        except UnicodeDecodeError as exc:
            raise AppError(
                "invalid_encoding",
                f"CSV contains bytes that are invalid for detected encoding {encoding}.",
                {"offset": exc.start},
            ) from exc

    def _detect_delimiter(self, text_sample: str) -> str:
        # First try Python's CSV dialect detector.
        try:
            dialect = csv.Sniffer().sniff(text_sample, delimiters="".join(self.CANDIDATE_DELIMITERS))
            return dialect.delimiter
        except csv.Error:
            pass

        # Deterministic fallback: choose the delimiter with the most stable multi-column width.
        logical_lines = [line for line in text_sample.splitlines()[:100] if line.strip()]
        scored: list[tuple[int, int, str]] = []
        for delimiter in self.CANDIDATE_DELIMITERS:
            widths: list[int] = []
            try:
                for row in csv.reader(logical_lines, delimiter=delimiter, strict=True):
                    widths.append(len(row))
            except csv.Error:
                continue
            if not widths:
                continue
            modal_width, frequency = Counter(widths).most_common(1)[0]
            if modal_width > 1:
                scored.append((frequency, modal_width, delimiter))
        if scored:
            scored.sort(reverse=True)
            return scored[0][2]

        # A valid one-column CSV has no visible delimiter. Comma is used as the canonical dialect.
        return ","

    def _count_and_validate(self, path: Path, *, encoding: str, delimiter: str, has_header: bool) -> tuple[int, int, list[str] | None]:
        try:
            with path.open("r", encoding=encoding, errors="strict", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter, strict=True)
                first_row: list[str] | None = None
                data_rows = 0
                physical_record = 0
                for row in reader:
                    if len(row) == 0:
                        continue
                    physical_record += 1
                    if any(len(cell) > self.max_field_bytes for cell in row):
                        raise AppError("field_too_large", "A CSV field exceeds the configured size limit.")
                    if first_row is None:
                        first_row = row
                        if len(row) > 100_000:
                            raise AppError("too_many_columns", "CSV has an unreasonable number of columns.")
                        if not has_header:
                            data_rows += 1
                        continue
                    if len(row) != len(first_row):
                        raise AppError(
                            "inconsistent_columns",
                            f"CSV record {physical_record} has {len(row)} columns; expected {len(first_row)}.",
                            {"record": physical_record, "expected": len(first_row), "actual": len(row)},
                        )
                    data_rows += 1

                if first_row is None:
                    raise AppError("no_records", "CSV contains no records.")
                if has_header and not any(cell.strip() for cell in first_row):
                    raise AppError("empty_header", "CSV header contains no column names.")

                return data_rows, len(first_row), first_row if has_header else None
        except UnicodeDecodeError as exc:
            raise AppError(
                "invalid_encoding",
                f"CSV cannot be fully decoded as {encoding}.",
                {"offset": exc.start},
            ) from exc
        except csv.Error as exc:
            if "field larger than field limit" in str(exc).lower():
                raise AppError("field_too_large", "A CSV field exceeds the configured size limit.") from exc
            raise AppError("malformed_csv", f"Malformed CSV: {exc}.") from exc
