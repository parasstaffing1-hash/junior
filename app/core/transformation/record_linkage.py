from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re
import pandas as pd
from rapidfuzz import fuzz

class RecordLinkageError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

SCORERS={"ratio":fuzz.ratio,"partial_ratio":fuzz.partial_ratio,"token_sort_ratio":fuzz.token_sort_ratio,"token_set_ratio":fuzz.token_set_ratio}

def _norm(v):
    if v is None or pd.isna(v): return ""
    return re.sub(r"\s+"," ",str(v).strip()).casefold()

@dataclass(frozen=True)
class LinkageResult:
    dataframe:pd.DataFrame
    left_rows:int
    right_rows:int
    matched_left_rows:int
    unmatched_left_rows:int
    matched_right_rows:int
    unmatched_right_rows:int
    output_rows:int
    candidate_pairs:int
    mean_match_score:float
    threshold:float
    scorer:str

def link_records(left,right,*,fields,blocking_keys=None,threshold=80,scorer="token_set_ratio",right_include_columns=None,right_prefix="linked__",include_unmatched=True):
    if not isinstance(left,pd.DataFrame) or not isinstance(right,pd.DataFrame):
        raise RecordLinkageError("INVALID_DATAFRAME","Both inputs must be pandas DataFrames.")
    if not fields: raise RecordLinkageError("FIELDS_REQUIRED","At least one linkage field is required.")
    if scorer not in SCORERS: raise RecordLinkageError("INVALID_SCORER","Unsupported scorer.")
    if not 0<=threshold<=100: raise RecordLinkageError("INVALID_THRESHOLD","threshold must be between 0 and 100.")
    blocking_keys=list(blocking_keys or [])
    right_include_columns=list(right_include_columns or [])
    total_weight=0.0
    for i,f in enumerate(fields):
        l=f.get("left");r=f.get("right");wt=float(f.get("weight",1))
        if l not in left.columns: raise RecordLinkageError("UNKNOWN_LEFT_COLUMN","Left field does not exist.",{"column":l})
        if r not in right.columns: raise RecordLinkageError("UNKNOWN_RIGHT_COLUMN","Right field does not exist.",{"column":r})
        if wt<=0: raise RecordLinkageError("INVALID_WEIGHT","Field weights must be > 0.",{"field_index":i})
        total_weight+=wt
    for b in blocking_keys:
        if b["left"] not in left.columns: raise RecordLinkageError("UNKNOWN_LEFT_BLOCK_COLUMN","Left blocking column does not exist.",{"column":b["left"]})
        if b["right"] not in right.columns: raise RecordLinkageError("UNKNOWN_RIGHT_BLOCK_COLUMN","Right blocking column does not exist.",{"column":b["right"]})
    for c in right_include_columns:
        if c not in right.columns: raise RecordLinkageError("UNKNOWN_RIGHT_INCLUDE_COLUMN","Right output column does not exist.",{"column":c})
        if f"{right_prefix}{c}" in left.columns: raise RecordLinkageError("OUTPUT_COLUMN_COLLISION","Linked output column collides with left dataset.",{"column":f"{right_prefix}{c}"})
    reserved={"__linked_right_row_index","__link_score","__linked"}
    if reserved & set(left.columns): raise RecordLinkageError("RESERVED_COLUMN_COLLISION","Left dataset contains reserved linkage metadata columns.")

    scorer_fn=SCORERS[scorer]
    block_index={}
    if blocking_keys:
        for ridx,row in right.iterrows():
            key=tuple(None if pd.isna(row[b["right"]]) else row[b["right"]] for b in blocking_keys)
            block_index.setdefault(key,[]).append(ridx)

    candidates=[];candidate_pairs=0
    for lidx,lrow in left.iterrows():
        if blocking_keys:
            bkey=tuple(None if pd.isna(lrow[b["left"]]) else lrow[b["left"]] for b in blocking_keys)
            rindices=block_index.get(bkey,[])
        else:
            rindices=list(right.index)
        for ridx in rindices:
            score=0.0
            for f in fields:
                score += scorer_fn(_norm(lrow[f["left"]]),_norm(right.loc[ridx,f["right"]]))*float(f.get("weight",1))
            score/=total_weight
            candidate_pairs+=1
            if score>=threshold:
                candidates.append((float(score),int(lidx),int(ridx)))

    # Highest score first; stable deterministic tie-breaking.
    candidates.sort(key=lambda x:(-x[0],x[1],x[2]))
    used_left=set();used_right=set();assign={}
    for score,lidx,ridx in candidates:
        if lidx in used_left or ridx in used_right: continue
        used_left.add(lidx);used_right.add(ridx);assign[lidx]=(ridx,score)

    rows=[];scores=[]
    for lidx,lrow in left.iterrows():
        out=lrow.to_dict()
        if lidx in assign:
            ridx,score=assign[lidx]
            out.update({"__linked_right_row_index":int(ridx),"__link_score":round(score,6),"__linked":True})
            for c in right_include_columns: out[f"{right_prefix}{c}"]=right.loc[ridx,c]
            rows.append(out);scores.append(score)
        elif include_unmatched:
            out.update({"__linked_right_row_index":pd.NA,"__link_score":pd.NA,"__linked":False})
            for c in right_include_columns: out[f"{right_prefix}{c}"]=pd.NA
            rows.append(out)

    df=pd.DataFrame(rows)
    return LinkageResult(
        dataframe=df,left_rows=len(left),right_rows=len(right),
        matched_left_rows=len(used_left),unmatched_left_rows=len(left)-len(used_left),
        matched_right_rows=len(used_right),unmatched_right_rows=len(right)-len(used_right),
        output_rows=len(df),candidate_pairs=candidate_pairs,
        mean_match_score=round(sum(scores)/len(scores),6) if scores else 0.0,
        threshold=float(threshold),scorer=scorer,
    )
