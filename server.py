#!/usr/bin/env python3
"""
Minimal web server for the thesis app — standard library only.

    python server.py            # http://localhost:8000
    python server.py --port 9000

Serves the two static pages from web/ and exposes the few endpoints the
orchestration page needs:

    GET  /api/config     current configuration (shared with the setup page)
    POST /api/config     save the configuration to config.json
    GET  /api/lmstudio   which models LM Studio currently has loaded
    GET  /api/models     which baseline/step ACT checkpoints actually exist on disk
    POST /api/run        start an orchestration run  {instruction, ...}
    POST /api/stop       stop the running orchestration
    GET  /api/status     is something running right now
    GET  /api/runs       list the saved run logs
    GET  /api/events     server-sent events stream (log / plan / step / ...)

Note: this server is a thin shell on purpose. The scheme being measured lives
in orchestrator.py; nothing here influences the experiment.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import orchestrator as orch

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
CONFIG_FILE = HERE / "config.json"

DEFAULT_CONFIG: dict = {
    # environment
    "python": "C:/Users/green/miniconda3/envs/lerobot/python.exe",
    "device": "cuda",
    # arms
    "robot_type": "so101_follower",
    "robot_port": "COM3",
    "robot_id": "my_follower_arm",
    "teleop_type": "so101_leader",
    "teleop_port": "COM4",
    "teleop_id": "my_leader_arm",
    # camera
    "camera_name": "top",
    "camera_index": "0",
    "camera_width": 640,
    "camera_height": 480,
    "camera_fps": 30,
    "camera2_name": "",
    "camera2_index": "1",
    "camera2_width": 640,
    "camera2_height": 480,
    "camera2_fps": 30,
    # task
    "fps": 30,
    "data_strategy": "split",
    "task_slug": "pick_and_place",
    "task_description": "vlož kostku do krabice",
    "scene_description": "Rameno SO-101 na stole, před ním kostka a krabice, kamera shora.",
    "steps": [
        {"slug": "grab_cube", "description": "gripper drží kostku", "grasp": True},
        {"slug": "move_to_box", "description": "kostka je nad krabicí", "grasp": False},
        {"slug": "release", "description": "kostka leží v krabici, gripper je otevřený", "grasp": False},
    ],
    # recording
    "episodes": 30,
    "episode_time_s": 20,
    "reset_time_s": 8,
    # training
    "policy_type": "act",
    "train_steps": 20000,
    "batch_size": 8,
    "save_freq": 5000,
    "output_root": "outputs/training",
    # AI models
    "lm_url": "http://localhost:1234/v1",
    "llm_model": "local-llm",
    "vlm_model": "local-vlm",
    # orchestration
    "max_replans": 3,
    # planner behaviour
    "planner_vision": True,
    "planner_reasoning": True,
    # step termination protocols — task- and hardware-specific, hence togglable
    "protocol_a_enabled": True,
    "protocol_a_threshold_rad": 0.005,
    "protocol_a_patience": 5,
    "protocol_b_enabled": True,
    "protocol_b_limit_ma": 250,
    "gripper_state_in_context": True,
}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


# ── Event bus (fan-out to every open SSE stream) ────────────────────────────

class EventBus:
    def __init__(self, history: int = 500):
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._history: list[dict] = []
        self._max_history = history

    def publish(self, kind: str, **fields) -> None:
        event = {"type": kind, "t": round(time.time(), 3), **fields}
        with self._lock:
            self._history.append(event)
            del self._history[:-self._max_history]
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()


bus = EventBus()


# ── Configuration ───────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            # utf-8-sig: an editor that saves with a BOM (Notepad does) would
            # otherwise make the whole file unreadable and silently unused.
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")))
        except Exception as e:
            print(f"  ! {CONFIG_FILE.name} se nepodařilo přečíst ({e}) — použity výchozí hodnoty.")
    return cfg


def save_config(cfg: dict) -> dict:
    merged = load_config()
    merged.update(cfg)
    CONFIG_FILE.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged


# ── Run state ───────────────────────────────────────────────────────────────

class RunState:
    def __init__(self):
        self.lock = threading.Lock()
        self.orchestrator: orch.Orchestrator | None = None
        self.thread: threading.Thread | None = None
        self.instruction = ""

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


run_state = RunState()


def start_run(body: dict) -> dict:
    with run_state.lock:
        if run_state.running:
            return {"ok": False, "error": "Orchestrace už běží."}

        cfg = load_config()
        cfg["skip_planner"] = bool(body.get("skip_planner"))
        cfg["skip_inspector"] = bool(body.get("skip_inspector"))
        instruction = (body.get("instruction") or "").strip() or cfg.get("task_description", "")

        bus.clear_history()
        bus.publish("run_started", instruction=instruction)

        orchestrator = orch.Orchestrator(cfg, bus.publish)
        run_state.orchestrator = orchestrator
        run_state.instruction = instruction

        def worker():
            try:
                orchestrator.run(instruction)
            except Exception as e:  # the orchestrator handles its own errors
                bus.publish("log", level="ERROR", message=f"Neočekávaná chyba: {e}")

        run_state.thread = threading.Thread(target=worker, daemon=True)
        run_state.thread.start()
        return {"ok": True, "instruction": instruction}


def stop_run() -> dict:
    with run_state.lock:
        if run_state.orchestrator:
            run_state.orchestrator.stop()
            bus.publish("log", level="WARN", message="Zastavuji běh…")
            return {"ok": True}
    return {"ok": False, "error": "Nic neběží."}


# ── HTTP handler ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "DiplomkaOrchestration/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if "/api/events" not in (self.path or ""):
            print(f"  {fmt % args}")

    # -- helpers -----------------------------------------------------------
    def _send_json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict | None:
        """Parsed JSON body, or None when it was sent but is not valid JSON.

        The difference matters: silently treating a broken body as empty would
        start a run with default settings instead of the ones that were asked
        for — the checkboxes on the run page would look ignored.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def _serve_static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self.send_error(404, "Not found")
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _stream_events(self) -> None:
        q = bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            # replay what already happened so a reload does not lose the run
            for event in bus.history():
                self._write_event(event)
            while True:
                try:
                    self._write_event(q.get(timeout=15))
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            bus.unsubscribe(q)

    def _write_event(self, event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/api/config":
            self._send_json(load_config())
        elif path == "/api/status":
            self._send_json({"running": run_state.running,
                             "instruction": run_state.instruction})
        elif path == "/api/lmstudio":
            cfg = load_config()
            try:
                models = orch.LMStudio(cfg["lm_url"]).models()
                self._send_json({"ok": True, "models": models})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
        elif path == "/api/models":
            # Same path lookup orchestrator.py itself uses (checkpoint_status),
            # so "Setup říká trénováno" and "co se skutečně nahraje" nemůžou
            # rozejít — počítá se to jednou, ne dvakrát na dvou místech.
            self._send_json(orch.model_status(load_config()))
        elif path == "/api/runs":
            runs_dir = HERE / "runs"
            files = sorted((f.name for f in runs_dir.glob("*.json")), reverse=True) \
                if runs_dir.exists() else []
            self._send_json({"runs": files[:50]})
        elif path == "/api/events":
            self._stream_events()
        else:
            self._serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()
        if body is None:
            self._send_json({"ok": False, "error": "Tělo požadavku není platný JSON."}, status=400)
            return

        if path == "/api/config":
            self._send_json({"ok": True, "config": save_config(body)})
        elif path == "/api/run":
            self._send_json(start_run(body))
        elif path == "/api/stop":
            self._send_json(stop_run())
        else:
            self.send_error(404, "Not found")


def main() -> None:
    ap = argparse.ArgumentParser(description="Web server for the orchestration app")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if not CONFIG_FILE.exists():
        save_config({})

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"\n  Diplomka — orchestrační schéma")
    print(f"  Setup:       http://{args.host}:{args.port}/")
    print(f"  Orchestrace: http://{args.host}:{args.port}/orchestrace.html")
    print(f"  Konfigurace: {CONFIG_FILE}\n  (Ctrl+C ukončí)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Konec.")
    finally:
        stop_run()
        server.server_close()


if __name__ == "__main__":
    main()
