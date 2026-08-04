#!/usr/bin/env python3
"""
The orchestration scheme itself — three layers, one loop.

    1. LLM planner ("CEO")    decomposes the instruction into known step IDs
    2. per-step policies      execute one step each, hot-swapped in one daemon
    3. VLM inspector          verifies the scene after every step

Everything runs on plain threads with the standard library only (LM Studio is
an OpenAI-compatible HTTP endpoint, so `urllib` is enough). The whole point of
the file is to be readable next to the thesis text — it is the experimental
condition being measured, not application infrastructure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent

PLANNER_SYSTEM_PROMPT = (
    "You are a robotic arm task planner. Decompose the user's instruction into an "
    "ordered plan built ONLY from the listed skill IDs. Respond with a pure JSON "
    "array of strings and nothing else."
)

VERIFY_PROMPT = (
    "The robot just finished the step '{step}'. Expected result: {expected}\n"
    "Look at the image and answer with EXACTLY one of these tags:\n"
    '- "SUCCESS" if the step finished correctly\n'
    '- "[object_missed]" if the robot missed the object\n'
    '- "[object_slipped]" if the object fell out of the gripper\n'
    '- "[target_moved]" if the target container moved\n'
    '- "[unknown_failure]" for any other failure\n'
    "Answer with the tag only, no other text."
)

FAILURE_TAGS = ["[object_missed]", "[object_slipped]", "[target_moved]", "[unknown_failure]"]


# ── LM Studio client (OpenAI-compatible, stdlib only) ───────────────────────

class LMStudio:
    def __init__(self, base_url: str, timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_s

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def models(self) -> list[str]:
        with urllib.request.urlopen(f"{self.base_url}/models", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("id", "") for m in data.get("data", [])]

    def chat(self, model: str, messages: list[dict], temperature: float = 0.1,
             max_tokens: int = 512) -> str:
        data = self._post("/chat/completions", {
            "model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        })
        return data["choices"][0]["message"]["content"]

    def chat_with_image(self, model: str, prompt: str, image_b64: str,
                        temperature: float = 0.1) -> str:
        return self.chat(model, [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ],
        }], temperature=temperature, max_tokens=64)


def parse_json_array(text: str) -> list[str] | None:
    """Parse a JSON array out of a model reply, tolerating ``` fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(l for l in cleaned.splitlines() if not l.strip().startswith("```"))
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
        return parsed
    return None


# ── The inference daemon, seen from the orchestrator side ───────────────────

