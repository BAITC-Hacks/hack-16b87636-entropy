"""Boundary diagnostics of the unchanged regression output, including failed expectations."""
import argparse
import json

import numpy as np
import pandas as pd

from .common import ForecastError, save_json, utc
from .model import load_model, predict


def synthetic_weather(temperature, winds=None):
    winds = np.arange(0, 30.5, 0.5) if winds is None else np.asarray(winds)
    valid = utc("2026-01-15T12:00:00+05:00")
    return pd.concat([pd.DataFrame({"turbine_id": tid, "wind_speed": winds,
                                   "temperature": temperature, "valid_at": valid,
                                   "forecast_origin": valid - pd.Timedelta(hours=24)})
                      for tid in [1, 2]], ignore_index=True)


def check_predict(bundle, temperature, zero_tolerance=0.05):
    outcomes = []

    def record(name, action):
        try:
            detail = action()
            outcomes.append({"name": name, "passed": True, "detail": detail})
        except AssertionError as exc:
            outcomes.append({"name": name, "passed": False, "detail": str(exc)})
        except Exception as exc:
            outcomes.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})

    def wind_case(winds, near_zero=False):
        raw = predict(bundle, synthetic_weather(temperature, winds))
        values = raw.tolist()
        assert np.isfinite(raw).all(), f"Non-finite predictions: {values}"
        assert ((raw >= 0) & (raw <= 1)).all(), f"Raw predict outside [0,1]: {values}; agent clips independently"
        if near_zero:
            assert (raw <= zero_tolerance).all(), f"Zero-wind output {values} exceeds {zero_tolerance}"
        return {"raw_predictions": values, "winds": winds}

    def missing_case():
        messages = {}
        for col in ["wind_speed", "temperature", "valid_at", "forecast_origin", "turbine_id"]:
            frame = synthetic_weather(temperature, [5.0])
            frame.loc[0, col] = pd.NaT if col in ("valid_at", "forecast_origin") else np.nan
            try:
                predict(bundle, frame)
            except ForecastError as exc:
                assert "NaN" in str(exc) or "missing" in str(exc), str(exc)
                messages[col] = str(exc)
            else:
                raise AssertionError(f"NaN in {col} silently accepted")
        return messages

    def deterministic_case():
        frame = synthetic_weather(temperature)
        a, b = predict(bundle, frame), predict(bundle, frame)
        assert np.array_equal(a, b), f"Max delta={np.max(np.abs(a-b))}"
        return {"rows": len(a), "max_absolute_difference": 0.0}

    record("zero_wind_near_zero_and_in_range", lambda: wind_case([0.0], True))
    record("extreme_wind_25_to_30_in_range", lambda: wind_case([25., 27.5, 30.]))
    record("nan_rejected_explicitly", missing_case)
    record("deterministic_repeated_input", deterministic_case)
    return {"temperature": temperature, "zero_tolerance": zero_tolerance,
            "outcomes": outcomes, "all_passed": all(x["passed"] for x in outcomes)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="artifacts/validation_model.joblib")
    parser.add_argument("--temperature", type=float, default=5.0)
    parser.add_argument("--out", default="artifacts/diagnostics/predict_checks.json")
    args = parser.parse_args()
    result = check_predict(load_model(args.model), args.temperature)
    save_json(args.out, result)
    print(json.dumps(result, indent=2))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
