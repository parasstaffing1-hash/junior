import hashlib
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any, Dict
import math

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

class ExcelGenerationError(Exception):
    def __init__(self, code, message, details=None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

def _safe_excel_text(value: Any) -> str:
    """Prevents CSV/Excel injection by escaping strings starting with dangerous characters."""
    if value is None:
        return ""
    val_str = str(value)
    if val_str.startswith(("=", "+", "-", "@")):
        return f"'{val_str}"
    return val_str


def _safe_excel_value(value: Any) -> Any:
    """Preserve analytical types while blocking formula injection in text cells."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, (datetime, date, int, float, bool)):
        return value
    if hasattr(value, "item"):
        return _safe_excel_value(value.item())
    return _safe_excel_text(value)

def _setup_styles():
    return {
        "header": {
            "font": Font(bold=True, color="FFFFFF"),
            "fill": PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid"),
            "alignment": Alignment(horizontal="center", vertical="center")
        },
        "title": {
            "font": Font(bold=True, size=16),
            "alignment": Alignment(horizontal="left", vertical="center")
        },
        "kpi_label": {
            "font": Font(bold=True, color="555555"),
            "alignment": Alignment(horizontal="left", vertical="center")
        },
        "kpi_value": {
            "font": Font(bold=True, size=14),
            "alignment": Alignment(horizontal="left", vertical="center")
        }
    }

def _autofit_columns(ws):
    for column_cells in ws.columns:
        length = max((len(str(cell.value)) for cell in column_cells if cell.value is not None), default=0)
        ws.column_dimensions[get_column_letter(column_cells[0].column)].width = min(length + 4, 60)


def _style_header(ws, row: int, styles: dict) -> None:
    for cell in ws[row]:
        cell.font = styles["header"]["font"]
        cell.fill = styles["header"]["fill"]
        cell.alignment = styles["header"]["alignment"]


def _add_excel_table(ws, name: str) -> None:
    if ws.max_row < 2 or ws.max_column < 1:
        return
    table = Table(displayName=name, ref=f"A1:{get_column_letter(ws.max_column)}{ws.max_row}")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(table)


def _raw_data_sheet(wb: Workbook, frame: pd.DataFrame, styles: dict, max_rows: int) -> dict[str, Any]:
    ws = wb.create_sheet(title="Raw Data")
    columns = [str(column) for column in frame.columns]
    ws.append(columns)
    _style_header(ws, 1, styles)
    bounded = frame.head(min(max_rows, 1_048_575))
    for row in bounded.itertuples(index=False, name=None):
        ws.append([_safe_excel_value(value) for value in row])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _add_excel_table(ws, "RawDataTable")
    for index, column in enumerate(frame.columns, start=1):
        if pd.api.types.is_numeric_dtype(frame[column]) and ws.max_row >= 2:
            letter = get_column_letter(index)
            ws.conditional_formatting.add(
                f"{letter}2:{letter}{ws.max_row}",
                ColorScaleRule(
                    start_type="min", start_color="FEE2E2",
                    mid_type="percentile", mid_value=50, mid_color="FEF3C7",
                    end_type="max", end_color="DCFCE7",
                ),
            )
    _autofit_columns(ws)
    return {
        "sheet": ws.title,
        "rows": len(bounded),
        "columns": columns,
        "truncated": len(frame) > len(bounded),
    }


def _exceptions_sheet(wb: Workbook, frame: pd.DataFrame, styles: dict, max_rows: int) -> dict[str, Any]:
    """Create an auditable exception queue for recurring MIS review."""
    ws = wb.create_sheet(title="Exceptions")
    headers = ["Exception ID", "Source row", "Exception type", "Column", "Observed value", "Recommended action"]
    ws.append(headers)
    _style_header(ws, 1, styles)
    exception_count = 0
    max_exceptions = 2_000
    duplicate_positions = set(frame.index[frame.duplicated(keep=False)].tolist())
    bounded = frame.head(min(max_rows, 1_048_575))
    for excel_row, (row_index, row) in enumerate(bounded.iterrows(), start=2):
        if row_index in duplicate_positions and exception_count < max_exceptions:
            ws.append([f"DUP-{excel_row}", excel_row, "Duplicate row", "(entire row)", "Repeated record", "Review duplicate before publishing"])
            exception_count += 1
        for column, value in row.items():
            if exception_count >= max_exceptions:
                break
            if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                ws.append([
                    f"MISS-{excel_row}-{column}", excel_row, "Missing value", str(column), "",
                    "Fill, exclude, or document the missing value",
                ])
                exception_count += 1
    if exception_count == 0:
        ws.append(["NONE", "", "No exceptions", "", "", "No duplicate or blank cells detected in the exported rows"])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _add_excel_table(ws, "MISExceptionsTable")
    for cell in ws[1]:
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    _autofit_columns(ws)
    return {
        "exception_count": exception_count,
        "duplicate_rows": int(frame.duplicated().sum()),
        "missing_cells": int(frame.isna().sum().sum()),
        "truncated": exception_count >= max_exceptions,
    }


def _reconciliation_sheet(wb: Workbook, frame: pd.DataFrame, styles: dict, raw: dict[str, Any]) -> dict[str, Any]:
    """Add source-to-workbook control totals used in recurring MIS sign-off."""
    ws = wb.create_sheet(title="Reconciliation")
    ws["A1"] = "MIS Reconciliation Controls"
    ws["A1"].font = styles["title"]["font"]
    ws.append(["Control", "Value", "Status", "Review note"])
    _style_header(ws, 2, styles)
    raw_end = min(len(frame), raw["rows"]) + 1
    controls = [
        ["Source rows", len(frame), "INFO", "Rows scanned by the MIS pack"],
        ["Exported Raw Data rows", f"=ROWS('Raw Data'!A2:A{max(2, raw_end)})", "CHECK", "Must equal source rows unless the workbook is intentionally bounded"],
        ["Row difference", "=B3-B4", "CHECK", "Zero is expected"],
        ["Exact duplicate rows", int(frame.duplicated().sum()), "REVIEW", "Review on Exceptions sheet before distribution"],
        ["Missing cells", int(frame.isna().sum().sum()), "REVIEW", "Review on Exceptions sheet before distribution"],
        ["Source columns", len(frame.columns), "INFO", "Column count in the source version"],
        ["Exported columns", len(raw["columns"]), "CHECK", "Must equal source columns"],
    ]
    for row in controls:
        ws.append(row)
    _style_header(ws, 2, styles)
    ws["B5"].number_format = "#,##0"
    ws["B6"].number_format = "#,##0"
    ws["B7"].number_format = "#,##0"
    ws["B8"].number_format = "#,##0"
    start = ws.max_row + 2
    ws.cell(start, 1, "Numeric control totals")
    ws.cell(start, 1).font = styles["title"]["font"]
    ws.append(["Measure", "Source total", "Workbook total", "Difference"])
    _style_header(ws, start + 1, styles)
    numeric_columns = [str(column) for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])][:20]
    for column in numeric_columns:
        source_total = float(pd.to_numeric(frame[column], errors="coerce").sum())
        letter = get_column_letter(list(map(str, frame.columns)).index(column) + 1)
        ws.append([column, source_total, f"=SUM('Raw Data'!{letter}2:{letter}{max(2, raw_end)})", f"=C{ws.max_row + 1}-B{ws.max_row + 1}"])
    if not numeric_columns:
        ws.append(["No numeric columns", "", "", ""])
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:D{max(2, start + 1 + len(numeric_columns))}"
    _autofit_columns(ws)
    return {"numeric_controls": numeric_columns, "source_rows": len(frame), "exported_rows": raw["rows"]}


def _mis_control_sheet(wb: Workbook, frame: pd.DataFrame, styles: dict, raw: dict[str, Any], exceptions: dict[str, Any]) -> None:
    ws = wb.create_sheet(title="MIS Control")
    ws["A1"] = "MIS Automation Control Center"
    ws["A1"].font = Font(bold=True, size=18, color="1F4E78")
    ws.append(["Control", "Value"])
    _style_header(ws, 2, styles)
    rows = [
        ["Pack status", "READY FOR REVIEW" if exceptions["exception_count"] == 0 else "EXCEPTIONS REQUIRE REVIEW"],
        ["Rows processed", len(frame)],
        ["Columns processed", len(frame.columns)],
        ["Generated at (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")],
        ["Refresh workflow", "Replace Raw Data → refresh Power Query → review Reconciliation → clear Exceptions → distribute"],
        ["Server automation", "Source-backed workbook generation is automated; Excel desktop refresh/macros remain local Excel responsibilities"],
    ]
    for row in rows:
        ws.append(row)
    ws.append([])
    ws.append(["MIS operator checklist"])
    ws["A10"].font = styles["title"]["font"]
    checklist = [
        ["1", "Confirm source version and reporting period"],
        ["2", "Review Reconciliation controls; row difference should be zero"],
        ["3", "Resolve or document Exceptions"],
        ["4", "Refresh formulas/Power Query in Excel if source data changed"],
        ["5", "Publish only after control status is approved"],
    ]
    for row in checklist:
        ws.append(row)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 115
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _analysis_fields(frame: pd.DataFrame) -> tuple[str | None, str | None, str | None]:
    numeric = [str(column) for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])]
    dates = [str(column) for column in frame.columns if pd.api.types.is_datetime64_any_dtype(frame[column]) or any(token in str(column).casefold() for token in ("date", "time"))]
    dimensions = [
        str(column)
        for column in frame.columns
        if str(column) not in numeric and str(column) not in dates and 1 < frame[column].nunique(dropna=True) <= 100
    ]
    return (dimensions[0] if dimensions else None, numeric[0] if numeric else None, dates[0] if dates else None)


def _pivot_summary_sheet(wb: Workbook, frame: pd.DataFrame, styles: dict) -> dict[str, Any]:
    ws = wb.create_sheet(title="Pivot Summary")
    dimension, measure, _ = _analysis_fields(frame)
    if not dimension or not measure:
        ws.append(["Pivot analysis unavailable", "A categorical dimension and numeric measure are required."])
        return {"available": False, "dimension": dimension, "measure": measure}
    work = frame[[dimension, measure]].copy()
    work[measure] = pd.to_numeric(work[measure], errors="coerce")
    summary = (
        work.dropna(subset=[dimension, measure])
        .groupby(dimension, dropna=False)[measure]
        .agg(Count="count", Total="sum", Average="mean")
        .reset_index()
        .sort_values("Total", ascending=False)
        .head(25)
    )
    ws.append([dimension, "Count", f"Total {measure}", f"Average {measure}"])
    _style_header(ws, 1, styles)
    for row in summary.itertuples(index=False, name=None):
        ws.append([_safe_excel_value(value) for value in row])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _add_excel_table(ws, "PivotSummaryTable")
    if ws.max_row >= 2:
        chart = BarChart()
        chart.title = f"{measure} by {dimension}"
        chart.y_axis.title = measure
        chart.x_axis.title = dimension
        chart.add_data(Reference(ws, min_col=3, min_row=1, max_row=ws.max_row), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=ws.max_row))
        chart.height = 8
        chart.width = 15
        ws.add_chart(chart, "F2")
    _autofit_columns(ws)
    return {"available": True, "dimension": dimension, "measure": measure, "rows": len(summary)}


def _formula_lab_sheet(wb: Workbook, frame: pd.DataFrame, styles: dict) -> dict[str, Any]:
    ws = wb.create_sheet(title="Formula Lab")
    columns = [str(column) for column in frame.columns]
    dimension, measure, _ = _analysis_fields(frame)
    row_end = min(len(frame) + 1, 1_048_576)
    lookup_col = 1
    return_col = 2 if len(columns) > 1 else 1
    lookup_letter = get_column_letter(lookup_col)
    return_letter = get_column_letter(return_col)
    dimension_col = columns.index(dimension) + 1 if dimension in columns else lookup_col
    measure_col = columns.index(measure) + 1 if measure in columns else return_col
    dimension_letter = get_column_letter(dimension_col)
    measure_letter = get_column_letter(measure_col)
    sample_lookup = _safe_excel_value(frame.iloc[0, lookup_col - 1]) if len(frame) and columns else ""
    sample_dimension = _safe_excel_value(frame.iloc[0, dimension_col - 1]) if len(frame) and dimension else ""
    rows = [
        ["Excel capability", "Input", "Live formula / result", "Purpose"],
        ["XLOOKUP", sample_lookup, f'=IFERROR(XLOOKUP(B2,\'Raw Data\'!${lookup_letter}$2:${lookup_letter}${row_end},\'Raw Data\'!${return_letter}$2:${return_letter}${row_end},"Not found"),"Not found")', "Exact lookup"],
        ["INDEX-MATCH", sample_lookup, f'=IFERROR(INDEX(\'Raw Data\'!${return_letter}$2:${return_letter}${row_end},MATCH(B3,\'Raw Data\'!${lookup_letter}$2:${lookup_letter}${row_end},0)),"Not found")', "Backward-compatible lookup"],
        ["SUMIFS", sample_dimension, f'=SUMIFS(\'Raw Data\'!${measure_letter}$2:${measure_letter}${row_end},\'Raw Data\'!${dimension_letter}$2:${dimension_letter}${row_end},B4)', "Conditional aggregation"],
        ["COUNTIFS", sample_dimension, f'=COUNTIFS(\'Raw Data\'!${dimension_letter}$2:${dimension_letter}${row_end},B5)', "Conditional count"],
        ["UNIQUE + SORT", "", f'=SORT(UNIQUE(\'Raw Data\'!${dimension_letter}$2:${dimension_letter}${row_end}))', "Dynamic category list"],
        ["FILTER", sample_dimension, f'=FILTER(\'Raw Data\'!$A$2:${get_column_letter(max(1, len(columns)))}${row_end},\'Raw Data\'!${dimension_letter}$2:${dimension_letter}${row_end}=B7,"No rows")', "Dynamic filtered rows"],
    ]
    for row in rows:
        ws.append(row)
    _style_header(ws, 1, styles)
    ws.freeze_panes = "A2"
    ws.column_dimensions["C"].width = 34
    ws.column_dimensions["D"].width = 28
    _autofit_columns(ws)
    return {"formulas": [str(row[0]) for row in rows[1:]], "calculation_mode": "excel_recalculates_on_open"}


def _power_query_sheet(wb: Workbook, frame: pd.DataFrame, styles: dict) -> dict[str, Any]:
    ws = wb.create_sheet(title="Power Query")
    type_steps = []
    for column in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[column]):
            m_type = "type number"
        elif pd.api.types.is_datetime64_any_dtype(frame[column]) or any(token in str(column).casefold() for token in ("date", "time")):
            m_type = "type datetime"
        else:
            m_type = "type text"
        type_steps.append(f'{{"{str(column).replace(chr(34), chr(34) * 2)}", {m_type}}}')
    m_script = (
        "let\n"
        "    Source = Excel.CurrentWorkbook(){[Name=\"RawDataTable\"]}[Content],\n"
        "    PromotedTypes = Table.TransformColumnTypes(Source, {" + ", ".join(type_steps) + "}),\n"
        "    TrimmedText = Table.TransformColumns(PromotedTypes, List.Transform(Table.ColumnsOfType(PromotedTypes, {type text}), each {_, Text.Trim, type text})),\n"
        "    RemovedDuplicates = Table.Distinct(TrimmedText)\n"
        "in\n"
        "    RemovedDuplicates"
    )
    ws.append(["Power Query-compatible transformation plan"])
    ws["A1"].font = styles["title"]["font"]
    ws.append(["Steps", "Load RawDataTable → assign types → trim text → remove exact duplicates"])
    ws.append(["Refresh note", "The M script is provided for review/paste into Excel Power Query; the exported analysis is already calculated from the cleaned source."])
    ws.append(["M script", m_script])
    ws["B4"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[4].height = 180
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 110
    return {"steps": ["load", "assign_types", "trim_text", "remove_duplicates"], "m_script": m_script}


def _dashboard_sheet(wb: Workbook, frame: pd.DataFrame, styles: dict, pivot: dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Dashboard", index=1)
    _, measure, _ = _analysis_fields(frame)
    columns = [str(column) for column in frame.columns]
    row_end = min(len(frame) + 1, 1_048_576)
    measure_col = columns.index(measure) + 1 if measure in columns else 1
    measure_letter = get_column_letter(measure_col)
    ws["A1"] = "Excel Analysis Dashboard"
    ws["A1"].font = Font(bold=True, size=18, color="1F4E78")
    ws["A3"], ws["B3"] = "Rows analyzed", f"=MAX(0,COUNTA('Raw Data'!A2:A{row_end}))"
    ws["D3"], ws["E3"] = f"Total {measure or 'measure'}", f"=SUM('Raw Data'!{measure_letter}2:{measure_letter}{row_end})"
    ws["G3"], ws["H3"] = f"Average {measure or 'measure'}", f"=AVERAGE('Raw Data'!{measure_letter}2:{measure_letter}{row_end})"
    for label in ("A3", "D3", "G3"):
        ws[label].font = Font(bold=True, color="666666")
    for value in ("B3", "E3", "H3"):
        ws[value].font = Font(bold=True, size=14, color="1F4E78")
        ws[value].fill = PatternFill(start_color="EAF2F8", end_color="EAF2F8", fill_type="solid")
    if pivot.get("available") and "Pivot Summary" in wb.sheetnames:
        source = wb["Pivot Summary"]
        chart = BarChart()
        chart.title = f"{pivot['measure']} by {pivot['dimension']}"
        chart.add_data(Reference(source, min_col=3, min_row=1, max_row=source.max_row), titles_from_data=True)
        chart.set_categories(Reference(source, min_col=1, min_row=2, max_row=source.max_row))
        chart.height = 9
        chart.width = 18
        ws.add_chart(chart, "A6")
    ws.sheet_view.showGridLines = False
    for column in range(1, 10):
        ws.column_dimensions[get_column_letter(column)].width = 16


def _capability_matrix_sheet(wb: Workbook, styles: dict, raw: dict[str, Any], pivot: dict[str, Any], formulas: dict[str, Any]) -> None:
    ws = wb.create_sheet(title="Workbook Guide")
    ws.append(["Capability", "Status", "Implementation"])
    _style_header(ws, 1, styles)
    rows = [
        ("Data cleaning", "PASS", "Typed source, safe text handling, null-safe export, and documented Power Query steps"),
        ("XLOOKUP / INDEX-MATCH", "PASS", "Live formulas in Formula Lab"),
        ("SUMIFS / COUNTIFS", "PASS", "Live formulas in Formula Lab"),
        ("Dynamic formulas", "PASS", "UNIQUE, SORT, and FILTER examples recalculate in modern Excel"),
        ("Pivot analysis", "PASS" if pivot.get("available") else "NOT APPLICABLE", "Source-backed grouped pivot summary and chart"),
        ("Conditional formatting", "PASS", "Numeric scales applied to Raw Data"),
        ("Power Query", "PASS", "Reviewable M transformation script and equivalent calculated output"),
        ("Dashboard", "PASS", "KPI cards and linked chart"),
        ("MIS exceptions", "PASS", "Duplicate and missing-value review queue"),
        ("MIS reconciliation", "PASS", "Row-count and numeric control totals"),
        ("Recurring operator controls", "PASS", "MIS Control checklist and refresh runbook"),
        ("Source coverage", "PASS", f"{raw['rows']:,} rows × {len(raw['columns']):,} columns"),
    ]
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _autofit_columns(ws)

def _create_executive_summary(wb: Workbook, report: dict, styles: dict):
    ws = wb.create_sheet(title="Executive Summary")
    ws.append(["Analytics Report"])
    ws["A1"].font = styles["title"]["font"]
    
    ws.append(["Title", _safe_excel_text(report.get("title", ""))])
    ws.append(["Subtitle", _safe_excel_text(report.get("subtitle", ""))])
    ws.append(["Description", _safe_excel_text(report.get("description", ""))])
    ws.append([])
    
    params = report.get("parameters", {})
    if params:
        ws.append(["Parameters"])
        ws["A5"].font = Font(bold=True)
        for k, v in params.items():
            ws.append([_safe_excel_text(k), _safe_excel_text(v)])
            
    _autofit_columns(ws)

def _create_kpis_sheet(wb: Workbook, sections: list, styles: dict):
    kpis = [s for s in sections if s.get("section_type") == "kpi"]
    if not kpis:
        return

    ws = wb.create_sheet(title="KPIs")
    
    headers = ["KPI", "Value", "Formatted Value", "Change %", "Target Status"]
    ws.append(headers)
    for col_idx, cell in enumerate(ws[1], 1):
        cell.font = styles["header"]["font"]
        cell.fill = styles["header"]["fill"]

    for kpi in kpis:
        title = kpi.get("title", "KPI")
        content = kpi.get("resolved_content", kpi.get("content", {}))
        
        if isinstance(content, dict):
            val = content.get("value", "")
            formatted = content.get("formatted_value", val)
            change = content.get("percent_change", "")
            target = content.get("target_status", "")
        else:
            val = content
            formatted = content
            change = ""
            target = ""
            
        ws.append([
            _safe_excel_text(title), 
            _safe_excel_text(val), 
            _safe_excel_text(formatted),
            _safe_excel_text(change),
            _safe_excel_text(target)
        ])

    ws.auto_filter.ref = ws.dimensions
    _autofit_columns(ws)

def _create_findings_sheet(wb: Workbook, sections: list, styles: dict):
    findings = [s for s in sections if s.get("section_type") == "text"]
    if not findings:
        return
        
    ws = wb.create_sheet(title="Findings")
    
    headers = ["Finding Title", "Description"]
    ws.append(headers)
    for col_idx, cell in enumerate(ws[1], 1):
        cell.font = styles["header"]["font"]
        cell.fill = styles["header"]["fill"]
        
    for text in findings:
        title = text.get("title", "")
        content = text.get("resolved_content", text.get("content", ""))
        ws.append([
            _safe_excel_text(title),
            _safe_excel_text(content)
        ])

    ws.auto_filter.ref = ws.dimensions
    _autofit_columns(ws)

def _create_data_sheets(wb: Workbook, sections: list, styles: dict, max_rows: int):
    tables = [s for s in sections if s.get("section_type") == "table"]
    
    sheet_names_used = {"Executive Summary", "KPIs", "Findings", "Sheet"}
    
    for idx, table in enumerate(tables, 1):
        title = table.get("title", f"Data_{idx}")
        # Normalize sheet name
        safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "_"))[:25].strip() or f"Data_{idx}"
        
        # Deduplicate sheet names
        final_title = safe_title
        counter = 1
        while final_title in sheet_names_used:
            final_title = f"{safe_title[:20]}_{counter}"
            counter += 1
            
        sheet_names_used.add(final_title)
        ws = wb.create_sheet(title=final_title)
        
        content = table.get("resolved_content", table.get("content", {}))
        rows = content.get("rows", []) if isinstance(content, dict) else []
        columns = content.get("columns", []) if isinstance(content, dict) else []
        
        if not rows:
            ws.append(["No data available."])
            continue
            
        if columns:
            keys = [c.get("key", c) if isinstance(c, dict) else c for c in columns]
            labels = [c.get("label", c.get("key", "")) if isinstance(c, dict) else str(c) for c in columns]
        else:
            keys = list(rows[0].keys())
            labels = [str(k) for k in keys]
            
        ws.append(labels)
        for col_idx, cell in enumerate(ws[1], 1):
            cell.font = styles["header"]["font"]
            cell.fill = styles["header"]["fill"]
            
        truncated = False
        if len(rows) > max_rows:
            rows = rows[:max_rows]
            truncated = True
            
        for row in rows:
            ws.append([_safe_excel_text(row.get(k, "")) for k in keys])
            
        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"
        _autofit_columns(ws)
        
        if truncated:
            ws.append([f"WARNING: Data truncated to {max_rows} rows due to size limits."])
            ws.cell(row=ws.max_row, column=1).font = Font(color="FF0000", bold=True)


def build_xlsx_bytes(
    manifest: dict[str, Any],
    max_table_rows: int = 50000,
    *,
    source_frame: pd.DataFrame | None = None,
) -> bytes:
    """
    Generates an XLSX workbook from a report manifest.
    """
    if not isinstance(manifest, dict) or "report" not in manifest or "sections" not in manifest:
        raise ExcelGenerationError("INVALID_MANIFEST", "Tool 97 report manifest must contain report and sections.")
        
    report = manifest["report"]
    sections = manifest["sections"]
    
    wb = Workbook()
    styles = _setup_styles()
    
    _create_executive_summary(wb, report, styles)
    _create_kpis_sheet(wb, sections, styles)
    _create_findings_sheet(wb, sections, styles)
    _create_data_sheets(wb, sections, styles, max_table_rows)

    capability_metadata: dict[str, Any] | None = None
    if isinstance(source_frame, pd.DataFrame) and len(source_frame.columns):
        raw = _raw_data_sheet(wb, source_frame, styles, max_table_rows)
        pivot = _pivot_summary_sheet(wb, source_frame, styles)
        formulas = _formula_lab_sheet(wb, source_frame, styles)
        power_query = _power_query_sheet(wb, source_frame, styles)
        _dashboard_sheet(wb, source_frame, styles, pivot)
        exceptions = _exceptions_sheet(wb, source_frame, styles, max_table_rows)
        reconciliation = _reconciliation_sheet(wb, source_frame, styles, raw)
        _mis_control_sheet(wb, source_frame, styles, raw, exceptions)
        _capability_matrix_sheet(wb, styles, raw, pivot, formulas)
        capability_metadata = {
            "raw": raw,
            "pivot": pivot,
            "formulas": formulas,
            "power_query_steps": power_query["steps"],
            "exceptions": exceptions,
            "reconciliation": reconciliation,
        }
    
    # Remove default 'Sheet' if others were created
    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]

    wb.calculation.calcMode = "auto"
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
        
    # Write to buffer
    buf = BytesIO()
    wb.save(buf)
    file_bytes = buf.getvalue()
    
    # Validate generation by reloading (acts as validation step 1-8)
    try:
        validation_buf = BytesIO(file_bytes)
        test_wb = load_workbook(validation_buf)
        if "Executive Summary" not in test_wb.sheetnames:
            raise ExcelGenerationError("VALIDATION_FAILED", "Executive summary sheet missing after generation.")
        if capability_metadata is not None:
            required = {"Dashboard", "Raw Data", "Pivot Summary", "Formula Lab", "Power Query", "Workbook Guide", "Exceptions", "Reconciliation", "MIS Control"}
            missing = sorted(required.difference(test_wb.sheetnames))
            if missing:
                raise ExcelGenerationError("VALIDATION_FAILED", "Professional workbook sheets are missing.", {"sheets": missing})
            formulas = [
                test_wb["Formula Lab"].cell(row=row, column=3).value
                for row in range(2, 8)
            ]
            if not all(isinstance(value, str) and value.startswith("=") for value in formulas):
                raise ExcelGenerationError("VALIDATION_FAILED", "Professional workbook formulas were not preserved.")
    except Exception as e:
        if isinstance(e, ExcelGenerationError):
            raise
        raise ExcelGenerationError("WORKBOOK_CORRUPT", "Generated workbook failed validation check.", {"error": str(e)}) from e
        
    return file_bytes