class Daemon:
    """Owns one `inference_daemon.py` subprocess and its stdin/stdout protocol."""

    def __init__(self, cfg: dict, emit: Callable[..., None]):
        self.cfg = cfg
        self.emit = emit
        self.proc: subprocess.Popen | None = None
        self.policy_path = ""
        self._ready = threading.Event()
        self._policy_event = threading.Event()
        self._policy_error = ""
        self._task_done = threading.Event()
        self._done_reason = ""
        self._snapshot = threading.Event()
        self._snapshot_b64 = ""

    # -- lifecycle ---------------------------------------------------------
    def start(self, policy_path: str) -> None:
        cfg = self.cfg
        cmd = [
            cfg.get("python") or sys.executable, "-u", str(HERE / "inference_daemon.py"),
            f"--robot.type={cfg.get('robot_type', 'so101_follower')}",
            f"--robot.id={cfg.get('robot_id', 'my_follower_arm')}",
            f"--policy.path={policy_path}",
            f"--fps={cfg.get('fps', 30)}",
        ]
        if cfg.get("robot_port"):
            cmd.append(f"--robot.port={cfg['robot_port']}")
        if cfg.get("device"):
            cmd.append(f"--device={cfg['device']}")
        cameras = cameras_json(cfg)
        if cameras:
            cmd.append(f"--robot.cameras={cameras}")

        self.emit("log", level="INFO", message="Spouštím inferenční daemon: " + " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            encoding="utf-8", errors="replace",
        )
        self.policy_path = policy_path
        threading.Thread(target=self._read_output, daemon=True).start()

        if not self._ready.wait(timeout=float(self.cfg.get("daemon_start_timeout_s", 180))):
            raise RuntimeError("Daemon se nespustil včas (nenahlásil DAEMON_READY).")

    def _read_output(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("[SNAPSHOT] "):
                self._snapshot_b64 = line[len("[SNAPSHOT] "):].strip()
                self._snapshot.set()
            elif line.startswith("[TELEMETRY] "):
                self.emit("telemetry", message=line[len("[TELEMETRY] "):])
            elif line.startswith("[STATUS] "):
                status = line[len("[STATUS] "):]
                self.emit("log", level="INFO", message=f"daemon: {status}")
                if status.startswith("DAEMON_READY"):
                    self._ready.set()
                elif status.startswith("POLICY_LOADED"):
                    self._policy_error = ""
                    self._policy_event.set()
                elif status.startswith("POLICY_ERROR"):
                    self._policy_error = status[len("POLICY_ERROR:"):].strip()
                    self._policy_event.set()
                elif status.startswith("TASK_DONE"):
                    self._done_reason = status.split("|", 1)[-1].strip()
                    self._task_done.set()
            else:
                self.emit("log", level="DEBUG", message=f"daemon: {line}")
        self.emit("log", level="WARN", message="Daemon ukončen.")

    def _send(self, command: str) -> None:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError("Daemon neběží.")
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    # -- protocol ----------------------------------------------------------
    def set_policy(self, policy_path: str, timeout: float = 180.0) -> None:
        """Hot-swap the weights; the robot and cameras stay connected."""
        if policy_path == self.policy_path:
            return
        self._policy_event.clear()
        self._send(f"SET_POLICY:{policy_path}")
        if not self._policy_event.wait(timeout):
            raise RuntimeError("Výměna modelu (SET_POLICY) nedoběhla včas.")
        if self._policy_error:
            raise RuntimeError(f"Výměna modelu selhala: {self._policy_error}")
        self.policy_path = policy_path

    def run_task(self, task: str, timeout: float, is_grasp: bool = False) -> str:
        """SET_TASK + wait for TASK_DONE (the task latch). Returns the reason."""
        self._task_done.clear()
        self._done_reason = ""
        # `|grasp` tells the daemon this step ends on contact (protocol B)
        self._send(f"SET_TASK:{task}|grasp" if is_grasp else f"SET_TASK:{task}")
        if not self._task_done.wait(timeout):
            self._send("STOP")
            return f"timeout po {timeout:.0f} s"
        return self._done_reason

    def snapshot(self, timeout: float = 10.0) -> str:
        self._snapshot.clear()
        self._snapshot_b64 = ""
        try:
            self._send("SNAP")
        except RuntimeError:
            return ""
        return self._snapshot_b64 if self._snapshot.wait(timeout) else ""

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            self._send("QUIT")
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


# ── Config helpers shared with the setup page ───────────────────────────────

def cameras_json(cfg: dict) -> str:
    """Camera map for the daemon ('' when no camera is configured).

    Supports up to two cameras (camera_* and camera2_*) — same fields the
    setup page's derive.camerasArg() builds, so both stay in sync.
    """
    def entry(prefix: str) -> dict:
        name = (cfg.get(f"{prefix}_name") or "").strip()
        source = str(cfg.get(f"{prefix}_index", "")).strip()
        if not name or source == "":
            return {}
        try:
            source_val: Any = int(source)
        except ValueError:
            source_val = source
        return {name: {
            "index_or_path": source_val,
            "width": int(cfg.get(f"{prefix}_width", 640)),
            "height": int(cfg.get(f"{prefix}_height", 480)),
            "fps": int(cfg.get(f"{prefix}_fps", 30)),
        }}

    cams = {**entry("camera"), **entry("camera2")}
    return json.dumps(cams) if cams else ""


def baseline_output_dir(cfg: dict) -> str:
    root = cfg.get("output_root") or "outputs/training"
    return f"{root}/{cfg.get('task_slug', 'task')}_{cfg.get('policy_type', 'act')}"


def step_output_dir(cfg: dict, step_slug: str) -> str:
    root = cfg.get("output_root") or "outputs/training"
    return f"{root}/{cfg.get('task_slug', 'task')}_{step_slug}_{cfg.get('policy_type', 'act')}"


def step_catalog(cfg: dict) -> list[dict]:
    """Ordered per-step skills of the configured task."""
    out = []
    for step in cfg.get("steps", []):
        slug = (step.get("slug") or "").strip()
        if slug:
            out.append({"slug": slug,
                        "description": (step.get("description") or "").strip(),
                        "grasp": bool(step.get("grasp"))})
    return out


# ── Orchestrator ────────────────────────────────────────────────────────────

class Orchestrator:
    """Plan -> (swap, execute, verify)* -> result, with bounded re-planning."""

    def __init__(self, cfg: dict, emit: Callable[..., None]):
        self.cfg = cfg
        self.emit = emit
        self.lm = LMStudio(cfg.get("lm_url", "http://localhost:1234/v1"),
                           float(cfg.get("llm_timeout_s", 60)))
        self.daemon: Daemon | None = None
        self._stop = threading.Event()
        self.results: list[dict] = []

    def stop(self) -> None:
        self._stop.set()
        if self.daemon:
            self.daemon.stop()

    # -- layer 1: the CEO --------------------------------------------------
    def _build_planner_prompt(self) -> str:
        cfg = self.cfg
        lines = [PLANNER_SYSTEM_PROMPT]
        if cfg.get("scene_description"):
            lines.append("\nScene: " + cfg["scene_description"])
        lines.append("\nAvailable skills:")
        lines.append(f"- ID: '{cfg.get('task_slug')}' (high-level goal) — "
                     f"{cfg.get('task_description', '')}")
        for step in step_catalog(cfg):
            lines.append(f"- ID: '{step['slug']}' (sub-step of '{cfg.get('task_slug')}') — "
                         f"{step['description']}")
        lines.append("\nUse ONLY these IDs.")
        return "\n".join(lines)

    def _create_plan(self, instruction: str) -> list[str]:
        if self.cfg.get("skip_planner"):
            plan = [s["slug"] for s in step_catalog(self.cfg)]
            self.emit("log", level="WARN",
                      message="Plánovač přeskočen — použito pevné pořadí kroků.")
            return plan

        self.emit("log", level="INFO", message=f"CEO plánuje: „{instruction}\"")
        reply = self.lm.chat(
            self.cfg.get("llm_model", "local-llm"),
            [{"role": "system", "content": self._build_planner_prompt()},
             {"role": "user", "content": instruction}],
        )
        plan = parse_json_array(reply)
        if plan is None:
            raise RuntimeError(f"CEO nevrátil platné JSON pole: {reply[:200]}")
        self.emit("log", level="INFO", message=f"Surový plán: {plan}")
        return plan

    def _resolve_plan(self, raw_plan: list[str]) -> list[str]:
        """Drop hallucinated IDs and expand the goal into its ordered steps."""
        steps = step_catalog(self.cfg)
        known = {s["slug"] for s in steps}
        goal = self.cfg.get("task_slug", "")
        resolved: list[str] = []
        for item in raw_plan:
            item = item.strip()
            if item == goal:
                resolved.extend(s["slug"] for s in steps)
            elif item in known:
                resolved.append(item)
            else:
                self.emit("log", level="WARN",
                          message=f"Neznámé ID kroku '{item}' — zahozeno.")
        return resolved

    # -- layer 3: the inspector -------------------------------------------
    def _verify(self, step_slug: str, image_b64: str) -> tuple[bool, str]:
        expected = next((s["description"] for s in step_catalog(self.cfg)
                         if s["slug"] == step_slug), step_slug)
        prompt = VERIFY_PROMPT.format(step=step_slug, expected=expected or step_slug)
        if self.cfg.get("scene_description"):
            prompt = f"Scene: {self.cfg['scene_description']}\n\n" + prompt
        reply = self.lm.chat_with_image(self.cfg.get("vlm_model", "local-vlm"),
                                        prompt, image_b64)
        upper = reply.strip().upper()
        if "SUCCESS" in upper:
            return True, "SUCCESS"
        for tag in FAILURE_TAGS:
            if tag.upper() in upper:
                return False, tag
        return False, "[unknown_failure]"

    # -- the loop ----------------------------------------------------------
    def run(self, instruction: str) -> dict:
        cfg = self.cfg
        max_replans = int(cfg.get("max_replans", 3))
        latch_timeout = float(cfg.get("latch_timeout_s", 60))
        replans = 0
        started = time.time()
        self.results = []

        try:
            self.emit("state", state="PLANNING")
            plan = self._resolve_plan(self._create_plan(instruction))
            self.emit("plan", steps=plan)

            if not plan:
                self.emit("log", level="WARN", message="Plán je prázdný — konec.")
                return self._finish(True, started)

            index = 0
            while index < len(plan):
                if self._stop.is_set():
                    self.emit("log", level="WARN", message="Běh zastaven uživatelem.")
                    return self._finish(False, started)

                step = plan[index]
                policy_path = step_output_dir(cfg, step)
                self.emit("state", state="EXECUTING")
                self.emit("step", index=index, total=len(plan), step=step, phase="start",
                          policy=policy_path)

                # 1) the muscles: one daemon, weights swapped per step
                if self.daemon is None:
                    self.daemon = Daemon(cfg, self.emit)
                    self.daemon.start(policy_path)
                else:
                    self.emit("log", level="INFO",
                              message=f"Hot-swap modelu na krok '{step}'.")
                    self.daemon.set_policy(policy_path)

                # 2) execution under the task latch
                is_grasp = any(s["slug"] == step and s["grasp"] for s in step_catalog(cfg))
                reason = self.daemon.run_task(step, latch_timeout, is_grasp)
                self.emit("step", index=index, step=step, phase="executed", reason=reason)

                # 3) the inspector
                self.emit("state", state="VERIFYING")
                image = self.daemon.snapshot()
                if image:
                    self.emit("snapshot", image=image, step=step)
                if image and not cfg.get("skip_inspector"):
                    success, tag = self._verify(step, image)
                else:
                    success, tag = True, "SKIPPED"
                    self.emit("log", level="WARN",
                              message="Bez snímku/inspektora — krok považován za úspěšný.")

                self.results.append({"step": step, "success": success, "tag": tag,
                                     "reason": reason})
                self.emit("step", index=index, step=step, phase="verified",
                          success=success, tag=tag)

                if success:
                    index += 1
                    continue

                # 4) failure -> re-plan with the failure context
                replans += 1
                if replans > max_replans:
                    raise RuntimeError(
                        f"Krok '{step}' selhal opakovaně ({tag}) — limit re-plánů vyčerpán.")
                self.emit("log", level="WARN",
                          message=f"Krok '{step}' selhal ({tag}) — re-plán {replans}/{max_replans}.")
                self.emit("state", state="PLANNING")
                context = (f"Step '{step}' failed with cause '{tag}'. Adapt the remaining plan "
                           f"accordingly. Original instruction: '{instruction}'")
                plan = self._resolve_plan(self._create_plan(context))
                self.emit("plan", steps=plan, replan=replans)
                index = 0
                if not plan:
                    raise RuntimeError("Re-plán vrátil prázdný plán.")

            return self._finish(all(r["success"] for r in self.results), started)

        except Exception as e:
            self.emit("state", state="ERROR")
            self.emit("log", level="ERROR", message=str(e))
            return self._finish(False, started, error=str(e))
        finally:
            if self.daemon:
                self.daemon.stop()
                self.daemon = None

    def _finish(self, success: bool, started: float, error: str = "") -> dict:
        summary = {
            "success": success,
            "error": error,
            "duration_s": round(time.time() - started, 1),
            "steps": self.results,
        }
        self.emit("state", state="COMPLETED" if success else "ERROR")
        self.emit("finished", **summary)
        self._save_run(summary)
        return summary

    def _save_run(self, summary: dict) -> None:
        """Append the run to runs/<timestamp>.json — the raw thesis data."""
        try:
            runs = HERE / "runs"
            runs.mkdir(exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + ".json"
            payload = dict(summary)
            payload["config"] = {k: v for k, v in self.cfg.items() if k != "steps"}
            payload["catalog"] = step_catalog(self.cfg)
            (runs / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
            self.emit("log", level="INFO", message=f"Záznam běhu uložen: runs/{name}")
        except Exception as e:
            self.emit("log", level="WARN", message=f"Záznam běhu se nepodařilo uložit: {e}")
