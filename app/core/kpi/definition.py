from __future__ import annotations
import ast,re
class KPIDefinitionError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
ALLOWED_AGGS={"sum","mean","median","min","max","count","nunique"};ALLOWED_DIR={None,"higher_is_better","lower_is_better","target"};ALLOWED_FMT={"number","currency","percent","integer"}
def slugify(name):
    slug=re.sub(r"[^a-z0-9]+","-",name.lower()).strip("-")
    if not slug:raise KPIDefinitionError("INVALID_NAME","KPI name must contain letters or numbers.")
    return slug
def validate_expression(expr,names):
    try:tree=ast.parse(expr,mode="eval")
    except SyntaxError as e:raise KPIDefinitionError("INVALID_FORMULA","Formula syntax is invalid.") from e
    allowed=(ast.Expression,ast.BinOp,ast.UnaryOp,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.Pow,ast.USub,ast.UAdd,ast.Constant,ast.Name,ast.Load)
    for node in ast.walk(tree):
        if not isinstance(node,allowed):raise KPIDefinitionError("UNSAFE_FORMULA","Formula contains unsupported syntax.")
        if isinstance(node,ast.Name) and node.id not in names:raise KPIDefinitionError("UNKNOWN_COMPONENT","Formula references unknown component.",{"component":node.id})
    return True
def validate_definition(d):
    typ=d.get("definition_type")
    if typ not in {"aggregate","ratio","formula"}:raise KPIDefinitionError("INVALID_DEFINITION_TYPE","definition_type must be aggregate, ratio, or formula.")
    comps=d.get("components") or {}
    if typ=="aggregate":
        c=comps.get("value")
        if not c:raise KPIDefinitionError("VALUE_COMPONENT_REQUIRED","Aggregate KPI requires components.value.")
    elif typ=="ratio":
        if "numerator" not in comps or "denominator" not in comps:raise KPIDefinitionError("RATIO_COMPONENTS_REQUIRED","Ratio KPI requires numerator and denominator components.")
    else:
        if not comps:raise KPIDefinitionError("COMPONENTS_REQUIRED","Formula KPI requires components.")
        if not d.get("formula"):raise KPIDefinitionError("FORMULA_REQUIRED","Formula KPI requires formula.")
        validate_expression(d["formula"],set(comps))
    for name,c in comps.items():
        agg=c.get("aggregation")
        if agg not in ALLOWED_AGGS:raise KPIDefinitionError("INVALID_AGGREGATION","Unsupported aggregation.",{"component":name,"aggregation":agg})
        if agg!="count" and not c.get("column"):raise KPIDefinitionError("COLUMN_REQUIRED","Component column is required unless aggregation=count.",{"component":name})
    if d.get("target_direction") not in ALLOWED_DIR:raise KPIDefinitionError("INVALID_TARGET_DIRECTION","Invalid target_direction.")
    if d.get("format_type","number") not in ALLOWED_FMT:raise KPIDefinitionError("INVALID_FORMAT","Invalid format_type.")
    dims=d.get("dimensions") or []
    if len(dims)!=len(set(dims)):raise KPIDefinitionError("DUPLICATE_DIMENSION","dimensions must be unique.")
    return {"valid":True,"component_count":len(comps),"definition_type":typ}
