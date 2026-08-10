from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import math
import numpy as np
import pandas as pd

class SamplingError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

@dataclass(frozen=True)
class SamplingResult:
    dataframe:pd.DataFrame
    method:str
    rows_before:int
    rows_after:int
    sample_fraction:float
    seed:int
    replace:bool
    selected_original_indexes:list[int]
    strata:list[str]
    allocation:str|None
    stratum_sample_counts:list[dict[str,Any]]

def _resolve_n(total,n,fraction,replace):
    if (n is None)==(fraction is None):raise SamplingError('SAMPLE_SIZE_REQUIRED','Specify exactly one of sample_size or sample_fraction.')
    if n is not None:
        if not isinstance(n,int) or n<1:raise SamplingError('INVALID_SAMPLE_SIZE','sample_size must be a positive integer.')
        out=n
    else:
        if not isinstance(fraction,(int,float)) or not 0<fraction<=1:raise SamplingError('INVALID_SAMPLE_FRACTION','sample_fraction must be > 0 and <= 1.')
        out=max(1,int(math.ceil(total*fraction))) if total else 0
    if not replace and out>total:raise SamplingError('SAMPLE_TOO_LARGE','Sample size exceeds dataset rows without replacement.',{'sample_size':out,'rows':total})
    return out

def _largest_remainder(sizes,total_n):
    total=sum(sizes)
    raw=[(s/total*total_n if total else 0) for s in sizes];base=[math.floor(x) for x in raw];left=total_n-sum(base)
    order=sorted(range(len(raw)),key=lambda i:(-(raw[i]-base[i]),i))
    for i in order[:left]:base[i]+=1
    return base

def sample_dataframe(df:pd.DataFrame,*,method:str,sample_size=None,sample_fraction=None,seed:int=0,replace:bool=False,strata=None,allocation:str='proportional')->SamplingResult:
    if not isinstance(df,pd.DataFrame):raise SamplingError('INVALID_DATAFRAME','Input must be a pandas DataFrame.')
    if not isinstance(seed,int):raise SamplingError('INVALID_SEED','seed must be an integer.')
    if method not in {'simple_random','systematic','stratified','bootstrap'}:raise SamplingError('INVALID_METHOD','Unsupported sampling method.',{'method':method})
    if len(df)==0:raise SamplingError('EMPTY_DATASET','Cannot sample an empty dataset.')
    rng=np.random.default_rng(seed);strata=list(strata or []);alloc_metrics=[]

    if method=='bootstrap':
        if sample_size is None and sample_fraction is None:sample_size=len(df)
        n=_resolve_n(len(df),sample_size,sample_fraction,True);replace=True
        idx=rng.choice(len(df),size=n,replace=True).tolist()
    elif method=='simple_random':
        n=_resolve_n(len(df),sample_size,sample_fraction,replace)
        idx=rng.choice(len(df),size=n,replace=replace).tolist()
    elif method=='systematic':
        if replace:raise SamplingError('REPLACEMENT_NOT_SUPPORTED','Systematic sampling does not support replacement.')
        n=_resolve_n(len(df),sample_size,sample_fraction,False)
        if n==0:idx=[]
        else:
            interval=len(df)/n;start=float(rng.uniform(0,interval));idx=[min(len(df)-1,int(start+i*interval)) for i in range(n)]
    else:
        if replace:raise SamplingError('REPLACEMENT_NOT_SUPPORTED','Stratified sampling v1 does not support replacement.')
        if not strata:raise SamplingError('STRATA_REQUIRED','Stratified sampling requires at least one strata column.')
        if len(strata)!=len(set(strata)):raise SamplingError('DUPLICATE_STRATA_COLUMN','strata contains duplicate columns.')
        unknown=[c for c in strata if c not in df.columns]
        if unknown:raise SamplingError('UNKNOWN_STRATA_COLUMN','One or more strata columns do not exist.',{'columns':unknown})
        if allocation not in {'proportional','equal'}:raise SamplingError('INVALID_ALLOCATION','allocation must be proportional or equal.')
        n=_resolve_n(len(df),sample_size,sample_fraction,False)
        groups=list(df.groupby(strata,dropna=False,sort=False))
        sizes=[len(g) for _,g in groups]
        if allocation=='proportional':counts=_largest_remainder(sizes,n)
        else:
            q,r=divmod(n,len(groups));counts=[q+(1 if i<r else 0) for i in range(len(groups))]
        # Rebalance any allocations that exceed a small stratum.
        counts=[min(c,s) for c,s in zip(counts,sizes)]
        remaining=n-sum(counts)
        while remaining>0:
            progressed=False
            for i,s in enumerate(sizes):
                if counts[i]<s and remaining>0:
                    counts[i]+=1;remaining-=1;progressed=True
            if not progressed:break
        idx=[]
        for (key,g),count in zip(groups,counts):
            positions=g.index.to_numpy()
            chosen=rng.choice(positions,size=count,replace=False).tolist() if count else []
            idx.extend(chosen)
            keytuple=key if isinstance(key,tuple) else (key,)
            alloc_metrics.append({'key':{c:(None if pd.isna(v) else v) for c,v in zip(strata,keytuple)},'population_rows':len(g),'sample_rows':count})

    sampled=df.loc[idx].copy(deep=True).reset_index(drop=True)
    return SamplingResult(sampled,method,len(df),len(sampled),len(sampled)/len(df),seed,replace,idx,strata,allocation if method=='stratified' else None,alloc_metrics)
