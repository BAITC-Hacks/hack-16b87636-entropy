from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

from .common import ForecastError, digest, file_hash, origins, save_json, utc
from .data import load_history
from .weather import WeatherClient


def weather_signature(config):
    return digest({"timezone": config["history_timezone"], "turbines": config["turbines"],
                   "model": config["weather"]["model"], "wind_variable": config["weather"]["wind_variable"],
                   "publication_lag_hours": config["weather"]["publication_lag_hours"]})


def estimator():
    return HistGradientBoostingRegressor(max_iter=180, max_leaf_nodes=15, min_samples_leaf=30,
                                        learning_rate=0.06, l2_regularization=5, early_stopping=False, random_state=42)


def feature_frame(weather, power_curve):
    valid = pd.to_datetime(weather.valid_at, utc=True)
    origin = pd.to_datetime(weather.forecast_origin, utc=True)
    values = weather[["wind_speed", "temperature"]].copy()
    values["lead_hours"] = (valid - origin).dt.total_seconds() / 3600
    values["hour_sin"] = np.sin(2 * np.pi * valid.dt.hour / 24)
    values["hour_cos"] = np.cos(2 * np.pi * valid.dt.hour / 24)
    values["power_curve"] = np.clip(power_curve.predict(weather[["wind_speed", "temperature"]]), 0, 1)
    return values


def supervised(history, config, start, end):
    client = WeatherClient(config, offline=True)
    frames, failures = [], []
    for origin in origins(start, end, config["history_timezone"]):
        try:
            frames.append(client.for_origin(origin, 48))
        except ForecastError as exc:
            failures.append({"origin": str(origin), "error": str(exc)})
    if failures:
        raise ForecastError(f"Missing weather for {len(failures)} origins; run fetch-weather first. First error: {failures[0]}")
    weather = pd.concat(frames, ignore_index=True)
    targets = history[["turbine_id", "timestamp", "power_norm"]].rename(columns={"timestamp": "valid_at"})
    joined = weather.merge(targets, on=["turbine_id", "valid_at"], how="inner", validate="many_to_one")
    return joined, len(weather) - len(joined)


def fit_bundle(history, training, cutoff, config, history_hash):
    cutoff = utc(cutoff)
    observed = history.loc[history.timestamp + pd.Timedelta(hours=1) <= cutoff]
    training = training.loc[training.valid_at + pd.Timedelta(hours=1) <= cutoff]
    bundle = {"models": {}, "trained_until": cutoff.isoformat(), "weather_signature": weather_signature(config),
              "history_hash": history_hash, "config": config, "algorithm": "observed-power-curve + archived-weather-residual-HGB",
              "random_seed": 42, "training_counts": {}, "feature_version": 1}
    with threadpool_limits(limits=2):
        for turbine in config["turbines"]:
            tid = turbine["id"]
            obs = observed.loc[observed.turbine_id == tid]
            rows = training.loc[training.turbine_id == tid]
            if len(obs) < 200 or len(rows) < 200:
                raise ForecastError(f"Insufficient training data for turbine {tid}: history={len(obs)}, forecast pairs={len(rows)}")
            curve = estimator().fit(obs[["wind_speed", "temperature"]], obs.power_norm)
            features = feature_frame(rows, curve)
            residual = estimator().fit(features, rows.power_norm.to_numpy() - features.power_curve.to_numpy())
            bundle["models"][tid] = {"curve": curve, "residual": residual}
            bundle["training_counts"][tid] = {"observed_hours": len(obs), "forecast_target_pairs": len(rows),
                                               "unique_target_hours": rows.valid_at.nunique()}
    return bundle


def predict(bundle, weather, observer=None):
    required = {"turbine_id", "wind_speed", "temperature", "valid_at", "forecast_origin"}
    missing = required - set(weather.columns)
    if missing:
        raise ForecastError(f"Prediction input is missing required columns: {sorted(missing)}")
    if weather.empty:
        raise ForecastError("Prediction input contains no rows.")
    if weather[list(required)].isna().any().any():
        raise ForecastError("Prediction features contain NaN/missing values; supply complete weather and timestamps.")
    try:
        numeric = weather[["wind_speed", "temperature"]].to_numpy(dtype=float)
        valid = pd.to_datetime(weather.valid_at, utc=True, errors="coerce")
        origin = pd.to_datetime(weather.forecast_origin, utc=True, errors="coerce")
    except (ValueError, TypeError) as exc:
        raise ForecastError("Prediction features must contain numeric weather and valid timestamps.") from exc
    if not np.isfinite(numeric).all() or valid.isna().any() or origin.isna().any():
        raise ForecastError("Prediction features contain NaN, infinity or invalid timestamps.")
    if (numeric[:, 0] < 0).any():
        raise ForecastError("Prediction wind speed must be non-negative.")
    output = np.empty(len(weather))
    if set(weather.turbine_id) - set(bundle["models"]):
        raise ForecastError("Model does not contain all requested turbines.")
    with threadpool_limits(limits=2):
        for tid, models in bundle["models"].items():
            positions = np.flatnonzero(weather.turbine_id.to_numpy() == tid)
            if not len(positions):
                continue
            features = feature_frame(weather.iloc[positions], models["curve"])
            if observer:
                observer("features_prepared", turbine_id=int(tid))
                observer("turbine_model_started", turbine_id=int(tid))
            output[positions] = features.power_curve.to_numpy() + models["residual"].predict(features)
    return output


