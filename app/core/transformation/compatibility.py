from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import pandas as pd
from pandas.api.types import is_numeric_dtype,is_bool_dtype,is_datetime64_any_dtype,is_string_dtype

class CompatibilityError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

@dataclass(frozen=True)
class Finding:
    severity:str
    code:str
    message:str
    details:dict[str,Any]

def _family(s):
    if is_bool_dtype(s): return "boolean"
    if is_numeric_dtype(s): return "numeric"
    if is_datetime64_any_dtype(s): return "datetime"
    if is_string_dtype(s) or s.dtype=="object": return "string"
    return "other"

def _validate(left,right,keys):
    if not isinstance(left,pd.DataFrame) or not isinstance(right,pd.DataFrame):
        raise CompatibilityError("INVALID_DATAFRAME","Both inputs must be pandas DataFrames.")
    if not keys: raise CompatibilityError("JOIN_KEYS_REQUIRED","At least one key pair is required.")
    lk=[];rk=[]
    for i,k in enumerate(keys):
        l=k.get("left");r=k.get("right")
        if not l or not r: raise CompatibilityError("JOIN_KEY_REQUIRED","Each key requires left and right names.",{"key_index":i})
        if l not in left.columns: raise CompatibilityError("UNKNOWN_LEFT_COLUMN","Left key does not exist.",{"column":l})
        if r not in right.columns: raise CompatibilityError("UNKNOWN_RIGHT_COLUMN","Right key does not exist.",{"column":r})
        lk.append(l);rk.append(r)
    if len(lk)!=len(set(lk)): raise CompatibilityError("DUPLICATE_LEFT_JOIN_KEY","Duplicate left join key.")
    if len(rk)!=len(set(rk)): raise CompatibilityError("DUPLICATE_RIGHT_JOIN_KEY","Duplicate right join key.")
    return lk,rk

