class AlertRuleError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

RULE_TYPES={"above","below","outside_range","inside_range","absolute_change_above","percent_change_above","target_status"}

def validate_rule(rule):
    rt=rule.get("rule_type")
    if rt not in RULE_TYPES:raise AlertRuleError("INVALID_RULE_TYPE","Unsupported alert rule type.",{"rule_type":rt})
    if not rule.get("kpi_slug"):raise AlertRuleError("KPI_SLUG_REQUIRED","kpi_slug is required.")
    if rt in {"above","below","absolute_change_above","percent_change_above"} and rule.get("threshold") is None:
        raise AlertRuleError("THRESHOLD_REQUIRED","This rule type requires threshold.")
    if rt in {"outside_range","inside_range"}:
        lo,hi=rule.get("lower_bound"),rule.get("upper_bound")
        if lo is None or hi is None:raise AlertRuleError("RANGE_REQUIRED","Range rule requires lower_bound and upper_bound.")
        if lo>hi:raise AlertRuleError("INVALID_RANGE","lower_bound must be <= upper_bound.")
    if rt=="target_status" and not rule.get("target_statuses"):
        raise AlertRuleError("TARGET_STATUSES_REQUIRED","target_status rule requires target_statuses.")
    return True

def _dimensions_match(rule_dims,snapshot_dims):
    for k,v in (rule_dims or {}).items():
        if (snapshot_dims or {}).get(k)!=v:return False
    return True

def evaluate_rule(rule,snapshot):
    validate_rule(rule)
    if snapshot.get("kpi_slug")!=rule["kpi_slug"]:
        return {"matched":False,"triggered":False,"reason":"kpi_slug_mismatch"}
    if not _dimensions_match(rule.get("dimensions"),snapshot.get("dimensions")):
        return {"matched":False,"triggered":False,"reason":"dimension_mismatch"}
    rt=rule["rule_type"];value=snapshot.get("value")
    if value is None and rt!="target_status":raise AlertRuleError("VALUE_REQUIRED","Snapshot value is required.")
    if rt=="above":
        trig=float(value)>float(rule["threshold"]);e={"value":value,"threshold":rule["threshold"]}
    elif rt=="below":
        trig=float(value)<float(rule["threshold"]);e={"value":value,"threshold":rule["threshold"]}
    elif rt=="outside_range":
        trig=float(value)<float(rule["lower_bound"]) or float(value)>float(rule["upper_bound"])
        e={"value":value,"lower_bound":rule["lower_bound"],"upper_bound":rule["upper_bound"]}
    elif rt=="inside_range":
        trig=float(rule["lower_bound"])<=float(value)<=float(rule["upper_bound"])
        e={"value":value,"lower_bound":rule["lower_bound"],"upper_bound":rule["upper_bound"]}
    elif rt=="absolute_change_above":
        ch=snapshot.get("absolute_change")
        if ch is None:raise AlertRuleError("ABSOLUTE_CHANGE_REQUIRED","Snapshot absolute_change is required.")
        trig=abs(float(ch))>float(rule["threshold"]);e={"absolute_change":ch,"threshold":rule["threshold"]}
    elif rt=="percent_change_above":
        ch=snapshot.get("percent_change")
        if ch is None:raise AlertRuleError("PERCENT_CHANGE_REQUIRED","Snapshot percent_change is required.")
        trig=abs(float(ch))>float(rule["threshold"]);e={"percent_change":ch,"threshold":rule["threshold"]}
    else:
        status=snapshot.get("target_status")
        trig=status in rule["target_statuses"];e={"target_status":status,"trigger_statuses":rule["target_statuses"]}
    return {"matched":True,"triggered":bool(trig),"reason":"triggered" if trig else "condition_not_met","evidence":e}
