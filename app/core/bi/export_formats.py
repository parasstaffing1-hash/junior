"""Which artifacts a BI report build produces, shared by every report generator."""

from __future__ import annotations

from collections.abc import Iterable

# Document exports are rendered from the report manifest alone.
DOCUMENT_EXPORT_FORMATS: tuple[str, ...] = ("html", "pdf", "xlsx")
# Desktop exports package a full data extract, so they dominate build time.
DESKTOP_EXPORT_FORMATS: tuple[str, ...] = ("powerbi", "tableau", "tableau_twb")
ALL_EXPORT_FORMATS: tuple[str, ...] = DOCUMENT_EXPORT_FORMATS + DESKTOP_EXPORT_FORMATS

# The interactive workspace needs a report manifest immediately.  Binary document
# exports and desktop packages are generated only when the user explicitly asks to
# download one, so a large dataset never leaves the UI waiting on packaging work.
INTERACTIVE_EXPORT_FORMATS: tuple[str, ...] = ("html",)

EXPORT_FILE_NAMES: dict[str, str] = {
    "html": "report.html",
    "pdf": "report.pdf",
    "xlsx": "report.xlsx",
    "powerbi": "report.pbip.zip",
    "tableau": "report.twbx",
    "tableau_twb": "report.twb",
}


def resolve_export_formats(requested: Iterable[str] | None) -> frozenset[str]:
    """Normalize a requested export selection, defaulting to every format."""
    if requested is None:
        return frozenset(ALL_EXPORT_FORMATS)
    formats = {str(item).strip().casefold() for item in requested}
    unknown = sorted(formats - set(ALL_EXPORT_FORMATS))
    if unknown:
        raise ValueError(
            f"Unsupported BI export format(s): {', '.join(unknown)}. "
            f"Supported formats: {', '.join(ALL_EXPORT_FORMATS)}."
        )
    if "tableau" in formats:
        # The editable workbook source is built in the same pass as the package.
        formats.add("tableau_twb")
    return frozenset(formats)


def write_export_files(output_path, export_formats: frozenset[str], payloads: dict[str, bytes]) -> dict[str, str]:
    """Write every selected export to the report directory and return their paths."""
    files: dict[str, str] = {}
    selected = [name for name in ALL_EXPORT_FORMATS if name in export_formats]
    if not selected:
        return files
    output_path.mkdir(parents=True, exist_ok=True)
    for name in selected:
        path = output_path / EXPORT_FILE_NAMES[name]
        path.write_bytes(payloads[name])
        files[name] = str(path)
    return files
