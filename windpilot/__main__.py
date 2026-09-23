from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from .agent import run_forecast
from .common import ForecastError, origins, read_config, save_json
from .data import prepare
from .model import train
from .weather import WeatherClient, fetch_range


def main():
    parser = argparse.ArgumentParser(description="WindPilot: reproducible hourly wind-power forecasting")
    parser.add_argument("--config", default="config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--turbine-1", required=True)
    p.add_argument("--turbine-2", required=True)
    p.add_argument("--out", default="data/processed")
    p = sub.add_parser("fetch-weather")
    p.add_argument("--start", default="2025-10-01", help="First target date (local station time)")
    p.add_argument("--end", default="2026-02-28")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("train")
    p.add_argument("--history", default="data/processed/hourly.csv")
    p.add_argument("--weather-start", default="2025-10-01")
    p.add_argument("--validation-start", default="2026-01-01")
    p.add_argument("--validation-end", default="2026-01-31")
    p.add_argument("--final-cutoff", default="2026-01-31T23:00:00+05:00")
    p.add_argument("--out", default="artifacts")
    for name in ["forecast", "replay", "watch"]:
        p = sub.add_parser(name)
        p.add_argument("--model", default="artifacts/model.joblib")
        p.add_argument("--out", default="artifacts/runs")
        p.add_argument("--horizon", type=int, choices=[24, 48], default=48)
        p.add_argument("--offline", action="store_true")
        p.add_argument("--refresh", action="store_true")
        if name == "forecast":
            p.add_argument("--origin", required=True, help="ISO datetime with explicit offset")
        elif name == "replay":
            p.add_argument("--start", default="2026-02-01")
            p.add_argument("--end", default="2026-02-28")
        else:
            p.add_argument("--origin", help="Fixed historical origin; default current UTC hour")
            p.add_argument("--interval", type=int, default=900)
            p.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted")
    args = parser.parse_args()
    try:
        cfg = read_config(args.config)
        if args.command == "prepare":
            result = prepare([args.turbine_1, args.turbine_2], cfg, args.out)
        elif args.command == "fetch-weather":
            failures = fetch_range(WeatherClient(cfg, refresh=args.refresh), origins(args.start, args.end, cfg["history_timezone"]), args.workers)
            save_json("artifacts/weather_fetch_report.json", {"failures": failures, "start": args.start, "end": args.end})
            if failures:
                raise ForecastError(f"{len(failures)} weather runs failed. See artifacts/weather_fetch_report.json")
            result = {"status": "cached", "start": args.start, "end": args.end}
        elif args.command == "train":
            result = train(args.history, cfg, args.weather_start, args.validation_start, args.validation_end, args.final_cutoff, args.out)
        elif args.command == "forecast":
            result = run_forecast(cfg, args.origin, args.horizon, args.model, args.out, args.offline, args.refresh)
        elif args.command == "replay":
            results, failures = [], []
            for origin in origins(args.start, args.end, cfg["history_timezone"]):
                try:
                    run = run_forecast(cfg, origin, args.horizon, args.model, args.out, args.offline, args.refresh)
                    results.append(run)
                    print(f"{origin}: {run['status']} {run['run_id']}", flush=True)
                except ForecastError as exc:
                    failures.append({"origin": str(origin), "error": str(exc)})
            save_json(Path(args.out) / "replay_manifest.json", {"runs": results, "failures": failures})
            if failures:
                raise ForecastError(f"Replay incomplete: {len(failures)} failures; see replay_manifest.json")
            frames = [pd.read_csv(Path(run["path"]) / "forecast.csv") for run in results]
            if not frames:
                raise ForecastError("Empty replay date range.")
            pd.concat(frames, ignore_index=True).to_csv(Path(args.out) / "replay_forecasts.csv", index=False)
            result = {"runs": len(results), "path": str(Path(args.out) / "replay_forecasts.csv")}
        else:
            if args.interval < 30:
                raise ForecastError("Watch interval must be at least 30 seconds.")
            n = 0
            while args.iterations == 0 or n < args.iterations:
                origin = args.origin or pd.Timestamp.now(tz="UTC").floor("h").isoformat()
                try:
                    result = run_forecast(cfg, origin, args.horizon, args.model, args.out, args.offline, True)
                    print(json.dumps(result, default=str), flush=True)
                except ForecastError as exc:
                    print(f"Waiting for next update: {exc}", file=sys.stderr, flush=True)
                n += 1
                if args.iterations == 0 or n < args.iterations:
                    time.sleep(args.interval)
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (ForecastError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
