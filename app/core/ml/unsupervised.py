from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import (
    AffinityPropagation,
    AgglomerativeClustering,
    Birch,
    DBSCAN,
    FeatureAgglomeration,
    KMeans,
    MeanShift,
    MiniBatchKMeans,
    OPTICS,
    SpectralClustering,
    estimate_bandwidth,
)
from sklearn.compose import ColumnTransformer
from sklearn.covariance import EllipticEnvelope
from sklearn.decomposition import DictionaryLearning, FactorAnalysis, FastICA, KernelPCA, NMF, PCA, SparsePCA, TruncatedSVD
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.manifold import Isomap, LocallyLinearEmbedding, MDS, TSNE
from sklearn.metrics import adjusted_rand_score, calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.mixture import BayesianGaussianMixture, GaussianMixture
from sklearn.neighbors import LocalOutlierFactor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder, StandardScaler
from sklearn.random_projection import GaussianRandomProjection
from sklearn.svm import OneClassSVM

from app.core.intelligence.common import ExecutionBudget, IntelligenceError, finish_metadata, json_safe, started_timer


CLUSTERING_ALGORITHMS = {
    "kmeans",
    "minibatch_kmeans",
    "dbscan",
    "optics",
    "birch",
    "mean_shift",
    "affinity_propagation",
    "spectral_clustering",
    "hierarchical_clustering",
    "gaussian_mixture",
    "bayesian_gaussian_mixture",
}
EMBEDDING_ALGORITHMS = {
    "pca",
    "kernel_pca",
    "sparse_pca",
    "truncated_svd",
    "factor_analysis",
    "fastica",
    "nmf",
    "dictionary_learning",
    "random_projection",
    "mds",
    "isomap",
    "lle",
    "tsne",
    "feature_agglomeration",
}
ANOMALY_ALGORITHMS = {"isolation_forest", "local_outlier_factor", "one_class_svm", "robust_covariance"}
UNSUPERVISED_ALGORITHMS = CLUSTERING_ALGORITHMS | EMBEDDING_ALGORITHMS | ANOMALY_ALGORITHMS


@dataclass
class UnsupervisedOutcome:
    result: dict[str, Any]
    artifact_frame: pd.DataFrame


