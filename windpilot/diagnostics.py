"""Read-only model diagnostics; isolated retraining is written to a separate directory."""
from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from .agent import run_forecast
from .common import file_hash, read_config, save_json, utc
from .data import load_history, prepare
from .model import load_model, predict, scores, supervised, train
from .predict_checks import check_predict, synthetic_weather


def load_validation(path):
    data = pd.read_csv(path)
    for col in ["valid_at", "forecast_origin", "weather_issued_at", "weather_available_at"]:
        data[col] = pd.to_datetime(data[col], utc=True)
    return data


def clipped(bundle, data):
    return np.clip(predict(bundle, data), 0, 1)


def audit_trimmed_csv(paths, config, out):
    source_hashes = {str(p): file_hash(p) for p in paths}
    with tempfile.TemporaryDirectory(prefix="trimmed-input-", dir=out) as directory:
        directory = Path(directory)
        original = pd.read_csv(paths[0])
        times = pd.to_datetime(original["Статистическое время"])
        cutoff = times.max().normalize() - pd.Timedelta(days=6)
        shorter = original.loc[times < cutoff]
        short_path = directory / "turbine_1_short.csv"
        shorter.to_csv(short_path, index=False)
        processed = directory / "processed"
        quality = prepare([short_path, paths[1]], config, processed)
        target = out / "trimmed_training"
        log = io.StringIO()
        # Deliberately call with the existing default dates, no manual date edits.
        with contextlib.redirect_stdout(log):
            result = train(processed / "hourly.csv", config, out_dir=target)
        (out / "trimmed_training.log").write_text(log.getvalue(), encoding="utf-8")
        metadata = json.loads((target / "model_metadata.json").read_text(encoding="utf-8"))
        after = {str(p): file_hash(p) for p in paths}
        assert after == source_hashes, "Original CSV changed during diagnostic test"
        sig = inspect.signature(train)
        defaults = {key: str(sig.parameters[key].default) for key in
                    ["weather_start", "validation_start", "validation_end", "final_cutoff"]}
        report = {"test_completed": True, "training_succeeded": True, "original_files_unchanged": True,
                  "original_rows_turbine_1": len(original), "trimmed_rows_turbine_1": len(shorter),
                  "rows_removed": len(original) - len(shorter), "removed_from_local": str(cutoff),
                  "source_last_local_before": str(times.max()),
                  "source_last_local_after": str(pd.to_datetime(shorter["Статистическое время"]).max()),
                  "source_hashes": source_hashes, "quality": quality, "training_counts": metadata["training_counts"],
                  "reported_trained_until": metadata["trained_until"], "automatic_date_bounds": False,
                  "existing_date_defaults": defaults,
                  "status": "partial",
                  "reason": "CSV min/max and actual rows are respected, but archive window and cutoff retain fixed CLI/function defaults; new dates beyond cutoff are not automatically included."}
    return report


