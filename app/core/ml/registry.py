from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.cross_decomposition import CCA, PLSCanonical, PLSRegression
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis, QuadraticDiscriminantAnalysis
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import (
    AdaBoostClassifier,
    AdaBoostRegressor,
    BaggingClassifier,
    BaggingRegressor,
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
    StackingClassifier,
    StackingRegressor,
    VotingClassifier,
    VotingRegressor,
)
from sklearn.gaussian_process import GaussianProcessClassifier, GaussianProcessRegressor
from sklearn.kernel_ridge import KernelRidge
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import (
    ARDRegression,
    BayesianRidge,
    ElasticNet,
    GammaRegressor,
    HuberRegressor,
    Lars,
    Lasso,
    LassoLars,
    LinearRegression,
    LogisticRegression,
    LogisticRegressionCV,
    MultiTaskElasticNet,
    MultiTaskLasso,
    OrthogonalMatchingPursuit,
    PassiveAggressiveClassifier,
    PassiveAggressiveRegressor,
    Perceptron,
    PoissonRegressor,
    QuantileRegressor,
    RANSACRegressor,
    Ridge,
    RidgeClassifier,
    RidgeClassifierCV,
    SGDClassifier,
    SGDRegressor,
    TheilSenRegressor,
    TweedieRegressor,
)
from sklearn.naive_bayes import BernoulliNB, CategoricalNB, ComplementNB, GaussianNB, MultinomialNB
from sklearn.neighbors import (
    KNeighborsClassifier,
    KNeighborsRegressor,
    NearestCentroid,
    RadiusNeighborsClassifier,
    RadiusNeighborsRegressor,
)
from sklearn.svm import LinearSVC, LinearSVR, NuSVC, NuSVR, SVC, SVR
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
from sklearn.compose import TransformedTargetRegressor
from sklearn.preprocessing import StandardScaler

from app.core.intelligence.common import IntelligenceError


@dataclass(frozen=True)
class ModelSpec:
    name: str
    task_type: str
    factory: Callable[[int, dict[str, Any]], BaseEstimator]
    non_negative_features: bool = False
    multioutput_target: bool = False
    maximum_rows: int | None = None
    description: str = ""


class FirstFeatureIsotonicRegressor(RegressorMixin, BaseEstimator):
    """Pipeline-compatible one-dimensional isotonic regression adapter."""

    def __init__(self, increasing: bool | str = "auto", out_of_bounds: str = "clip"):
        self.increasing = increasing
        self.out_of_bounds = out_of_bounds

    def fit(self, X, y):
        matrix = np.asarray(X)
        if matrix.ndim != 2 or matrix.shape[1] != 1:
            raise ValueError("Isotonic regression requires exactly one transformed feature.")
        self.model_ = IsotonicRegression(increasing=self.increasing, out_of_bounds=self.out_of_bounds)
        self.model_.fit(matrix[:, 0], np.asarray(y).ravel())
        return self

    def predict(self, X):
        matrix = np.asarray(X)
        return self.model_.predict(matrix[:, 0])


def _merge(defaults: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(supplied) - set(defaults))
    if unknown:
        raise IntelligenceError("UNKNOWN_MODEL_PARAMETER", "One or more model parameters are not allowed.", {"parameters": unknown})
    return {**defaults, **supplied}


def _factory(cls: type[BaseEstimator], defaults: dict[str, Any]) -> Callable[[int, dict[str, Any]], BaseEstimator]:
    def build(random_state: int, supplied: dict[str, Any]) -> BaseEstimator:
        values = {key: (random_state if value == "__RANDOM_STATE__" else value) for key, value in defaults.items()}
        return cls(**_merge(values, supplied))

    return build


def _voting_classifier(random_state: int, supplied: dict[str, Any]) -> BaseEstimator:
    if supplied:
        raise IntelligenceError("UNKNOWN_MODEL_PARAMETER", "Voting classifier uses the governed default members.")
    return VotingClassifier(
        estimators=[
            ("logistic", LogisticRegression(max_iter=1000, random_state=random_state)),
            ("forest", RandomForestClassifier(n_estimators=100, max_depth=8, random_state=random_state, n_jobs=1)),
            ("boost", GradientBoostingClassifier(random_state=random_state)),
        ],
        voting="soft",
        n_jobs=1,
    )


