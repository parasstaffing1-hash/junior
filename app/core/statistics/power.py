from __future__ import annotations
import math
from scipy import stats

class PowerError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

def _za(alpha,alternative):
    return float(stats.norm.ppf(1-alpha/(2 if alternative=="two-sided" else 1)))

def _validate(alpha,power,alternative):
    if not 0<alpha<1:raise PowerError("INVALID_ALPHA","alpha must be between 0 and 1.")
    if not 0<power<1:raise PowerError("INVALID_POWER","power must be between 0 and 1.")
    if alternative not in {"two-sided","one-sided"}:raise PowerError("INVALID_ALTERNATIVE","alternative must be two-sided or one-sided.")

def _approx_power(effect,info,alpha,alternative):
    za=_za(alpha,alternative);delta=abs(effect)*math.sqrt(info)
    if alternative=="two-sided":
        return float(stats.norm.cdf(-za-delta)+1-stats.norm.cdf(za-delta))
    return float(1-stats.norm.cdf(za-delta))

def calculate_power(*,design,mode="sample_size",effect_size,alpha=.05,power=.8,n=None,n2=None,allocation_ratio=1.0,groups=3,alternative="two-sided"):
    _validate(alpha,power,alternative)
    if mode not in {"sample_size","power"}:raise PowerError("INVALID_MODE","mode must be sample_size or power.")
    if effect_size<=0:raise PowerError("INVALID_EFFECT_SIZE","effect_size must be > 0.")
    if allocation_ratio<=0:raise PowerError("INVALID_ALLOCATION_RATIO","allocation_ratio must be > 0.")
    if groups<2:raise PowerError("INVALID_GROUPS","groups must be >=2.")

    za=_za(alpha,alternative)
    zb=float(stats.norm.ppf(power))

    if design in {"one_sample_mean","paired_means","one_proportion"}:
        if mode=="sample_size":
            req=max(2,math.ceil(((za+zb)/effect_size)**2))
            return {"design":design,"mode":mode,"required_n":req,"effect_size":effect_size,"alpha":alpha,"target_power":power,"alternative":alternative}
        if not n or n<2:raise PowerError("N_REQUIRED","n must be >=2.")
        return {"design":design,"mode":mode,"n":n,"achieved_power":_approx_power(effect_size,n,alpha,alternative),"effect_size":effect_size,"alpha":alpha,"alternative":alternative}

    if design in {"two_independent_means","two_proportions"}:
        if mode=="sample_size":
            n1=max(2,math.ceil(((za+zb)**2*(1+1/allocation_ratio))/(effect_size**2)))
            n_b=max(2,math.ceil(n1*allocation_ratio))
            return {"design":design,"mode":mode,"required_n_a":n1,"required_n_b":n_b,"required_total_n":n1+n_b,"allocation_ratio":allocation_ratio,"effect_size":effect_size,"alpha":alpha,"target_power":power,"alternative":alternative}
        if not n or not n2 or n<2 or n2<2:raise PowerError("N_REQUIRED","n and n2 must both be >=2.")
        info=(n*n2)/(n+n2)
        return {"design":design,"mode":mode,"n_a":n,"n_b":n2,"achieved_power":_approx_power(effect_size,info,alpha,alternative),"effect_size":effect_size,"alpha":alpha,"alternative":alternative}

    if design=="correlation":
        if effect_size>=1:raise PowerError("INVALID_EFFECT_SIZE","correlation effect_size must be <1.")
        ze=math.atanh(effect_size)
        if mode=="sample_size":
            req=max(4,math.ceil(3+((za+zb)/ze)**2))
            return {"design":design,"mode":mode,"required_n":req,"correlation":effect_size,"alpha":alpha,"target_power":power,"alternative":alternative}
        if not n or n<4:raise PowerError("N_REQUIRED","n must be >=4.")
        return {"design":design,"mode":mode,"n":n,"achieved_power":_approx_power(ze,n-3,alpha,alternative),"correlation":effect_size,"alpha":alpha,"alternative":alternative}

    if design=="one_way_anova":
        if mode=="sample_size":
            total=max(groups*2,math.ceil(((za+zb)/effect_size)**2+groups))
            per=math.ceil(total/groups);total=per*groups
            return {"design":design,"mode":mode,"groups":groups,"required_n_per_group":per,"required_total_n":total,"cohen_f":effect_size,"alpha":alpha,"target_power":power}
        if not n or n<groups*2:raise PowerError("N_REQUIRED","n must provide >=2 observations per group.")
        return {"design":design,"mode":mode,"groups":groups,"total_n":n,"achieved_power":_approx_power(effect_size,n-groups,alpha,"one-sided"),"cohen_f":effect_size,"alpha":alpha}

    raise PowerError("INVALID_DESIGN","Unsupported design.",{"design":design})
