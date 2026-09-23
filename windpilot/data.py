from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .common import ForecastError, file_hash, save_json

COLUMNS = {
    "Статистическое время": "timestamp",
    "Средняя скорость ветра(m/s)": "wind_speed",
    "Нормализованная активная мощность": "power_norm",
    "Средняя температура окружающей среды(°C)": "temperature",
}


def prepare_one(path, turbine_id, timezone):
    raw = pd.read_csv(path)
    missing = set(COLUMNS) - set(raw.columns)
    if missing:
        raise ForecastError(f"{path}: missing columns {sorted(missing)}")
    df = raw.rename(columns=COLUMNS)[list(COLUMNS.values())].copy()
    dates = pd.to_datetime(df.timestamp, errors="coerce")
    # Ambiguous/nonexistent wall times are excluded rather than guessed (2024 timezone change).
    localized = dates.dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
    df["timestamp"] = localized.dt.tz_convert("UTC")
    for col in ["wind_speed", "power_norm", "temperature"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    invalid_time = df.timestamp.isna()
    off_grid = ~invalid_time & ((df.timestamp.dt.minute % 10 != 0) | (df.timestamp.dt.second != 0))
    duplicate = df.timestamp.duplicated(keep=False) & ~invalid_time
    invalid_value = (~np.isfinite(df[["wind_speed", "temperature", "power_norm"]]).all(axis=1)
                     | (df.wind_speed < 0) | ~df.power_norm.between(0, 1))
    rejected = invalid_time | off_grid | duplicate | invalid_value
    valid_times = df.loc[~invalid_time & ~off_grid, "timestamp"]
    if valid_times.empty:
        raise ForecastError(f"No valid timestamps in {path}")
    grid = pd.date_range(valid_times.min().floor("h"), valid_times.max().floor("h") + pd.Timedelta(minutes=50), freq="10min")
    clean = df.loc[~rejected].set_index("timestamp").sort_index()
    if clean.empty:
        raise ForecastError(f"No valid observations in {path}")
    hourly = clean.resample("h").agg(wind_speed=("wind_speed", "mean"), temperature=("temperature", "mean"),
                                      power_norm=("power_norm", "mean"), samples_count=("power_norm", "count"))
    full_hours = pd.date_range(grid.min().floor("h"), grid.max().floor("h"), freq="h")
    hourly = hourly.reindex(full_hours)
    hourly.index.name = "timestamp"
    hourly["samples_count"] = hourly.samples_count.fillna(0).astype(int)
    report = {
        "turbine_id": turbine_id, "source_file": Path(path).name, "sha256": file_hash(path),
        "raw_rows": len(raw), "raw_start": str(dates.min()), "raw_end": str(dates.max()),
        "invalid_or_ambiguous_timestamps": int(invalid_time.sum()), "off_grid_rows": int(off_grid.sum()),
        "duplicate_rows_all_excluded": int(duplicate.sum()), "invalid_numeric_rows": int(invalid_value.sum()),
        "rejected_rows_union": int(rejected.sum()), "missing_10_minute_intervals": len(grid.difference(valid_times)),
        "total_hours": len(hourly), "complete_hours": int((hourly.samples_count == 6).sum()),
        "excluded_incomplete_hours": int((hourly.samples_count != 6).sum()), "timezone": timezone,
    }
    hourly = hourly.loc[hourly.samples_count == 6].reset_index()
    hourly["turbine_id"] = turbine_id
    return hourly, report


def prepare(paths, config, out_dir="data/processed"):
    parts, reports = [], []
    for turbine, path in zip(config["turbines"], paths, strict=True):
        frame, report = prepare_one(path, turbine["id"], config["history_timezone"])
        parts.append(frame)
        reports.append(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.concat(parts, ignore_index=True).to_csv(out / "hourly.csv", index=False)
    save_json(out / "quality.json", {"turbines": reports, "timezone_basis": config.get("timezone_basis")})
    return reports


def load_history(path):
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df.timestamp, utc=True)
    if df.duplicated(["turbine_id", "timestamp"]).any():
        raise ForecastError("Prepared history contains duplicate turbine/hour keys.")
    return df.sort_values(["turbine_id", "timestamp"])