def scores(actual, prediction):
    error = np.asarray(prediction) - np.asarray(actual)
    return {"n": len(error), "mae": float(np.abs(error).mean()), "rmse": float(np.sqrt((error ** 2).mean()))}


def evaluate(bundle, test, history):
    result = test.copy().reset_index(drop=True)
    result["prediction"] = np.clip(predict(bundle, result), 0, 1)
    result["persistence"] = np.nan
    for (tid, origin), group in result.groupby(["turbine_id", "forecast_origin"]):
        prior = history.loc[(history.turbine_id == tid) & (history.timestamp + pd.Timedelta(hours=1) <= origin)]
        if not prior.empty:
            last = prior.iloc[-1]
            # Explicitly report missing baseline when the last observed hour is too old.
            if origin - (last.timestamp + pd.Timedelta(hours=1)) <= pd.Timedelta(hours=3):
                result.loc[group.index, "persistence"] = last.power_norm
    result["lead_hours"] = (result.valid_at - result.forecast_origin).dt.total_seconds() / 3600
    metrics = {}
    for tid, group in result.groupby("turbine_id"):
        metrics[int(tid)] = {}
        for label, part in [("all", group), ("hours_1_24", group[group.lead_hours <= 24]),
                            ("hours_25_48", group[group.lead_hours > 24])]:
            if part.empty:
                continue
            paired = part.dropna(subset=["persistence"])
            item = {"model": scores(part.power_norm, part.prediction), "baseline_missing_pairs": len(part) - len(paired)}
            if not paired.empty:
                item["model_on_baseline_pairs"] = scores(paired.power_norm, paired.prediction)
                item["persistence_on_same_pairs"] = scores(paired.power_norm, paired.persistence)
            metrics[int(tid)][label] = item
    return result, metrics


def train(history_path, config, weather_start="2025-10-01", validation_start="2026-01-01",
          validation_end="2026-01-31", final_cutoff="2026-01-31T23:00:00+05:00", out_dir="artifacts"):
    history = load_history(history_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paired, unmatched = supervised(history, config, weather_start, validation_end)
    validation_boundary = pd.Timestamp(validation_start).tz_localize(config["history_timezone"]).tz_convert("UTC")
    evaluation_cutoff = validation_boundary - pd.Timedelta(hours=1)
    test_end = (pd.Timestamp(validation_end) + pd.Timedelta(days=1)).tz_localize(config["history_timezone"]).tz_convert("UTC")
    hist_hash = file_hash(history_path)
    print("Training validation model (all targets end before first validation origin)...", flush=True)
    validation_bundle = fit_bundle(history, paired, evaluation_cutoff, config, hist_hash)
    test = paired.loc[(paired.forecast_origin >= evaluation_cutoff) & (paired.valid_at >= validation_boundary)
                      & (paired.valid_at < test_end)]
    if test.empty:
        raise ForecastError("No matched validation data.")
    predictions, metrics = evaluate(validation_bundle, test, history)
    predictions.to_csv(out / "validation_predictions.csv", index=False)
    joblib.dump(validation_bundle, out / "validation_model.joblib")
    report = {"evaluation": "Fixed model; archived weather at each historical origin; hourly targets from held-out January.",
              "validation_start": str(validation_boundary), "validation_end_exclusive": str(test_end),
              "model_trained_until": str(evaluation_cutoff), "metrics_by_turbine": metrics,
              "unmatched_weather_target_pairs": unmatched, "training_counts": validation_bundle["training_counts"],
              "availability_assumption": config["weather"]["availability_basis"],
              "archive_provenance_status": "Provider archive; original operational publication not independently verified.",
              "note": "Overlapping 48-hour forecasts are scored per origin/target pair. No February actuals exist."}
    save_json(out / "metrics.json", report)
    print("Training final model...", flush=True)
    final = fit_bundle(history, paired, final_cutoff, config, hist_hash)
    joblib.dump(final, out / "model.joblib")
    save_json(out / "model_metadata.json", {k: v for k, v in final.items() if k != "models"})
    return report


def load_model(path):
    # joblib uses pickle: load only locally generated/trusted model artifacts.
    if not Path(path).exists():
        raise ForecastError(f"Model missing: {path}. Run train first.")
    return joblib.load(path)
