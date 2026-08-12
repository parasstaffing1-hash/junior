from typing import Dict, Any

def generate_echarts_option(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generates a full ECharts option object from the generic chart spec.
    """
    chart_type = spec.get("chart_type", "bar")
    orientation = spec.get("orientation", "vertical")
    category_column = spec.get("category_column") or spec.get("x_column")
    series_column = spec.get("series_column")
    data = spec.get("data", [])
    title = spec.get("title", "")
    
    # Extract unique categories
    categories = []
    for row in data:
        cat = row.get(category_column)
        if cat is None:
            cat = row.get("label")
        cat = str(cat) if cat is not None else "Unknown"
        if cat not in categories:
            categories.append(cat)
            
    # Series mapping
    series_dict = {}
    if series_column:
        for row in data:
            s_name = str(row.get(series_column, "Unknown"))
            if s_name not in series_dict:
                series_dict[s_name] = {c: 0 for c in categories}
            
            cat = row.get(category_column)
            if cat is None: cat = row.get("label")
            cat = str(cat) if cat is not None else "Unknown"
            
            series_dict[s_name][cat] = row.get("value", row.get("sales", 0))
    else:
        series_dict["Series"] = {}
        for row in data:
            cat = row.get(category_column)
            if cat is None: cat = row.get("label")
            cat = str(cat) if cat is not None else "Unknown"
            series_dict["Series"][cat] = row.get("value", 0)

    # Pie charts use named slices rather than Cartesian axes. Keep this path
    # explicit so share-of-total visuals render as real donut charts.
    if chart_type in {"pie", "donut"}:
        pie_data = []
        for row in data:
            name = row.get(category_column)
            if name is None:
                name = row.get("label", "Unknown")
            pie_data.append({"name": str(name), "value": row.get("value", 0)})
        return {
            "backgroundColor": "transparent",
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"type": "scroll", "orient": "vertical", "right": "0%", "top": "middle"},
            "series": [{"name": title, "type": "pie", "radius": ["42%", "72%"], "center": ["38%", "52%"], "avoidLabelOverlap": True, "label": {"show": False}, "emphasis": {"label": {"show": True, "fontWeight": "bold"}}, "data": pie_data}],
        }

    # Build Series array
    series_array = []
    for s_name, s_data in series_dict.items():
        s_obj = {
            "name": s_name,
            "type": chart_type,
            "data": [s_data.get(c, 0) for c in categories]
        }
        if chart_type == "line":
            s_obj["smooth"] = True
            # if we have many data points, maybe fill area
            s_obj["areaStyle"] = {"opacity": 0.2}
        series_array.append(s_obj)

    option = {
        "backgroundColor": "transparent",
        "title": {
            "show": False, # rendered by HTML usually
            "text": title
        },
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {
                "type": "shadow" if chart_type == "bar" else "cross"
            }
        },
        "grid": {
            "left": "3%",
            "right": "4%",
            "bottom": "5%",
            "top": "15%",
            "containLabel": True
        }
    }
    
    if series_column:
        option["legend"] = {
            "data": list(series_dict.keys()),
            "top": "5%"
        }

    x_axis = {
        "type": "category" if orientation == "vertical" else "value",
    }
    y_axis = {
        "type": "value" if orientation == "vertical" else "category",
    }
    
    if orientation == "vertical":
        x_axis["data"] = categories
        if len(categories) > 5:
            x_axis["axisLabel"] = {"rotate": 30, "interval": 0}
    else:
        y_axis["data"] = categories

    option["xAxis"] = x_axis
    option["yAxis"] = y_axis
    option["series"] = series_array

    return option