def analyze_merge_compatibility(left,right,*,keys,max_examples=20):
    lk,rk=_validate(left,right,keys)
    findings=[];key_reports=[]
    type_ok=True
    for l,r in zip(lk,rk):
        lf,rf=_family(left[l]),_family(right[r])
        compatible=(lf==rf) or ({lf,rf}<={"numeric"})
        if not compatible:
            type_ok=False
            findings.append(Finding("error","KEY_TYPE_MISMATCH",f"Join key types are incompatible: {l} ({lf}) vs {r} ({rf}).",{"left":l,"right":r,"left_family":lf,"right_family":rf}))
        key_reports.append({
            "left":l,"right":r,"left_dtype":str(left[l].dtype),"right_dtype":str(right[r].dtype),
            "left_family":lf,"right_family":rf,"compatible":compatible,
            "left_null_count":int(left[l].isna().sum()),"right_null_count":int(right[r].isna().sum()),
        })

    lvalid=left.loc[~left[lk].isna().any(axis=1)].copy()
    rvalid=right.loc[~right[rk].isna().any(axis=1)].copy()
    left_null_rows=len(left)-len(lvalid);right_null_rows=len(right)-len(rvalid)
    if left_null_rows: findings.append(Finding("warning","LEFT_NULL_KEYS","Left dataset contains rows with null merge keys.",{"rows":left_null_rows}))
    if right_null_rows: findings.append(Finding("warning","RIGHT_NULL_KEYS","Right dataset contains rows with null merge keys.",{"rows":right_null_rows}))

    ldup=int(lvalid.duplicated(subset=lk,keep=False).sum())
    rdup=int(rvalid.duplicated(subset=rk,keep=False).sum())
    lu=ldup==0;ru=rdup==0
    relation="one_to_one" if lu and ru else "one_to_many" if lu else "many_to_one" if ru else "many_to_many"
    recommendation=relation if relation!="many_to_many" else "none"
    if relation=="many_to_many":
        findings.append(Finding("warning","MANY_TO_MANY","Both sides contain duplicate merge keys; row multiplication is possible.",{"left_duplicate_rows":ldup,"right_duplicate_rows":rdup}))

    # Compare keys without relying on pandas merge coercion. This allows the
    # checker to report incompatible dtypes instead of failing while checking.
    from collections import Counter

    def tuple_keys(frame, cols):
        return [tuple(row) for row in frame[cols].itertuples(index=False, name=None)]

    left_key_rows = tuple_keys(lvalid, lk)
    right_key_rows = tuple_keys(rvalid, rk)
    left_counts = Counter(left_key_rows)
    right_counts = Counter(right_key_rows)
    left_unique_order = list(dict.fromkeys(left_key_rows))
    right_unique_order = list(dict.fromkeys(right_key_rows))
    right_key_set = set(right_unique_order)
    left_key_set = set(left_unique_order)

    left_matched_keys = sum(1 for key in left_unique_order if key in right_key_set) if type_ok else 0
    right_matched_keys = sum(1 for key in right_unique_order if key in left_key_set) if type_ok else 0
    left_key_coverage=round(left_matched_keys/len(left_unique_order)*100,6) if left_unique_order else 0.0
    right_key_coverage=round(right_matched_keys/len(right_unique_order)*100,6) if right_unique_order else 0.0

    unmatched_left=[]
    for key in left_unique_order:
        if type_ok and key in right_key_set:
            continue
        unmatched_left.append({c:v for c,v in zip(lk,key)})
        if len(unmatched_left)>=max_examples:
            break

    unmatched_right=[]
    for key in right_unique_order:
        if type_ok and key in left_key_set:
            continue
        unmatched_right.append({c:v for c,v in zip(rk,key)})
        if len(unmatched_right)>=max_examples:
            break

    if left_key_coverage<100: findings.append(Finding("warning","LEFT_UNMATCHED_KEYS","Some distinct left keys have no exact match.",{"coverage_pct":left_key_coverage}))
    if right_key_coverage<100: findings.append(Finding("info","RIGHT_UNMATCHED_KEYS","Some distinct right keys have no exact match.",{"coverage_pct":right_key_coverage}))

    estimated = sum(left_counts[key] * right_counts[key] for key in left_key_set & right_key_set) if type_ok else 0
    factor=round(estimated/len(left),6) if len(left) else 0.0
    if factor>2:
        findings.append(Finding("warning","ROW_EXPLOSION_RISK","Estimated inner join produces more than 2x left rows.",{"factor":factor,"estimated_rows":estimated}))

    collapsed={l for l,r in zip(lk,rk) if l==r}
    overlaps=sorted((set(left.columns)&set(right.columns))-collapsed)
    if overlaps: findings.append(Finding("info","OVERLAPPING_COLUMNS","Non-key columns overlap and will require suffixes.",{"columns":overlaps}))

    score=100
    score-=0 if type_ok else 35
    score-=min(20,left_null_rows*5)
    score-=min(10,right_null_rows*3)
    score-=20 if relation=="many_to_many" else 0
    score-=15 if left_key_coverage<80 else 5 if left_key_coverage<100 else 0
    score-=10 if factor>2 else 0
    score=max(0,score)
    status="ready" if score>=80 and type_ok and relation!="many_to_many" else "review" if score>=50 else "not_ready"

    return {
        "compatibility_score":score,"status":status,"key_reports":key_reports,
        "left_rows":len(left),"right_rows":len(right),
        "left_null_key_rows":left_null_rows,"right_null_key_rows":right_null_rows,
        "left_duplicate_key_rows":ldup,"right_duplicate_key_rows":rdup,
        "inferred_relationship":relation,"recommended_validation":recommendation,
        "left_distinct_keys":len(left_unique_order),"right_distinct_keys":len(right_unique_order),
        "left_key_match_coverage_pct":left_key_coverage,"right_key_match_coverage_pct":right_key_coverage,
        "unmatched_left_key_examples":unmatched_left,"unmatched_right_key_examples":unmatched_right,
        "estimated_inner_join_rows":estimated,"estimated_row_multiplication_factor":factor,
        "overlapping_non_key_columns":overlaps,
        "findings":[f.__dict__ for f in findings],
    }
