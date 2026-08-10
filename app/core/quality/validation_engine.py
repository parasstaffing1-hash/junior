from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value == "")


def _number(value: Any) -> float | None:
    try:
        return None if _null(value) else float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _valid_date(value: Any) -> bool:
    if _null(value):
        return False
    try:
        date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def validate_dataset(rows: list[dict[str, Any]], dataset_id: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    before = copy.deepcopy(rows)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for rule in rules:
        column = str(rule.get("column", ""))
        rule_name = str(rule.get("rule", rule.get("type", ""))).casefold()
        severity = str(rule.get("severity", "error")).casefold()
        ignore_nulls = bool(rule.get("ignore_nulls", rule_name not in {"required", "email", "allowed_values", "required_non_empty_string"}))
        invalid: list[int] = []
        values = [row.get(column) for row in rows]
        if rule_name == "required":
            invalid = [i for i, value in enumerate(values) if _null(value)]
        elif rule_name == "unique":
            positions: dict[Any, list[int]] = {}
            for i, value in enumerate(values):
                if _null(value) and ignore_nulls:
                    continue
                positions.setdefault(repr(value), []).append(i)
            invalid = [i for group in positions.values() if len(group) > 1 for i in group]
        elif rule_name in {"range", "min", "max"}:
            for i, value in enumerate(values):
                if _null(value) and ignore_nulls:
                    continue
                number = _number(value)
                if number is None or (rule_name == "range" and (number < float(rule["min"]) or number > float(rule["max"]))) or (rule_name == "min" and number < float(rule.get("value", rule.get("min")))) or (rule_name == "max" and number > float(rule.get("value", rule.get("max")))):
                    invalid.append(i)
        elif rule_name in {"email", "email_format"}:
            invalid = [i for i, value in enumerate(values) if (not _null(value) or not ignore_nulls) and (not isinstance(value, str) or EMAIL_RE.fullmatch(value) is None)]
        elif rule_name in {"date", "date_format"}:
            invalid = [i for i, value in enumerate(values) if (not _null(value) or not ignore_nulls) and not _valid_date(value)]
        elif rule_name in {"allowed_values", "allowed"}:
            allowed = set(rule.get("values", []))
            invalid = [i for i, value in enumerate(values) if (not (_null(value) and ignore_nulls)) and value not in allowed]
        elif rule_name in {"regex", "pattern"}:
            rx = re.compile(str(rule.get("pattern", "")))
            invalid = [i for i, value in enumerate(values) if (not (_null(value) and ignore_nulls)) and (not isinstance(value, str) or rx.fullmatch(value) is None)]
        else:
            invalid = list(range(len(rows))) if column not in (rows[0].keys() if rows else set()) else []
        status = "failed" if invalid else "passed"
        message = rule.get("message") or f"{column} failed {rule_name} validation."
        item = {"column": column, "rule": rule_name, "severity": severity, "status": status, "invalid_count": len(invalid), "invalid_row_indices": invalid, "examples": [values[i] for i in invalid[:5]], "message": message if invalid else None}
        results.append(item)
        if invalid:
            target = warnings if severity == "warning" else errors
            target.append(item)
    source_unchanged = rows == before
    return {"dataset_id": dataset_id, "status": "PASS" if not errors and not warnings else "NEEDS_CLEANING", "valid": not errors, "rules_checked": len(results), "rules_passed": sum(item["status"] == "passed" for item in results), "rules_failed": sum(item["status"] == "failed" for item in results), "error_count": len(errors), "warning_count": len(warnings), "errors": errors, "warnings": warnings, "results": results, "invalid_rows": sorted({i for item in results for i in item["invalid_row_indices"]}), "source_unchanged": source_unchanged}
