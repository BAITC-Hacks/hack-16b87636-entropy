from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


class ForecastError(ValueError):
    """Actionable input/data error, displayed without a CLI traceback."""


def read_config(path="config.json"):
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    if not config.get("history_timezone"):
        raise ForecastError("Set history_timezone explicitly; CSV timestamps have no offset.")
    if len(config.get("turbines", [])) != 2:
        raise ForecastError("Configure exactly two turbines with distinct ids and coordinates.")
    if len({t["id"] for t in config["turbines"]}) != 2:
        raise ForecastError("Turbine ids must be unique.")
    for turbine in config["turbines"]:
        if not -90 <= turbine["latitude"] <= 90 or not -180 <= turbine["longitude"] <= 180:
            raise ForecastError("Invalid turbine coordinates.")
    if config["weather"]["publication_lag_hours"] < 6:
        raise ForecastError("Use a conservative publication lag of at least 6 hours.")
    return config


def utc(value):
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        raise ForecastError(f"Timestamp must contain a timezone offset: {value}")
    return t.tz_convert("UTC")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def origins(start, end, timezone):
    """Daily 23:00 local runs, preceding each requested target date."""
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ForecastError("Start date must not be after end date.")
    days = pd.date_range(start, end, freq="D", tz=timezone)
    return [(day - pd.Timedelta(hours=1)).tz_convert("UTC") for day in days]


def target_hours(origin, horizon):
    if horizon not in (24, 48):
        raise ForecastError("Horizon must be 24 or 48 hours.")
    return pd.date_range(utc(origin).floor("h") + pd.Timedelta(hours=1), periods=horizon, freq="h")
