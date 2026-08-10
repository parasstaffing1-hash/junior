from __future__ import annotations
import ast,math
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
class KPICalculationError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
AGGS={"sum","mean","median","min","max","count","nunique"}
def validate_expr(expr,names):
    try:tree=ast.parse(expr,mode="eval")
    except SyntaxError as e:raise KPICalculationError("INVALID_FORMULA","Formula syntax invalid.") from e
    allowed=(ast.Expression,ast.BinOp,ast.UnaryOp,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.Pow,ast.USub,ast.UAdd,ast.Constant,ast.Name,ast.Load)
    for node in ast.walk(tree):
        if not isinstance(node,allowed):raise KPICalculationError("UNSAFE_FORMULA","Formula contains unsupported syntax.")
        if isinstance(node,ast.Name) and node.id not in names:raise KPICalculationError("UNKNOWN_COMPONENT","Formula references unknown component.",{"component":node.id})
    return compile(tree,"<kpi>","eval")
def _filter(df,filters):
    out=df
    for f in filters or []:
        c=f["column"];op=f.get("operator","eq");v=f.get("value")
        if c not in out.columns:raise KPICalculationError("UNKNOWN_FILTER_COLUMN","Filter column does not exist.",{"column":c})
        s=out[c]
        if op=="eq":mask=s==v
        elif op=="ne":mask=s!=v
        elif op=="gt":mask=s>v
        elif op=="gte":mask=s>=v
        elif op=="lt":mask=s<v
        elif op=="lte":mask=s<=v
        elif op=="in":mask=s.isin(v)
        elif op=="not_in":mask=~s.isin(v)
        elif op=="is_null":mask=s.isna()
        elif op=="not_null":mask=s.notna()
        elif op=="contains":mask=s.astype(str).str.contains(str(v),case=False,na=False)
        else:raise KPICalculationError("INVALID_FILTER_OPERATOR","Unsupported filter operator.",{"operator":op})
        out=out[mask]
    return out
def _agg(df,spec):
    agg=spec.get("aggregation","sum");col=spec.get("column")
    if agg not in AGGS:raise KPICalculationError("INVALID_AGGREGATION","Unsupported aggregation.")
    d=_filter(df,spec.get("filters"))
    if agg=="count":return float(len(d) if not col else d[col].count())
    if not col or col not in d.columns:raise KPICalculationError("UNKNOWN_COLUMN","Component column does not exist.",{"column":col})
    if agg!="nunique" and not is_numeric_dtype(d[col]):raise KPICalculationError("INCOMPATIBLE_COLUMN_TYPE","Numeric aggregation requires numeric column.",{"column":col})
    s=d[col].dropna()
    if agg=="nunique":return float(s.nunique())
    if len(s)==0:return None
    return float(getattr(s,agg)())
def _value(df,definition):
    typ=definition.get("definition_type","aggregate");comps=definition.get("components") or {}
    vals={name:_agg(df,spec) for name,spec in comps.items()}
    if typ=="aggregate":
        if "value" not in vals:raise KPICalculationError("VALUE_COMPONENT_REQUIRED","Aggregate KPI needs value component.")
        val=vals["value"]
    elif typ=="ratio":
        if "numerator" not in vals or "denominator" not in vals:raise KPICalculationError("RATIO_COMPONENTS_REQUIRED","Ratio needs numerator and denominator.")
        den=vals["denominator"];val=None if den in (None,0) else vals["numerator"]/den*float(definition.get("ratio_scale",1.0))
    elif typ=="formula":
        expr=definition.get("formula")
        if not expr:raise KPICalculationError("FORMULA_REQUIRED","Formula KPI requires formula.")
        code=validate_expr(expr,set(vals))
        if any(v is None for v in vals.values()):val=None
        else:
            try:val=float(eval(code,{"__builtins__":{}},vals))
            except ZeroDivisionError:val=None
    else:raise KPICalculationError("INVALID_DEFINITION_TYPE","Unknown definition type.")
    return val,vals
def _target(value,target,direction):
    if value is None or target is None:return {"target_value":target,"variance":None,"variance_pct":None,"status":None}
    variance=value-target;vp=None if target==0 else variance/abs(target)*100
    if direction=="higher_is_better":status="met" if value>=target else "below_target"
    elif direction=="lower_is_better":status="met" if value<=target else "above_target"
    elif direction=="target":status="met" if value==target else "off_target"
    else:status=None
    return {"target_value":target,"variance":variance,"variance_pct":vp,"status":status}
def calculate_kpi(df:pd.DataFrame,definition,*,filters=None,dimensions=None,time_column=None,time_grain=None):
    if not isinstance(df,pd.DataFrame):raise KPICalculationError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    work=_filter(df,filters)
    dims=list(dimensions if dimensions is not None else definition.get("dimensions") or [])
    unknown=[c for c in dims if c not in work.columns]
    if unknown:raise KPICalculationError("UNKNOWN_DIMENSION","Dimension column missing.",{"columns":unknown})
    tc=time_column or definition.get("time_column");grain=time_grain or definition.get("time_grain")
    group_cols=list(dims)
    if tc:
        if tc not in work.columns:raise KPICalculationError("UNKNOWN_TIME_COLUMN","time_column does not exist.")
        dt=pd.to_datetime(work[tc],errors="coerce");work=work.assign(__kpi_time=dt).dropna(subset=["__kpi_time"])
        if grain=="day":work["__period"]=work["__kpi_time"].dt.to_period("D").astype(str)
        elif grain=="week":work["__period"]=work["__kpi_time"].dt.to_period("W").astype(str)
        elif grain=="month":work["__period"]=work["__kpi_time"].dt.to_period("M").astype(str)
        elif grain=="quarter":work["__period"]=work["__kpi_time"].dt.to_period("Q").astype(str)
        elif grain=="year":work["__period"]=work["__kpi_time"].dt.to_period("Y").astype(str)
        else:raise KPICalculationError("INVALID_TIME_GRAIN","time_grain is required and must be day/week/month/quarter/year.")
        group_cols.append("__period")
    target=definition.get("target_value");direction=definition.get("target_direction")
    rows=[]
    if group_cols:
        grouper=group_cols[0] if len(group_cols)==1 else group_cols
        for keys,part in work.groupby(grouper,dropna=False,sort=True):
            if not isinstance(keys,tuple):keys=(keys,)
            val,components=_value(part,definition);row={c:(str(k) if c=="__period" else k) for c,k in zip(group_cols,keys)}
            if "__period" in row:row["period"]=row.pop("__period")
            row.update({"value":val,"components":components,**_target(val,target,direction)});rows.append(row)
    else:
        val,components=_value(work,definition);rows=[{"value":val,"components":components,**_target(val,target,direction)}]
    total,total_components=_value(work,definition)
    return {"definition_type":definition.get("definition_type","aggregate"),"rows":rows,"row_count":len(rows),"total":{"value":total,"components":total_components,**_target(total,target,direction)},"dimensions":dims,"time_column":tc,"time_grain":grain,"filtered_source_rows":len(work)}
