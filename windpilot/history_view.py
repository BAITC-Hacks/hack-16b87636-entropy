"""Read-only views of saved forecasts and independent turbine observations.

Never calls a model, fills observation gaps or substitutes weather forecasts for
observed weather. A timestamp has at most one next-day forecast per turbine.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import HTTPException

from .common import file_hash


TIMEZONE = "Asia/Almaty"
ROOT = Path(__file__).resolve().parent.parent
KEYS = ["valid_at", "turbine_id"]
PREDICTION_COLUMNS = KEYS + ["power_pred", "forecast_origin", "model_version", "forecast_kind",
                             "wind_speed_forecast", "temperature_forecast", "weather_issued_at",
                             "weather_available_at", "weather_selection_reason"]
LABELS = {
    "january_validation": "Отложенный январь 2026",
    "retrospective": "Ретроспективная проверка августа 2025",
    "historical_replay": "Исторический расчёт февраля 2026",
}
ARCHIVE_NOTICE = (
    "Это ретроспективные расчёты по архиву Open-Meteo, а не журнал прогнозов, "
    "реально выпущенных системой в те даты. Время исторической публикации погоды "
    "не подтверждено независимо; применяется допущение доступности через 12 часов "
    "после инициализации. Для части архива IFS поставщик упоминает hindcast."
)


def _stamp(path):
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=96)
def _csv(path, mtime, size):
    return pd.read_csv(path)


def _read(path):
    return _csv(*_stamp(path)).copy()


@lru_cache(maxsize=8)
def _hash(path, mtime, size):
    return file_hash(path)


def _metadata(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _iso(timestamp):
    return None if pd.isna(timestamp) else pd.Timestamp(timestamp).tz_convert(TIMEZONE).isoformat()


def _number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def _observations(root):
    path = root / "data/processed/hourly.csv"
    columns = KEYS + ["power_actual", "wind_speed_actual", "temperature_actual"]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    frame = _read(path).rename(columns={"timestamp": "valid_at", "power_norm": "power_actual",
        "wind_speed": "wind_speed_actual", "temperature": "temperature_actual"})[columns]
    frame["valid_at"] = pd.to_datetime(frame.valid_at, utc=True)
    if frame.valid_at.isna().any() or not frame.turbine_id.isin([1, 2]).all():
        raise ValueError("Наблюдения содержат неверное время или номер турбины.")
    for column in ["power_actual", "wind_speed_actual", "temperature_actual"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if (frame[column].notna() & ~np.isfinite(frame[column])).any():
            raise ValueError("Наблюдения содержат нечисловые или бесконечные значения.")
    if (frame.power_actual.notna() & ~frame.power_actual.between(0, 1)).any():
        raise ValueError("Наблюдаемая мощность вне диапазона 0–1.")
    if (frame.wind_speed_actual < 0).any():
        raise ValueError("Наблюдаемая скорость ветра не может быть отрицательной.")
    if frame.duplicated(KEYS).any():
        raise ValueError("Найдены повторяющиеся часы наблюдений одной турбины.")
    return frame


def _prediction_frame(frame, kind, model_version=None, trained_until=None):
    frame = frame.copy().rename(columns={"prediction": "power_pred"})
    frame["valid_at"] = pd.to_datetime(frame.valid_at, utc=True)
    frame["forecast_origin"] = pd.to_datetime(frame.forecast_origin, utc=True)
    # Derive the actual lead rather than trusting a possibly stale CSV column.
    lead = (frame.valid_at - frame.forecast_origin).dt.total_seconds() / 3600
    frame = frame.loc[lead.between(1, 24) & (lead % 1 == 0)].copy()
    if trained_until and (frame.forecast_origin < pd.Timestamp(trained_until).tz_convert("UTC")).any():
        raise ValueError("Время обучения модели позже момента исторического прогноза.")
    if "model_version" not in frame:
        frame["model_version"] = model_version
    frame["forecast_kind"] = kind
    for column in ["weather_issued_at", "weather_available_at"]:
        frame[column] = (pd.to_datetime(frame[column], utc=True) if column in frame else
                         pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]"))
        if (frame[column].notna() & (frame[column] > frame.forecast_origin)).any():
            raise ValueError("Погода выпущена или доступна позже момента прогноза.")
    if "weather_selection_reason" not in frame:
        frame["weather_selection_reason"] = None
    for source, target in [("wind_speed", "wind_speed_forecast"), ("temperature", "temperature_forecast")]:
        frame[target] = pd.to_numeric(frame[source], errors="raise") if source in frame else None
    values = pd.to_numeric(frame.power_pred, errors="raise")
    if ((values.notna()) & (~np.isfinite(values) | ~values.between(0, 1))).any():
        raise ValueError("Сохранённый прогноз содержит мощность вне диапазона 0–1.")
    frame["power_pred"] = values
    return frame[PREDICTION_COLUMNS]


@lru_cache(maxsize=96)
def _prediction_csv(path, mtime, size, kind, version, trained_until):
    # Stamped absolute paths also keep isolated test/deployment roots separate.
    return _prediction_frame(_csv(path, mtime, size), kind, version, trained_until)


def _predictions(root):
    frames = []
    january = root / "artifacts/validation_predictions.csv"
    if january.exists():
        model_path = root / "artifacts/validation_model.joblib"
        version = _hash(*_stamp(model_path)) if model_path.exists() else None
        cutoff = _metadata(root / "artifacts/metrics.json").get("model_trained_until")
        frames.append(_prediction_csv(*_stamp(january), "january_validation", version, cutoff))
    august = root / "artifacts/august_2025/validation_predictions.csv"
    if august.exists():
        metadata = _metadata(august.parent / "model_metadata.json")
        version = metadata.get("model_version")
        model_path = august.parent / "validation_model.joblib"
        if version is None and model_path.exists():
            version = _hash(*_stamp(model_path))
        frames.append(_prediction_csv(*_stamp(august), "retrospective", version,
            metadata.get("model_trained_until", metadata.get("trained_until"))))
    # Packaged immutable dashboard runs include both 24h and 48h versions. Their
    # common next-day rows are deduplicated below, with no second-day fallback.
    for path in sorted((root / "artifacts/dashboard_runs").glob("*/forecast.csv")):
        metadata = _metadata(path.parent / "analysis.json")
        frames.append(_prediction_csv(*_stamp(path), "historical_replay",
            metadata.get("model_version"), metadata.get("model_trained_until")))
    if not frames:
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    result = pd.concat(frames, ignore_index=True)
    # All next-day runs sharing a key must agree. Reject accidental ambiguity
    # instead of silently choosing a different model/run for the same hour.
    for column in PREDICTION_COLUMNS[2:]:
        if (result.groupby(KEYS)[column].nunique(dropna=False) > 1).any():
            raise ValueError("Конфликт сохранённых прогнозов для одного часа и турбины.")
    return result.drop_duplicates(KEYS).sort_values(KEYS).reset_index(drop=True)


def _availability(observed, predicted):
    actual = observed.loc[observed.power_actual.notna()]
    periods = []
    if not predicted.empty:
        for kind, rows in predicted.dropna(subset=["power_pred"]).groupby("forecast_kind", sort=False):
            versions = rows.model_version.dropna().unique().tolist()
            periods.append({"start": _iso(rows.valid_at.min())[:10], "end": _iso(rows.valid_at.max())[:10],
                "forecast_kind": kind, "label": LABELS[kind],
                "model_version": versions[0] if len(versions) == 1 else None})
    return {"timezone": TIMEZONE, "max_days": 31,
        "observed_start": _iso(actual.valid_at.min()), "observed_end": _iso(actual.valid_at.max()),
        "observed_by_turbine": [{"turbine_id": tid,
            "start": _iso(actual.loc[actual.turbine_id == tid, "valid_at"].min()),
            "end": _iso(actual.loc[actual.turbine_id == tid, "valid_at"].max())} for tid in (1, 2)],
        "forecast_periods": sorted(periods, key=lambda period: period["start"])}


def history_availability(root=ROOT):
    root = Path(root)
    return _availability(_observations(root), _predictions(root))


def history_data(start: date, end: date, root=ROOT):
    if end < start or (end - start).days >= 31:
        raise ValueError("Выберите от 1 до 31 дня включительно; начало не должно быть позже конца.")
    root = Path(root)
    observed, predicted = _observations(root), _predictions(root)
    availability = _availability(observed, predicted)
    first = pd.Timestamp(start).tz_localize(TIMEZONE)
    stop = pd.Timestamp(end + timedelta(days=1)).tz_localize(TIMEZONE)
    hours = pd.date_range(first, stop, freq="h", inclusive="left").tz_convert("UTC")
    grid = pd.MultiIndex.from_product([hours, (1, 2)], names=KEYS).to_frame(index=False)
    for frame in (observed, predicted):
        if frame.empty:
            for column in frame.columns.difference(KEYS):
                grid[column] = None
        else:
            grid = grid.merge(frame, on=KEYS, how="left", validate="one_to_one")
    rows = []
    for row in grid.itertuples(index=False):
        rows.append({"valid_at": _iso(row.valid_at), "turbine_id": int(row.turbine_id),
            "power_actual": _number(row.power_actual), "power_pred": _number(row.power_pred),
            "wind_speed_actual": _number(row.wind_speed_actual), "temperature_actual": _number(row.temperature_actual),
            "wind_speed_forecast": _number(row.wind_speed_forecast), "temperature_forecast": _number(row.temperature_forecast),
            "forecast_origin": _iso(row.forecast_origin),
            "weather_issued_at": _iso(row.weather_issued_at), "weather_available_at": _iso(row.weather_available_at),
            "weather_selection_reason": None if pd.isna(row.weather_selection_reason) else str(row.weather_selection_reason),
            "model_version": None if pd.isna(row.model_version) else str(row.model_version),
            "forecast_kind": None if pd.isna(row.forecast_kind) else str(row.forecast_kind)})
    actual = sum(row["power_actual"] is not None for row in rows)
    predictions = sum(row["power_pred"] is not None for row in rows)
    paired = sum(row["power_actual"] is not None and row["power_pred"] is not None for row in rows)
    notices = [{"code": "next_day_selection", "message": "Для каждого часа показан один прогноз: горизонт 1–24 часа от предыдущего вечернего расчёта. Мощность нормализована (0–1)."}]
    if actual < len(rows):
        message = ("Фактические данные за выбранный период не загружены." if not actual else
                   "В фактических данных есть пропуски: они оставлены пустыми, без интерполяции.")
        notices.append({"code": "missing_actual", "message": message})
    if predictions < len(rows):
        notices.append({"code": "missing_prediction", "message":
            "Для части или всех выбранных часов сохранённых прогнозов нет. Новые прогнозы задним числом автоматически не создаются."})
    if predictions:
        notices.append({"code": "archive_provenance", "message": ARCHIVE_NOTICE})
    if any(row["forecast_kind"] == "retrospective" for row in rows):
        notices.append({"code": "retrospective", "message": "Август 2025 — отдельная ретроспективная проверка моделью, обученной до августа. Основная модель и январский сплит не изменены."})
    older_origins = {row["forecast_origin"] for row in rows
        if row["weather_selection_reason"] == "older_cycle_after_nominal_archive_unavailable"}
    if older_origins:
        notices.append({"code": "older_weather", "message":
            f"Для {len(older_origins)} моментов расчёта выбран более ранний выпуск погоды: основной выпуск в архиве отсутствует или неполон. "
            "Сдвиг выпуска ограничен 24 часами; погода доступна до момента расчёта по принятому допущению. Время выбранного выпуска сохранено в строках и CSV."})
    august_metadata = _metadata(root / "artifacts/august_2025/model_metadata.json")
    missing_days = sorted({(pd.Timestamp(origin).tz_convert(TIMEZONE) + pd.Timedelta(hours=1)).date()
        for origin in august_metadata.get("weather_missing_origins", [])})
    missing_days = [day.isoformat() for day in missing_days if start <= day <= end]
    if missing_days:
        notices.append({"code": "weather_archive_gaps", "message":
            "Прогноз отсутствует на даты " + ", ".join(missing_days) +
            ": архив погоды отсутствует или содержит пропуски; проверка более ранних выпусков в пределах 24 часов не дала полного входа. Фактические измерения показаны независимо."})
    return {**availability, "start": start.isoformat(), "end": end.isoformat(), "rows": rows,
        "counts": {"actual": actual, "predicted": predictions, "paired": paired,
            "missing_actual": len(rows) - actual, "missing_prediction": len(rows) - predictions},
        "notices": notices}


def install_history_routes(app, root=ROOT):
    @app.get("/history/availability")
    def availability():
        try:
            return history_availability(root)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=503, detail="Архив истории недоступен или повреждён.") from exc

    @app.get("/history")
    def history(start: date, end: date):
        if end < start or (end - start).days >= 31:
            raise HTTPException(status_code=422, detail="Выберите от 1 до 31 дня включительно; начало не должно быть позже конца.")
        try:
            return history_data(start, end, root)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=503, detail="Архив истории недоступен или повреждён.") from exc