def _stacking_classifier(random_state: int, supplied: dict[str, Any]) -> BaseEstimator:
    if supplied:
        raise IntelligenceError("UNKNOWN_MODEL_PARAMETER", "Stacking classifier uses the governed default members.")
    return StackingClassifier(
        estimators=[
            ("logistic", LogisticRegression(max_iter=1000, random_state=random_state)),
            ("forest", RandomForestClassifier(n_estimators=80, max_depth=7, random_state=random_state, n_jobs=1)),
        ],
        final_estimator=LogisticRegression(max_iter=1000, random_state=random_state),
        cv=3,
        n_jobs=1,
    )


def _voting_regressor(random_state: int, supplied: dict[str, Any]) -> BaseEstimator:
    if supplied:
        raise IntelligenceError("UNKNOWN_MODEL_PARAMETER", "Voting regressor uses the governed default members.")
    return VotingRegressor(
        estimators=[
            ("ridge", Ridge(alpha=1.0, random_state=random_state)),
            ("forest", RandomForestRegressor(n_estimators=100, max_depth=8, random_state=random_state, n_jobs=1)),
            ("boost", GradientBoostingRegressor(random_state=random_state)),
        ],
        n_jobs=1,
    )


def _stacking_regressor(random_state: int, supplied: dict[str, Any]) -> BaseEstimator:
    if supplied:
        raise IntelligenceError("UNKNOWN_MODEL_PARAMETER", "Stacking regressor uses the governed default members.")
    return StackingRegressor(
        estimators=[
            ("ridge", Ridge(alpha=1.0, random_state=random_state)),
            ("forest", RandomForestRegressor(n_estimators=80, max_depth=7, random_state=random_state, n_jobs=1)),
        ],
        final_estimator=Ridge(alpha=1.0, random_state=random_state),
        cv=3,
        n_jobs=1,
    )


