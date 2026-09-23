"""Explicit demonstration mode using current weather, never an archived run."""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import ForecastError, digest, file_hash, save_json, target_hours
from .model import load_model, predict, weather_signature

LIVE_ENDPOINT = "https://api.open-meteo.com/v1/forecast"


def fetch_live_weather(config, origin, horizon):
    expected = target_hours(origin, horizon)
    frames, raw_responses = [], []
    wind = config["weather"]["wind_variable"]
    for turbine in config["turbines"]:
        params = {"latitude": turbine["latitude"], "longitude": turbine["longitude"],
                  "models": config["weather"]["model"], "hourly": f"temperature_2m,{wind}",
                  "wind_speed_unit": "ms", "timezone": "UTC", "forecast_days": 4}
        payload = None
        error = ""
        for attempt in range(2):
            try:
                response = requests.get(LIVE_ENDPOINT, params=params, timeout=(5, 20))
                response.raise_for_status()
                payload = response.json()
                break
            except (requests.RequestException, ValueError) as exc:
                error = str(exc)
                if attempt == 0:
                    time.sleep(1)
        if payload is None:
            raise ForecastError(f"Live weather unavailable: {error}")
        units = payload.get("hourly_units", {})
        if units.get(wind) != "m/s" or units.get("temperature_2m") != "°C":
            raise ForecastError("Live weather returned unexpected units.")
        received = pd.Timestamp.now(tz="UTC").isoformat()
        try:
            hourly = payload["hourly"]
            frame = pd.DataFrame({"valid_at": pd.to_datetime(hourly["time"], utc=True),
                                  "wind_speed": hourly[wind], "temperature": hourly["temperature_2m"]})
            if frame.valid_at.duplicated().any():
                raise ForecastError("Duplicate live weather hours.")
            frame = frame.set_index("valid_at").reindex(expected).rename_axis("valid_at").reset_index()
        except (KeyError, ValueError) as exc:
            raise ForecastError(f"Invalid live weather response: {exc}") from exc
        if not np.isfinite(frame[["wind_speed", "temperature"]].to_numpy(dtype=float)).all():
            raise ForecastError("Incomplete live weather horizon; no fabricated values are allowed.")
        frame["turbine_id"] = turbine["id"]
        frame["forecast_origin"] = origin
        frame["weather_source"] = "open-meteo/live/" + config["weather"]["model"]
        # Forecast API does not report the initialization time of the stitched forecast.
        frame["weather_issued_at"] = None
        frame["weather_received_at"] = received
        frame["wind_variable"] = wind
        frame["latitude"], frame["longitude"] = turbine["latitude"], turbine["longitude"]
        frames.append(frame)
        raw_responses.append({"endpoint": LIVE_ENDPOINT, "params": params, "received_at": received, "payload": payload})
    return pd.concat(frames, ignore_index=True), raw_responses


def run_live_forecast(config, horizon=48, model_path="artifacts/model.joblib", out_dir="artifacts/live_runs"):
    started = time.perf_counter()
    origin = pd.Timestamp.now(tz="UTC").floor("s")
    events = []

    def emit(event, **fields):
        events.append({"event": event, "at": pd.Timestamp.now(tz="UTC").isoformat(), **fields})

    emit("started")
    bundle = load_model(model_path)
    if bundle["weather_signature"] != weather_signature(config):
        raise ForecastError("Live weather configuration differs from training; use matching config.")
    weather, source = fetch_live_weather(config, origin, horizon)
    emit("weather_validated", rows=len(weather))
    model_version = file_hash(model_path)
    inputs = weather[["turbine_id", "valid_at", "wind_speed", "temperature", "weather_source"]].astype(str).to_dict("records")
    # Same weather, model and target hours keep their original forecast origin.
    # A changed weather payload gets a new real origin, never a backdated one.
    input_signature = digest({"weather": inputs, "model": model_version,
                              "weather_signature": bundle["weather_signature"], "horizon": horizon})
    latest_path = Path(out_dir) / f"latest_{horizon}.json"
    previous = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else None
    previous_response = Path(out_dir) / previous["run_id"] / "response.json" if previous else None
    if previous and previous.get("input_signature") == input_signature and previous_response.exists():
        result = json.loads(previous_response.read_text(encoding="utf-8"))
        run_id = result["run_id"]
        emit("unchanged_inputs_reused", run_id=run_id)
        result.update(status="reused", trace=events, checked_at=pd.Timestamp.now(tz="UTC").isoformat(),
                      request_duration_ms=round((time.perf_counter() - started) * 1000, 2))
        save_json(latest_path, {"run_id": run_id, "checked_at": result["checked_at"], "input_signature": input_signature})
        return result
    run_id = "live-" + digest({"origin": origin.isoformat(), "input_signature": input_signature})[:16]
    folder = Path(out_dir) / run_id
    raw = predict(bundle, weather, observer=emit)
    if not np.isfinite(raw).all():
        raise ForecastError("Model returned non-finite live predictions.")
    forecast = weather.copy()
    forecast["power_pred"] = np.clip(raw, 0, 1)
    forecast["run_id"], forecast["model_version"] = run_id, model_version
    emit("forecast_formed", rows=len(forecast), horizon=horizon)
    analysis = {"run_id": run_id, "forecast_origin": origin.isoformat(), "horizon_hours": horizon,
                "created_at": pd.Timestamp.now(tz="UTC").isoformat(), "model_version": model_version,
                "model_trained_until": bundle["trained_until"], "missing_weather_values": 0,
                "clipped_prediction_count": int(((raw < 0) | (raw > 1)).sum()), "mode": "live_demo",
                "limitations": ["Demonstration with current weather; the model was trained before February 2026.",
                                "Live weather API does not expose forecast issuance time. No confidence interval."]}
    if previous and previous["run_id"] != run_id:
        previous_path = Path(out_dir) / previous["run_id"] / "forecast.csv"
        if previous_path.exists():
            old = pd.read_csv(previous_path)
            old["valid_at"] = pd.to_datetime(old.valid_at, utc=True)
            paired = forecast.merge(old[["turbine_id", "valid_at", "power_pred"]], on=["turbine_id", "valid_at"], suffixes=("", "_previous"))
            if len(paired):
                analysis.update(previous_run_id=previous["run_id"], compared_hours=len(paired),
                                mean_absolute_change=float((paired.power_pred - paired.power_pred_previous).abs().mean()))
    emit("analysis_completed", clipped=analysis["clipped_prediction_count"], rows=len(forecast))
    folder.mkdir(parents=True, exist_ok=True)
    forecast.to_csv(folder / "forecast.csv", index=False)
    save_json(folder / "analysis.json", analysis)
    save_json(folder / "source.json", source)
    emit("completed", run_id=run_id)
    save_json(folder / "trace.json", events)
    result = {"run_id": run_id, "status": "created", "mode": "live_demo", "forecast_origin": origin.isoformat(),
            "timezone": config["history_timezone"], "horizon_hours": horizon, "row_count": len(forecast),
            "forecast": json.loads(forecast.to_json(orient="records", date_format="iso")), "analysis": analysis,
            "trace": events, "request_duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "baseline": [], "baseline_note": "No current power observations are available.", "weather_connection": "online"}
    result["checked_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    save_json(folder / "response.json", result)
    save_json(latest_path, {"run_id": run_id, "checked_at": result["checked_at"], "input_signature": input_signature})
    return result
