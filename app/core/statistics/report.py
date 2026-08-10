from __future__ import annotations
from html import escape
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype
from app.core.statistics.descriptive_stats import analyze_descriptive_statistics
from app.core.statistics.percentiles import analyze_percentiles
from app.core.statistics.correlation import analyze_correlations
from app.core.statistics.covariance import analyze_covariance
from app.core.statistics.distribution import analyze_distributions
from app.core.statistics.normality import analyze_normality
from app.core.statistics.confidence_intervals import calculate_confidence_interval
from app.core.statistics.effect_size import calculate_effect_size
class StatisticsReportError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
DEFAULT_SECTIONS=["descriptive","percentiles","correlation","covariance","distribution","normality"]
def _fmt(v):
    if v is None:return "—"
    if isinstance(v,float):
        if math.isnan(v) or math.isinf(v):return "—"
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return str(v)
def _safe_call(name,fn,warnings):
    try:return fn()
    except Exception as e:
        warnings.append({"section":name,"code":getattr(e,"code",e.__class__.__name__),"message":getattr(e,"message",str(e))});return None
def _findings(sections):
    findings=[]
    for s in (sections.get("descriptive") or {}).get("statistics",[]):
        if s.get("null_rate_pct",0)>=20:findings.append({"severity":"warning","code":"HIGH_MISSINGNESS","message":f"{s['column']} has {s['null_rate_pct']:.1f}% missing values."})
        cv=s.get("coefficient_of_variation")
        if cv is not None and abs(cv)>=1:findings.append({"severity":"info","code":"HIGH_RELATIVE_VARIABILITY","message":f"{s['column']} has high relative variability (CV={cv:.2f})."})
    for d in (sections.get("distribution") or {}).get("distributions",[]):
        if d.get("iqr_outlier_count",0)>0:findings.append({"severity":"info","code":"IQR_OUTLIERS","message":f"{d['column']} has {d['iqr_outlier_count']} IQR outlier(s)."})
        if d.get("shape") not in {None,"unknown","approximately_symmetric"}:findings.append({"severity":"info","code":"SKEWED_DISTRIBUTION","message":f"{d['column']} is classified as {d['shape']}."})
    corr=sections.get("correlation") or {}
    pairs=corr.get("strongest_pairs") or corr.get("strongest_absolute_pairs") or []
    for p in pairs[:5]:
        val=p.get("correlation")
        if val is not None and abs(val)>=.7:findings.append({"severity":"info","code":"STRONG_CORRELATION","message":f"{p.get('column_a',p.get('left'))} and {p.get('column_b',p.get('right'))} have strong correlation ({val:.3f})."})
    for n in (sections.get("normality") or {}).get("normality",[]):
        if n.get("consensus")=="evidence_against_normality":findings.append({"severity":"info","code":"NON_NORMAL_EVIDENCE","message":f"{n['column']} shows evidence against normality."})
    return findings[:30]
def _markdown(title,columns,sections,findings,warnings):
    lines=[f"# {title}","",f"Analyzed numeric columns: {', '.join(columns)}",""]
    if findings:
        lines += ["## Key findings",""]+[f"- **{f['code']}** — {f['message']}" for f in findings]+[""]
    d=sections.get("descriptive")
    if d:
        lines += ["## Descriptive statistics","","| Column | N | Missing % | Mean | Median | Std dev | Min | Max |","|---|---:|---:|---:|---:|---:|---:|---:|"]
        for s in d.get("statistics",[]):lines.append(f"| {s['column']} | {s['count']} | {_fmt(s['null_rate_pct'])} | {_fmt(s['mean'])} | {_fmt(s['median'])} | {_fmt(s['std_dev'])} | {_fmt(s['min'])} | {_fmt(s['max'])} |")
        lines.append("")
    corr=sections.get("correlation") or {}; pairs=corr.get("strongest_pairs") or corr.get("strongest_absolute_pairs") or []
    if pairs:
        lines += ["## Strongest correlations",""]+[f"- {p.get('column_a',p.get('left'))} ↔ {p.get('column_b',p.get('right'))}: {_fmt(p.get('correlation'))} (n={p.get('pairwise_count',p.get('observations'))})" for p in pairs[:10]]+[""]
    if sections.get("confidence_intervals"):
        lines += ["## Confidence intervals",""]
        for item in sections["confidence_intervals"]:
            if item.get("status")=="ok":
                r=item["result"];lines.append(f"- {r['interval_type']}: estimate {_fmt(r.get('estimate'))}, CI [{_fmt(r.get('lower'))}, {_fmt(r.get('upper'))}]")
        lines.append("")
    if sections.get("effect_sizes"):
        lines += ["## Effect sizes",""]
        for item in sections["effect_sizes"]:
            if item.get("status")=="ok":
                r=item["result"];lines.append(f"- {r.get('effect_type')}: {_fmt(r.get('value'))} ({r.get('magnitude','')})")
        lines.append("")
    if warnings:lines += ["## Warnings",""]+[f"- {x.get('section')}: {x.get('message')}" for x in warnings]
    return "\n".join(lines).strip()+"\n"