def _calibrated_linear_svc(random_state: int, supplied: dict[str, Any]) -> BaseEstimator:
    defaults = _merge({"C": 1.0}, supplied)
    return CalibratedClassifierCV(LinearSVC(C=defaults["C"], random_state=random_state), cv=3)


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "dummy_classifier": ModelSpec("dummy_classifier", "classification", _factory(DummyClassifier, {"strategy": "prior", "random_state": "__RANDOM_STATE__"})),
    "logistic_regression": ModelSpec("logistic_regression", "classification", _factory(LogisticRegression, {"C": 1.0, "max_iter": 1000, "class_weight": None, "random_state": "__RANDOM_STATE__"})),
    "logistic_regression_cv": ModelSpec("logistic_regression_cv", "classification", _factory(LogisticRegressionCV, {"Cs": 5, "cv": 3, "max_iter": 1000, "class_weight": None, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "ridge_classifier": ModelSpec("ridge_classifier", "classification", _factory(RidgeClassifier, {"alpha": 1.0, "class_weight": None, "random_state": "__RANDOM_STATE__"})),
    "ridge_classifier_cv": ModelSpec("ridge_classifier_cv", "classification", _factory(RidgeClassifierCV, {"alphas": (0.1, 1.0, 10.0), "class_weight": None})),
    "decision_tree_classifier": ModelSpec("decision_tree_classifier", "classification", _factory(DecisionTreeClassifier, {"max_depth": 8, "min_samples_leaf": 2, "class_weight": None, "random_state": "__RANDOM_STATE__"})),
    "random_forest_classifier": ModelSpec("random_forest_classifier", "classification", _factory(RandomForestClassifier, {"n_estimators": 150, "max_depth": 10, "min_samples_leaf": 2, "class_weight": None, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "extra_trees_classifier": ModelSpec("extra_trees_classifier", "classification", _factory(ExtraTreesClassifier, {"n_estimators": 150, "max_depth": 10, "min_samples_leaf": 2, "class_weight": None, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "gradient_boosting_classifier": ModelSpec("gradient_boosting_classifier", "classification", _factory(GradientBoostingClassifier, {"n_estimators": 100, "learning_rate": 0.05, "max_depth": 3, "random_state": "__RANDOM_STATE__"})),
    "hist_gradient_boosting_classifier": ModelSpec("hist_gradient_boosting_classifier", "classification", _factory(HistGradientBoostingClassifier, {"max_iter": 100, "learning_rate": 0.08, "max_depth": None, "random_state": "__RANDOM_STATE__"})),
    "adaboost_classifier": ModelSpec("adaboost_classifier", "classification", _factory(AdaBoostClassifier, {"n_estimators": 100, "learning_rate": 0.5, "random_state": "__RANDOM_STATE__"})),
    "bagging_classifier": ModelSpec("bagging_classifier", "classification", _factory(BaggingClassifier, {"n_estimators": 50, "max_samples": 0.8, "max_features": 1.0, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "voting_classifier": ModelSpec("voting_classifier", "classification", _voting_classifier, maximum_rows=20_000),
    "stacking_classifier": ModelSpec("stacking_classifier", "classification", _stacking_classifier, maximum_rows=10_000),
    "svc": ModelSpec("svc", "classification", _factory(SVC, {"C": 1.0, "kernel": "rbf", "gamma": "scale", "class_weight": None, "probability": True, "random_state": "__RANDOM_STATE__"}), maximum_rows=10_000),
    "linear_svc": ModelSpec("linear_svc", "classification", _calibrated_linear_svc),
    "nu_svc": ModelSpec("nu_svc", "classification", _factory(NuSVC, {"nu": 0.5, "kernel": "rbf", "gamma": "scale", "probability": True, "random_state": "__RANDOM_STATE__"}), maximum_rows=10_000),
    "knn_classifier": ModelSpec("knn_classifier", "classification", _factory(KNeighborsClassifier, {"n_neighbors": 5, "weights": "distance", "p": 2, "n_jobs": 1}), maximum_rows=20_000),
    "radius_neighbors_classifier": ModelSpec("radius_neighbors_classifier", "classification", _factory(RadiusNeighborsClassifier, {"radius": 1.0, "weights": "distance", "outlier_label": "most_frequent", "p": 2, "n_jobs": 1}), maximum_rows=20_000),
    "nearest_centroid": ModelSpec("nearest_centroid", "classification", _factory(NearestCentroid, {"metric": "euclidean", "shrink_threshold": None})),
    "linear_discriminant_analysis": ModelSpec("linear_discriminant_analysis", "classification", _factory(LinearDiscriminantAnalysis, {"solver": "svd"})),
    "quadratic_discriminant_analysis": ModelSpec("quadratic_discriminant_analysis", "classification", _factory(QuadraticDiscriminantAnalysis, {"reg_param": 0.01})),
    "perceptron": ModelSpec("perceptron", "classification", _factory(Perceptron, {"alpha": 0.0001, "max_iter": 1000, "class_weight": None, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "sgd_classifier": ModelSpec("sgd_classifier", "classification", _factory(SGDClassifier, {"loss": "log_loss", "alpha": 0.0001, "max_iter": 1000, "class_weight": None, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "passive_aggressive_classifier": ModelSpec("passive_aggressive_classifier", "classification", _factory(PassiveAggressiveClassifier, {"C": 1.0, "max_iter": 1000, "class_weight": None, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "gaussian_naive_bayes": ModelSpec("gaussian_naive_bayes", "classification", _factory(GaussianNB, {"var_smoothing": 1e-9})),
    "multinomial_naive_bayes": ModelSpec("multinomial_naive_bayes", "classification", _factory(MultinomialNB, {"alpha": 1.0, "fit_prior": True}), non_negative_features=True),
    "complement_naive_bayes": ModelSpec("complement_naive_bayes", "classification", _factory(ComplementNB, {"alpha": 1.0, "fit_prior": True}), non_negative_features=True),
    "bernoulli_naive_bayes": ModelSpec("bernoulli_naive_bayes", "classification", _factory(BernoulliNB, {"alpha": 1.0, "binarize": 0.0, "fit_prior": True}), non_negative_features=True),
    "categorical_naive_bayes": ModelSpec("categorical_naive_bayes", "classification", _factory(CategoricalNB, {"alpha": 1.0, "fit_prior": True}), non_negative_features=True),
    "gaussian_process_classifier": ModelSpec("gaussian_process_classifier", "classification", _factory(GaussianProcessClassifier, {"max_iter_predict": 100, "n_jobs": 1, "random_state": "__RANDOM_STATE__"}), maximum_rows=2_000),
    "dummy_regressor": ModelSpec("dummy_regressor", "regression", _factory(DummyRegressor, {"strategy": "mean"})),
    "linear_regression": ModelSpec("linear_regression", "regression", _factory(LinearRegression, {"fit_intercept": True, "positive": False, "n_jobs": 1})),
    "ridge_regression": ModelSpec("ridge_regression", "regression", _factory(Ridge, {"alpha": 1.0, "fit_intercept": True, "random_state": "__RANDOM_STATE__"})),
    "lasso_regression": ModelSpec("lasso_regression", "regression", _factory(Lasso, {"alpha": 0.01, "max_iter": 5000, "random_state": "__RANDOM_STATE__"})),
    "elastic_net_regression": ModelSpec("elastic_net_regression", "regression", _factory(ElasticNet, {"alpha": 0.01, "l1_ratio": 0.5, "max_iter": 5000, "random_state": "__RANDOM_STATE__"})),
    "bayesian_ridge": ModelSpec("bayesian_ridge", "regression", _factory(BayesianRidge, {"max_iter": 300})),
    "ard_regression": ModelSpec("ard_regression", "regression", _factory(ARDRegression, {"max_iter": 300})),
    "huber_regression": ModelSpec("huber_regression", "regression", _factory(HuberRegressor, {"epsilon": 1.35, "alpha": 0.0001, "max_iter": 300})),
    "ransac_regression": ModelSpec("ransac_regression", "regression", _factory(RANSACRegressor, {"min_samples": None, "max_trials": 100, "random_state": "__RANDOM_STATE__"})),
    "quantile_regression": ModelSpec("quantile_regression", "regression", _factory(QuantileRegressor, {"quantile": 0.5, "alpha": 0.0, "solver": "highs"}), maximum_rows=20_000),
    "sgd_regressor": ModelSpec("sgd_regressor", "regression", _factory(SGDRegressor, {"loss": "squared_error", "alpha": 0.0001, "max_iter": 1000, "random_state": "__RANDOM_STATE__"})),
    "passive_aggressive_regressor": ModelSpec("passive_aggressive_regressor", "regression", _factory(PassiveAggressiveRegressor, {"C": 1.0, "max_iter": 1000, "random_state": "__RANDOM_STATE__"})),
    "knn_regressor": ModelSpec("knn_regressor", "regression", _factory(KNeighborsRegressor, {"n_neighbors": 5, "weights": "distance", "p": 2, "n_jobs": 1}), maximum_rows=20_000),
    "radius_neighbors_regressor": ModelSpec("radius_neighbors_regressor", "regression", _factory(RadiusNeighborsRegressor, {"radius": 1.0, "weights": "distance", "p": 2, "n_jobs": 1}), maximum_rows=20_000),
    "decision_tree_regressor": ModelSpec("decision_tree_regressor", "regression", _factory(DecisionTreeRegressor, {"max_depth": 8, "min_samples_leaf": 2, "random_state": "__RANDOM_STATE__"})),
    "random_forest_regressor": ModelSpec("random_forest_regressor", "regression", _factory(RandomForestRegressor, {"n_estimators": 150, "max_depth": 10, "min_samples_leaf": 2, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "extra_trees_regressor": ModelSpec("extra_trees_regressor", "regression", _factory(ExtraTreesRegressor, {"n_estimators": 150, "max_depth": 10, "min_samples_leaf": 2, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "gradient_boosting_regressor": ModelSpec("gradient_boosting_regressor", "regression", _factory(GradientBoostingRegressor, {"n_estimators": 100, "learning_rate": 0.05, "max_depth": 3, "loss": "squared_error", "random_state": "__RANDOM_STATE__"})),
    "hist_gradient_boosting_regressor": ModelSpec("hist_gradient_boosting_regressor", "regression", _factory(HistGradientBoostingRegressor, {"max_iter": 100, "learning_rate": 0.08, "max_depth": None, "random_state": "__RANDOM_STATE__"})),
    "adaboost_regressor": ModelSpec("adaboost_regressor", "regression", _factory(AdaBoostRegressor, {"n_estimators": 100, "learning_rate": 0.5, "loss": "linear", "random_state": "__RANDOM_STATE__"})),
    "bagging_regressor": ModelSpec("bagging_regressor", "regression", _factory(BaggingRegressor, {"n_estimators": 50, "max_samples": 0.8, "max_features": 1.0, "n_jobs": 1, "random_state": "__RANDOM_STATE__"})),
    "voting_regressor": ModelSpec("voting_regressor", "regression", _voting_regressor, maximum_rows=20_000),
    "stacking_regressor": ModelSpec("stacking_regressor", "regression", _stacking_regressor, maximum_rows=10_000),
    "svr": ModelSpec("svr", "regression", _factory(SVR, {"C": 1.0, "epsilon": 0.1, "kernel": "rbf", "gamma": "scale"}), maximum_rows=10_000),
    "linear_svr": ModelSpec("linear_svr", "regression", _factory(LinearSVR, {"C": 1.0, "epsilon": 0.0, "max_iter": 2000, "random_state": "__RANDOM_STATE__"})),
    "nu_svr": ModelSpec("nu_svr", "regression", _factory(NuSVR, {"C": 1.0, "nu": 0.5, "kernel": "rbf", "gamma": "scale"}), maximum_rows=10_000),
    "gaussian_process_regressor": ModelSpec("gaussian_process_regressor", "regression", _factory(GaussianProcessRegressor, {"alpha": 1e-10, "normalize_y": True, "random_state": "__RANDOM_STATE__"}), maximum_rows=2_000),
    "kernel_ridge": ModelSpec("kernel_ridge", "regression", _factory(KernelRidge, {"alpha": 1.0, "kernel": "rbf", "gamma": None}), maximum_rows=10_000),
    "theil_sen_regression": ModelSpec("theil_sen_regression", "regression", _factory(TheilSenRegressor, {"max_subpopulation": 10000.0, "n_jobs": 1, "random_state": "__RANDOM_STATE__"}), maximum_rows=5_000),
    "poisson_regression": ModelSpec("poisson_regression", "regression", _factory(PoissonRegressor, {"alpha": 1.0, "max_iter": 300})),
    "gamma_regression": ModelSpec("gamma_regression", "regression", _factory(GammaRegressor, {"alpha": 1.0, "max_iter": 300})),
    "tweedie_regression": ModelSpec("tweedie_regression", "regression", _factory(TweedieRegressor, {"power": 1.5, "alpha": 1.0, "link": "auto", "max_iter": 300})),
    "lars_regression": ModelSpec("lars_regression", "regression", _factory(Lars, {"n_nonzero_coefs": 500, "fit_intercept": True})),
    "lasso_lars_regression": ModelSpec("lasso_lars_regression", "regression", _factory(LassoLars, {"alpha": 0.01, "max_iter": 500})),
    "orthogonal_matching_pursuit": ModelSpec("orthogonal_matching_pursuit", "regression", _factory(OrthogonalMatchingPursuit, {"n_nonzero_coefs": None, "tol": None, "fit_intercept": True})),
    "multitask_lasso": ModelSpec("multitask_lasso", "regression", _factory(MultiTaskLasso, {"alpha": 0.01, "max_iter": 1000, "random_state": "__RANDOM_STATE__"}), multioutput_target=True),
    "multitask_elastic_net": ModelSpec("multitask_elastic_net", "regression", _factory(MultiTaskElasticNet, {"alpha": 0.01, "l1_ratio": 0.5, "max_iter": 1000, "random_state": "__RANDOM_STATE__"}), multioutput_target=True),
    "isotonic_regression": ModelSpec("isotonic_regression", "regression", _factory(FirstFeatureIsotonicRegressor, {"increasing": "auto", "out_of_bounds": "clip"})),
    "pls_regression": ModelSpec("pls_regression", "regression", _factory(PLSRegression, {"n_components": 2, "scale": True, "max_iter": 500})),
    "pls_canonical": ModelSpec("pls_canonical", "regression", _factory(PLSCanonical, {"n_components": 2, "scale": True, "max_iter": 500}), multioutput_target=True),
    "canonical_correlation": ModelSpec("canonical_correlation", "regression", _factory(CCA, {"n_components": 2, "scale": True, "max_iter": 500}), multioutput_target=True),
    "transformed_target_regression": ModelSpec("transformed_target_regression", "regression", lambda random_state, supplied: TransformedTargetRegressor(regressor=Ridge(alpha=float(supplied.get("alpha", 1.0)), random_state=random_state), transformer=StandardScaler())),
}


DEFAULT_CANDIDATES = {
    "classification": ["dummy_classifier", "logistic_regression", "decision_tree_classifier", "random_forest_classifier", "gradient_boosting_classifier"],
    "regression": ["dummy_regressor", "linear_regression", "ridge_regression", "random_forest_regressor", "gradient_boosting_regressor"],
}


def model_catalog(*, task_type: str | None = None) -> list[dict[str, Any]]:
    specs = [spec for spec in MODEL_REGISTRY.values() if task_type is None or spec.task_type == task_type]
    return [
        {
            "algorithm": spec.name,
            "task_type": spec.task_type,
            "non_negative_features": spec.non_negative_features,
            "multioutput_target": spec.multioutput_target,
            "maximum_rows": spec.maximum_rows,
            "description": spec.description,
        }
        for spec in sorted(specs, key=lambda item: (item.task_type, item.name))
    ]


def build_estimator(algorithm: str, *, task_type: str, random_state: int, parameters: dict[str, Any] | None = None) -> tuple[ModelSpec, BaseEstimator]:
    key = str(algorithm).strip().casefold()
    spec = MODEL_REGISTRY.get(key)
    if spec is None:
        raise IntelligenceError("UNKNOWN_ALGORITHM", "Requested model algorithm is not registered.", {"algorithm": algorithm})
    if spec.task_type != task_type:
        raise IntelligenceError("ALGORITHM_TASK_MISMATCH", "Model algorithm does not support the detected task.", {"algorithm": key, "task_type": task_type})
    return spec, spec.factory(random_state, dict(parameters or {}))
