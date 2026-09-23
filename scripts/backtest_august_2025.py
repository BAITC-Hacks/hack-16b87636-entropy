"""Reproduce an independent August backtest without touching the January split.

Run from the repository root:
    python scripts/backtest_august_2025.py --fetch-weather
Once archived runs are cached the same command works offline without the flag.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd

from windpilot.common import ForecastError, file_hash, origins, read_config, save_json
from windpilot.data import load_history
from windpilot.model import evaluate, fit_bundle, predict, supervised
from windpilot.weather import WeatherClient, fetch_range


class FixedArchivedCycle(WeatherClient):
    """A script-local older-cycle fallback; production WeatherClient is unchanged."""

    def __init__(self, config, issue, offline):
        super().__init__(config, offline=offline)
        self.issue = issue

    def issue_for(self, origin):
        nominal = super().issue_for(origin)
        if not nominal - pd.Timedelta(hours=24) <= self.issue <= nominal:
            raise ForecastError("Retrospective fallback must be at most 24 hours older, never newer.")
        return self.issue


def august_weather(config, online=False):
    client = WeatherClient(config, offline=not online)
    frames, selections = [], []
    for origin in origins("2025-08-01", "2025-08-31", config["history_timezone"]):
        requested = client.issue_for(origin)
        attempts = []
        for older_hours in (0, 12, 24):
            selected = requested - pd.Timedelta(hours=older_hours)
            try:
                frame = FixedArchivedCycle(config, selected, offline=not online).for_origin(origin, 48)
                assert (frame.weather_issued_at == selected).all()
                assert (frame.weather_available_at <= origin).all()
                reason = ("nominal_cycle" if older_hours == 0 else "older_cycle_after_nominal_archive_unavailable")
                frame["requested_weather_issued_at"] = requested
                frame["weather_selection_reason"] = reason
                frame["weather_cycle_fallback_hours"] = older_hours
                frames.append(frame)
                selections.append({"forecast_origin": origin.isoformat(), "requested_issue": requested.isoformat(),
                                   "selected_issue": selected.isoformat(), "fallback_hours": older_hours,
                                   "source_age_hours": (origin - selected).total_seconds() / 3600,
                                   "reason": reason, "failed_attempts": attempts})
                break
            except ForecastError as exc:
                attempts.append({"issue": selected.isoformat(), "error": str(exc)})
        else:
            selections.append({"forecast_origin": origin.isoformat(), "requested_issue": requested.isoformat(),
                               "selected_issue": None, "reason": "archive_unavailable_within_24h_fallback",
                               "failed_attempts": attempts})
        print(f"August weather {origin.date()}: {selections[-1]['reason']}", flush=True)
    if not frames:
        raise ForecastError("No archived August forecasts are available; no predictions can be produced.")
    return pd.concat(frames, ignore_index=True), selections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--history", default="data/processed/hourly.csv")
    parser.add_argument("--out", default="artifacts/august_2025")
    parser.add_argument("--fetch-weather", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    config = read_config(args.config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    protected_paths = [Path(p) for p in (
        "artifacts/model.joblib", "artifacts/validation_model.joblib",
        "artifacts/validation_predictions.csv", "artifacts/metrics.json",
    )]
    planned_outputs = {(out / name).resolve() for name in ("model.joblib", "validation_predictions.csv", "metrics.json")}
    if planned_outputs.intersection(p.resolve() for p in protected_paths):
        raise ForecastError("Choose a separate August output directory; production and January artifacts are protected.")
    protected_before = {str(p): file_hash(p) for p in protected_paths if p.exists()}
    history = load_history(args.history)
    tz = config["history_timezone"]
    start = pd.Timestamp("2025-08-01", tz=tz).tz_convert("UTC")
    end = pd.Timestamp("2025-09-01", tz=tz).tz_convert("UTC")
    cutoff = start - pd.Timedelta(hours=1)
    weather_start = "2025-05-01"
    if args.fetch_weather:
        failures = fetch_range(WeatherClient(config), origins(weather_start, "2025-07-31", tz), args.workers)
        save_json(out / "weather_fetch_report.json", {"failures": failures, "start": weather_start, "end": "2025-07-31"})
        if failures:
            raise ForecastError(f"Missing {len(failures)} archived runs; see {out}/weather_fetch_report.json. Rerun to retry only missing cache entries.")
    pairs, unmatched = supervised(history, config, weather_start, "2025-07-31")
    # Physically exclude all future observations and labels before fit_bundle.
    # A target hour must have finished by the origin to be known at training time.
    observed_train = history.loc[history.timestamp + pd.Timedelta(hours=1) <= cutoff].copy()
    forecast_train = pairs.loc[(pairs.valid_at + pd.Timedelta(hours=1) <= cutoff)
                               & (pairs.forecast_origin < cutoff)].copy()
    if observed_train.empty or forecast_train.empty:
        raise ForecastError("Training data is empty.")
    assert observed_train.timestamp.max() + pd.Timedelta(hours=1) <= cutoff
    assert forecast_train.valid_at.max() + pd.Timedelta(hours=1) <= cutoff
    assert forecast_train.forecast_origin.max() < cutoff
    assert (forecast_train.weather_available_at <= forecast_train.forecast_origin).all()
    print("Training an independent pre-August model; January artifacts are protected...", flush=True)
    bundle = fit_bundle(observed_train, forecast_train, cutoff, config, file_hash(args.history))
    # Forecasts must not disappear when a target measurement is missing. The
    # existing evaluate() path is retained for metrics on observed labels only.
    weather, selections = august_weather(config, online=args.fetch_weather)
    missing_origins = [item["forecast_origin"] for item in selections if item["selected_issue"] is None]
    save_json(out / "weather_selections.json", selections)
    weather = weather.loc[(weather.valid_at >= start) & (weather.valid_at < end)].copy()
    targets = history[["turbine_id", "timestamp", "power_norm"]].rename(columns={"timestamp": "valid_at"})
    predictions = weather.merge(targets, on=["turbine_id", "valid_at"], how="left", validate="many_to_one")
    test = predictions.dropna(subset=["power_norm"]).copy()
    if test.empty:
        raise ForecastError("No August forecasts have matching observed labels.")
    assert (test.weather_available_at <= test.forecast_origin).all()
    assert test.forecast_origin.min() >= cutoff
    matched_predictions, metrics = evaluate(bundle, test, history)
    predictions["prediction"] = np.clip(predict(bundle, predictions), 0, 1)
    predictions["persistence"] = np.nan
    for (tid, origin), group in predictions.groupby(["turbine_id", "forecast_origin"]):
        prior = history.loc[(history.turbine_id == tid)
                            & (history.timestamp + pd.Timedelta(hours=1) <= origin)]
        if not prior.empty:
            last = prior.iloc[-1]
            if origin - (last.timestamp + pd.Timedelta(hours=1)) <= pd.Timedelta(hours=3):
                predictions.loc[group.index, "persistence"] = last.power_norm
    predictions["lead_hours"] = (predictions.valid_at - predictions.forecast_origin).dt.total_seconds() / 3600
    checked = predictions.merge(matched_predictions, on=["turbine_id", "forecast_origin", "valid_at"],
                                how="inner", suffixes=("_full", "_matched"), validate="one_to_one")
    assert np.array_equal(checked.prediction_full, checked.prediction_matched)
    assert len(predictions.loc[predictions.lead_hours <= 24]) == (31 - len(missing_origins)) * 24 * len(config["turbines"])
    assert predictions.prediction.between(0, 1).all()
    assert np.isfinite(predictions.prediction).all()
    assert not predictions.duplicated(["turbine_id", "forecast_origin", "valid_at"]).any()
    model_path = out / "model.joblib"
    joblib.dump(bundle, model_path)
    model_sha256 = file_hash(model_path)
    model_version = model_sha256[:12]
    predictions.to_csv(out / "validation_predictions.csv", index=False)
    caveat = (
        "Retrospective simulation created after August 2025, not a forecast actually issued then. "
        "Provider archived model runs; original operational publication is not independently verified. "
        "Availability assumes initialization + 12 hours. The January validation and production models are unchanged."
    )
    metadata = {k: v for k, v in bundle.items() if k != "models"}
    metadata.update({
        "model_version": model_version, "model_sha256": model_sha256,
        "evaluation_kind": "retrospective_backtest",
        "target_period_start": start.isoformat(), "target_period_end_exclusive": end.isoformat(),
        "residual_weather_target_start": weather_start,
        "observed_train_last_hour": observed_train.timestamp.max().isoformat(),
        "residual_train_last_target_hour": forecast_train.valid_at.max().isoformat(),
        "residual_train_last_origin": forecast_train.forecast_origin.max().isoformat(),
        "availability_assumption": config["weather"]["availability_basis"], "caveat": caveat,
        "weather_selection_policy": "Nominal cycle, otherwise a validated cycle 12 or 24 hours older; never a newer cycle.",
        "weather_missing_origins": missing_origins,
        "weather_fallback_origins": [item for item in selections if item.get("fallback_hours", 0) > 0],
    })
    save_json(out / "model_metadata.json", metadata)
    report = {"evaluation_kind": "retrospective_backtest", "caveat": caveat,
              "validation_start": start.isoformat(), "validation_end_exclusive": end.isoformat(),
              "model_trained_until": cutoff.isoformat(), "model_version": model_version,
              "metrics_by_turbine": metrics, "unmatched_weather_target_pairs": unmatched,
              "training_counts": bundle["training_counts"],
              "total_origin_target_pairs": len(predictions),
              "pairs_without_actual": int(predictions.power_norm.isna().sum()),
              "weather_missing_origins": missing_origins,
              "weather_selections": selections,
              "ui_selection": "lead_hours 1 through 24 only: one day-ahead prediction per turbine and target hour",
              "protected_january_artifacts_sha256": protected_before}
    protected_after = {str(p): file_hash(p) for p in protected_paths if p.exists()}
    if protected_before != protected_after:
        raise ForecastError("Protected January/production artifacts changed during August evaluation.")
    report["protected_january_artifacts_unchanged"] = True
    save_json(out / "metrics.json", report)
    lines = ["# Ретроспективная проверка за август 2025", "",
             "Это восстановленный эксперимент, а не прогноз, фактически выданный в августе 2025 года.", "",
             "Отдельная модель обучена только на полностью завершённых часах до 31 июля 2025, 23:00 Asia/Almaty. "
             "Архитектура совпадает с основным пайплайном: наблюдаемая кривая мощности и поправка по архивной погоде, "
             "два HistGradientBoostingRegressor на каждую турбину. Основные модели и январский сплит не изменены.", "",
             "Погодные пары для обучения поправки: май–июль 2025. Архивные прогоны Open-Meteo/ECMWF IFS "
             "выбираются для каждого момента прогноза с допущением о задержке доступности 12 часов. "
             "Фактическое время исторической публикации независимо не подтверждено; архив может содержать hindcast.", "",
             "В интерфейсе используется горизонт 1–24 часа: один прогноз на каждый час. "
             "В CSV сохранён также второй день (25–48 часов), если прогнозируемый час относится к августу.", "",
             "| Турбина | Часов 1–24 с фактом | MAE модели | RMSE модели | MAE last known value |", "|---|---:|---:|---:|---:|"]
    for tid, values in metrics.items():
        day = values["hours_1_24"]
        m = day["model"]
        b = day.get("persistence_on_same_pairs", {})
        lines.append(f"| {tid} | {m['n']} | {m['mae']:.4f} | {m['rmse']:.4f} | {b.get('mae', float('nan')):.4f} |")
    lines += ["", "Доступность архивной погоды: сначала запрашивается исходный цикл; при его отсутствии "
              "или неполных значениях допускается только цикл на 12 или 24 часа старше. "
              "Более свежие циклы не используются. Если все три варианта недоступны, прогноз остаётся отсутствующим.", "",
              "| Первый день прогноза (местное время) | Выбранный цикл (UTC) | Причина |", "|---|---|---|"]
    for item in selections:
        if item["reason"] == "nominal_cycle":
            continue
        target_day = (pd.Timestamp(item["forecast_origin"]).tz_convert(tz) + pd.Timedelta(hours=1)).date()
        reason = (f"Цикл на {item['fallback_hours']} ч старше" if item["selected_issue"] else "Архивная погода недоступна; прогноз отсутствует")
        lines.append(f"| {target_day} | {item['selected_issue'] or '—'} | {reason} |")
    lines += ["", "Мощность нормализована в диапазоне 0–1. Пропущенные фактические часы не заполнялись нулями.", "",
              "Проверки: завершение всех обучающих часов до первого origin; погода доступна по принятому допущению "
              "не позже origin; диапазон прогноза 0–1; отсутствие повторов turbine/origin/target; SHA-256 основных "
              "моделей, январских предсказаний и метрик не изменился.", "",
              "Воспроизведение: `python scripts/backtest_august_2025.py --fetch-weather` (повторный запуск использует кэш).", ""]
    report_path = ROOT / "reports/august_2025.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {len(predictions)} origin/target predictions to {out}; model version {model_version}", flush=True)
    for tid, values in metrics.items():
        print(f"Turbine {tid}: {values['hours_1_24']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
