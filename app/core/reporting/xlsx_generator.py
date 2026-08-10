import hashlib
from io import BytesIO
from typing import Any, Dict
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
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


def build_xlsx_bytes(manifest: dict[str, Any], max_table_rows: int = 50000) -> bytes:
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
    
    # Remove default 'Sheet' if others were created
    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]
        
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
    except Exception as e:
        raise ExcelGenerationError("WORKBOOK_CORRUPT", "Generated workbook failed validation check.", {"error": str(e)}) from e
        
    return file_bytes
