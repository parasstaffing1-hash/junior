import math,pandas as pd
from pandas.api.types import is_numeric_dtype
class PercentileError(Exception):
 def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
def safe(v):
 if v is None or pd.isna(v):return None
 x=float(v);return None if math.isnan(x) or math.isinf(x) else x
def analyze_percentiles(df,columns=None,quantiles=None,interpolation='linear'):
 if not isinstance(df,pd.DataFrame):raise PercentileError('INVALID_DATAFRAME','Input must be a pandas DataFrame.')
 if interpolation not in {'linear','lower','higher','midpoint','nearest'}:raise PercentileError('INVALID_INTERPOLATION','Unsupported interpolation mode.')
 columns=[c for c in df.columns if is_numeric_dtype(df[c])] if columns is None else columns
 if not columns:raise PercentileError('NO_NUMERIC_COLUMNS','No numeric columns selected or detected.')
 u=[c for c in columns if c not in df.columns]
 if u:raise PercentileError('UNKNOWN_COLUMN','Unknown columns.',{'columns':u})
 n=[c for c in columns if not is_numeric_dtype(df[c])]
 if n:raise PercentileError('INCOMPATIBLE_COLUMN_TYPE','Numeric columns required.',{'columns':n})
 quantiles=[0,0.25,0.5,0.75,1] if quantiles is None else quantiles
 if not quantiles:raise PercentileError('QUANTILES_REQUIRED','At least one quantile is required.')
 if any((not isinstance(q,(int,float))) or q<0 or q>1 for q in quantiles):raise PercentileError('INVALID_QUANTILE','Quantiles must be numeric values between 0 and 1.')
 if len(quantiles)!=len(set(float(q) for q in quantiles)):raise PercentileError('DUPLICATE_QUANTILE','Quantiles must be unique.')
 qs=[float(q) for q in quantiles]
 out=[]
 for c in columns:
  s=df[c].dropna()
  vals={str(q):safe(s.quantile(q,interpolation=interpolation)) if len(s) else None for q in qs}
  q1=safe(s.quantile(.25,interpolation=interpolation)) if len(s) else None;q2=safe(s.quantile(.5,interpolation=interpolation)) if len(s) else None;q3=safe(s.quantile(.75,interpolation=interpolation)) if len(s) else None
  iqr=None if q1 is None or q3 is None else q3-q1
  out.append({'column':c,'count':int(len(s)),'null_count':int(df[c].isna().sum()),'quantiles':vals,'q1':q1,'median':q2,'q3':q3,'iqr':iqr,'lower_fence':None if iqr is None else q1-1.5*iqr,'upper_fence':None if iqr is None else q3+1.5*iqr,'p10':safe(s.quantile(.1,interpolation=interpolation)) if len(s) else None,'p90':safe(s.quantile(.9,interpolation=interpolation)) if len(s) else None,'p90_p10_spread':None if not len(s) else safe(s.quantile(.9,interpolation=interpolation)-s.quantile(.1,interpolation=interpolation))})
 return {'row_count':len(df),'analyzed_columns':columns,'quantiles_requested':qs,'interpolation':interpolation,'percentiles':out}