def _html(title,md):
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title><style>body{{font-family:system-ui;max-width:1100px;margin:40px auto;color:#172033}}pre{{white-space:pre-wrap}}</style></head><body><pre>{escape(md)}</pre></body></html>'
def generate_statistics_report(df:pd.DataFrame,*,title="Statistics Summary Report",columns=None,sections=None,correlation_method="pearson",alpha=.05,histogram_bins=10,confidence_intervals=None,effect_sizes=None):
    if not isinstance(df,pd.DataFrame):raise StatisticsReportError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if columns is None:columns=[c for c in df.columns if is_numeric_dtype(df[c])]
    unknown=[c for c in columns if c not in df.columns]
    if unknown:raise StatisticsReportError("UNKNOWN_COLUMN","One or more selected columns do not exist.",{"columns":unknown})
    nonnumeric=[c for c in columns if not is_numeric_dtype(df[c])]
    if nonnumeric:raise StatisticsReportError("INCOMPATIBLE_COLUMN_TYPE","Report columns must be numeric.",{"columns":nonnumeric})
    if not columns:raise StatisticsReportError("NO_NUMERIC_COLUMNS","At least one numeric column is required.")
    wanted=list(sections or DEFAULT_SECTIONS);invalid=[x for x in wanted if x not in DEFAULT_SECTIONS]
    if invalid:raise StatisticsReportError("INVALID_SECTION","Unknown report section.",{"sections":invalid})
    results={};warnings=[]
    if "descriptive" in wanted:results["descriptive"]=_safe_call("descriptive",lambda:analyze_descriptive_statistics(df,columns=columns,ddof=1),warnings)
    if "percentiles" in wanted:results["percentiles"]=_safe_call("percentiles",lambda:analyze_percentiles(df,columns=columns),warnings)
    if "correlation" in wanted:
        if len(columns)>=2:results["correlation"]=_safe_call("correlation",lambda:analyze_correlations(df,columns=columns,method=correlation_method,min_periods=2,top_pairs=20),warnings)
        else:warnings.append({"section":"correlation","code":"INSUFFICIENT_COLUMNS","message":"At least two numeric columns are required."})
    if "covariance" in wanted:
        if len(columns)>=2:results["covariance"]=_safe_call("covariance",lambda:analyze_covariance(df,columns=columns,ddof=1,top_pairs=10),warnings)
        else:warnings.append({"section":"covariance","code":"INSUFFICIENT_COLUMNS","message":"At least two numeric columns are required."})
    if "distribution" in wanted:results["distribution"]=_safe_call("distribution",lambda:analyze_distributions(df,columns=columns,histogram_bins=histogram_bins),warnings)
    if "normality" in wanted:results["normality"]=_safe_call("normality",lambda:analyze_normality(df,columns=columns,alpha=alpha,shapiro_max_n=5000),warnings)
    ci=[]
    for i,spec in enumerate(confidence_intervals or []):
        try:ci.append({"index":i,"status":"ok","result":calculate_confidence_interval(df,**spec)})
        except Exception as e:ci.append({"index":i,"status":"error","error":{"code":getattr(e,"code",e.__class__.__name__),"message":getattr(e,"message",str(e))}})
    if ci:results["confidence_intervals"]=ci
    es=[]
    for i,spec in enumerate(effect_sizes or []):
        try:es.append({"index":i,"status":"ok","result":calculate_effect_size(df,**spec)})
        except Exception as e:es.append({"index":i,"status":"error","error":{"code":getattr(e,"code",e.__class__.__name__),"message":getattr(e,"message",str(e))}})
    if es:results["effect_sizes"]=es
    findings=_findings(results);md=_markdown(title,columns,results,findings,warnings)
    return {"title":title,"row_count":len(df),"column_count":len(df.columns),"analyzed_columns":columns,"included_sections":wanted,"sections":results,"findings":findings,"warnings":warnings,"report_markdown":md,"report_html":_html(title,md)}
