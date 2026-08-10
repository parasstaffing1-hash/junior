from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re
import pandas as pd
from rapidfuzz import fuzz

class FuzzyMatchError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

SCORERS={
    "ratio":fuzz.ratio,
    "partial_ratio":fuzz.partial_ratio,
    "token_sort_ratio":fuzz.token_sort_ratio,
    "token_set_ratio":fuzz.token_set_ratio,
}

@dataclass(frozen=True)
class FieldSummary:
    left:str
    right:str
    weight:float

@dataclass(frozen=True)
class FuzzyMatchResult:
    dataframe:pd.DataFrame
    left_rows:int
    right_rows:int
    output_rows:int
    matched_left_rows:int
    unmatched_left_rows:int
    match_rate_pct:float
    mean_best_score:float
    threshold:float
    top_k:int
    scorer:str
    fields:list[FieldSummary]
    blocking_keys:list[dict[str,str]]
    candidate_pairs_evaluated:int

def _norm(v,case_sensitive,trim,collapse_whitespace):
    if v is None or pd.isna(v):return ""
    s=str(v)
    if trim:s=s.strip()
    if collapse_whitespace:s=re.sub(r"\s+"," ",s)
    if not case_sensitive:s=s.casefold()
    return s

def _validate(left,right,fields,blocking,right_include,prefix,top_k):
    if not isinstance(left,pd.DataFrame) or not isinstance(right,pd.DataFrame):
        raise FuzzyMatchError("INVALID_DATAFRAME","Both inputs must be pandas DataFrames.")
    if not fields:raise FuzzyMatchError("FIELDS_REQUIRED","At least one fuzzy field pair is required.")
    seen_l=set();seen_r=set()
    total=0
    for i,f in enumerate(fields):
        l=f.get("left");r=f.get("right");w=float(f.get("weight",1))
        if l not in left.columns:raise FuzzyMatchError("UNKNOWN_LEFT_COLUMN","Fuzzy field missing on left.",{"column":l,"field_index":i})
        if r not in right.columns:raise FuzzyMatchError("UNKNOWN_RIGHT_COLUMN","Fuzzy field missing on right.",{"column":r,"field_index":i})
        if w<=0:raise FuzzyMatchError("INVALID_WEIGHT","Field weights must be > 0.",{"field_index":i})
        total+=w
    for i,b in enumerate(blocking or []):
        l=b.get("left");r=b.get("right")
        if l not in left.columns:raise FuzzyMatchError("UNKNOWN_LEFT_BLOCKING_COLUMN","Blocking column missing on left.",{"column":l})
        if r not in right.columns:raise FuzzyMatchError("UNKNOWN_RIGHT_BLOCKING_COLUMN","Blocking column missing on right.",{"column":r})
    for c in right_include or []:
        if c not in right.columns:raise FuzzyMatchError("UNKNOWN_RIGHT_INCLUDE_COLUMN","Requested right output column does not exist.",{"column":c})
    if top_k<1:raise FuzzyMatchError("INVALID_TOP_K","top_k must be >= 1.")
    reserved=["__left_row_index","__right_row_index","__match_rank","__match_score","__matched"]
    collisions=[c for c in reserved if c in left.columns]
    if collisions:raise FuzzyMatchError("RESERVED_COLUMN_COLLISION","Left dataset contains reserved match metadata columns.",{"columns":collisions})
    outnames=[f"{prefix}{c}" for c in (right_include or [])]
    collision=[c for c in outnames if c in left.columns]
    if collision:raise FuzzyMatchError("OUTPUT_COLUMN_COLLISION","Matched right output columns collide with left columns.",{"columns":collision})
    if len(outnames)!=len(set(outnames)):raise FuzzyMatchError("OUTPUT_COLUMN_COLLISION","Matched right output columns are duplicated.")

