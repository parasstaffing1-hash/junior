from __future__ import annotations
import pandas as pd
from pandas.api.types import is_numeric_dtype,is_datetime64_any_dtype
class RecommendationError(Exception):
    def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
TOOLS={71:("Bar Chart","/api/v1/datasets/{dataset_id}/charts/bar/generate"),72:("Line Chart","/api/v1/datasets/{dataset_id}/charts/line/generate"),73:("Area Chart","/api/v1/datasets/{dataset_id}/charts/area/generate"),74:("Pie/Donut Chart","/api/v1/datasets/{dataset_id}/charts/pie-donut/generate"),75:("Histogram","/api/v1/datasets/{dataset_id}/charts/histogram/generate"),76:("Scatter/Bubble Plot","/api/v1/datasets/{dataset_id}/charts/scatter-bubble/generate"),77:("Box/Violin Plot","/api/v1/datasets/{dataset_id}/charts/box-violin/generate"),78:("Heatmap/Correlation Matrix","/api/v1/datasets/{dataset_id}/charts/heatmap/generate"),79:("Business Chart","/api/v1/datasets/{dataset_id}/charts/business/generate")}
def _dt(s):
    if is_datetime64_any_dtype(s):return True
    if is_numeric_dtype(s):return False
    v=s.dropna()
    if len(v)<2:return False
    return float(pd.to_datetime(v,errors="coerce",format="mixed").notna().mean())>=.8
def _roles(df):
    n=[c for c in df.columns if is_numeric_dtype(df[c])];t=[c for c in df.columns if _dt(df[c])];cat=[c for c in df.columns if c not in n and c not in t];return n,cat,t
def _r(tool,score,why,body):
    name,endpoint=TOOLS[tool];return {"tool_number":tool,"chart":name,"score":round(float(score),3),"rationale":why,"endpoint":endpoint,"request_body":body}
