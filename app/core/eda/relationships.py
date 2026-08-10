from __future__ import annotations
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats

class RelationshipError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _cramers_v(chi2,n,r,k):
    if n<=1:return None
    phi2=chi2/n
    phi2corr=max(0,phi2-((k-1)*(r-1))/(n-1))
    rcorr=r-((r-1)**2)/(n-1)
    kcorr=k-((k-1)**2)/(n-1)
    denom=min(kcorr-1,rcorr-1)
    return None if denom<=0 else math.sqrt(phi2corr/denom)

def _strength(v):
    if v is None:return "undefined"
    if v<.1:return "negligible"
    if v<.3:return "weak"
    if v<.5:return "moderate"
    return "strong"

def _analyze(df,a,b,alpha,min_expected_threshold):
    pair=df[[a,b]].dropna()
    if len(pair)<2:return {"left":a,"right":b,"status":"insufficient_data","observations":len(pair)}
    tab=pd.crosstab(pair[a],pair[b])
    if tab.shape[0]<2 or tab.shape[1]<2:
        return {"left":a,"right":b,"status":"insufficient_levels","observations":len(pair),"shape":list(tab.shape)}
    chi2,p,dfree,expected=stats.chi2_contingency(tab,correction=False)
    v=_cramers_v(float(chi2),int(tab.to_numpy().sum()),tab.shape[0],tab.shape[1])
    expected_df=pd.DataFrame(expected,index=tab.index,columns=tab.columns)
    min_expected=float(expected_df.to_numpy().min())
    below5=int((expected_df.to_numpy()<5).sum())
    cells=int(expected_df.size)
    below_threshold=int((expected_df.to_numpy()<min_expected_threshold).sum())
    assumption_ok=bool(min_expected>=1 and below_threshold/cells<=.2)
    fisher=None
    odds=None
    if tab.shape==(2,2):
        arr=tab.to_numpy()
        fr=stats.fisher_exact(arr,alternative="two-sided")
        fisher_odds=float(fr.statistic)
        fisher={"odds_ratio":None if not math.isfinite(fisher_odds) else fisher_odds,"p_value":float(fr.pvalue),"reject_null":bool(fr.pvalue<alpha)}
        a1,b1,c1,d1=map(float,arr.ravel())
        odds=None if b1*c1==0 else (a1*d1)/(b1*c1)
    row_props=tab.div(tab.sum(axis=1),axis=0).fillna(0)
    col_props=tab.div(tab.sum(axis=0),axis=1).fillna(0)
    return {
        "left":a,"right":b,"status":"ok","observations":len(pair),
        "contingency_table":{"index":[str(x) for x in tab.index],"columns":[str(x) for x in tab.columns],"values":tab.astype(int).values.tolist()},
        "row_proportions":{"values":row_props.values.tolist()},
        "column_proportions":{"values":col_props.values.tolist()},
        "chi_square":{"statistic":float(chi2),"p_value":float(p),"degrees_of_freedom":int(dfree),"reject_null":bool(p<alpha)},
        "cramers_v":v,"association_strength":_strength(v),
        "expected_count_diagnostics":{"min_expected":min_expected,"cells_below_5":below5,"cells_below_threshold":below_threshold,"cell_count":cells,"assumption_ok":assumption_ok,"threshold":min_expected_threshold},
        "fisher_exact":fisher,"odds_ratio_2x2":odds
    }

def analyze_categorical_relationships(df:pd.DataFrame,*,left=None,right=None,columns=None,alpha=.05,min_expected_threshold=5,strong_threshold=.5,include_numeric_low_cardinality=True,numeric_cardinality_limit=20):
    if not isinstance(df,pd.DataFrame):raise RelationshipError("INVALID_DATAFRAME","Input must be a pandas DataFrame.")
    if not 0<alpha<1:raise RelationshipError("INVALID_ALPHA","alpha must be between 0 and 1.")
    if min_expected_threshold<=0:raise RelationshipError("INVALID_EXPECTED_THRESHOLD","min_expected_threshold must be >0.")
    if not 0<=strong_threshold<=1:raise RelationshipError("INVALID_THRESHOLD","strong_threshold must be in [0,1].")
    if left is not None or right is not None:
        if not left or not right:raise RelationshipError("PAIR_REQUIRED","Both left and right are required.")
        selected=[left,right]
    elif columns is not None:selected=list(columns)
    else:
        selected=[]
        for c in df.columns:
            if not is_numeric_dtype(df[c]):selected.append(c)
            elif include_numeric_low_cardinality and df[c].nunique(dropna=True)<=numeric_cardinality_limit:selected.append(c)
    unknown=[c for c in selected if c not in df.columns]
    if unknown:raise RelationshipError("UNKNOWN_COLUMN","Selected column does not exist.",{"columns":unknown})
    if len(set(selected))<2:raise RelationshipError("INSUFFICIENT_COLUMNS","At least two categorical columns are required.")
    pairs=[]
    if left is not None:pairs=[_analyze(df,left,right,alpha,min_expected_threshold)]
    else:
        for i,a in enumerate(selected):
            for b in selected[i+1:]:pairs.append(_analyze(df,a,b,alpha,min_expected_threshold))
    findings=[]
    for item in pairs:
        if item.get("status")!="ok":continue
        v=item["cramers_v"]
        if v is not None and v>=strong_threshold:
            findings.append({"type":"strong_categorical_association","columns":[item["left"],item["right"]],"message":f"{item['left']} and {item['right']} have strong association (Cramér's V={v:.3f})."})
        if not item["expected_count_diagnostics"]["assumption_ok"]:
            findings.append({"type":"chi_square_assumption_warning","columns":[item["left"],item["right"]],"message":"Chi-square expected-count assumptions may be weak; inspect Fisher exact when 2x2."})
    ranked=sorted(pairs,key=lambda x:-(x.get("cramers_v") or 0))
    return {"analyzed_columns":selected,"pair_count":len(pairs),"relationships":pairs,"strongest_relationships":ranked[:20],"findings":findings}