def fuzzy_match(
    left,right,*,
    fields:list[dict[str,Any]],
    scorer:str="token_set_ratio",
    threshold:float=80,
    top_k:int=1,
    case_sensitive:bool=False,
    trim:bool=True,
    collapse_whitespace:bool=True,
    blocking_keys:list[dict[str,str]]|None=None,
    right_include_columns:list[str]|None=None,
    right_prefix:str="matched__",
    include_unmatched:bool=True,
)->FuzzyMatchResult:
    blocking_keys=list(blocking_keys or [])
    right_include_columns=list(right_include_columns or [])
    _validate(left,right,fields,blocking_keys,right_include_columns,right_prefix,top_k)
    if scorer not in SCORERS:raise FuzzyMatchError("INVALID_SCORER","Unsupported fuzzy scorer.",{"scorer":scorer})
    if not 0<=threshold<=100:raise FuzzyMatchError("INVALID_THRESHOLD","threshold must be between 0 and 100.")
    score_fn=SCORERS[scorer]
    weights=[float(f.get("weight",1)) for f in fields]
    total_weight=sum(weights)

    # Precompute normalized strings.
    right_norm={}
    for fi,f in enumerate(fields):
        right_norm[fi]=right[f["right"]].map(lambda v:_norm(v,case_sensitive,trim,collapse_whitespace))
    left_norm={}
    for fi,f in enumerate(fields):
        left_norm[fi]=left[f["left"]].map(lambda v:_norm(v,case_sensitive,trim,collapse_whitespace))

    # Blocking index preserves right source row order.
    block_index={}
    if blocking_keys:
        for ridx,row in right.iterrows():
            key=tuple(row[b["right"]] if not pd.isna(row[b["right"]]) else None for b in blocking_keys)
            block_index.setdefault(key,[]).append(ridx)

    rows=[];best_scores=[];matched_left=0;candidates_evaluated=0
    for lidx,lrow in left.iterrows():
        if blocking_keys:
            bkey=tuple(lrow[b["left"]] if not pd.isna(lrow[b["left"]]) else None for b in blocking_keys)
            candidates=block_index.get(bkey,[])
        else:
            candidates=list(right.index)

        scored=[]
        for ridx in candidates:
            weighted=0.0
            for fi,f in enumerate(fields):
                weighted += score_fn(left_norm[fi].loc[lidx],right_norm[fi].loc[ridx]) * weights[fi]
            score=weighted/total_weight
            scored.append((float(score),int(ridx)))
            candidates_evaluated+=1
        scored.sort(key=lambda x:(-x[0],x[1]))
        accepted=[x for x in scored if x[0]>=threshold][:top_k]

        if accepted:
            matched_left+=1;best_scores.append(accepted[0][0])
            for rank,(score,ridx) in enumerate(accepted,start=1):
                out=lrow.to_dict()
                out.update({"__left_row_index":int(lidx),"__right_row_index":int(ridx),"__match_rank":rank,"__match_score":round(score,6),"__matched":True})
                for c in right_include_columns:out[f"{right_prefix}{c}"]=right.loc[ridx,c]
                rows.append(out)
        elif include_unmatched:
            out=lrow.to_dict()
            out.update({"__left_row_index":int(lidx),"__right_row_index":pd.NA,"__match_rank":pd.NA,"__match_score":pd.NA,"__matched":False})
            for c in right_include_columns:out[f"{right_prefix}{c}"]=pd.NA
            rows.append(out)

    output=pd.DataFrame(rows)
    unmatched=len(left)-matched_left
    return FuzzyMatchResult(
        dataframe=output,left_rows=len(left),right_rows=len(right),output_rows=len(output),
        matched_left_rows=matched_left,unmatched_left_rows=unmatched,
        match_rate_pct=round(matched_left/len(left)*100,6) if len(left) else 0.0,
        mean_best_score=round(sum(best_scores)/len(best_scores),6) if best_scores else 0.0,
        threshold=float(threshold),top_k=top_k,scorer=scorer,
        fields=[FieldSummary(f["left"],f["right"],float(f.get("weight",1))) for f in fields],
        blocking_keys=blocking_keys,candidate_pairs_evaluated=candidates_evaluated,
    )