def recommend_charts(df:pd.DataFrame,*,intent="auto",x_column=None,y_column=None,category_column=None,time_column=None,group_column=None,size_column=None,numeric_columns=None,business_chart_type=None,comparison_column=None,line_column=None,max_recommendations=5):
    if not isinstance(df,pd.DataFrame):raise RecommendationError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    allowed={"auto","comparison","trend","distribution","relationship","composition","group_distribution","correlation","business"}
    if intent not in allowed:raise RecommendationError("INVALID_INTENT","Unsupported visualization intent.")
    if max_recommendations<1:raise RecommendationError("INVALID_MAX_RECOMMENDATIONS","max_recommendations must be >=1.")
    named=[x for x in [x_column,y_column,category_column,time_column,group_column,size_column,comparison_column,line_column] if x]+(numeric_columns or [])
    unknown=[c for c in named if c not in df.columns]
    if unknown:raise RecommendationError("UNKNOWN_COLUMN","Selected column does not exist.",{"columns":sorted(set(unknown))})
    nums,cats,times=_roles(df);recs=[]
    def add(tool,score,why,body):recs.append(_r(tool,score,why,body))
    if intent=="business":
        bt=business_chart_type or "pareto";cat=category_column or x_column or (cats[0] if cats else None);val=y_column or (nums[0] if nums else None)
        if bt not in {"waterfall","variance","pareto","funnel","combo"}:raise RecommendationError("INVALID_BUSINESS_CHART","Unsupported business chart type.")
        if not cat or not val:raise RecommendationError("INSUFFICIENT_COLUMNS","Business chart needs category and numeric value.")
        body={"chart_type":bt,"category_column":cat,"value_column":val}
        if bt=="variance":
            comp=comparison_column or (nums[1] if len(nums)>1 else None)
            if not comp:raise RecommendationError("COMPARISON_REQUIRED","Variance chart needs comparison_column.")
            body["comparison_column"]=comp
        if bt=="combo":
            line=line_column or (nums[1] if len(nums)>1 else None)
            if not line:raise RecommendationError("LINE_REQUIRED","Combo chart needs line_column.")
            body["line_column"]=line
        add(79,.99,f"{bt.title()} is a purpose-built business chart.",body)
    elif intent=="trend":
        t=time_column or x_column or (times[0] if times else None);y=y_column or (nums[0] if nums else None)
        if not t or not y:raise RecommendationError("INSUFFICIENT_COLUMNS","Trend requires time/order and numeric value.")
        add(72,.98,"Line charts are the default for ordered/time trends.",{"x_column":t,"y_column":y,"parse_datetime":t in times})
        add(73,.82,"Area charts emphasize magnitude over time.",{"x_column":t,"y_column":y,"parse_datetime":t in times})
    elif intent=="distribution":
        y=y_column or x_column or (nums[0] if nums else None)
        if not y or y not in nums:raise RecommendationError("NUMERIC_REQUIRED","Distribution requires a numeric column.")
        add(75,.98,"Histogram reveals numeric distribution shape.",{"value_column":y});add(77,.78,"Box plot highlights quartiles and outliers.",{"value_column":y,"plot_type":"box"})
    elif intent=="group_distribution":
        y=y_column or (nums[0] if nums else None);g=group_column or category_column or (cats[0] if cats else None)
        if not y or not g:raise RecommendationError("INSUFFICIENT_COLUMNS","Group distribution needs numeric value and category.")
        add(77,.99,"Box/violin plots compare distributions across groups.",{"value_column":y,"group_column":g,"plot_type":"box"});add(75,.68,"Grouped histograms compare distribution shapes.",{"value_column":y,"group_column":g})
    elif intent=="relationship":
        x=x_column or (nums[0] if nums else None);y=y_column or (nums[1] if len(nums)>1 else None)
        if not x or not y or x not in nums or y not in nums:raise RecommendationError("TWO_NUMERIC_REQUIRED","Relationship needs two numeric columns.")
        body={"x_column":x,"y_column":y,"regression_line":True}
        if size_column:
            if size_column not in nums:raise RecommendationError("SIZE_MUST_BE_NUMERIC","size_column must be numeric.")
            body["size_column"]=size_column
        if group_column:body["group_column"]=group_column
        add(76,.99,"Scatter/bubble plots directly encode numeric relationships.",body)
        if len(nums)>=3:add(78,.67,"A correlation heatmap provides wider numeric context.",{"mode":"correlation","columns":numeric_columns or nums})
    elif intent=="composition":
        cat=category_column or x_column or (cats[0] if cats else None);y=y_column or (nums[0] if nums else None)
        if not cat:raise RecommendationError("CATEGORY_REQUIRED","Composition requires a category.")
        card=int(df[cat].nunique(dropna=True))
        if card<=8:
            body={"category_column":cat,"mode":"donut","aggregation":"sum" if y else "count"};
            if y:body["value_column"]=y
            add(74,.96,"Few categories make donut/pie suitable for share-of-total.",body);add(71,.82,"Bar chart is a precise alternative.",{"category_column":cat,"value_column":y,"aggregation":"sum" if y else "count"})
        else:add(71,.98,"High cardinality is easier to compare with bars than slices.",{"category_column":cat,"value_column":y,"aggregation":"sum" if y else "count","top_n":10})
    elif intent=="comparison":
        cat=category_column or x_column or (cats[0] if cats else None);y=y_column or (nums[0] if nums else None)
        if not cat:raise RecommendationError("CATEGORY_REQUIRED","Comparison requires a category.")
        add(71,.99,"Bar charts are the clearest default for category comparisons.",{"category_column":cat,"value_column":y,"aggregation":"sum" if y else "count"})
    elif intent=="correlation":
        selected=numeric_columns or nums
        if len(selected)<2:raise RecommendationError("INSUFFICIENT_COLUMNS","Correlation needs at least two numeric columns.")
        add(78,.99,"Correlation matrix summarizes many numeric relationships.",{"mode":"correlation","columns":selected});add(76,.65,"Scatter plot inspects a pair in detail.",{"x_column":selected[0],"y_column":selected[1],"regression_line":True})
    else:
        if time_column or (x_column and x_column in times) or (times and nums):
            t=time_column or (x_column if x_column in times else times[0]);y=y_column or nums[0];add(72,.95,"Detected time field with numeric measure.",{"x_column":t,"y_column":y,"parse_datetime":True})
        if x_column in nums and y_column in nums:add(76,.94,"Two explicit numeric axes indicate a relationship view.",{"x_column":x_column,"y_column":y_column,"regression_line":True})
        if category_column and y_column in nums:add(71,.92,"Category plus numeric measure indicates comparison.",{"category_column":category_column,"value_column":y_column,"aggregation":"sum"})
        if group_column and y_column in nums:add(77,.88,"Group plus numeric measure supports distribution comparison.",{"value_column":y_column,"group_column":group_column,"plot_type":"box"})
        if len(nums)>=3:add(78,.82,"Multiple numeric variables support a correlation heatmap.",{"mode":"correlation","columns":numeric_columns or nums})
        if nums:add(75,.72,"Numeric variables benefit from distribution inspection.",{"value_column":y_column or nums[0]})
        if cats and not recs:add(71,.9,"Categorical data is naturally summarized with count bars.",{"category_column":category_column or cats[0],"aggregation":"count"})
        if not recs:raise RecommendationError("NO_RECOMMENDATION","Could not infer a suitable chart.")
    unique=[];seen=set()
    for item in recs:
        key=(item["tool_number"],repr(sorted(item["request_body"].items())))
        if key not in seen:seen.add(key);unique.append(item)
    unique.sort(key=lambda x:-x["score"])
    return {"intent":intent,"detected_roles":{"numeric":nums,"categorical":cats,"datetime":times},"recommendations":unique[:max_recommendations],"recommended":unique[0]}
