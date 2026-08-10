from __future__ import annotations
from copy import deepcopy

class GatewayError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

ACTIONS={
    "analyst.analyze":{"method":"POST","path":"/api/v1/automated-analyst/analyze","requires":["dataset_id"]},
    "quality.analyze":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/quality/analyze","requires":["dataset_id"]},
    "cleaning.preview":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/cleaning/preview","requires":["dataset_id"]},
    "cleaning.apply":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/cleaning/apply","requires":["dataset_id"]},
    "transformation.preview":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/transformations/preview","requires":["dataset_id"]},
    "transformation.apply":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/transformations/apply","requires":["dataset_id"]},
    "statistics.summary":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/statistics/summary","requires":["dataset_id"]},
    "eda.report":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/eda/report","requires":["dataset_id"]},
    "charts.recommend":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/visualization/recommend","requires":["dataset_id"]},
    "kpi.calculate":{"method":"POST","path":"/api/v1/datasets/{dataset_id}/kpis/calculate","requires":["dataset_id"]},
    "dashboard.validate":{"method":"POST","path":"/api/v1/dashboards/{dashboard_id}/validate","requires":["dashboard_id"]},
    "report.assemble":{"method":"POST","path":"/api/v1/analytics-reports/{report_id}/assemble","requires":["report_id"]},
    "report.pdf":{"method":"POST","path":"/api/v1/pdf-reports/generate","requires":[]},
    "report.excel":{"method":"POST","path":"/api/v1/excel-reports/generate","requires":[]},
    "bi.report":{"method":"GET","path":"/api/v1/datasets/{dataset_id}/bi_report","requires":["dataset_id"]},
}

FINAL={"COMPLETED","FAILED","CANCELLED"}

def action_catalog():
    return [{"action":k,**deepcopy(v)} for k,v in sorted(ACTIONS.items())]

def validate_action(action,context,payload):
    if action not in ACTIONS:
        raise GatewayError("ACTION_NOT_ALLOWED","Analytics action is not allowlisted.",{"action":action})
    if not isinstance(context,dict) or not isinstance(payload,dict):
        raise GatewayError("INVALID_REQUEST","context and payload must be objects.")
    spec=ACTIONS[action]
    missing=[key for key in spec["requires"] if not context.get(key)]
    if missing:
        raise GatewayError("MISSING_CONTEXT","Required action context is missing.",{"keys":missing})
    for key in ("dataset_id","dashboard_id","report_id"):
        if key in context and context[key] is not None and not isinstance(context[key],str):
            raise GatewayError("INVALID_IDENTIFIER","Context identifiers must be strings.",{"key":key})
    return spec

def dispatch_plan(action,context,payload):
    spec=validate_action(action,context,payload)
    path=spec["path"]
    for key,value in context.items():
        path=path.replace("{"+key+"}",str(value))
    if "{" in path or "}" in path:
        raise GatewayError("UNRESOLVED_PATH","Action path contains unresolved parameters.",{"path":path})
    return {
        "action":action,
        "method":spec["method"],
        "path":path,
        "body":deepcopy(payload),
        "headers":{"Content-Type":"application/json"},
    }

def ensure_transition(current,target):
    allowed={
        "QUEUED":{"DISPATCHED","FAILED","CANCELLED"},
        "DISPATCHED":{"RUNNING","COMPLETED","FAILED","CANCELLED"},
        "RUNNING":{"COMPLETED","FAILED","CANCELLED"},
        "COMPLETED":set(),"FAILED":set(),"CANCELLED":set(),
    }
    if target not in allowed.get(current,set()):
        raise GatewayError("INVALID_STATE_TRANSITION","Run state transition is not allowed.",{"current":current,"target":target})
    return True
