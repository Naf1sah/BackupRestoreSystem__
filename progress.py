import os, json, time, pathlib, threading, io

LOG_PATH = pathlib.Path(os.getenv("PROGRESS_LOG_PATH", "progress_events.jsonl")).resolve()
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

archive_dir = LOG_PATH.parent / "logs"
archive_dir.mkdir(exist_ok=True)

# --- Arsip log lama ---
if LOG_PATH.exists():
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    archived = archive_dir / f"progress_events_{timestamp}.jsonl"
    LOG_PATH.rename(archived)

    # hapus file arsip lama jika lebih dari 5
    logs = sorted(archive_dir.glob("progress_events_*.jsonl"), key=os.path.getmtime)
    while len(logs) > 5:
        logs[0].unlink()
        logs.pop(0)

_lock = threading.Lock()

def _append_line(line: str):
    with _lock, io.open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

def emit(event: str, **data):
    rec = {"ts": time.time(), "event": event, "data": data}
    _append_line(json.dumps(rec, ensure_ascii=False))

class stage:
    def __init__(self, name: str, **meta):
        self.name = name
        self.meta = meta
    def __enter__(self):
        self.t0 = time.time()
        emit(self.name + "_start", **self.meta)
        return self
    def __exit__(self, exc_type, exc, tb):
        dur_ms = int((time.time() - self.t0) * 1000)
        info = dict(self.meta)
        info["duration_ms"] = dur_ms
        info["ok"] = exc_type is None
        emit(self.name + "_end", **info)
        return False
