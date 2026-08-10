from __future__ import annotations
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
import re

class XLSXGenerationError(Exception):
    def __init__(self, code, message, details=None):
        self.code=code; self.message=message; self.details=details or {}; super().__init__(message)

INVALID_SHEET = re.compile(r'[:\\/?*\[\]]')

def sheet_name(name, used):
    raw = INVALID_SHEET.sub(" ", str(name or "Sheet")).strip() or "Sheet"
    raw = raw[:31]
    candidate = raw
    i = 2
    while candidate in used:
        suffix = f" {i}"
        candidate = raw[:31-len(suffix)] + suffix
        i += 1
    used.add(candidate)
    return candidate

def col_letter(n):
    s = ""
    while n:
        n, rem = divmod(n-1, 26)
        s = chr(65+rem) + s
    return s

def _xml_text(v):
    return escape("" if v is None else str(v))

def cell(ref, value, style=0):
    if value is None:
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" s="{style}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int,float)) and not isinstance(value,bool):
        return f'<c r="{ref}" s="{style}" t="n"><v>{value}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(value)}</t></is></c>'

def worksheet_xml(rows, widths=None, freeze=True, autofilter=None):
    max_cols = max((len(r) for r in rows), default=1)
    widths = widths or {}
    cols = ""
    for i in range(1, max_cols+1):
        width = min(45, max(8, float(widths.get(i,14))))
        cols += f'<col min="{i}" max="{i}" width="{width:.1f}" customWidth="1"/>'
    xml_rows = []
    for ridx, row in enumerate(rows, 1):
        cells = []
        for cidx, item in enumerate(row,1):
            value, style = item if isinstance(item, tuple) else (item,0)
            cells.append(cell(f"{col_letter(cidx)}{ridx}", value, style))
        height = ' ht="28" customHeight="1"' if ridx == 1 else ""
        xml_rows.append(f'<row r="{ridx}"{height}>{"".join(cells)}</row>')
    pane = '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>' if freeze else ""
    af = f'<autoFilter ref="{autofilter}"/>' if autofilter else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetViews><sheetView workbookViewId="0">{pane}</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="18"/>'
        f'<cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData>{af}'
        '<pageMargins left="0.4" right="0.4" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
        '</worksheet>'
    )

def styles_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="0.00%"/></numFmts>'
        '<fonts count="4">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="18"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FF1F1F1F"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="4">'
        '<fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color rgb="FFD9D9D9"/></left><right style="thin"><color rgb="FFD9D9D9"/></right>'
        '<top style="thin"><color rgb="FFD9D9D9"/></top><bottom style="thin"><color rgb="FFD9D9D9"/></bottom><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="7">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyFill="1" applyFont="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFill="1" applyFont="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>'
        '<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>'
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )

def _table_rows(content):
    rows = (content or {}).get("rows",[]) if isinstance(content,dict) else []
    columns = (content or {}).get("columns") if isinstance(content,dict) else None
    if not rows:
        return [], []
    if columns:
        keys = [c.get("key",c) if isinstance(c,dict) else c for c in columns]
        labels = [c.get("label",c.get("key","")) if isinstance(c,dict) else str(c) for c in columns]
    else:
        keys = list(rows[0].keys())
        labels = [str(x) for x in keys]
    return keys, labels

def build_xlsx_bytes(manifest, *, include_metadata=True, max_table_rows=100000):
    if not isinstance(manifest,dict) or "report" not in manifest or "sections" not in manifest:
        raise XLSXGenerationError("INVALID_MANIFEST","Tool 97 report manifest must contain report and sections.")
    report = manifest["report"]
    sections = manifest["sections"]
    used = set()
    sheets = []

    summary = sheet_name("Report Summary", used)
    sr = [
        [(report.get("title") or "Analytics Report",1)],
        [(report.get("subtitle") or "",3)],
        [("Description",3),(report.get("description") or "",4)],
        [],
        [("KPI Summary",2),("Value",2),("Change %",2),("Target Status",2)],
    ]
    for section in sections:
        if section.get("section_type") != "kpi":
            continue
        content = section.get("resolved_content",section.get("content"))
        content = content if isinstance(content,dict) else {"value":content}
        value = content.get("formatted_value",content.get("value",content.get("current_value","")))
        pct = content.get("percent_change")
        status = content.get("target_status","")
        sr.append([
            (section.get("title") or section.get("source_ref") or "KPI",4),
            (value,4),
            ((float(pct)/100) if isinstance(pct,(int,float)) else "",6),
            (status,4)
        ])
    sr.extend([[],[("Report Sections",2),("Type",2),("Source",2)]])
    for section in sections:
        sr.append([(section.get("title") or "",4),(section.get("section_type") or "",4),(section.get("source_ref") or "",4)])
    params = report.get("parameters") or {}
    if params:
        sr.extend([[],[("Parameters",2),("Value",2)]])
        for k,v in params.items():
            sr.append([(k,4),(v,4)])
    sheets.append({"name":summary,"rows":sr,"widths":{1:28,2:30,3:20,4:18},"freeze":False,"autofilter":None})

    table_num = 0
    for section in sections:
        if section.get("section_type") != "table":
            continue
        content = section.get("resolved_content",section.get("content"))
        keys, labels = _table_rows(content)
        rows = (content or {}).get("rows",[]) if isinstance(content,dict) else []
        table_num += 1
        name = sheet_name(section.get("title") or f"Table {table_num}", used)
        matrix = [[(label,2) for label in labels]]
        widths = {}
        for idx,label in enumerate(labels,1):
            widths[idx] = min(36,max(10,len(str(label))+3))
        for row in rows[:max_table_rows]:
            out = []
            for idx,key in enumerate(keys,1):
                val = row.get(key)
                style = 5 if isinstance(val,float) else 4
                out.append((val,style))
                widths[idx] = min(36,max(widths.get(idx,10),min(36,len(str(val))+2 if val is not None else 10)))
            matrix.append(out)
        af = f"A1:{col_letter(max(1,len(keys)))}{max(1,len(matrix))}" if keys else None
        sheets.append({"name":name,"rows":matrix,"widths":widths,"freeze":True,"autofilter":af})

    if include_metadata:
        name = sheet_name("Metadata", used)
        rows = [
            [("Field",2),("Value",2)],
            [("Report ID",4),(report.get("id",""),4)],
            [("Report Title",4),(report.get("title",""),4)],
            [("Revision",4),(report.get("revision",""),4)],
            [("Section Count",4),(len(sections),4)],
        ]
        for k,v in (report.get("parameters") or {}).items():
            rows.append([(f"Parameter: {k}",4),(v,4)])
        sheets.append({"name":name,"rows":rows,"widths":{1:28,2:45},"freeze":True,"autofilter":f"A1:B{len(rows)}"})

    buf = BytesIO()
    with ZipFile(buf,"w",ZIP_DEFLATED) as z:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1,len(sheets)+1)
        )
        z.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + overrides + '</Types>'
        )
        z.writestr("_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'
        )
        z.writestr("xl/styles.xml", styles_xml())
        sheet_defs = "".join(
            f'<sheet name="{escape(s["name"])}" sheetId="{i}" r:id="rId{i}"/>'
            for i,s in enumerate(sheets,1)
        )
        z.writestr("xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheet_defs}</sheets></workbook>'
        )
        rels = [
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1,len(sheets)+1)
        ]
        rels.append(
            f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        z.writestr("xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels) + '</Relationships>'
        )
        for i,s in enumerate(sheets,1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", worksheet_xml(s["rows"],s["widths"],s["freeze"],s["autofilter"]))
    return buf.getvalue()
