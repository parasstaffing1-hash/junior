from __future__ import annotations

class SelectorError(Exception):
    def __init__(self,code,message,details=None):
        self.code=code;self.message=message;self.details=details or {};super().__init__(message)

TOOLS={
 "one_sample_t":51,"independent_t_welch":52,"independent_t_student":52,"paired_t":53,
 "chi_square":54,"fisher_exact":55,"anova":56,"welch_anova":56,
 "mann_whitney_u":57,"wilcoxon_signed_rank":57,"kruskal_wallis":57,"friedman":57,"sign_test":57,
 "pearson_correlation":43,"spearman_correlation":43
}

def select_statistical_test(*,question_type:str,outcome_type:str="numeric",predictor_type:str|None=None,groups:int|None=None,paired:bool=False,repeated:bool=False,normality:str="unknown",equal_variance:str="unknown",small_expected_counts:bool=False,sample_size:int|None=None):
    valid_q={"compare_one_sample","compare_two_groups","compare_three_plus_groups","association","correlation"}
    if question_type not in valid_q:raise SelectorError("INVALID_QUESTION_TYPE","Unsupported question_type.")
    if outcome_type not in {"numeric","categorical"}:raise SelectorError("INVALID_OUTCOME_TYPE","outcome_type must be numeric or categorical.")
    if normality not in {"supported","violated","unknown"}:raise SelectorError("INVALID_NORMALITY","normality must be supported, violated, or unknown.")
    if equal_variance not in {"supported","violated","unknown"}:raise SelectorError("INVALID_EQUAL_VARIANCE","equal_variance must be supported, violated, or unknown.")

    reasoning=[];warnings=[];alt=None

    if question_type=="compare_one_sample":
        if outcome_type!="numeric":raise SelectorError("UNSUPPORTED_STRUCTURE","One-sample selector currently expects numeric outcome.")
        if normality=="violated":
            primary="sign_test";alt="one_sample_t";reasoning.append("Normality evidence is violated, so a distribution-free one-sample sign test is preferred.")
        else:
            primary="one_sample_t";alt="sign_test";reasoning.append("A numeric sample is being compared with a reference value.")
            if normality=="unknown" and (sample_size or 0)<30:warnings.append("Normality is unknown with a small sample; inspect Tool 46 before relying on the t-test.")

    elif question_type=="compare_two_groups":
        if outcome_type!="numeric":raise SelectorError("UNSUPPORTED_STRUCTURE","Two-group mean/rank comparison expects numeric outcome.")
        if paired:
            if normality=="violated":
                primary="wilcoxon_signed_rank";alt="paired_t";reasoning.append("Samples are paired and normality of differences is violated.")
            else:
                primary="paired_t";alt="wilcoxon_signed_rank";reasoning.append("Samples are paired/repeated for two conditions.")
        else:
            if normality=="violated":
                primary="mann_whitney_u";alt="independent_t_welch";reasoning.append("Independent groups with non-normal outcome favor Mann-Whitney U.")
            elif equal_variance=="supported":
                primary="independent_t_student";alt="independent_t_welch";reasoning.append("Independent numeric groups with supported equal variance can use pooled Student t-test.")
            else:
                primary="independent_t_welch";alt="mann_whitney_u";reasoning.append("Welch t-test is robust to unequal or unknown variances.")

    elif question_type=="compare_three_plus_groups":
        if outcome_type!="numeric":raise SelectorError("UNSUPPORTED_STRUCTURE","3+ group comparison expects numeric outcome.")
        if groups is not None and groups<3:raise SelectorError("INVALID_GROUP_COUNT","groups must be at least 3.")
        if repeated or paired:
            primary="friedman";alt=None;reasoning.append("Three or more repeated/paired conditions require a repeated-measures non-parametric test in the current roadmap.")
        elif normality=="violated":
            primary="kruskal_wallis";alt="welch_anova";reasoning.append("Independent 3+ groups with violated normality favor Kruskal-Wallis.")
        elif equal_variance=="violated":
            primary="welch_anova";alt="kruskal_wallis";reasoning.append("Normal numeric groups with unequal variance favor Welch ANOVA.")
        else:
            primary="anova";alt="kruskal_wallis";reasoning.append("Independent 3+ numeric groups with acceptable assumptions favor one-way ANOVA.")

    elif question_type=="association":
        if outcome_type!="categorical" or predictor_type!="categorical":
            raise SelectorError("UNSUPPORTED_STRUCTURE","Association selector expects two categorical variables.")
        if small_expected_counts:
            primary="fisher_exact";alt="chi_square";reasoning.append("Small expected cell counts favor Fisher exact for a 2x2 table.")
            warnings.append("Fisher exact in Tool 55 is limited to 2x2 tables.")
        else:
            primary="chi_square";alt="fisher_exact";reasoning.append("Categorical association with adequate expected counts favors chi-square.")

    else: # correlation
        if outcome_type!="numeric" or predictor_type!="numeric":
            raise SelectorError("UNSUPPORTED_STRUCTURE","Correlation selector expects two numeric variables.")
        if normality=="violated":
            primary="spearman_correlation";alt="pearson_correlation";reasoning.append("Non-normal numeric variables favor rank-based Spearman correlation.")
        else:
            primary="pearson_correlation";alt="spearman_correlation";reasoning.append("Approximately normal numeric variables favor Pearson correlation.")

    return {
        "primary_test":primary,"primary_tool_number":TOOLS[primary],
        "alternative_test":alt,"alternative_tool_number":TOOLS.get(alt) if alt else None,
        "reasoning":reasoning,"warnings":warnings,
        "assumptions":{"question_type":question_type,"outcome_type":outcome_type,"predictor_type":predictor_type,"groups":groups,"paired":paired,"repeated":repeated,"normality":normality,"equal_variance":equal_variance,"small_expected_counts":small_expected_counts,"sample_size":sample_size},
        "api_hint":_api_hint(primary)
    }

def _api_hint(test):
    if test=="one_sample_t":return "/statistics/one-sample-t-test/analyze"
    if test.startswith("independent_t"):return "/statistics/independent-t-test/analyze"
    if test=="paired_t":return "/statistics/paired-t-test/analyze"
    if test=="chi_square":return "/statistics/chi-square/analyze"
    if test=="fisher_exact":return "/statistics/fisher-exact/analyze"
    if test in {"anova","welch_anova"}:return "/statistics/anova/analyze"
    if test in {"mann_whitney_u","wilcoxon_signed_rank","kruskal_wallis","friedman","sign_test"}:return "/statistics/non-parametric/analyze"
    if test in {"pearson_correlation","spearman_correlation"}:return "/statistics/correlation/analyze"
    return None
