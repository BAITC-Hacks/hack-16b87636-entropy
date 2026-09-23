"""A single-process recurring weather check, independent of the browser."""
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread


class ForecastScheduler:
    def __init__(self, callback, interval_seconds=0):
        if interval_seconds and interval_seconds < 30:
            raise ValueError("Automatic forecast interval must be at least 30 seconds.")
        self.callback, self.interval = callback, interval_seconds
        self.stop_event, self.state_lock = Event(), Lock()
        self.thread = None
        self.state = {"enabled": bool(interval_seconds), "interval_seconds": interval_seconds,
                      "status": "waiting" if interval_seconds else "disabled", "last_checked_at": None,
                      "next_check_at": None, "last_run_id": None, "last_result": None, "error": None}

    def snapshot(self):
        with self.state_lock:
            return dict(self.state)

    def tick(self):
        with self.state_lock:
            self.state.update(status="running", next_check_at=None)
        try:
            result = self.callback()
            with self.state_lock:
                self.state.update(status="waiting", error=None, last_run_id=result["run_id"],
                                  last_result=result["status"], forecast_origin=result["forecast_origin"])
        except Exception as exc:
            # Keep the worker alive; publish the error instead of claiming success.
            with self.state_lock:
                self.state.update(status="error", error=str(exc))
        finally:
            now = datetime.now(timezone.utc)
            with self.state_lock:
                self.state.update(last_checked_at=now.isoformat(),
                                  next_check_at=(now + timedelta(seconds=self.interval)).isoformat() if self.interval else None)

    def start(self):
        if not self.interval or (self.thread and self.thread.is_alive()):
            return
        self.stop_event.clear()

        def loop():
            while not self.stop_event.is_set():
                self.tick()
                if self.stop_event.wait(self.interval):
                    break

        self.thread = Thread(target=loop, name="windpilot-weather-check", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
