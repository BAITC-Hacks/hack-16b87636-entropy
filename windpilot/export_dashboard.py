"""Embed real saved forecasts so the dashboard also works without a server."""
import json
import re
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from .api import create_app
from .common import file_hash, save_json
from .dashboard_data import quality_data


def main():
    hashes = {str(p): file_hash(p) for p in [Path("artifacts/model.joblib"), Path("artifacts/validation_model.joblib")]}
    runs = {}
    with TestClient(create_app(out_dir="artifacts/dashboard_runs")) as client:
        for origin_day in pd.date_range("2026-01-31", "2026-02-28"):
            target_day = (origin_day + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            for horizon in [24, 48]:
                response = client.get("/forecast", params={"date": target_day, "horizon": horizon, "offline": True})
                if response.status_code != 200:
                    raise RuntimeError(f"Cannot embed {origin_day} / {horizon}: {response.text}")
                data = response.json()
                # Keep only fields needed by the portable UI and its CSV export.
                fields = ["run_id", "turbine_id", "forecast_origin", "valid_at", "power_pred", "weather_source", "weather_issued_at", "model_version"]
                data["forecast"] = [{key: row[key] for key in fields} for row in data["forecast"]]
                runs[f"{origin_day:%Y-%m-%d}_{horizon}"] = data
            print(f"Embedded {origin_day:%Y-%m-%d}", flush=True)
    snapshot = {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "data_kind": "real_saved_forecasts", "runs": runs, "quality": quality_data()}
    path = Path("dashboard.html")
    html = path.read_text(encoding="utf-8")
    serialized = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    html, count = re.subn(r"/\*DATA_START\*/.*?/\*DATA_END\*/", lambda _: f"/*DATA_START*/{serialized}/*DATA_END*/", html, flags=re.S)
    if count != 1:
        raise RuntimeError("Expected exactly one dashboard data placeholder")
    path.write_text(html, encoding="utf-8")
    assert hashes == {p: file_hash(p) for p in hashes}, "Model weights changed"
    save_json("artifacts/dashboard_export.json", {"runs": len(runs), "html_bytes": path.stat().st_size, "model_hashes": hashes, "data_kind": "real_saved_forecasts"})
    print(f"Saved {path}: {len(runs)} real runs, {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
