from __future__ import annotations

import importlib
import pkgutil
import warnings

import pandas as pd

import app
from app.core.eda.report import generate_eda_report
from app.core.statistics.report import generate_statistics_report
from app.core.transformation.pipeline import execute_pipeline


def test_every_app_module_imports_cleanly():
    failures = []
    modules = [item.name for item in pkgutil.walk_packages(app.__path__, app.__name__ + ".")]
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - failure details are asserted below
            failures.append((module_name, type(exc).__name__, str(exc)))
    assert failures == []


def test_merged_reports_and_transformation_pipeline():
    frame = pd.DataFrame(
        {
            "value": [10, 12, 14, 20, 22, 24, 30, 32],
            "value2": [11, 13, 15, 19, 21, 25, 29, 33],
            "group": ["A", "A", "A", "B", "B", "B", "C", "C"],
            "label": ["x", "x", "y", "y", "y", "z", "z", "z"],
        }
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eda = generate_eda_report(frame)
        statistics = generate_statistics_report(frame)
        pipeline = execute_pipeline(
            frame,
            steps=[
                {
                    "tool": "column_operations",
                    "parameters": {"operations": [{"type": "rename", "mapping": {"value": "amount"}}]},
                },
                {
                    "tool": "calculated_columns",
                    "parameters": {
                        "calculations": [
                            {
                                "name": "double_amount",
                                "expression": {
                                    "op": "multiply",
                                    "left": {"op": "column", "name": "amount"},
                                    "right": {"op": "literal", "value": 2},
                                },
                            }
                        ]
                    },
                },
            ],
        )

    assert not eda["warnings"]
    assert not statistics["warnings"]
    assert [step.tool for step in pipeline.steps] == ["column_operations", "calculated_columns"]
    assert pipeline.dataframe["double_amount"].tolist() == [20, 24, 28, 40, 44, 48, 60, 64]
