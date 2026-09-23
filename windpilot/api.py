"""HTTP adapter around the existing agent. Start with one Uvicorn worker."""
from datetime import date as Date
import json
from pathlib import Path
from threading import Lock
import time

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .agent import run_forecast
from .common import ForecastError, read_config, utc
from .model import load_model
from .dashboard_data import baseline_at, quality_data
from .live import LIVE_ENDPOINT, run_live_forecast


def create_app(config_path="config.json", model_path="artifacts/model.joblib",
               validation_model_path="artifacts/validation_model.joblib", out_dir="artifacts/api_runs"):
    app = FastAPI(title="WindPilot", version="0.1.0",
                  description="Date denotes the first forecast day in station local time; origin is 23:00 the preceding day.")
    lock = Lock()
    connection = {"checked_at": None, "monotonic": 0, "status": "offline"}

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(Path(__file__).resolve().parent.parent / "dashboard.html", media_type="text/html")

    @app.get("/quality")
    def quality():
        try:
            return quality_data()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail="January quality report is not available.") from exc

    @app.get("/weather/status")
    def weather_status():
        if time.monotonic() - connection["monotonic"] > 60:
            cfg = read_config(config_path)
            turbine = cfg["turbines"][0]
            try:
                response = requests.get(LIVE_ENDPOINT, params={"latitude": turbine["latitude"], "longitude": turbine["longitude"],
                    "hourly": "temperature_2m", "forecast_hours": 1}, timeout=(2, 3))
                connection["status"] = "online" if response.status_code == 200 and "hourly" in response.json() else "offline"
            except (requests.RequestException, ValueError):
                connection["status"] = "offline"
            connection.update(checked_at=pd.Timestamp.now(tz="UTC").isoformat(), monotonic=time.monotonic())
        return {"source": "Open-Meteo", "status": connection["status"], "checked_at": connection["checked_at"]}

    @app.get("/forecast/live")
    def forecast_live(horizon: int = Query(default=48, ge=24, le=48)):
        if horizon not in (24, 48):
            raise HTTPException(status_code=422, detail="Horizon must be 24 or 48 hours.")
        try:
            with lock:
                result = run_live_forecast(read_config(config_path), horizon, model_path)
            connection.update(status="online", checked_at=pd.Timestamp.now(tz="UTC").isoformat(), monotonic=time.monotonic())
            return result
        except (ForecastError, FileNotFoundError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/health")
    def health():
        return {"status": "ok", "model_available": Path(model_path).exists()}

    @app.get("/forecast")
    def forecast(date: Date, horizon: int = Query(default=48, ge=24, le=48), offline: bool = False, refresh: bool = False):
        started = time.perf_counter()
        if horizon not in (24, 48):
            raise HTTPException(status_code=422, detail="Horizon must be 24 or 48 hours.")
        try:
            config = read_config(config_path)
            origin = pd.Timestamp(date).tz_localize(config["history_timezone"]) - pd.Timedelta(hours=1)
            chosen_model = model_path
            final_model = load_model(model_path)
            if origin.tz_convert("UTC") < utc(final_model["trained_until"]):
                chosen_model = validation_model_path
            # The underlying workflow writes an event log and immutable run directories.
            with lock:
                event_path = Path(out_dir) / "events.jsonl"
                offset = event_path.stat().st_size if event_path.exists() else 0
                run = run_forecast(config, origin, horizon, chosen_model, out_dir, offline, refresh)
                folder = Path(run["path"])
                rows = pd.read_csv(folder / "forecast.csv")
                analysis = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
                with event_path.open("rb") as events:
                    events.seek(offset)
                    trace = [json.loads(line) for line in events.read().decode("utf-8").splitlines() if line]
                if run["status"] == "created":
                    from .common import save_json
                    save_json(folder / "trace.json", trace)
            return {"run_id": run["run_id"], "status": run["status"], "date": date.isoformat(),
                    "forecast_origin": origin.isoformat(), "timezone": config["history_timezone"],
                    "horizon_hours": horizon, "row_count": len(rows),
                    "forecast": json.loads(rows.to_json(orient="records")), "analysis": analysis,
                    "mode": "historical", "trace": trace, "baseline": baseline_at(origin),
                    "request_duration_ms": round((time.perf_counter() - started) * 1000, 2)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=f"Required local artifact unavailable: {exc.filename}") from exc
        except ForecastError as exc:
            status = 503 if "weather" in str(exc).lower() or "missing" in str(exc).lower() else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    return app


app = create_app()
