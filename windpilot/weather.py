from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import ForecastError, digest, save_json, target_hours, utc

ENDPOINT = "https://single-runs-api.open-meteo.com/v1/forecast"


class WeatherClient:
    def __init__(self, config, offline=False, refresh=False):
        self.config = config
        self.settings = config["weather"]
        self.cache = Path(self.settings["cache_dir"])
        self.offline = offline
        self.refresh = refresh

    def issue_for(self, origin):
        # Use only 00/12 UTC cycles and a conservative documented publication lag.
        return (utc(origin) - pd.Timedelta(hours=self.settings["publication_lag_hours"])).floor("12h")

    def fetch(self, turbine, issued_at):
        params = {"latitude": turbine["latitude"], "longitude": turbine["longitude"],
                  "models": self.settings["model"], "run": utc(issued_at).strftime("%Y-%m-%dT%H:%M"),
                  "hourly": f"temperature_2m,{self.settings['wind_variable']}",
                  "wind_speed_unit": "ms", "timezone": "UTC", "forecast_days": 5}
        key = digest({"endpoint": ENDPOINT, "params": params})
        path = self.cache / f"{key}.json"
        if path.exists() and (self.offline or not self.refresh):
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached["params"] != params or cached["payload_hash"] != digest(cached["payload"]):
                    raise ForecastError(f"Weather cache integrity error: {path}")
                return cached
            except (KeyError, json.JSONDecodeError) as exc:
                raise ForecastError(f"Invalid weather cache: {path}") from exc
        if self.offline:
            raise ForecastError(f"Weather cache missing for turbine {turbine['id']}, run {issued_at}. Run fetch-weather online first.")
        error = ""
        for attempt in range(3):
            try:
                response = requests.get(ENDPOINT, params=params, timeout=(10, 40))
                if response.status_code in (429, 500, 502, 503, 504):
                    error = f"HTTP {response.status_code}"
                    time.sleep(2 ** attempt)
                    continue
                if response.status_code != 200:
                    raise ForecastError(f"Weather API HTTP {response.status_code}: {response.text[:300]}")
                payload = response.json()
                if payload.get("error") or "hourly" not in payload:
                    raise ForecastError(f"Invalid weather response: {str(payload)[:300]}")
                cached = {"endpoint": ENDPOINT, "params": params, "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(),
                          "payload_hash": digest(payload), "payload": payload}
                save_json(path, cached)
                return cached
            except (requests.RequestException, ValueError) as exc:
                if isinstance(exc, ForecastError):
                    raise
                error = str(exc)
                time.sleep(2 ** attempt)
        if path.exists():
            # Only this exact historical run is eligible; preserve its original provenance.
            return WeatherClient(self.config, offline=True).fetch(turbine, issued_at)
        raise ForecastError(f"Cannot fetch weather after 3 attempts: {error}")

    def for_origin(self, origin, horizon=48):
        origin = utc(origin)
        expected_hours = target_hours(origin, horizon)
        issued_at = self.issue_for(origin)
        available_at = issued_at + pd.Timedelta(hours=self.settings["publication_lag_hours"])
        frames = []
        for turbine in self.config["turbines"]:
            cached = self.fetch(turbine, issued_at)
            payload = cached["payload"]
            wind = self.settings["wind_variable"]
            units = payload.get("hourly_units", {})
            if units.get(wind) != "m/s" or units.get("temperature_2m") != "°C":
                raise ForecastError("Unexpected weather units; expected m/s and Celsius.")
            h = payload["hourly"]
            frame = pd.DataFrame({"valid_at": pd.to_datetime(h["time"], utc=True),
                                  "wind_speed": h[wind], "temperature": h["temperature_2m"]})
            if frame.valid_at.duplicated().any():
                raise ForecastError("Duplicate weather timestamps.")
            frame = frame.set_index("valid_at").reindex(expected_hours).rename_axis("valid_at").reset_index()
            frame["turbine_id"] = turbine["id"]
            frame["forecast_origin"] = origin
            frame["weather_issued_at"] = issued_at
            frame["weather_available_at"] = available_at
            frame["availability_basis"] = self.settings["availability_basis"]
            frame["weather_source"] = "open-meteo/single-runs/" + self.settings["model"]
            frame["wind_variable"] = wind
            frame["latitude"], frame["longitude"] = turbine["latitude"], turbine["longitude"]
            frame["grid_latitude"], frame["grid_longitude"] = payload["latitude"], payload["longitude"]
            frame["weather_payload_hash"] = cached["payload_hash"]
            frame["wind_unit"], frame["temperature_unit"] = "m/s", "degC"
            frames.append(frame)
        result = pd.concat(frames, ignore_index=True)
        validate_weather(result, origin, horizon, [t["id"] for t in self.config["turbines"]])
        return result


def validate_weather(frame, origin, horizon, turbine_ids):
    expected = target_hours(origin, horizon)
    if set(frame.turbine_id) != set(turbine_ids):
        raise ForecastError("Weather does not cover exactly the configured turbines.")
    for turbine_id in turbine_ids:
        part = frame.loc[frame.turbine_id == turbine_id]
        if len(part) != horizon or set(part.valid_at) != set(expected):
            raise ForecastError(f"Expected {horizon} unique weather hours for turbine {turbine_id}.")
    if not np.isfinite(frame[["wind_speed", "temperature"]].to_numpy(dtype=float)).all():
        raise ForecastError("Missing/non-finite weather values; no zero filling is allowed.")
    if (frame.wind_speed < 0).any():
        raise ForecastError("Negative forecast wind speed.")
    if (frame.weather_available_at > utc(origin)).any() or (frame.weather_issued_at > frame.weather_available_at).any():
        raise ForecastError("Weather publication occurs after forecast origin (future leakage).")


def fetch_range(client, run_origins, workers=4):
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(client.for_origin, origin, 48): origin for origin in run_origins}
        for n, future in enumerate(as_completed(pending), 1):
            try:
                future.result()
            except Exception as exc:
                failures.append({"origin": str(pending[future]), "error": str(exc)})
            if n % 10 == 0 or n == len(pending):
                print(f"Weather: {n}/{len(pending)} origins, failures={len(failures)}", flush=True)
    return failures
