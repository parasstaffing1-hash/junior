class ChartWidgetError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

SUPPORTED={"chart_run","artifact_url","json_spec"}

def validate_source(*,source_type,source_ref=None,chart_spec=None):
    if source_type not in SUPPORTED:
        raise ChartWidgetError("INVALID_SOURCE_TYPE","Unsupported chart widget source type.")
    if source_type in {"chart_run","artifact_url"} and not source_ref:
        raise ChartWidgetError("SOURCE_REF_REQUIRED","source_ref is required for this source type.")
    if source_type=="json_spec" and not chart_spec:
        raise ChartWidgetError("CHART_SPEC_REQUIRED","chart_spec is required for json_spec mode.")
    return True

def render_manifest(definition,*,source_ref=None,chart_spec=None):
    effective_ref=source_ref if source_ref is not None else definition.get("source_ref")
    effective_spec=chart_spec if chart_spec is not None else definition.get("chart_spec")
    validate_source(source_type=definition["source_type"],source_ref=effective_ref,chart_spec=effective_spec)
    return {
        "widget_type":"chart",
        "title":definition["title"],
        "subtitle":definition.get("subtitle"),
        "source_type":definition["source_type"],
        "source_ref":effective_ref,
        "chart_spec":effective_spec,
        "show_legend":definition["show_legend"],
        "show_x_axis":definition["show_x_axis"],
        "show_y_axis":definition["show_y_axis"],
        "responsive":definition["responsive"],
        "refresh_seconds":definition.get("refresh_seconds"),
        "presentation":definition["presentation"]
    }

def dashboard_payload(definition,rendered,*,x=0,y=0,w=6,h=4,position=0):
    return {
        "widget_type":"chart",
        "title":definition["title"],
        "source_ref":f"dashboard-chart:{definition['id']}",
        "config":{"definition":definition,"rendered":rendered},
        "x":x,"y":y,"w":w,"h":h,"position":position
    }
