import math,pandas as pd
from pandas.api.types import is_numeric_dtype
class CorrelationError(Exception):
 def __init__(self,code,message,details=None):self.code=code;self.message=message;self.details=details or {};super().__init__(message)
def safe(v):
 if v is None or pd.isna(v):return None
 x=float(v);return None if math.isnan(x) or math.isinf(x) else x
def analyze_correlations(df,columns=None,method='pearson',min_periods=2,top_pairs=20):
 if not isinstance(df,pd.DataFrame):raise CorrelationError('INVALID_DATAFRAME','Input must be a pandas DataFrame.')
 if method not in {'pearson','spearman','kendall'}:raise CorrelationError('INVALID_METHOD','method must be pearson, spearman, or kendall.')
 if not isinstance(min_periods,int) or min_periods<2:raise CorrelationError('INVALID_MIN_PERIODS','min_periods must be an integer >= 2.')
 if not isinstance(top_pairs,int) or top_pairs<1:raise CorrelationError('INVALID_TOP_PAIRS','top_pairs must be >= 1.')
 columns=[c for c in df.columns if is_numeric_dtype(df[c])] if columns is None else columns
 if len(columns)<2:raise CorrelationError('INSUFFICIENT_NUMERIC_COLUMNS','At least two numeric columns are required.')
 u=[c for c in columns if c not in df.columns]
 if u:raise CorrelationError('UNKNOWN_COLUMN','Unknown columns.',{'columns':u})
 n=[c for c in columns if not is_numeric_dtype(df[c])]
 if n:raise CorrelationError('INCOMPATIBLE_COLUMN_TYPE','Correlation requires numeric columns.',{'columns':n})
 sub=df[columns]
 corr=sub.corr(method=method,min_periods=min_periods)
 matrix={a:{b:safe(corr.loc[a,b]) for b in columns} for a in columns}
 sample_sizes={};pairs=[];warnings=[]
 constants=[c for c in columns if sub[c].dropna().nunique()<=1]
 for c in constants:warnings.append({'code':'CONSTANT_COLUMN','column':c,'message':'Constant columns have undefined correlations.'})
 for i,a in enumerate(columns):
  sample_sizes[a]={}
  for b in columns:
   sample_sizes[a][b]=int(sub[[a,b]].dropna().shape[0])
  for b in columns[i+1:]:
   val=matrix[a][b];nobs=sample_sizes[a][b]
   pairs.append({'column_a':a,'column_b':b,'correlation':val,'absolute_correlation':None if val is None else abs(val),'pairwise_count':nobs})
 ranked=sorted([p for p in pairs if p['correlation'] is not None],key=lambda p:(-p['absolute_correlation'],p['column_a'],p['column_b']))[:top_pairs]
 positives=sorted([p for p in pairs if p['correlation'] is not None and p['correlation']>0],key=lambda p:-p['correlation'])[:top_pairs]
 negatives=sorted([p for p in pairs if p['correlation'] is not None and p['correlation']<0],key=lambda p:p['correlation'])[:top_pairs]
 return {'row_count':len(df),'columns':columns,'method':method,'min_periods':min_periods,'matrix':matrix,'pairwise_sample_sizes':sample_sizes,'pairs':pairs,'strongest_pairs':ranked,'strongest_positive_pairs':positives,'strongest_negative_pairs':negatives,'constant_columns':constants,'warnings':warnings}
