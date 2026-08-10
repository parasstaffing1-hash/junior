import html

class ReportBuilderError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

SECTION_TYPES={"heading","text","kpi","chart","table","page_break"}

def validate_section(s):
    if s.get("section_type") not in SECTION_TYPES:
        raise ReportBuilderError("INVALID_SECTION_TYPE","Unsupported report section type.",{"section_type":s.get("section_type")})
    if s.get("section_type") in {"kpi","chart","table"} and not s.get("source_ref"):
        raise ReportBuilderError("SOURCE_REF_REQUIRED","Data-backed report sections require source_ref.")
    return True

def assemble_report(report,sections,content_by_ref=None,parameters=None,missing_content_policy="placeholder"):
    if missing_content_policy not in {"placeholder","omit","error"}:
        raise ReportBuilderError("INVALID_MISSING_POLICY","Unsupported missing content policy.")
    content_by_ref=content_by_ref or {}
    assembled=[]
    for section in sorted(sections,key=lambda x:(x.get("position",0),x.get("id",""))):
        validate_section(section)
        st=section["section_type"];content=section.get("content")
        if st in {"kpi","chart","table"}:
            ref=section["source_ref"]
            if ref not in content_by_ref:
                if missing_content_policy=="error":
                    raise ReportBuilderError("CONTENT_NOT_FOUND","Runtime content is missing.",{"source_ref":ref})
                if missing_content_policy=="omit":continue
                content={"status":"missing","source_ref":ref}
            else:content=content_by_ref[ref]
        assembled.append({**section,"resolved_content":content})
    return {
        "report":{"id":report["id"],"title":report["title"],"subtitle":report.get("subtitle"),
                  "description":report.get("description"),"revision":report.get("revision"),
                  "parameters":parameters or {}},
        "sections":assembled,
        "section_count":len(assembled)
    }

def render_html(manifest):
    rpt=manifest["report"]
    pieces=[]
    for s in manifest["sections"]:
        st=s["section_type"];title=html.escape(s.get("title") or "")
        content=s.get("resolved_content")
        if st=="page_break":
            pieces.append('<div class="page-break"></div>');continue
        if st=="heading":
            pieces.append(f"<h2>{html.escape(str(content or s.get('content') or title))}</h2>");continue
        if st=="text":
            pieces.append(f'<section><h3>{title}</h3><p>{html.escape(str(content or ""))}</p></section>');continue
        if st=="kpi":
            value=(content or {}).get("formatted_value",(content or {}).get("value","—")) if isinstance(content,dict) else content
            pieces.append(f'<section class="card"><h3>{title}</h3><div class="kpi">{html.escape(str(value))}</div></section>');continue
        if st=="chart":
            pieces.append(f'<section class="card"><h3>{title}</h3><pre>{html.escape(str(content))}</pre></section>');continue
        if st=="table":
            rows=(content or {}).get("rows",[]) if isinstance(content,dict) else []
            if rows:
                cols=list(rows[0].keys())
                head="".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
                body="".join("<tr>"+"".join(f"<td>{html.escape(str(row.get(c,'')))}</td>" for c in cols)+"</tr>" for row in rows)
                table=f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
            else:table="<p>No rows</p>"
            pieces.append(f'<section class="card"><h3>{title}</h3>{table}</section>')
    title=html.escape(rpt["title"])
    subtitle=html.escape(rpt.get("subtitle") or "")
    description=html.escape(rpt.get("description") or "")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:system-ui;margin:0;color:#222}}main{{max-width:1000px;margin:auto;padding:40px}}
.card{{border:1px solid #ddd;border-radius:10px;padding:18px;margin:16px 0}}
.kpi{{font-size:34px;font-weight:700}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;padding:8px;text-align:left}}.page-break{{break-after:page;page-break-after:always}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
</style></head><body><main><h1>{title}</h1><h4>{subtitle}</h4><p>{description}</p>{''.join(pieces)}</main></body></html>"""
