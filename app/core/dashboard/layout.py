import html

class LayoutError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def validate_widget(widget,grid_columns=12):
    for key in ("x","y","w","h"):
        if key not in widget:raise LayoutError("LAYOUT_REQUIRED","Widget layout requires x,y,w,h.")
        if not isinstance(widget[key],int):raise LayoutError("LAYOUT_INTEGER_REQUIRED","Layout values must be integers.")
    if widget["x"]<0 or widget["y"]<0 or widget["w"]<1 or widget["h"]<1:
        raise LayoutError("INVALID_LAYOUT","Widget coordinates/sizes are invalid.")
    if widget["x"]+widget["w"]>grid_columns:
        raise LayoutError("GRID_OVERFLOW","Widget exceeds dashboard grid width.",{"grid_columns":grid_columns})
    return True

def overlap(a,b):
    return not (
        a["x"]+a["w"]<=b["x"] or b["x"]+b["w"]<=a["x"]
        or a["y"]+a["h"]<=b["y"] or b["y"]+b["h"]<=a["y"]
    )

def validate_layout(widgets,grid_columns=12,allow_overlap=False):
    for widget in widgets:validate_widget(widget,grid_columns)
    collisions=[]
    if not allow_overlap:
        for i,a in enumerate(widgets):
            for b in widgets[i+1:]:
                if overlap(a,b):collisions.append([a.get("id"),b.get("id")])
    return {"valid":not collisions,"collisions":collisions,"widget_count":len(widgets),"grid_columns":grid_columns}

def render_html(dashboard,widgets):
    title=html.escape(dashboard["title"])
    cards=[]
    for widget in widgets:
        config=widget.get("config") or {}
        content=config.get("text") or config.get("label") or widget.get("source_ref") or "Widget content"
        cards.append(
            '<section class="widget" style="grid-column:%s/span %s;grid-row:%s/span %s">'
            '<h3>%s</h3><div>%s</div><small>%s</small></section>' % (
                widget["x"]+1,widget["w"],widget["y"]+1,widget["h"],
                html.escape(widget["title"]),html.escape(str(content)),html.escape(widget["widget_type"])
            )
        )
    return """<!doctype html><html><head><meta charset="utf-8"><title>%s</title>
<style>
body{font-family:system-ui;margin:0;background:#f5f6f8}
main{padding:24px}.grid{display:grid;grid-template-columns:repeat(12,1fr);grid-auto-rows:90px;gap:12px}
.widget{background:white;border:1px solid #ddd;border-radius:12px;padding:16px;overflow:auto}
h1{margin-top:0}h3{margin:0 0 10px}
</style></head><body><main><h1>%s</h1><p>%s</p><div class="grid">%s</div></main></body></html>""" % (
        title,title,html.escape(dashboard.get("description") or ""),"".join(cards)
    )