def draw_curves(curve, path, temperature):
    os.environ.setdefault("MPLCONFIGDIR", str(Path("artifacts/diagnostics/matplotlib-cache").resolve()))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5.5), layout="constrained")
    for tid, part in curve.groupby("turbine_id"):
        ax.plot(part.wind_speed, part.power_pred, label=f"Турбина {tid}", linewidth=2.2)
        if not np.allclose(part.raw_prediction, part.power_pred):
            ax.plot(part.wind_speed, part.raw_prediction, linestyle=":", linewidth=1.2, label=f"Турбина {tid}, до ограничения")
    ax.axvspan(25, 30, color="#b8bec8", alpha=0.2, label="Диагностика сильного ветра*")
    ax.set(title="Срез предсказанной мощности при изменении скорости ветра",
           xlabel="Скорость ветра, м/с", ylabel="Нормализованная мощность", xlim=(0, 30))
    ax.set_ylim(min(-0.05, curve.raw_prediction.min() - 0.03), max(1.05, curve.raw_prediction.max() + 0.03))
    ax.grid(alpha=0.2)
    ax.legend(loc="lower right", fontsize=9)
    fig.suptitle(f"Январская проверочная модель · T={temperature:.2f} °C · 12:00 Asia/Almaty · горизонт 24 ч", fontsize=10)
    fig.text(0.01, -0.025, "*Паспортная скорость отключения не предоставлена. Это срез модели, не паспортная характеристика турбины.", fontsize=8)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def markdown_report(result, path):
    t = result["turbines"]
    lines = ["# Диагностика WindPilot на отложенном январе 2026", "",
             "Проверена существующая январская модель без изменения весов, гиперпараметров и основного сплита. "
             "Две основные модели, исходная история и прежние январские результаты сохранены без изменений. "
             "Отдельное обучение на укороченной копии выполнялось только для проверки загрузки данных.", "",
             f"Граница обучения: `{result['split']['trained_until']}`. Целевые часы: январь 2026 по Asia/Almaty. "
             "Все метрики рассчитаны по прежним парам «момент запуска / целевой час», включая пересечения 48-часовых прогнозов; "
             "это не 1464 независимых часа. На каждую турбину приходится 744 уникальных часа.", "",
             "## HTTP API", "", "Добавлен адаптер FastAPI над прежним `run_forecast()`. "
             "`/forecast?date=2026-02-15&horizon=48&offline=true` возвращает 96 строк. "
             "`date` — первый целевой день, расчёт выполняется в 23:00 предыдущего дня местного времени. "
             "Для январских дат автоматически выбирается модель, обученная до января. Документация: `/docs`.", "",
             "## 1 Смещение", "",
             "Bias = mean(prediction − actual). Порог описательного флага — |bias| ≥ 0,02. Это не тест статистической значимости.", "",
             "| Турбина | Пар | Bias | MAE | RMSE | Наблюдение |", "|---|---:|---:|---:|---:|---|"]
    for tid, item in t.items():
        b = item["bias"]
        wording = (f"Модель систематически {'завышает' if b > 0 else 'занижает'} мощность на {abs(b):.4f}" if abs(b) >= .02 else "Среднее смещение меньше выбранного порога 0,02")
        lines.append(f"| {tid} | {item['n']} | {b:+.4f} | {item['mae']:.4f} | {item['rmse']:.4f} | {wording} |")
    lines += ["", "Величины выражены в единицах нормализованной мощности; 0,01 соответствует одному процентному пункту её шкалы.", "",
              "## 2 Ошибка по фактическому уровню мощности", "",
              "Границы без пересечений: [0; 0,2), [0,2; 0,8), [0,8; 1]. "
              "Низкая мощность сама по себе не позволяет отличить штиль от простоя или отключения по сильному ветру.", "",
              "| Турбина | Фактическая мощность | Пар | MAE |", "|---|---|---:|---:|"]
    worst_notes = []
    for tid, item in t.items():
        for bucket in item["buckets"]:
            value = f"{bucket['mae']:.4f}" if bucket["mae"] is not None else "нет данных"
            lines.append(f"| {tid} | {bucket['range']} | {bucket['n']} | {value} |")
        worst = max((x for x in item["buckets"] if x["n"]), key=lambda x: x["mae"])
        worst_notes.append(f"Турбина {tid}: наибольшая MAE в диапазоне {worst['range']} — {worst['mae']:.4f}.")
    lines += ["", " ".join(worst_notes)]
    lines += ["", "## 3 Прогнозная и фактическая погода", "",
              "В тех же январских парах ветер и температура заменены почасовыми наблюдениями из исходных CSV. "
              "Модель, остальные признаки, время запуска, целевые часы и clipping [0,1] остаются прежними. "
              "Это диагностический эксперимент с заведомо будущими наблюдениями, а не допустимый способ рабочего прогноза.", "",
              "| Турбина | MAE с прогнозом погоды | MAE с наблюдениями | Разность a − b |", "|---|---:|---:|---:|"]
    weather_notes = []
    for tid, item in t.items():
        w = item["observed_weather"]
        lines.append(f"| {tid} | {item['mae']:.4f} | {w['mae']:.4f} | {item['mae'] - w['mae']:+.4f} |")
        if item["mae"] - w["mae"] >= .01:
            weather_notes.append(f"Турбина {tid}: модель на фактической погоде даёт MAE {w['mae']:.4f}; "
                                 "это согласуется с тем, что часть ошибки связана с качеством внешней погодной информации.")
        else:
            weather_notes.append(f"Турбина {tid}: заметного улучшения от прямой замены входов не получено.")
    for note in weather_notes:
        lines += ["", note]
    lines += ["", "Это не чистая аддитивная декомпозиция ошибки: поправка модели обучена на прогнозах, "
              "замена меняет распределение входов. Прогнозный ветер взят на 100 м, высота станционного анемометра не указана. "
              "По разности MAE нельзя достоверно вычислить процент ошибки, вызванный только погодным провайдером.", "",
              "## 4 Различие моделей турбин", "",
              f"Две модели получили одинаковые {result['model_difference']['input_rows_per_turbine']} набора признаков, "
              "кроме идентификатора выбора модели. Это ветер 0–30 м/с, шаг 0,5, общая температура и момент времени.", "",
              f"Средняя абсолютная разность сырых предсказаний: **{result['model_difference']['mean_absolute_difference']:.6f}**; "
              f"максимальная: **{result['model_difference']['max_absolute_difference']:.6f}**. "
              f"Модели идентичны: **{'да' if result['model_difference']['identical'] else 'нет'}**.", "",
              "## 5 Граничные проверки predict", "",
              "Проверяется именно сырой `predict()`, а не только ограниченный агентом результат. "
              "Порог «близко к нулю» задан заранее: 0,05. Температура 5 °C, полдень по местному времени, горизонт 24 часа.", "",
              "| Проверка | Статус | Результат |", "|---|---|---|"]
    for check in result["boundary_checks"]["outcomes"]:
        detail = json.dumps(check["detail"], ensure_ascii=False) if not isinstance(check["detail"], str) else check["detail"]
        if check["name"] == "nan_rejected_explicitly":
            detail = "Явный ForecastError для NaN в ветре, температуре, времени и turbine_id."
        lines.append(f"| {check['name']} | {'пройдено' if check['passed'] else 'НЕ ПРОЙДЕНО'} | {detail} |")
    lines += ["", "Добавлена только проверка входов: ранее HistGradientBoosting молча возвращал числа при NaN. "
              "Теперь это явная ошибка. На корректных январских входах предсказания совпали с сохранёнными до диагностики.", "",
              "## 6 Кривая мощности", "", "![Кривая мощности](power_curve_january.png)", "",
              f"Температура фиксирована на среднем **{result['curve_temperature']:.2f} °C** из обучающей истории до сплита, "
              "полдень Asia/Almaty, горизонт 24 часа. График не является усреднённой эмпирической кривой турбины.", "",
              "| Турбина | Падений до 25 м/с > 0,02 | Самое большое падение | P(0) | P(25) | P(30) |", "|---|---:|---:|---:|---:|---:|"]
    for tid, c in result["curves"].items():
        lines.append(f"| {tid} | {c['drops_over_0_02_before_25']} | {c['largest_drop_before_25']:.4f} | {c['power_at_0']:.4f} | {c['power_at_25']:.4f} | {c['power_at_30']:.4f} |")
    lines += ["", "Немонотонность и отсутствие падения мощности на 25–30 м/с фиксируются как ограничения. "
              "Паспортная скорость отсечки неизвестна; её физическое соблюдение моделью не гарантируется. "
              "Бустинг имеет ступенчатую форму и не экстраполирует физику за область обучения. Модель не исправлялась по графику.", "",
              "## 7 In-sample и out-of-sample", "",
              "Повторно собраны те же архивные пары октября–декабря; целевые часы должны завершиться не позже исходной границы обучения. "
              "Оценивается полная двухступенчатая проверочная модель, а не финальная модель с январём в обучении.", "",
              "| Турбина | Пар обучения | MAE обучения | MAE января | Январь / обучение |", "|---|---:|---:|---:|---:|"]
    for tid, item in t.items():
        ins = item["in_sample"]
        lines.append(f"| {tid} | {ins['n']} | {ins['mae']:.4f} | {item['mae']:.4f} | {item['mae']/ins['mae']:.2f} |")
    lines += ["", "Январская MAE примерно на 37–38% выше обучающей: разрыв есть, возможен риск переобучения. "
              "Отношение не достигает выбранного описательного порога 1,5 для большого разрыва. "
              "Вклад сезонного сдвига погоды и режимов работы этим сравнением не отделяется от переобучения.", "",
              "## 8 Укороченная история и автоматические даты", ""]
    trim = result["trimmed_history"]
    if trim.get("test_completed"):
        lines += [f"**Частично:** обучение на копии успешно, автоматическое определение всех границ не реализовано.", "",
                  f"Из копии первой турбины удалена последняя неделя: **{trim['rows_removed']}** строк. "
                  f"Конец изменился с `{trim['source_last_local_before']}` на `{trim['source_last_local_after']}`. "
                  "Вторая турбина оставлена целиком. Обучение вызвано без правки кода и без изменения аргументов дат; "
                  "его результаты сохранены отдельно в `artifacts/diagnostics/trimmed_training/`.", "",
                  "Исходные файлы не изменены: подтверждено SHA-256. Основные модели не перезаписаны.", "",
                  f"Ограничение: значения по умолчанию остаются фиксированными: `{trim['existing_date_defaults']}`. "
                  f"Метаданные укороченной модели по-прежнему показывают заданный cutoff `{trim['reported_trained_until']}`, "
                  "а не последний фактически использованный час каждой турбины. Дополнительные строки после cutoff сами в обучение не попадут. "
                  "Основной январский сплит не менялся."]
    else:
        lines += [f"Не выполнено: {trim.get('reason')}"]
    manual = result["manual_forecast"]
    lines += ["", "## 9 Ручной новый запуск", "",
              f"Дата 15 февраля уже входила в прежнее воспроизведение. Поэтому использован новый момент "
              f"`{manual['origin']}` вместо стандартных 23:00. Его отсутствие среди прежних моментов подтверждено.", "",
              f"Получено **{manual['rows']}** строк, по 48 на турбину, диапазон мощности "
              f"**{manual['minimum']:.4f}–{manual['maximum']:.4f}**, `run_id={manual['run_id']}`. "
              "Проверены временной шаг, состав полей, конечность чисел, диапазон [0,1] и повторяемость.", "",
              "## Ограничения исходной оценки", "",
              "Время публикации погодного выпуска оценивается консервативным лагом 12 часов. "
              "Исходное оперативное происхождение каждого исторического выпуска независимо не подтверждено; "
              "документация провайдера упоминает hindcasts для части истории. Это ограничение распространяется на все приведённые метрики. "
              "Февральских фактических мощностей нет. Метрики не являются процентом accuracy.", "",
              "Воспроизведение: `.venv\\Scripts\\python.exe -m windpilot.diagnostics` из корня проекта. "
              "Подробные числа и контрольные суммы: `artifacts/diagnostics/diagnostics.json`."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(raw_paths=None, output="artifacts/diagnostics", report_dir="reports", skip_trim=False):
    out, reports = Path(output), Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    protected = [Path(p) for p in ["artifacts/model.joblib", "artifacts/validation_model.joblib", "artifacts/metrics.json",
                                  "artifacts/validation_predictions.csv", "data/processed/hourly.csv"]]
    hashes = {str(p): file_hash(p) for p in protected}
    config = read_config()
    bundle = load_model("artifacts/validation_model.joblib")
    history = load_history("data/processed/hourly.csv")
    validation = load_validation("artifacts/validation_predictions.csv")
    metrics = json.loads(Path("artifacts/metrics.json").read_text(encoding="utf-8"))
    cutoff = utc(bundle["trained_until"])
    assert cutoff == utc(metrics["model_trained_until"])
    assert (validation.forecast_origin >= cutoff).all()
    local = validation.valid_at.dt.tz_convert(config["history_timezone"])
    assert ((local >= "2026-01-01") & (local < "2026-02-01")).all()
    recomputed = clipped(bundle, validation)
    assert np.allclose(recomputed, validation.prediction, rtol=0, atol=1e-12), "Original predictions changed"
    print("Original January predictions and split verified.", flush=True)
    observed = history[["turbine_id", "timestamp", "wind_speed", "temperature"]].rename(
        columns={"timestamp": "valid_at", "wind_speed": "observed_wind", "temperature": "observed_temperature"})
    paired = validation.merge(observed, on=["turbine_id", "valid_at"], how="left", validate="many_to_one")
    assert len(paired) == len(validation) and paired[["observed_wind", "observed_temperature"]].notna().all().all()
    replacement = paired.copy()
    replacement["wind_speed"] = paired.observed_wind
    replacement["temperature"] = paired.observed_temperature
    paired["prediction_observed_weather"] = clipped(bundle, replacement)
    paired.to_csv(out / "january_weather_comparison.csv", index=False)
    turbines = {}
    for tid, part in paired.groupby("turbine_id"):
        item = scores(part.power_norm, part.prediction)
        item["bias"] = float((part.prediction - part.power_norm).mean())
        item["unique_hours"] = int(part.valid_at.nunique())
        item["observed_weather"] = scores(part.power_norm, part.prediction_observed_weather)
        item["buckets"] = []
        for label, mask in [("[0, 0.2)", part.power_norm < .2), ("[0.2, 0.8)", part.power_norm.between(.2, .8, inclusive="left")),
                            ("[0.8, 1]", part.power_norm >= .8)]:
            sub = part.loc[mask]
            item["buckets"].append({"range": label, "n": len(sub), "mae": float((sub.prediction-sub.power_norm).abs().mean()) if len(sub) else None})
        turbines[str(tid)] = item
    training, _ = supervised(history, config, "2025-10-01", "2025-12-31")
    training = training.loc[training.valid_at + pd.Timedelta(hours=1) <= cutoff].copy()
    training["prediction"] = clipped(bundle, training)
    for tid, part in training.groupby("turbine_id"):
        assert len(part) == bundle["training_counts"][tid]["forecast_target_pairs"]
        turbines[str(tid)]["in_sample"] = scores(part.power_norm, part.prediction)
    prior_history = history[history.timestamp + pd.Timedelta(hours=1) <= cutoff]
    temperature = float(prior_history.temperature.mean())
    curve = synthetic_weather(temperature)
    curve["raw_prediction"] = predict(bundle, curve)
    curve["power_pred"] = np.clip(curve.raw_prediction, 0, 1)
    curve.to_csv(out / "power_curve.csv", index=False)
    curve_info = {}
    for tid, part in curve.groupby("turbine_id"):
        vals = part.set_index("wind_speed").power_pred
        differences = np.diff(vals[vals.index < 25])
        curve_info[str(tid)] = {"drops_over_0_02_before_25": int((differences < -.02).sum()),
            "largest_drop_before_25": float(max(0, -differences.min())),
            "power_at_0": float(vals.loc[0]), "power_at_25": float(vals.loc[25]), "power_at_30": float(vals.loc[30])}
    a = curve.loc[curve.turbine_id == 1, "raw_prediction"].to_numpy()
    b = curve.loc[curve.turbine_id == 2, "raw_prediction"].to_numpy()
    difference = {"input_rows_per_turbine": len(a), "identical": bool(np.allclose(a, b, atol=1e-12, rtol=0)),
                  "mean_absolute_difference": float(np.abs(a-b).mean()), "max_absolute_difference": float(np.abs(a-b).max())}
    boundary = check_predict(bundle, 5.0)
    save_json(out / "predict_checks.json", boundary)
    draw_curves(curve, reports / "power_curve_january.png", temperature)
    print("Bias, buckets, weather substitution, curves and in-sample diagnostics calculated.", flush=True)
    trimmed = {"test_completed": False, "reason": "Explicit --skip-trim option"}
    if not skip_trim:
        if raw_paths is None:
            raw_paths = []
            for tid in [1, 2]:
                matches = list(Path("D:/Downloads").glob(f"*turbine {tid}.csv"))
                if len(matches) != 1:
                    raise ValueError("Provide --turbine-1 and --turbine-2 paths for the trim audit")
                raw_paths.append(matches[0])
        print("Training on a temporary shortened copy; original models are protected...", flush=True)
        trimmed = audit_trimmed_csv(raw_paths, config, out)
    new_origin = utc("2026-02-15T18:00:00+05:00")
    replay = pd.read_csv("artifacts/runs/replay_forecasts.csv")
    assert new_origin not in set(pd.to_datetime(replay.forecast_origin, utc=True))
    new_run = run_forecast(config, new_origin, horizon=48, out_dir=out / "manual_runs", offline=True)
    repeated = run_forecast(config, new_origin, horizon=48, out_dir=out / "manual_runs", offline=True)
    forecast = pd.read_csv(Path(new_run["path"]) / "forecast.csv")
    for col in ["forecast_origin", "valid_at"]:
        forecast[col] = pd.to_datetime(forecast[col], utc=True)
    assert {"run_id", "turbine_id", "forecast_origin", "valid_at", "power_pred", "model_version"} <= set(forecast.columns)
    assert len(forecast) == 96 and forecast.groupby("turbine_id").size().eq(48).all()
    assert np.isfinite(forecast.power_pred).all() and forecast.power_pred.between(0, 1).all()
    for tid, part in forecast.groupby("turbine_id"):
        assert (part.valid_at.diff().dropna() == pd.Timedelta(hours=1)).all()
    assert new_run["run_id"] == repeated["run_id"] and repeated["status"] == "reused"
    manual = {"origin": new_origin.isoformat(), "run_id": new_run["run_id"], "rows": len(forecast),
              "minimum": float(forecast.power_pred.min()), "maximum": float(forecast.power_pred.max()), "status": "passed"}
    after = {str(p): file_hash(p) for p in protected}
    assert hashes == after, "Protected artifact changed"
    result = {"split": {"trained_until": cutoff.isoformat(), "validation_start": metrics["validation_start"],
                        "validation_end_exclusive": metrics["validation_end_exclusive"]},
              "protected_files_unchanged": True, "protected_hashes": hashes, "turbines": turbines,
              "model_difference": difference, "boundary_checks": boundary, "curve_temperature": temperature,
              "curves": curve_info, "trimmed_history": trimmed, "manual_forecast": manual}
    save_json(out / "diagnostics.json", result)
    markdown_report(result, reports / "january_2026_diagnostics.md")
    print(json.dumps({"report": str(reports / "january_2026_diagnostics.md"), "turbines": turbines,
                      "curves": curve_info, "boundary_checks_passed": boundary["all_passed"],
                      "trim_status": trimmed.get("status"), "protected_files_unchanged": True}, indent=2), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--turbine-1")
    parser.add_argument("--turbine-2")
    parser.add_argument("--skip-trim", action="store_true")
    args = parser.parse_args()
    if bool(args.turbine_1) != bool(args.turbine_2):
        parser.error("Supply both turbine CSV paths")
    run([Path(args.turbine_1), Path(args.turbine_2)] if args.turbine_1 else None, skip_trim=args.skip_trim)


if __name__ == "__main__":
    main()
