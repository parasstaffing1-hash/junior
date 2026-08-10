from __future__ import annotations
import math

class KPICardError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def format_value(value,*,format_type="number",precision=2,currency_symbol="$",prefix="",suffix=""):
    if value is None:return None
    v=float(value)
    if not math.isfinite(v):raise KPICardError("NON_FINITE_VALUE","KPI value must be finite.")
    if format_type=="integer":body=f"{v:,.0f}"
    elif format_type=="currency":body=f"{currency_symbol}{v:,.{precision}f}"
    elif format_type=="percent":body=f"{v:,.{precision}f}%"
    elif format_type=="number":body=f"{v:,.{precision}f}"
    else:raise KPICardError("INVALID_FORMAT","Unsupported KPI format.",{"format_type":format_type})
    return f"{prefix}{body}{suffix}"

def build_card(*,label,current_value,prior_value=None,target_value=None,direction="neutral",
               format_type="number",precision=2,currency_symbol="$",prefix="",suffix="",
               sparkline=None,presentation="standard",subtitle=None):
    if direction not in {"neutral","higher_is_better","lower_is_better"}:
        raise KPICardError("INVALID_DIRECTION","Unsupported direction.")
    if presentation not in {"compact","standard","expanded"}:
        raise KPICardError("INVALID_PRESENTATION","Unsupported presentation.")
    if not label:raise KPICardError("LABEL_REQUIRED","KPI card label is required.")
    current=float(current_value)
    if not math.isfinite(current):raise KPICardError("NON_FINITE_VALUE","KPI value must be finite.")
    change=None;pct=None;movement=None;favorable=None
    if prior_value is not None:
        prior=float(prior_value)
        change=current-prior
        pct=None if prior==0 else change/abs(prior)*100
        movement="increase" if change>0 else "decrease" if change<0 else "unchanged"
        if direction=="higher_is_better":favorable=change>0 if change else None
        elif direction=="lower_is_better":favorable=change<0 if change else None
    attainment=None;target_status=None
    if target_value is not None:
        target=float(target_value)
        attainment=None if target==0 else current/target*100
        if direction=="lower_is_better":
            target_status="met" if current<=target else "missed"
        elif direction=="higher_is_better":
            target_status="met" if current>=target else "missed"
        else:
            target_status="met" if current==target else "off_target"
    spark=[]
    for x in sparkline or []:
        x=float(x)
        if not math.isfinite(x):raise KPICardError("INVALID_SPARKLINE","Sparkline values must be finite.")
        spark.append(x)
    fmt={"format_type":format_type,"precision":precision,"currency_symbol":currency_symbol,"prefix":prefix,"suffix":suffix}
    return {
        "widget_type":"kpi",
        "label":label,
        "subtitle":subtitle,
        "presentation":presentation,
        "current_value":current,
        "formatted_value":format_value(current,**fmt),
        "prior_value":None if prior_value is None else float(prior_value),
        "absolute_change":change,
        "percent_change":pct,
        "movement":movement,
        "favorable":favorable,
        "target_value":None if target_value is None else float(target_value),
        "target_attainment_pct":attainment,
        "target_status":target_status,
        "sparkline":spark,
        "format":fmt
    }

def dashboard_payload(definition,rendered,*,x=0,y=0,w=3,h=2,position=0):
    return {
        "widget_type":"kpi",
        "title":definition["title"],
        "source_ref":f"kpi-card:{definition['id']}",
        "config":{"definition":definition,"rendered":rendered},
        "x":x,"y":y,"w":w,"h":h,"position":position
    }
