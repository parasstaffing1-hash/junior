from __future__ import annotations
from html import escape
from typing import Any
import pandas as pd
from pandas.api.types import is_numeric_dtype

from app.core.eda.numeric_eda import analyze_numeric_eda
from app.core.eda.categorical_eda import analyze_categorical_eda
from app.core.eda.numeric_relationships import (
    analyze_numeric_relationships,
    analyze_numeric_categorical_relationships,
)
from app.core.eda.relationships import analyze_categorical_relationships
from app.core.eda.explorer import explore_correlations, explore_distributions
from app.core.eda.group_comparison import analyze_group_comparison
from app.core.eda.findings import detect_findings

class EDAReportError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)

DEFAULT_SECTIONS=["numeric_eda","categorical_eda","numeric_numeric","numeric_categorical","categorical_categorical","correlation_explorer","distribution_explorer","group_comparisons","findings"]

def _safe(section,fn,warnings):
    try:return fn()
    except Exception as e:
        warnings.append({"section":section,"code":getattr(e,"code",e.__class__.__name__),"message":getattr(e,"message",str(e))})
        return None

def _eligible_categorical(df,max_groups):
    return [c for c in df.columns if not is_numeric_dtype(df[c]) and 2<=df[c].nunique(dropna=True)<=max_groups]

def _markdown(title,df,sections,warnings):
    f=(sections.get("findings") or {}).get("findings",[])
    lines=[f"# {title}","",f"Rows: {len(df):,}  ",f"Columns: {len(df.columns):,}",""]
    if f:
        lines += ["## Executive findings",""]
        for x in f[:15]:lines.append(f"- **{x['severity'].upper()} · {x['code']}** — {x['message']}" + (f" Next: {x['recommended_action']}" if x.get('recommended_action') else ""))
        lines.append("")
    ne=sections.get("numeric_eda") or {}
    if ne:
        lines += ["## Numeric overview","",f"Numeric columns analyzed: {len(ne.get('analyzed_columns',[]))}",""]
        for p in ne.get("profiles",[])[:20]:lines.append(f"- **{p['column']}**: mean={p.get('mean')}, median={p.get('median')}, missing={p.get('missing_rate',0):.1%}, outliers={p.get('iqr_outlier_count',0)}")
        lines.append("")
    ce=sections.get("categorical_eda") or {}
    if ce:
        lines += ["## Categorical overview",""]
        for p in ce.get("profiles",[])[:20]:lines.append(f"- **{p['column']}**: {p.get('unique_count')} categories, mode={p.get('mode')}, mode share={p.get('mode_share',0):.1%}")
        lines.append("")
    corr=sections.get("correlation_explorer") or {}
    if corr.get("strongest_positive") or corr.get("strongest_negative"):
        lines += ["## Strong numeric relationships",""]
        for x in (corr.get("strongest_positive",[])[:5]+corr.get("strongest_negative",[])[:5]):lines.append(f"- {x['left']} ↔ {x['right']}: r={x['correlation']:.3f}")
        lines.append("")
    groups=sections.get("group_comparisons") or []
    if groups:
        lines += ["## Group comparisons",""]
        for g in groups[:10]:
            if g.get("status")=="ok": lines.append(f"- {g['value_column']} by {g['group_column']}: best={g['best_group']['group']}, worst={g['worst_group']['group']}, eta²={g['eta_squared']:.3f}")
        lines.append("")
    if warnings:
        lines += ["## Section warnings",""]
        for x in warnings:lines.append(f"- {x['section']}: {x['message']}")
    return "\n".join(lines).strip()+"\n"

