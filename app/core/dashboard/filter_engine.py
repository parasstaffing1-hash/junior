from __future__ import annotations
from datetime import date, datetime
from typing import Any

class DashboardFilterError(Exception):
    def __init__(self, code, message, details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

OPERATORS={"eq","ne","in","not_in","contains","starts_with","ends_with","gt","gte","lt","lte",
           "between","is_null","not_null","date_range"}
TYPES={"string","number","integer","boolean","date","datetime"}

def _coerce(value, value_type):
    if value is None:return None
    try:
        if value_type=="string":return str(value)
        if value_type=="number":return float(value)
        if value_type=="integer":return int(value)
        if value_type=="boolean":
            if isinstance(value,bool):return value
            s=str(value).strip().lower()
            if s in {"true","1","yes","y"}:return True
            if s in {"false","0","no","n"}:return False
            raise ValueError("invalid boolean")
        if value_type=="date":
            return value if isinstance(value,date) and not isinstance(value,datetime) else date.fromisoformat(str(value))
        if value_type=="datetime":
            return value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except Exception as e:
        raise DashboardFilterError("VALUE_COERCION_FAILED","Filter value could not be coerced.",
                                   {"value":value,"value_type":value_type}) from e
    raise DashboardFilterError("INVALID_VALUE_TYPE","Unsupported filter value type.",{"value_type":value_type})

def validate_definition(definition:dict):
    op=definition.get("operator")
    vt=definition.get("value_type")
    if op not in OPERATORS:raise DashboardFilterError("INVALID_OPERATOR","Unsupported filter operator.",{"operator":op})
    if vt not in TYPES:raise DashboardFilterError("INVALID_VALUE_TYPE","Unsupported filter value type.",{"value_type":vt})
    if definition.get("scope") not in {"global","dashboard","widget"}:
        raise DashboardFilterError("INVALID_SCOPE","scope must be global, dashboard, or widget.")
    if definition.get("scope")=="widget" and not definition.get("target_widget_ids"):
        raise DashboardFilterError("TARGET_WIDGET_REQUIRED","Widget-scoped filters require target_widget_ids.")
    if op in {"in","not_in"} and not definition.get("multi_select",False):
        raise DashboardFilterError("MULTI_SELECT_REQUIRED","in/not_in filters must enable multi_select.")
    return True

def normalize_value(definition:dict,value):
    validate_definition(definition)
    op=definition["operator"];vt=definition["value_type"]
    if op in {"is_null","not_null"}:return None
    if value is None:
        if definition.get("default_value") is not None:value=definition["default_value"]
        elif definition.get("required"):raise DashboardFilterError("FILTER_VALUE_REQUIRED","Required filter has no value.")
        else:return None
    if op in {"in","not_in"}:
        vals=value if isinstance(value,list) else [value]
        return [_coerce(x,vt) for x in vals]
    if op in {"between","date_range"}:
        if not isinstance(value,(list,tuple)) or len(value)!=2:
            raise DashboardFilterError("RANGE_VALUE_REQUIRED","Range filter requires exactly two values.")
        a,b=_coerce(value[0],vt),_coerce(value[1],vt)
        if a>b:raise DashboardFilterError("INVALID_RANGE","Range start must be <= range end.")
        return [a,b]
    return _coerce(value,vt)

def evaluate_value(definition:dict,candidate,value):
    normalized=normalize_value(definition,value)
    op=definition["operator"];vt=definition["value_type"]
    if op=="is_null":return candidate is None
    if op=="not_null":return candidate is not None
    if candidate is None:return False
    cand=_coerce(candidate,vt)
    if normalized is None:return True
    if op=="eq":return cand==normalized
    if op=="ne":return cand!=normalized
    if op=="in":return cand in normalized
    if op=="not_in":return cand not in normalized
    if op=="contains":return str(normalized).lower() in str(cand).lower()
    if op=="starts_with":return str(cand).lower().startswith(str(normalized).lower())
    if op=="ends_with":return str(cand).lower().endswith(str(normalized).lower())
    if op=="gt":return cand>normalized
    if op=="gte":return cand>=normalized
    if op=="lt":return cand<normalized
    if op=="lte":return cand<=normalized
    if op in {"between","date_range"}:return normalized[0]<=cand<=normalized[1]
    raise DashboardFilterError("INVALID_OPERATOR","Unsupported operator.")

def apply_filter(rows:list[dict[str,Any]],definition:dict,value):
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise DashboardFilterError("INVALID_ROWS","rows must be a list of objects.")
    field=definition["field"]
    out=[]
    for row in rows:
        if evaluate_value(definition,row.get(field),value):out.append(row)
    return out

def validate_state(definitions:list[dict],state:dict):
    result={};errors=[]
    by_id={d["id"]:d for d in definitions}
    for fid,d in by_id.items():
        try:
            result[fid]=normalize_value(d,state.get(fid))
        except DashboardFilterError as e:
            errors.append({"filter_id":fid,"code":e.code,"message":e.message,"details":e.details})
    unknown=sorted(set(state)-set(by_id))
    if unknown:errors.append({"code":"UNKNOWN_FILTERS","filter_ids":unknown})
    return {"valid":not errors,"normalized_state":result,"errors":errors}

def widget_payload(definitions:list[dict],state:dict,widget_id:str):
    check=validate_state(definitions,state)
    if not check["valid"]:raise DashboardFilterError("INVALID_FILTER_STATE","Filter state is invalid.",{"errors":check["errors"]})
    active=[]
    for d in definitions:
        if d["scope"]=="widget" and widget_id not in (d.get("target_widget_ids") or []):continue
        val=check["normalized_state"].get(d["id"])
        if val is None and d["operator"] not in {"is_null","not_null"}:continue
        active.append({"filter_id":d["id"],"field":d["field"],"operator":d["operator"],"value":val,"value_type":d["value_type"]})
    return {"widget_id":widget_id,"filters":active}
