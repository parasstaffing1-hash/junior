class DrilldownError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def validate_definition(d):
    if not d.get("source_widget_id"):raise DrilldownError("SOURCE_WIDGET_REQUIRED","source_widget_id is required.")
    if not d.get("target_dashboard_id"):raise DrilldownError("TARGET_DASHBOARD_REQUIRED","target_dashboard_id is required.")
    if d.get("max_depth",1)<1:raise DrilldownError("INVALID_MAX_DEPTH","max_depth must be >=1.")
    mapping=d.get("mapping") or {}
    if not isinstance(mapping,dict):raise DrilldownError("INVALID_MAPPING","mapping must be an object.")
    return True

def resolve_drilldown(definition,*,selected,context=None,breadcrumbs=None,current_depth=0):
    validate_definition(definition)
    if current_depth>=definition.get("max_depth",1):
        raise DrilldownError("MAX_DEPTH_REACHED","Drilldown depth limit reached.",{"max_depth":definition.get("max_depth",1)})
    if not isinstance(selected,dict):raise DrilldownError("INVALID_SELECTION","selected must be an object.")
    inherited=dict(context or {})
    required=definition.get("required_context_keys") or []
    missing=[k for k in required if k not in inherited and k not in selected]
    if missing:raise DrilldownError("MISSING_CONTEXT","Required drilldown context is missing.",{"keys":missing})
    if not definition.get("preserve_context",True):
        inherited={}
    for key in definition.get("drop_context_keys") or []:
        inherited.pop(key,None)
    mapped={}
    for src,dst in (definition.get("mapping") or {}).items():
        if src in selected:mapped[dst]=selected[src]
        elif src in inherited:mapped[dst]=inherited[src]
        else:raise DrilldownError("MAPPING_SOURCE_MISSING","Mapping source is absent.",{"source":src})
    merged={**inherited,**mapped}
    trail=list(breadcrumbs or [])
    trail.append({
        "label":definition.get("breadcrumb_label") or definition.get("name") or "Drilldown",
        "dashboard_id":definition.get("source_dashboard_id"),
        "widget_id":definition["source_widget_id"],
        "depth":current_depth
    })
    return {
        "target_dashboard_id":definition["target_dashboard_id"],
        "target_widget_id":definition.get("target_widget_id"),
        "navigation_mode":definition.get("navigation_mode","replace"),
        "context":merged,
        "breadcrumbs":trail,
        "depth":current_depth+1,
        "can_drill_further":current_depth+1<definition.get("max_depth",1)
    }