def _html(title,md):
    return f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{escape(title)}</title><style>body{{font-family:system-ui;max-width:1150px;margin:40px auto;color:#172033}}pre{{white-space:pre-wrap;line-height:1.55}}</style></head><body><pre>{escape(md)}</pre></body></html>"

def generate_eda_report(df:pd.DataFrame,*,title="Automated EDA Report",sections=None,numeric_columns=None,categorical_columns=None,max_group_comparisons=10,max_groups=20,max_findings=50,histogram_bins=20):
    if not isinstance(df,pd.DataFrame):raise EDAReportError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    wanted=list(sections or DEFAULT_SECTIONS)
    bad=[x for x in wanted if x not in DEFAULT_SECTIONS]
    if bad:raise EDAReportError("INVALID_SECTION","Unknown EDA report section.",{"sections":bad})
    if max_group_comparisons<0:raise EDAReportError("INVALID_GROUP_LIMIT","max_group_comparisons must be >=0.")
    nums=list(numeric_columns) if numeric_columns is not None else [c for c in df.columns if is_numeric_dtype(df[c])]
    cats=list(categorical_columns) if categorical_columns is not None else [c for c in df.columns if not is_numeric_dtype(df[c])]
    unknown=[c for c in nums+cats if c not in df.columns]
    if unknown:raise EDAReportError("UNKNOWN_COLUMN","Selected report column does not exist.",{"columns":unknown})
    warnings=[];out={}
    if "numeric_eda" in wanted and nums:out["numeric_eda"]=_safe("numeric_eda",lambda:analyze_numeric_eda(df,columns=nums,histogram_bins=min(histogram_bins,200)),warnings)
    if "categorical_eda" in wanted and cats:out["categorical_eda"]=_safe("categorical_eda",lambda:analyze_categorical_eda(df,columns=cats,include_numeric_low_cardinality=False),warnings)
    if "numeric_numeric" in wanted and len(nums)>=2:out["numeric_numeric"]=_safe("numeric_numeric",lambda:analyze_numeric_relationships(df,columns=nums),warnings)
    if "numeric_categorical" in wanted and nums and cats:out["numeric_categorical"]=_safe("numeric_categorical",lambda:analyze_numeric_categorical_relationships(df,numeric_columns=nums,categorical_columns=cats,max_groups=max_groups),warnings)
    if "categorical_categorical" in wanted and len(cats)>=2:out["categorical_categorical"]=_safe("categorical_categorical",lambda:analyze_categorical_relationships(df,columns=cats,include_numeric_low_cardinality=False),warnings)
    if "correlation_explorer" in wanted and len(nums)>=2:out["correlation_explorer"]=_safe("correlation_explorer",lambda:explore_correlations(df,columns=nums),warnings)
    if "distribution_explorer" in wanted and nums:out["distribution_explorer"]=_safe("distribution_explorer",lambda:explore_distributions(df,columns=nums,histogram_bins=histogram_bins),warnings)
    if "findings" in wanted:out["findings"]=_safe("findings",lambda:detect_findings(df,max_findings=max_findings),warnings)
    if "group_comparisons" in wanted and nums and cats and max_group_comparisons:
        candidates=[]
        nc=out.get("numeric_categorical") or {}
        for x in nc.get("strongest_relationships",[]):
            if x.get("status")=="ok":candidates.append((x["numeric"],x["categorical"],x.get("eta_squared",0)))
        if not candidates:
            for n in nums:
                for c in _eligible_categorical(df,max_groups):candidates.append((n,c,0))
        seen=set();group_results=[]
        for n,c,_ in sorted(candidates,key=lambda x:-x[2]):
            if (n,c) in seen:continue
            seen.add((n,c))
            if len(group_results)>=max_group_comparisons:break
            r=_safe(f"group_comparison:{n}:{c}",lambda n=n,c=c:analyze_group_comparison(df,value_column=n,group_column=c,max_groups=max_groups),warnings)
            if r:group_results.append({"status":"ok",**r})
        out["group_comparisons"]=group_results
    md=_markdown(title,df,out,warnings)
    return {"title":title,"row_count":len(df),"column_count":len(df.columns),"numeric_columns":nums,"categorical_columns":cats,"included_sections":wanted,"sections":out,"warnings":warnings,"report_markdown":md,"report_html":_html(title,md)}
