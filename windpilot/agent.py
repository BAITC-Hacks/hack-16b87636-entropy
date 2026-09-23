from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ForecastError, digest, file_hash, save_json, utc
from .model import load_model, predict, weather_signature
from .weather import WeatherClient

PIPELINE_VERSION = 1


def append_event(out, event, **fields):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    record = {"at": pd.Timestamp.now(tz="UTC").isoformat(), "event": event, **fields}
    with (out / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def run_forecast(config, origin, horizon=48, model_path="artifacts/model.joblib", out_dir="artifacts/runs",
                 offline=False, refresh=False):
    out = Path(out_dir)
    origin = utc(origin)
    append_event(out, "started", origin=str(origin), horizon=horizon)
    try:
        bundle = load_model(model_path)
        if utc(bundle["trained_until"]) > origin:
            raise ForecastError("Model trained after requested forecast origin. Use validation_model for January.")
        if bundle["weather_signature"] != weather_signature(config):
            raise ForecastError("Config differs from model training config. Retrain after changing timezone/weather/coordinates.")
        weather = WeatherClient(config, offline=offline, refresh=refresh).for_origin(origin, horizon)
        append_event(out, "weather_validated", rows=len(weather), origin=str(origin))
        model_version = file_hash(model_path)
        input_columns = ["turbine_id", "forecast_origin", "valid_at", "wind_speed", "temperature",
                         "weather_issued_at", "weather_available_at", "weather_source", "wind_variable", "latitude", "longitude"]
        key = digest({"model": model_version, "weather": weather[input_columns].astype(str).to_dict("records"),
                      "horizon": horizon, "feature_version": bundle["feature_version"], "pipeline_version": PIPELINE_VERSION})
        run_id = key[:20]
        folder = out / run_id
        if (folder / "analysis.json").exists() and (folder / "forecast.csv").exists():
            append_event(out, "unchanged_inputs_reused", run_id=run_id)
            return {"status": "reused", "run_id": run_id, "path": str(folder)}
        append_event(out, "model_started", run_id=run_id)
        raw_prediction = predict(bundle, weather, observer=lambda event, **fields: append_event(out, event, run_id=run_id, **fields))
        if not np.isfinite(raw_prediction).all():
            raise ForecastError("Model produced non-finite predictions.")
        clipped_count = int(((raw_prediction < 0) | (raw_prediction > 1)).sum())
        forecast = weather.copy()
        forecast["power_pred"] = np.clip(raw_prediction, 0, 1)
        forecast["run_id"], forecast["model_version"] = run_id, model_version
        forecast["lead_hours"] = (forecast.valid_at - origin).dt.total_seconds() / 3600
        stats = {}
        for tid, part in forecast.groupby("turbine_id"):
            stats[int(tid)] = {"hours": len(part), "mean": float(part.power_pred.mean()),
                               "min": float(part.power_pred.min()), "max": float(part.power_pred.max())}
        analysis = {"run_id": run_id, "pipeline_version": PIPELINE_VERSION, "forecast_origin": origin.isoformat(), "horizon_hours": horizon,
                    "created_at": pd.Timestamp.now(tz="UTC").isoformat(), "model_version": model_version,
                    "model_trained_until": bundle["trained_until"], "missing_weather_values": 0,
                    "clipped_prediction_count": clipped_count, "turbines": stats,
                    "availability_basis": config["weather"]["availability_basis"],
                    "archive_provenance_status": "Provider archive; original operational publication not independently verified.",
                    "limitations": ["Publication time uses a conservative lag, not verified historical ingestion timestamps.",
                                    "Provider documentation mentions hindcasts for part of IFS history; confirm eligibility with organizers.",
                                    "Normalized power, not MW or MWh. February actuals are unavailable.",
                                    "Explicit workflow agent; no LLM reasoning or turbine control."]}
        # Compare the most recently completed run at the same origin and horizon.
        prior = sorted(out.glob("*/analysis.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in prior:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old["forecast_origin"] == origin.isoformat() and old["horizon_hours"] == horizon:
                old_frame = pd.read_csv(path.parent / "forecast.csv")
                old_frame["valid_at"] = pd.to_datetime(old_frame.valid_at, utc=True)
                merged = forecast.merge(old_frame[["turbine_id", "valid_at", "power_pred"]], on=["turbine_id", "valid_at"], suffixes=("", "_previous"))
                analysis["previous_run_id"] = old["run_id"]
                analysis["mean_absolute_change"] = float((merged.power_pred - merged.power_pred_previous).abs().mean())
                break
        append_event(out, "analysis_completed", run_id=run_id, clipped=clipped_count)
        folder.mkdir(parents=True, exist_ok=True)
        forecast.to_csv(folder / "forecast.csv", index=False)
        weather.to_csv(folder / "weather.csv", index=False)
        save_json(folder / "analysis.json", analysis)
        append_event(out, "completed", run_id=run_id, clipped=clipped_count, rows=len(forecast))
        return {"status": "created", "run_id": run_id, "path": str(folder), "analysis": analysis}
    except Exception as exc:
        append_event(out, "failed", origin=str(origin), error=str(exc))
        raise
