from __future__ import annotations
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy.stats import chi2_contingency

class EffectSizeError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _magnitude(value,kind):
    a=abs(value)
    if kind=='cramers_v':
        if a<0.1:return 'negligible'
        if a<0.3:return 'small'
        if a<0.5:return 'medium'
        return 'large'
    if kind=='cohens_h':
        if a<0.2:return 'negligible'
        if a<0.5:return 'small'
        if a<0.8:return 'medium'
        return 'large'
    if a<0.2:return 'negligible'
    if a<0.5:return 'small'
    if a<0.8:return 'medium'
    return 'large'

def _numeric(df,a,b):
    if a not in df.columns or b not in df.columns:raise EffectSizeError('UNKNOWN_COLUMN','One or both selected columns do not exist.',{'column':a,'column_b':b})
    if not is_numeric_dtype(df[a]) or not is_numeric_dtype(df[b]):raise EffectSizeError('INCOMPATIBLE_COLUMN_TYPE','Selected columns must be numeric.')

def calculate_effect_size(df:pd.DataFrame,*,effect_type:str,column=None,column_b=None,success_value=True,control_column=None):
    if not isinstance(df,pd.DataFrame):raise EffectSizeError('INVALID_DATAFRAME','Input must be a pandas DataFrame.')
    if effect_type in {'cohens_d','hedges_g','glass_delta','paired_dz'}:
        _numeric(df,column,column_b)
    if effect_type=='cohens_d':
        a=df[column].dropna().astype(float);b=df[column_b].dropna().astype(float);n1,n2=len(a),len(b)
        if n1<2 or n2<2:raise EffectSizeError('INSUFFICIENT_SAMPLE','At least 2 observations per group are required.')
        pooled=math.sqrt(((n1-1)*a.var(ddof=1)+(n2-1)*b.var(ddof=1))/(n1+n2-2))
        if pooled==0:raise EffectSizeError('ZERO_VARIANCE','Pooled standard deviation is zero.')
        value=float((a.mean()-b.mean())/pooled)
        return {'effect_type':effect_type,'value':value,'magnitude':_magnitude(value,effect_type),'n_a':n1,'n_b':n2,'mean_a':float(a.mean()),'mean_b':float(b.mean()),'pooled_std':float(pooled)}
    if effect_type=='hedges_g':
        base=calculate_effect_size(df,effect_type='cohens_d',column=column,column_b=column_b);dfree=base['n_a']+base['n_b']-2
        correction=1-(3/(4*dfree-1)) if dfree>1 else 1.0;value=base['value']*correction
        return {'effect_type':effect_type,'value':value,'magnitude':_magnitude(value,effect_type),'correction_factor':correction,'degrees_of_freedom':dfree,'cohens_d':base['value'],'n_a':base['n_a'],'n_b':base['n_b']}
    if effect_type=='glass_delta':
        a=df[column].dropna().astype(float);b=df[column_b].dropna().astype(float);n1,n2=len(a),len(b)
        if n1<1 or n2<2:raise EffectSizeError('INSUFFICIENT_SAMPLE','Glass delta requires observations and at least 2 control observations.')
        sd=float(b.std(ddof=1))
        if sd==0:raise EffectSizeError('ZERO_VARIANCE','Control standard deviation is zero.')
        value=float((a.mean()-b.mean())/sd)
        return {'effect_type':effect_type,'value':value,'magnitude':_magnitude(value,effect_type),'control':'column_b','control_std':sd,'n_a':n1,'n_b':n2}
    if effect_type=='paired_dz':
        pair=df[[column,column_b]].dropna();n=len(pair)
        if n<2:raise EffectSizeError('INSUFFICIENT_SAMPLE','At least 2 complete pairs are required.')
        d=pair[column].astype(float)-pair[column_b].astype(float);sd=float(d.std(ddof=1))
        if sd==0:raise EffectSizeError('ZERO_VARIANCE','Paired differences have zero variance.')
        value=float(d.mean()/sd)
        return {'effect_type':effect_type,'value':value,'magnitude':_magnitude(value,effect_type),'n_pairs':n,'mean_difference':float(d.mean()),'sd_difference':sd}
    if effect_type=='cohens_h':
        if column not in df.columns or column_b not in df.columns:raise EffectSizeError('UNKNOWN_COLUMN','One or both selected columns do not exist.')
        a=df[column].dropna();b=df[column_b].dropna()
        if not len(a) or not len(b):raise EffectSizeError('INSUFFICIENT_SAMPLE','Both proportion samples require observations.')
        p1=float((a==success_value).mean());p2=float((b==success_value).mean())
        value=2*math.asin(math.sqrt(p1))-2*math.asin(math.sqrt(p2))
        return {'effect_type':effect_type,'value':value,'magnitude':_magnitude(value,effect_type),'proportion_a':p1,'proportion_b':p2,'n_a':len(a),'n_b':len(b),'success_value':success_value}
    if effect_type=='cramers_v':
        if column not in df.columns or column_b not in df.columns:raise EffectSizeError('UNKNOWN_COLUMN','One or both selected columns do not exist.')
        pair=df[[column,column_b]].dropna();n=len(pair)
        if n<2:raise EffectSizeError('INSUFFICIENT_SAMPLE','At least 2 complete observations are required.')
        table=pd.crosstab(pair[column],pair[column_b])
        if table.shape[0]<2 or table.shape[1]<2:raise EffectSizeError('INSUFFICIENT_CATEGORIES','Both variables must contain at least two observed categories.')
        chi2,p,dfree,expected=chi2_contingency(table,correction=False);denom=min(table.shape[0]-1,table.shape[1]-1)
        value=math.sqrt((chi2/n)/denom)
        return {'effect_type':effect_type,'value':value,'magnitude':_magnitude(value,effect_type),'chi_square':float(chi2),'p_value':float(p),'degrees_of_freedom':int(dfree),'n':n,'table_shape':list(table.shape)}
    raise EffectSizeError('INVALID_EFFECT_TYPE','Unsupported effect size type.',{'effect_type':effect_type})
