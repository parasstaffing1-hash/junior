from __future__ import annotations
from io import BytesIO
from pathlib import Path
from typing import Any
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, KeepTogether
)

class PDFGenerationError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _safe_text(value):
    if value is None:return ""
    return str(value).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _styles():
    base=getSampleStyleSheet()
    return {
        "title":ParagraphStyle("ReportTitle",parent=base["Title"],fontSize=24,leading=30,spaceAfter=12),
        "subtitle":ParagraphStyle("ReportSubtitle",parent=base["Normal"],fontSize=11,leading=15,textColor=colors.HexColor("#555555"),spaceAfter=8),
        "h2":ParagraphStyle("SectionTitle",parent=base["Heading2"],fontSize=15,leading=20,spaceBefore=10,spaceAfter=8),
        "text":ParagraphStyle("Body",parent=base["BodyText"],fontSize=9.5,leading=14,spaceAfter=8),
        "small":ParagraphStyle("Small",parent=base["BodyText"],fontSize=7.5,leading=10,textColor=colors.HexColor("#666666")),
        "kpi":ParagraphStyle("KPI",parent=base["Normal"],fontSize=22,leading=26,alignment=TA_LEFT,spaceAfter=2),
    }

def _page_footer(canvas,doc):
    canvas.saveState()
    canvas.setFont("Helvetica",7.5)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(18*mm,10*mm,"Generated analytics report")
    canvas.drawRightString(doc.pagesize[0]-18*mm,10*mm,f"Page {doc.page}")
    canvas.restoreState()

def _table_from_content(title,content,styles,max_rows=500):
    rows=(content or {}).get("rows",[]) if isinstance(content,dict) else []
    columns=(content or {}).get("columns") if isinstance(content,dict) else None
    if not rows:
        return [Paragraph(_safe_text(title),styles["h2"]),Paragraph("No rows available.",styles["text"])]
    if columns:
        keys=[c.get("key",c) if isinstance(c,dict) else c for c in columns]
        labels=[c.get("label",c.get("key","")) if isinstance(c,dict) else str(c) for c in columns]
    else:
        keys=list(rows[0].keys())
        labels=[str(k) for k in keys]
    matrix=[labels]
    for row in rows[:max_rows]:
        matrix.append([_safe_text(row.get(k,"")) for k in keys])
    col_count=max(1,len(keys))
    available=170*mm
    widths=[available/col_count]*col_count
    table=Table(matrix,colWidths=widths,repeatRows=1,hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1F4E78")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),7.5),
        ("GRID",(0,0),(-1,-1),0.25,colors.HexColor("#CCCCCC")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#F7F9FC")]),
        ("LEFTPADDING",(0,0),(-1,-1),4),
        ("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    return [Paragraph(_safe_text(title),styles["h2"]),table,Spacer(1,5*mm)]

def build_pdf_bytes(manifest:dict[str,Any],*,document_title=None,author=None,subject=None,
                    page_size="A4",max_table_rows=500):
    if not isinstance(manifest,dict) or "report" not in manifest or "sections" not in manifest:
        raise PDFGenerationError("INVALID_MANIFEST","Tool 97 report manifest must contain report and sections.")
    report=manifest["report"]
    sections=manifest["sections"]
    if page_size not in {"A4","A4_LANDSCAPE"}:
        raise PDFGenerationError("INVALID_PAGE_SIZE","page_size must be A4 or A4_LANDSCAPE.")
    pagesize=A4 if page_size=="A4" else landscape(A4)
    buf=BytesIO()
    doc=SimpleDocTemplate(
        buf,pagesize=pagesize,rightMargin=18*mm,leftMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm,
        title=document_title or report.get("title") or "Analytics Report",
        author=author or "",
        subject=subject or report.get("description") or "",
    )
    st=_styles()
    story=[
        Paragraph(_safe_text(report.get("title") or document_title or "Analytics Report"),st["title"])
    ]
    if report.get("subtitle"):story.append(Paragraph(_safe_text(report["subtitle"]),st["subtitle"]))
    if report.get("description"):story.append(Paragraph(_safe_text(report["description"]),st["text"]))
    params=report.get("parameters") or {}
    if params:
        pairs=" | ".join(f"{_safe_text(k)}: {_safe_text(v)}" for k,v in params.items())
        story.extend([Paragraph(pairs,st["small"]),Spacer(1,5*mm)])
    else:
        story.append(Spacer(1,4*mm))

    for section in sections:
        kind=section.get("section_type")
        title=section.get("title") or ""
        content=section.get("resolved_content",section.get("content"))
        if kind=="page_break":
            story.append(PageBreak());continue
        if kind=="heading":
            story.append(Paragraph(_safe_text(content or title),st["h2"]));continue
        if kind=="text":
            if title:story.append(Paragraph(_safe_text(title),st["h2"]))
            story.append(Paragraph(_safe_text(content),st["text"]));continue
        if kind=="kpi":
            val=""
            if isinstance(content,dict):
                val=content.get("formatted_value",content.get("value",content.get("current_value","")))
            else:val=content
            block=[
                Paragraph(_safe_text(title),st["small"]),
                Paragraph(_safe_text(val),st["kpi"])
            ]
            if isinstance(content,dict):
                meta=[]
                if content.get("percent_change") is not None:meta.append(f"Change: {content['percent_change']:.2f}%")
                if content.get("target_status"):meta.append(f"Target: {content['target_status']}")
                if meta:block.append(Paragraph(" | ".join(meta),st["small"]))
            story.extend([KeepTogether(block),Spacer(1,4*mm)]);continue
        if kind=="chart":
            if title:story.append(Paragraph(_safe_text(title),st["h2"]))
            path=None
            if isinstance(content,dict):
                path=content.get("artifact_path") or content.get("image_path")
            if path and Path(path).exists():
                try:
                    img=Image(path)
                    maxw=170*mm;maxh=100*mm
                    scale=min(maxw/img.imageWidth,maxh/img.imageHeight,1)
                    img.drawWidth=img.imageWidth*scale;img.drawHeight=img.imageHeight*scale
                    story.extend([img,Spacer(1,4*mm)])
                except Exception as e:
                    raise PDFGenerationError("CHART_IMAGE_FAILED","Failed to embed chart image.",{"path":path,"error":str(e)}) from e
            else:
                story.append(Paragraph(_safe_text(content),st["small"]))
            continue
        if kind=="table":
            story.extend(_table_from_content(title,content,st,max_rows=max_table_rows));continue
        raise PDFGenerationError("INVALID_SECTION_TYPE","Unsupported section type.",{"section_type":kind})
    doc.build(story,onFirstPage=_page_footer,onLaterPages=_page_footer)
    return buf.getvalue()
