from __future__ import annotations
import math
import pandas as pd
from pandas.api.types import is_numeric_dtype
from scipy import stats
class OneSampleTTestError(Exception):
 def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
def _safe(v):
 if v is None or pd.isna(v):return None
 x=float(v);return None if math.isnan(x) or math.isinf(x) else x
def one_sample_t_test(df:pd.DataFrame,*,column:str,population_mean:float=0.0,alternative:str='two-sided',alpha:float=.05,confidence_level:float=.95):
 if not isinstance(df,pd.DataFrame):raise OneSampleTTestError('INVALID_DATAFRAME','Input must be a pandas DataFrame.')
 if column not in df.columns:raise OneSampleTTestError('UNKNOWN_COLUMN','Selected column does not exist.',{'column':column})
 if not is_numeric_dtype(df[column]):raise OneSampleTTestError('INCOMPATIBLE_COLUMN_TYPE','One-sample t-test requires a numeric column.')
 if alternative not in {'two-sided','less','greater'}:raise OneSampleTTestError('INVALID_ALTERNATIVE','alternative must be two-sided, less, or greater.')
 if not 0<alpha<1:raise OneSampleTTestError('INVALID_ALPHA','alpha must be between 0 and 1.')
 if not 0<confidence_level<1:raise OneSampleTTestError('INVALID_CONFIDENCE_LEVEL','confidence_level must be between 0 and 1.')
 s=df[column].dropna().astype(float);n=len(s)
 if n<2:raise OneSampleTTestError('INSUFFICIENT_SAMPLE','At least two non-null observations are required.')
 sd=float(s.std(ddof=1))
 if sd==0:raise OneSampleTTestError('ZERO_VARIANCE','Sample standard deviation is zero; t-test is undefined.')
 mean=float(s.mean());diff=mean-float(population_mean);se=sd/math.sqrt(n);t=diff/se;dfree=n-1
 cdf=float(stats.t.cdf(t,dfree))
 if alternative=='two-sided':p=2*min(cdf,1-cdf)
 elif alternative=='less':p=cdf
 else:p=1-cdf
 conf_alpha=1-confidence_level
 if alternative=='two-sided':
  crit=float(stats.t.ppf(1-conf_alpha/2,dfree));lo=diff-crit*se;hi=diff+crit*se
 elif alternative=='greater':
  crit=float(stats.t.ppf(confidence_level,dfree));lo=diff-crit*se;hi=None
 else:
  crit=float(stats.t.ppf(confidence_level,dfree));lo=None;hi=diff+crit*se
 d=diff/sd
 return {'test':'one_sample_t','column':column,'population_mean':float(population_mean),'alternative':alternative,'alpha':alpha,'confidence_level':confidence_level,'n':n,'null_count':int(df[column].isna().sum()),'sample_mean':mean,'sample_std':sd,'mean_difference':diff,'standard_error':se,'t_statistic':t,'degrees_of_freedom':dfree,'p_value':max(0.0,min(1.0,p)),'reject_null':bool(p<alpha),'decision':'reject_null' if p<alpha else 'fail_to_reject_null','confidence_interval_mean_difference':{'lower':_safe(lo),'upper':_safe(hi)},'confidence_interval_mean':{'lower':None if lo is None else float(population_mean)+lo,'upper':None if hi is None else float(population_mean)+hi},'cohens_d':d,'effect_magnitude':'negligible' if abs(d)<.2 else 'small' if abs(d)<.5 else 'medium' if abs(d)<.8 else 'large'}
