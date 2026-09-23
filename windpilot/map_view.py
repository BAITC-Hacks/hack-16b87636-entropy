"""Optional Windy view, isolated from forecasting and its weather provider."""
import os
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse

from .common import read_config

ROOT = Path(__file__).resolve().parents[1]


def map_key(env_file=None):
    # Only this one optional browser API key is read. Never expose the .env file.
    value = os.getenv("WINDY_MAP_API_KEY")
    if value is not None:
        return value.strip()
    path = Path(env_file) if env_file is not None else ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name.strip() == "WINDY_MAP_API_KEY":
                return value.strip().strip("\"'")
    return ""


def install_map_routes(app, config_path):
    @app.get("/map/config", include_in_schema=False)
    def map_config():
        key = map_key()
        cfg = read_config(config_path)
        turbines = [{"id": t["id"], "latitude": t["latitude"], "longitude": t["longitude"]}
                    for t in cfg["turbines"]]
        # Map Forecast keys are sent to the browser by design. Restrict their
        # allowed domains in Windy; environment storage only keeps them out of Git.
        return JSONResponse({"enabled": bool(key), "key": key or None, "turbines": turbines,
                             "product": "gfs", "overlay": "wind", "level": "surface"},
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/map/windy", include_in_schema=False)
    def windy_frame():
        return FileResponse(ROOT / "web/windy.html", media_type="text/html",
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN",
                                     "Referrer-Policy": "strict-origin-when-cross-origin"})
