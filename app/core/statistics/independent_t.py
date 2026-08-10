from __future__ import annotations
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats
class IndependentTTestError(Exception):
 def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
def _safe(v):
 if v is None or pd.isna(v):return None
 x=float(v);return None if math.isnan(x) or math.isinf(x) else x
def _samples(df,sample_mode,column_a,column_b,value_column,group_column,group_a,group_b):
 if sample_mode=='columns':
  if not column_a or not column_b or column_a not in df.columns or column_b not in df.columns:raise IndependentTTestError('UNKNOWN_COLUMN','Both sample columns must exist.',{'column_a':column_a,'column_b':column_b})
  if not is_numeric_dtype(df[column_a]) or not is_numeric_dtype(df[column_b]):raise IndependentTTestError('INCOMPATIBLE_COLUMN_TYPE','Both sample columns must be numeric.')
  return df[column_a].dropna().astype(float),df[column_b].dropna().astype(float),str(column_a),str(column_b)
 if sample_mode=='grouped':
  if not value_column or not group_column or value_column not in df.columns or group_column not in df.columns:raise IndependentTTestError('UNKNOWN_COLUMN','value_column and group_column must exist.')
  if not is_numeric_dtype(df[value_column]):raise IndependentTTestError('INCOMPATIBLE_COLUMN_TYPE','value_column must be numeric.')
  if group_a==group_b:raise IndependentTTestError('GROUPS_MUST_DIFFER','group_a and group_b must be different.')
  observed=set(df[group_column].dropna().tolist())
  if group_a not in observed or group_b not in observed:raise IndependentTTestError('GROUP_NOT_FOUND','One or both selected groups were not observed.',{'group_a':group_a,'group_b':group_b})
  return df.loc[df[group_column]==group_a,value_column].dropna().astype(float),df.loc[df[group_column]==group_b,value_column].dropna().astype(float),str(group_a),str(group_b)
 raise IndependentTTestError('INVALID_SAMPLE_MODE','sample_mode must be columns or grouped.')
def independent_t_test(df:pd.DataFrame,*,sample_mode='columns',column_a=None,column_b=None,value_column=None,group_column=None,group_a=None,group_b=None,equal_var=False,alternative='two-sided',alpha=.05,confidence_level=.95):
 if not isinstance(df,pd.DataFrame):raise IndependentTTestError('INVALID_DATAFRAME','Input must be a pandas DataFrame.')
 if alternative not in {'two-sided','less','greater'}:raise IndependentTTestError('INVALID_ALTERNATIVE','alternative must be two-sided, less, or greater.')
 if not 0<alpha<1:raise IndependentTTestError('INVALID_ALPHA','alpha must be between 0 and 1.')
 if not 0<confidence_level<1:raise IndependentTTestError('INVALID_CONFIDENCE_LEVEL','confidence_level must be between 0 and 1.')
 a,b,label_a,label_b=_samples(df,sample_mode,column_a,column_b,value_column,group_column,group_a,group_b);n1,n2=len(a),len(b)
 if n1<2 or n2<2:raise IndependentTTestError('INSUFFICIENT_SAMPLE','At least two observations are required in each independent sample.')
 m1,m2=float(a.mean()),float(b.mean());v1,v2=float(a.var(ddof=1)),float(b.var(ddof=1));sd1,sd2=math.sqrt(v1),math.sqrt(v2)
 if v1==0 and v2==0:raise IndependentTTestError('ZERO_VARIANCE','Both samples have zero variance; t-test is undefined.')
 if equal_var:
  dfree=n1+n2-2;pooled_var=((n1-1)*v1+(n2-1)*v2)/dfree;se=math.sqrt(pooled_var*(1/n1+1/n2));method='student_pooled'
 else:
  se=math.sqrt(v1/n1+v2/n2);denom=((v1/n1)**2/(n1-1))+((v2/n2)**2/(n2-1));dfree=((v1/n1+v2/n2)**2/denom) if denom else float('inf');pooled_var=((n1-1)*v1+(n2-1)*v2)/(n1+n2-2);method='welch'
 if se==0:raise IndependentTTestError('ZERO_STANDARD_ERROR','Standard error is zero; t-test is undefined.')
 diff=m1-m2;t=diff/se;cdf=float(stats.t.cdf(t,dfree)) if math.isfinite(dfree) else float(stats.norm.cdf(t))
 if alternative=='two-sided':p=2*min(cdf,1-cdf)
 elif alternative=='less':p=cdf
 else:p=1-cdf
 ca=1-confidence_level
 if alternative=='two-sided':crit=float(stats.t.ppf(1-ca/2,dfree)) if math.isfinite(dfree) else float(stats.norm.ppf(1-ca/2));lo=diff-crit*se;hi=diff+crit*se
 elif alternative=='greater':crit=float(stats.t.ppf(confidence_level,dfree)) if math.isfinite(dfree) else float(stats.norm.ppf(confidence_level));lo=diff-crit*se;hi=None
 else:crit=float(stats.t.ppf(confidence_level,dfree)) if math.isfinite(dfree) else float(stats.norm.ppf(confidence_level));lo=None;hi=diff+crit*se
 lev_stat,lev_p=stats.levene(a.to_numpy(),b.to_numpy(),center='median')
 pooled_sd=math.sqrt(pooled_var);d=diff/pooled_sd if pooled_sd else None;correction=1-(3/(4*(n1+n2)-9)) if n1+n2>2 else 1.0;g=None if d is None else d*correction
 mag='not_available' if d is None else 'negligible' if abs(d)<.2 else 'small' if abs(d)<.5 else 'medium' if abs(d)<.8 else 'large'
 return {'test':'independent_t','method':method,'sample_mode':sample_mode,'sample_a_label':label_a,'sample_b_label':label_b,'equal_var':bool(equal_var),'alternative':alternative,'alpha':alpha,'confidence_level':confidence_level,'n_a':n1,'n_b':n2,'mean_a':m1,'mean_b':m2,'std_a':sd1,'std_b':sd2,'mean_difference':diff,'standard_error':se,'t_statistic':t,'degrees_of_freedom':_safe(dfree),'p_value':max(0.0,min(1.0,p)),'reject_null':bool(p<alpha),'decision':'reject_null' if p<alpha else 'fail_to_reject_null','confidence_interval_mean_difference':{'lower':_safe(lo),'upper':_safe(hi)},'levene_test':{'statistic':_safe(lev_stat),'p_value':_safe(lev_p),'reject_equal_variances':bool(lev_p<alpha)},'cohens_d':_safe(d),'hedges_g':_safe(g),'effect_magnitude':mag}
