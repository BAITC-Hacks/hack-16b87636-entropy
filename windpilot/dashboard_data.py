"""Presentation metadata sourced exclusively from existing observations and reports."""
import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .common import utc
from .data import load_history


@lru_cache(maxsize=4)
def _history(path, mtime):
    return load_history(path)


def baseline_at(origin, path="data/processed/hourly.csv"):
    path = Path(path)
    if not path.exists():
        return []
    history = _history(str(path.resolve()), path.stat().st_mtime_ns)
    origin = utc(origin)
    known = history[history.timestamp + pd.Timedelta(hours=1) <= origin]
    result = []
    for tid, part in known.groupby("turbine_id"):
        last = part.iloc[-1]
        available = last.timestamp + pd.Timedelta(hours=1)
        result.append({"turbine_id": int(tid), "value": float(last.power_norm), "observed_at": last.timestamp.isoformat(),
                       "available_at": available.isoformat(), "age_hours": float((origin - available).total_seconds() / 3600)})
    return result


def quality_data(path="artifacts/metrics.json"):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"validation_start": data["validation_start"], "validation_end_exclusive": data["validation_end_exclusive"],
            "model_trained_until": data["model_trained_until"], "metrics_by_turbine": data["metrics_by_turbine"],
            "availability_assumption": data["availability_assumption"], "archive_provenance_status": data["archive_provenance_status"]}
