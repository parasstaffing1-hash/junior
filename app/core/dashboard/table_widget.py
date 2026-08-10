class TableWidgetError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _filter(rows,filters):
    out=list(rows)
    for f in filters or []:
        col=f.get("column");op=f.get("operator","eq");val=f.get("value")
        if op=="eq":out=[r for r in out if r.get(col)==val]
        elif op=="ne":out=[r for r in out if r.get(col)!=val]
        elif op=="contains":out=[r for r in out if val.lower() in str(r.get(col,"")).lower()]
        elif op=="gt":out=[r for r in out if r.get(col) is not None and r.get(col)>val]
        elif op=="gte":out=[r for r in out if r.get(col) is not None and r.get(col)>=val]
        elif op=="lt":out=[r for r in out if r.get(col) is not None and r.get(col)<val]
        elif op=="lte":out=[r for r in out if r.get(col) is not None and r.get(col)<=val]
        else:raise TableWidgetError("INVALID_FILTER_OPERATOR","Unsupported table filter operator.",{"operator":op})
    return out

def _sort(rows,sorts):
    out=list(rows)
    for spec in reversed(sorts or []):
        col=spec["column"];reverse=spec.get("direction","asc")=="desc"
        out.sort(key=lambda r:(r.get(col) is None,r.get(col)),reverse=reverse)
    return out

def build_table(*,rows,columns=None,filters=None,sorts=None,page=1,page_size=25,max_rows=500,
                column_labels=None,column_formats=None,conditional_rules=None,totals=None):
    if page<1 or page_size<1 or max_rows<1:raise TableWidgetError("INVALID_PAGINATION","page, page_size and max_rows must be >=1.")
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise TableWidgetError("INVALID_ROWS","rows must be a list of objects.")
    work=_sort(_filter(rows,filters),sorts)[:max_rows]
    cols=columns or (list(work[0].keys()) if work else [])
    for c in cols:
        if any(c not in row for row in work):
            raise TableWidgetError("COLUMN_MISSING","A selected column is missing from one or more rows.",{"column":c})
    total_rows=len(work);start=(page-1)*page_size;end=start+page_size
    shown=[{c:r.get(c) for c in cols} for r in work[start:end]]
    totals_out={}
    for c in totals or []:
        vals=[r.get(c) for r in work if isinstance(r.get(c),(int,float)) and not isinstance(r.get(c),bool)]
        totals_out[c]=sum(vals) if vals else 0
    return {
        "columns":[{"key":c,"label":(column_labels or {}).get(c,c),"format":(column_formats or {}).get(c)} for c in cols],
        "rows":shown,"page":page,"page_size":page_size,"total_rows":total_rows,
        "total_pages":0 if total_rows==0 else (total_rows+page_size-1)//page_size,
        "totals":totals_out,"conditional_rules":conditional_rules or []
    }

def dashboard_payload(definition,rendered,*,x=0,y=0,w=12,h=5,position=0):
    return {"widget_type":"table","title":definition["title"],"source_ref":f"dashboard-table:{definition['id']}",
            "config":{"definition":definition,"rendered":rendered},"x":x,"y":y,"w":w,"h":h,"position":position}
