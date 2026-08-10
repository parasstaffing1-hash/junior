import math,pandas as pd
from pandas.api.types import is_numeric_dtype
class DescriptiveStatsError(Exception):
 def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
def safe(v):
 if v is None or pd.isna(v):return None
 try:
  x=float(v);return None if math.isnan(x) or math.isinf(x) else x
 except:return v
def analyze_descriptive_statistics(df,columns=None,ddof=1):
 if not isinstance(df,pd.DataFrame):raise DescriptiveStatsError('INVALID_DATAFRAME','Input must be a pandas DataFrame.')
 if ddof not in (0,1):raise DescriptiveStatsError('INVALID_DDOF','ddof must be 0 or 1.')
 columns=[c for c in df.columns if is_numeric_dtype(df[c])] if columns is None else columns
 if not columns:raise DescriptiveStatsError('NO_NUMERIC_COLUMNS','No numeric columns selected or detected.')
 u=[c for c in columns if c not in df.columns]
 if u:raise DescriptiveStatsError('UNKNOWN_COLUMN','Unknown columns.',{'columns':u})
 n=[c for c in columns if not is_numeric_dtype(df[c])]
 if n:raise DescriptiveStatsError('INCOMPATIBLE_COLUMN_TYPE','Numeric columns required.',{'columns':n})
 stats=[]
 for c in columns:
  s=df[c];v=s.dropna();count=len(v);nulls=int(s.isna().sum());modes=v.mode();mean=safe(v.mean()) if count else None;std=safe(v.std(ddof=ddof)) if count>ddof else None
  stats.append({'column':c,'dtype':str(s.dtype),'count':count,'null_count':nulls,'null_rate_pct':round(nulls/len(s)*100,6) if len(s) else 0.0,'unique_count':int(v.nunique()),'mean':mean,'median':safe(v.median()) if count else None,'mode':safe(modes.iloc[0]) if len(modes) else None,'min':safe(v.min()) if count else None,'max':safe(v.max()) if count else None,'range':safe(v.max()-v.min()) if count else None,'sum':safe(v.sum()) if count else None,'variance':safe(v.var(ddof=ddof)) if count>ddof else None,'std_dev':std,'coefficient_of_variation':None if mean in (None,0) or std is None else safe(std/mean),'skewness':safe(v.skew()) if count>=3 else None,'kurtosis':safe(v.kurt()) if count>=4 else None})
 return {'row_count':len(df),'column_count':len(df.columns),'analyzed_columns':columns,'ddof':ddof,'statistics':stats}