class UnsupervisedLearningService:
    """Canonical clustering, representation-learning, and anomaly service."""

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget = budget or ExecutionBudget()

    @staticmethod
    def catalog() -> dict[str, Any]:
        return {
            "algorithm_count": len(UNSUPERVISED_ALGORITHMS),
            "clustering": sorted(CLUSTERING_ALGORITHMS),
            "embedding": sorted(EMBEDDING_ALGORITHMS),
            "anomaly": sorted(ANOMALY_ALGORITHMS),
        }

    def _prepare(self, frame: pd.DataFrame, *, feature_columns: list[str] | None, algorithm: str) -> tuple[pd.DataFrame, np.ndarray, list[str], dict[str, Any]]:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise IntelligenceError("EMPTY_DATASET", "Dataset must contain at least one row.")
        features = [str(column) for column in (feature_columns or list(frame.columns))]
        missing = [column for column in features if column not in frame.columns]
        if missing:
            raise IntelligenceError("FEATURE_NOT_FOUND", "One or more feature columns do not exist.", {"columns": missing})
        if not features or len(features) > self.budget.max_features:
            raise IntelligenceError("INVALID_FEATURE_SELECTION", "Select between 1 and the configured maximum feature count.", {"max_features": self.budget.max_features})
        work = frame[features].copy()
        for column in work.columns:
            if pd.api.types.is_datetime64_any_dtype(work[column]):
                work[column] = work[column].astype("string")
        unusable = [column for column in features if work[column].notna().sum() == 0]
        features = [column for column in features if column not in unusable]
        work = work[features]
        if not features:
            raise IntelligenceError("NO_USABLE_FEATURES", "All selected features are empty.")
        algorithm_limit = 3_000 if algorithm in {"tsne", "mds", "spectral_clustering", "affinity_propagation"} else min(self.budget.max_rows, 20_000)
        sampled = len(work) > algorithm_limit
        if sampled:
            work = work.sample(n=algorithm_limit, random_state=self.budget.random_state).sort_index()
        numeric = [column for column in features if pd.api.types.is_numeric_dtype(work[column])]
        categorical = [column for column in features if column not in numeric]
        non_negative = algorithm == "nmf"
        transforms = []
        if numeric:
            transforms.append(("numeric", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", MinMaxScaler() if non_negative else StandardScaler())]), numeric))
        if categorical:
            transforms.append(("categorical", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("encoder", OneHotEncoder(handle_unknown="ignore", max_categories=50, sparse_output=False))]), categorical))
        preprocessor = ColumnTransformer(transforms, remainder="drop", verbose_feature_names_out=False)
        matrix = np.asarray(preprocessor.fit_transform(work), dtype=float)
        if matrix.shape[1] == 0:
            raise IntelligenceError("NO_USABLE_FEATURES", "Preprocessing produced no usable features.")
        metadata = {
            "rows_scanned": len(frame),
            "rows_used": len(work),
            "feature_count": len(features),
            "transformed_feature_count": int(matrix.shape[1]),
            "sampled": sampled,
            "sample_size": len(work),
            "cache_hit": False,
            "empty_features_removed": unusable,
        }
        return work, matrix, features, metadata

    def _cluster(self, matrix: np.ndarray, algorithm: str, params: dict[str, Any], random_state: int) -> tuple[np.ndarray, dict[str, Any]]:
        clusters = max(2, min(int(params.get("n_clusters", 3)), min(50, len(matrix) - 1)))
        if algorithm == "kmeans":
            model = KMeans(n_clusters=clusters, n_init=10, max_iter=int(params.get("max_iter", 300)), random_state=random_state)
            labels = model.fit_predict(matrix)
            metadata = {"inertia": float(model.inertia_), "iterations": int(model.n_iter_)}
        elif algorithm == "minibatch_kmeans":
            model = MiniBatchKMeans(n_clusters=clusters, batch_size=min(1024, len(matrix)), n_init=5, max_iter=int(params.get("max_iter", 200)), random_state=random_state)
            labels = model.fit_predict(matrix)
            metadata = {"inertia": float(model.inertia_), "iterations": int(model.n_iter_)}
        elif algorithm == "dbscan":
            model = DBSCAN(eps=float(params.get("eps", 0.5)), min_samples=int(params.get("min_samples", 5)), n_jobs=1)
            labels = model.fit_predict(matrix)
            metadata = {}
        elif algorithm == "optics":
            model = OPTICS(min_samples=int(params.get("min_samples", 5)), max_eps=float(params.get("max_eps", math.inf)), n_jobs=1)
            labels = model.fit_predict(matrix)
            metadata = {}
        elif algorithm == "birch":
            model = Birch(n_clusters=clusters, threshold=float(params.get("threshold", 0.5)), branching_factor=int(params.get("branching_factor", 50)))
            labels = model.fit_predict(matrix)
            metadata = {}
        elif algorithm == "mean_shift":
            bandwidth = params.get("bandwidth")
            if bandwidth is None:
                bandwidth = estimate_bandwidth(matrix, quantile=0.2, n_samples=min(500, len(matrix)), random_state=random_state, n_jobs=1)
            model = MeanShift(bandwidth=float(bandwidth) if bandwidth and bandwidth > 0 else None, bin_seeding=True, n_jobs=1)
            labels = model.fit_predict(matrix)
            metadata = {"bandwidth": None if bandwidth is None else float(bandwidth)}
        elif algorithm == "affinity_propagation":
            model = AffinityPropagation(damping=float(params.get("damping", 0.8)), max_iter=int(params.get("max_iter", 200)), random_state=random_state)
            labels = model.fit_predict(matrix)
            metadata = {"iterations": int(model.n_iter_)}
        elif algorithm == "spectral_clustering":
            model = SpectralClustering(n_clusters=clusters, affinity=str(params.get("affinity", "nearest_neighbors")), assign_labels="kmeans", n_neighbors=min(10, len(matrix) - 1), n_jobs=1, random_state=random_state)
            labels = model.fit_predict(matrix)
            metadata = {}
        elif algorithm == "hierarchical_clustering":
            model = AgglomerativeClustering(n_clusters=clusters, linkage=str(params.get("linkage", "ward")))
            labels = model.fit_predict(matrix)
            metadata = {}
        elif algorithm == "gaussian_mixture":
            model = GaussianMixture(n_components=clusters, covariance_type=str(params.get("covariance_type", "full")), max_iter=int(params.get("max_iter", 200)), random_state=random_state)
            labels = model.fit_predict(matrix)
            metadata = {"aic": float(model.aic(matrix)), "bic": float(model.bic(matrix)), "converged": bool(model.converged_)}
        elif algorithm == "bayesian_gaussian_mixture":
            model = BayesianGaussianMixture(n_components=clusters, covariance_type=str(params.get("covariance_type", "full")), max_iter=int(params.get("max_iter", 200)), random_state=random_state)
            labels = model.fit_predict(matrix)
            metadata = {"converged": bool(model.converged_), "active_components": int((model.weights_ > 0.01).sum())}
        else:
            raise IntelligenceError("UNKNOWN_UNSUPERVISED_ALGORITHM", "Unsupported clustering algorithm.")
        return np.asarray(labels, dtype=int), metadata

    @staticmethod
    def _quality(matrix: np.ndarray, labels: np.ndarray) -> dict[str, Any]:
        clustered = labels >= 0
        valid_labels = np.unique(labels[clustered])
        if clustered.sum() < 3 or len(valid_labels) < 2 or len(valid_labels) >= clustered.sum():
            return {"silhouette": None, "davies_bouldin": None, "calinski_harabasz": None}
        values = matrix[clustered]
        chosen = labels[clustered]
        return {
            "silhouette": float(silhouette_score(values, chosen, sample_size=min(5000, len(values)), random_state=42)),
            "davies_bouldin": float(davies_bouldin_score(values, chosen)),
            "calinski_harabasz": float(calinski_harabasz_score(values, chosen)),
        }

    def _embedding(self, matrix: np.ndarray, algorithm: str, params: dict[str, Any], random_state: int) -> tuple[np.ndarray, dict[str, Any]]:
        components = max(1, min(int(params.get("n_components", 2)), matrix.shape[1], max(1, len(matrix) - 1)))
        if algorithm == "pca":
            model = PCA(n_components=components, random_state=random_state)
        elif algorithm == "kernel_pca":
            model = KernelPCA(n_components=components, kernel=str(params.get("kernel", "rbf")), gamma=params.get("gamma"), fit_inverse_transform=False, random_state=random_state, n_jobs=1)
        elif algorithm == "sparse_pca":
            model = SparsePCA(n_components=components, alpha=float(params.get("alpha", 1.0)), max_iter=int(params.get("max_iter", 500)), n_jobs=1, random_state=random_state)
        elif algorithm == "truncated_svd":
            model = TruncatedSVD(n_components=components, n_iter=7, random_state=random_state)
        elif algorithm == "factor_analysis":
            model = FactorAnalysis(n_components=components, max_iter=int(params.get("max_iter", 500)), random_state=random_state)
        elif algorithm == "fastica":
            model = FastICA(n_components=components, max_iter=int(params.get("max_iter", 500)), whiten="unit-variance", random_state=random_state)
        elif algorithm == "nmf":
            model = NMF(n_components=components, init="nndsvda", max_iter=int(params.get("max_iter", 500)), random_state=random_state)
        elif algorithm == "dictionary_learning":
            model = DictionaryLearning(n_components=components, alpha=float(params.get("alpha", 1.0)), max_iter=int(params.get("max_iter", 200)), n_jobs=1, random_state=random_state)
        elif algorithm == "random_projection":
            model = GaussianRandomProjection(n_components=components, random_state=random_state)
        elif algorithm == "mds":
            model = MDS(n_components=components, n_init=2, max_iter=int(params.get("max_iter", 200)), n_jobs=1, random_state=random_state, normalized_stress="auto")
        elif algorithm == "isomap":
            model = Isomap(n_neighbors=min(int(params.get("n_neighbors", 5)), len(matrix) - 1), n_components=components, n_jobs=1)
        elif algorithm == "lle":
            model = LocallyLinearEmbedding(n_neighbors=min(max(components + 1, int(params.get("n_neighbors", 8))), len(matrix) - 1), n_components=components, method=str(params.get("method", "standard")), n_jobs=1, random_state=random_state)
        elif algorithm == "tsne":
            perplexity = min(float(params.get("perplexity", 30)), max(2.0, (len(matrix) - 1) / 3))
            model = TSNE(n_components=components, perplexity=perplexity, max_iter=int(params.get("max_iter", 1000)), init="pca", learning_rate="auto", random_state=random_state)
        elif algorithm == "feature_agglomeration":
            model = FeatureAgglomeration(n_clusters=components)
        else:
            raise IntelligenceError("UNKNOWN_UNSUPERVISED_ALGORITHM", "Unsupported embedding algorithm.")
        try:
            embedding = np.asarray(model.fit_transform(matrix), dtype=float)
        except Exception as exc:
            raise IntelligenceError("UNSUPERVISED_MODEL_FAILED", "Embedding model failed.", {"algorithm": algorithm, "error": str(exc)}) from exc
        metadata: dict[str, Any] = {}
        if hasattr(model, "explained_variance_ratio_"):
            metadata["explained_variance_ratio"] = json_safe(model.explained_variance_ratio_)
            metadata["total_explained_variance"] = float(np.sum(model.explained_variance_ratio_))
        if hasattr(model, "reconstruction_err_"):
            metadata["reconstruction_error"] = float(model.reconstruction_err_)
        if hasattr(model, "stress_"):
            metadata["stress"] = float(model.stress_)
        return embedding, metadata

    def _anomaly(self, matrix: np.ndarray, algorithm: str, params: dict[str, Any], random_state: int) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        contamination = float(params.get("contamination", 0.05))
        if not 0 < contamination <= 0.5:
            raise IntelligenceError("INVALID_CONTAMINATION", "contamination must be in (0, 0.5].")
        if algorithm == "isolation_forest":
            model = IsolationForest(n_estimators=150, contamination=contamination, n_jobs=1, random_state=random_state)
            raw = model.fit_predict(matrix)
            score = -model.score_samples(matrix)
        elif algorithm == "local_outlier_factor":
            model = LocalOutlierFactor(n_neighbors=min(int(params.get("n_neighbors", 20)), len(matrix) - 1), contamination=contamination, n_jobs=1)
            raw = model.fit_predict(matrix)
            score = -model.negative_outlier_factor_
        elif algorithm == "one_class_svm":
            model = OneClassSVM(nu=min(0.5, max(0.01, contamination)), kernel=str(params.get("kernel", "rbf")), gamma=str(params.get("gamma", "scale")))
            raw = model.fit_predict(matrix)
            score = -model.score_samples(matrix)
        elif algorithm == "robust_covariance":
            if len(matrix) <= matrix.shape[1] + 1:
                raise IntelligenceError("INSUFFICIENT_ROWS", "Robust covariance requires more rows than transformed features.")
            model = EllipticEnvelope(contamination=contamination, random_state=random_state)
            raw = model.fit_predict(matrix)
            score = -model.score_samples(matrix)
        else:
            raise IntelligenceError("UNKNOWN_UNSUPERVISED_ALGORITHM", "Unsupported anomaly algorithm.")
        labels = np.where(raw == -1, 1, 0)
        return labels.astype(int), np.asarray(score, dtype=float), {"contamination": contamination, "anomaly_count": int(labels.sum())}

    def run(self, frame: pd.DataFrame, *, algorithm: str, feature_columns: list[str] | None = None, **params: Any) -> UnsupervisedOutcome:
        started = started_timer()
        algorithm = str(algorithm).strip().casefold()
        if algorithm not in UNSUPERVISED_ALGORITHMS:
            raise IntelligenceError("UNKNOWN_UNSUPERVISED_ALGORITHM", "Requested unsupervised algorithm is not registered.", {"algorithm": algorithm})
        work, matrix, features, metadata = self._prepare(frame, feature_columns=feature_columns, algorithm=algorithm)
        random_state = int(params.get("random_state", self.budget.random_state))
        artifact = pd.DataFrame({"source_row_index": work.index.astype(str)}, index=work.index)
        if algorithm in CLUSTERING_ALGORITHMS:
            try:
                labels, model_metadata = self._cluster(matrix, algorithm, params, random_state)
            except IntelligenceError:
                raise
            except Exception as exc:
                raise IntelligenceError("UNSUPERVISED_MODEL_FAILED", "Clustering model failed.", {"algorithm": algorithm, "error": str(exc)}) from exc
            quality = self._quality(matrix, labels)
            artifact["label"] = labels
            result = {
                "cluster_count": int(len(set(labels)) - (1 if -1 in labels else 0)),
                "noise_count": int((labels == -1).sum()),
                "quality_metrics": quality,
                "model_metadata": model_metadata,
                "labels_preview": labels[:500].tolist(),
            }
        elif algorithm in EMBEDDING_ALGORITHMS:
            embedding, model_metadata = self._embedding(matrix, algorithm, params, random_state)
            for component in range(embedding.shape[1]):
                artifact[f"component_{component + 1}"] = embedding[:, component]
            result = {"component_count": int(embedding.shape[1]), "embedding_preview": json_safe(embedding[:250]), "model_metadata": model_metadata}
        else:
            try:
                labels, scores, model_metadata = self._anomaly(matrix, algorithm, params, random_state)
            except IntelligenceError:
                raise
            except Exception as exc:
                raise IntelligenceError("UNSUPERVISED_MODEL_FAILED", "Anomaly model failed.", {"algorithm": algorithm, "error": str(exc)}) from exc
            artifact["is_anomaly"] = labels
            artifact["anomaly_score"] = scores
            result = {"anomaly_count": int(labels.sum()), "anomaly_rate": float(labels.mean()), "score_summary": {"min": float(scores.min()), "mean": float(scores.mean()), "max": float(scores.max())}, "model_metadata": model_metadata, "labels_preview": labels[:500].tolist()}
        return UnsupervisedOutcome(
            result=json_safe({"algorithm": algorithm, "row_count": len(work), "feature_count": len(features), **result, "artifact_rows": len(artifact), "execution": finish_metadata(started, metadata)}),
            artifact_frame=artifact.reset_index(drop=True),
        )

    def compare(self, frame: pd.DataFrame, *, feature_columns: list[str] | None = None, algorithms: list[str] | None = None, **params: Any) -> dict[str, Any]:
        candidates = algorithms or ["kmeans", "minibatch_kmeans", "birch", "gaussian_mixture", "hierarchical_clustering", "dbscan"]
        successes = []
        failures = []
        for algorithm in candidates[:10]:
            try:
                outcome = self.run(frame, algorithm=algorithm, feature_columns=feature_columns, **params)
                successes.append(outcome.result)
            except IntelligenceError as exc:
                failures.append({"algorithm": algorithm, "code": exc.code, "message": exc.message})
        if not successes:
            raise IntelligenceError("NO_SUCCESSFUL_MODELS", "All clustering candidates failed.", {"failures": failures})
        ranked = sorted(successes, key=lambda item: (-(item["quality_metrics"].get("silhouette") if item["quality_metrics"].get("silhouette") is not None else -math.inf), item["algorithm"]))
        return {"champion_algorithm": ranked[0]["algorithm"], "ranking": [{"rank": rank, "algorithm": item["algorithm"], "cluster_count": item["cluster_count"], "noise_count": item["noise_count"], "quality_metrics": item["quality_metrics"]} for rank, item in enumerate(ranked, 1)], "failures": failures}

    def stability(self, frame: pd.DataFrame, *, algorithm: str = "kmeans", feature_columns: list[str] | None = None, repeats: int = 5, **params: Any) -> dict[str, Any]:
        if algorithm not in {"kmeans", "minibatch_kmeans", "gaussian_mixture", "bayesian_gaussian_mixture", "spectral_clustering"}:
            raise IntelligenceError("STABILITY_UNSUPPORTED", "Stability requires a stochastic clustering algorithm.")
        work, matrix, _, metadata = self._prepare(frame, feature_columns=feature_columns, algorithm=algorithm)
        runs = []
        for repeat in range(min(10, max(2, int(repeats)))):
            labels, _ = self._cluster(matrix, algorithm, params, self.budget.random_state + repeat)
            runs.append(labels)
        scores = [float(adjusted_rand_score(runs[0], labels)) for labels in runs[1:]]
        return {"algorithm": algorithm, "repeats": len(runs), "adjusted_rand_scores": scores, "mean_stability": float(np.mean(scores)), "minimum_stability": float(np.min(scores)), "rows_used": len(work), "execution": metadata}
