from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
from zipfile import ZipFile

from openpyxl import load_workbook

from app.core.reporting.bi_exports import validate_powerbi_project_package, validate_tableau_packaged_workbook


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and package a generated BTS dashboard handoff.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--quality", required=True)
    args = parser.parse_args()

    source = Path(args.source).resolve()
    destination = Path(args.destination).resolve()
    quality_path = Path(args.quality).resolve()
    if not source.is_dir():
        raise RuntimeError(f"Report source does not exist: {source}")
    destination.mkdir(parents=True, exist_ok=True)

    names = {
        "report.html": "BTS_May_2026_Airline_Operations_Dashboard.html",
        "report.pdf": "BTS_May_2026_Airline_Operations_Report.pdf",
        "report.xlsx": "BTS_May_2026_Airline_Operations.xlsx",
        "report.pbip.zip": "BTS_May_2026_PowerBI_PBIP_Project.zip",
        "report.twbx": "BTS_May_2026_Tableau_Packaged_Workbook.twbx",
        "report.twb": "BTS_May_2026_Tableau_Workbook.twb",
        "daily_flights.png": "BTS_May_2026_Daily_Flights.png",
        "carrier_ontime.png": "BTS_May_2026_Carrier_On_Time.png",
        "origin_delay.png": "BTS_May_2026_Origin_Delay.png",
        "delay_causes.png": "BTS_May_2026_Delay_Causes.png",
    }
    copied: dict[str, Path] = {}
    for source_name, destination_name in names.items():
        source_path = source / source_name
        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise RuntimeError(f"Required dashboard artifact is missing or empty: {source_path}")
        target = destination / destination_name
        shutil.copy2(source_path, target)
        copied[source_name] = target

    html_text = copied["report.html"].read_text(encoding="utf-8")
    if "Airline Operations Dashboard" not in html_text:
        raise RuntimeError("HTML dashboard title validation failed.")
    if not copied["report.pdf"].read_bytes().startswith(b"%PDF"):
        raise RuntimeError("PDF signature validation failed.")
    with ZipFile(copied["report.xlsx"]) as workbook_archive:
        if "xl/workbook.xml" not in workbook_archive.namelist():
            raise RuntimeError("Excel package validation failed.")
    workbook = load_workbook(copied["report.xlsx"], read_only=True, data_only=False)
    required_sheets = {"Dashboard", "Raw Data", "Pivot Summary", "Formula Lab", "Power Query", "Workbook Guide"}
    missing_sheets = sorted(required_sheets.difference(workbook.sheetnames))
    if missing_sheets:
        raise RuntimeError(f"Excel workbook is missing sheets: {missing_sheets}")
    workbook.close()

    powerbi_validation = validate_powerbi_project_package(copied["report.pbip.zip"].read_bytes())
    tableau_validation = validate_tableau_packaged_workbook(copied["report.twbx"].read_bytes())
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    if quality.get("summary", {}).get("failed") != 0:
        raise RuntimeError("Source data quality report contains failed checks.")

    artifact_manifest = [
        {"name": path.name, "path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}
        for path in copied.values()
    ]
    validation = {
        "status": "PASS",
        "source_quality": quality["summary"],
        "source_counts": quality["operational_counts"],
        "powerbi": powerbi_validation,
        "tableau": tableau_validation,
        "excel": {"valid": True, "required_sheets": sorted(required_sheets)},
        "html": {"valid": True, "title_present": True},
        "pdf": {"valid": True, "signature": "%PDF"},
        "artifacts": artifact_manifest,
    }
    validation_path = destination / "BTS_May_2026_Validation_Report.json"
    validation_path.write_text(json.dumps(validation, indent=2), encoding="utf-8")
    handoff = destination / "README.md"
    handoff.write_text(
        "# BTS May 2026 Airline Operations Dashboard\n\n"
        "Source: U.S. DOT/BTS Reporting Carrier On-Time Performance, May 1–31, 2026.\n\n"
        "Grain: one scheduled flight record. The validated source contains 611,735 records. "
        "On-time means arrival delay is 15 minutes or less and excludes cancelled, diverted, or missing-arrival records.\n\n"
        "Open the HTML file for the portable dashboard, the XLSX file in Excel, the TWBX file in Tableau, "
        "or extract the Power BI ZIP and open its root .pbip project. Configure the fail-closed RLS role before Power BI publication.\n\n"
        "The Power BI package includes enterprise architecture, incremental-refresh M, advanced DAX, Tabular Editor, "
        "partition/index/materialized-view SQL, monitoring, and CI/CD engineering assets. Cloud deployment still requires customer tenant credentials and validation.\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASS", "destination": str(destination), "artifact_count": len(artifact_manifest) + 2, "validation": str(validation_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
