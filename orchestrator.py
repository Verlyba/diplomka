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
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
# Kam record_with_marks.py / lerobot-record doopravdy ukládá "local/<name>"
# datasety, bez ohledu na to, odkud tenhle proces běží (server.py má stejnou
# konstantu — nejde sdílet importem, protože server.py naopak importuje tenhle
# modul, tak by vznikl cyklus).
LOCAL_DATASETS_DIR = Path.home() / ".cache" / "huggingface" / "lerobot" / "local"


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` via a temp-file + os.replace() so a crash or
    kill mid-write can never leave a truncated/corrupt file behind (used for
    runs/*.json, the raw thesis run data)."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        # mkstemp() creates the temp file mode 0600 (owner-only); os.replace()
        # would carry that onto the target, silently making it less readable
        # than the plain open(path, "w") it replaces (which follows the umask).
        os.chmod(tmp_name, 0o644)
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

PLANNER_SYSTEM_PROMPT = (
    "You are the planning layer of a three-layer robotic manipulation system.\n\n"
    "WHAT YOU CONTROL\n"
    "You do not move the robot directly. You choose WHICH pre-trained skill runs next. Each skill "
    "is a separate neural policy trained on demonstrations of one phase of a manipulation task. "
    "A skill runs for a bounded time, after which a visual inspector and robot telemetry evaluate the workspace.\n\n"
    "HOW TO DECIDE\n"
    "- 1. GOAL SATISFACTION CHECK (CRITICAL): Always check if the goal is ALREADY satisfied in the workspace photos "
    "(e.g., if the target object is already inside the container / target location). If the goal is satisfied, "
    "DO NOT plan any more skills — output ONLY [\"DONE\"].\n"
    "- 2. WORKSPACE & STATE PERCEPTION: Evaluate the current state of the workspace and robot from the "
    "attached photos, sensor telemetry (e.g. ROBOT STATE / gripper current), and past progress BEFORE choosing skills.\n"
    "- 3. INTERMEDIATE STATE CONTINUATION: The workspace or robot may ALREADY be in an intermediate stage "
    "(e.g., an object is already held, aligned, or moved). Do NOT start over or repeat skills whose outcome is "
    "already achieved. Re-executing an already accomplished skill (such as attempting to grasp when an object is "
    "already held) can disrupt the environment.\n"
    "- 4. CONTINUATION FROM PROGRESS: Review PROGRESS THIS RUN. If earlier skills succeeded and their effects remain valid, "
    "continue from the current state by selecting ONLY the remaining skills needed to complete the goal.\n"
    "- 5. OPTIMAL SEQUENCE: Output the shortest sequence of valid skill IDs that transitions the current state to the goal.\n"
    "- 6. RECOVERY VIA RESET SKILL: If AVAILABLE SKILLS includes one tagged [RESET] (returns the arm to a known/safe "
    "position from ANY current position or state, not one of the task's forward-progress phases), treat it as a "
    "general-purpose recovery action available at any point in the plan — not only at the very end. Schedule it when "
    "the workspace is unclear, the target object is not visible or not reachable, or the same skill has failed "
    "repeatedly, so the next attempt starts from a known, well-conditioned position instead of wherever the arm was "
    "left after a failed attempt. A [RESET] step's own completion is verified physically (the arm's joints stopping), "
    "not just by a photo, so trust its reported outcome. If no [RESET] skill exists in AVAILABLE SKILLS for this task, "
    "this point does not apply.\n\n"
    "SPECIAL ANSWERS\n"
    '- ["DONE"]  — the goal is ALREADY satisfied in the current workspace state (e.g. object is in destination).\n'
    '- ["ABORT"] — no sequence of the available skills can reach the goal from the current state.\n'
    "Use either one alone, never mixed with skill IDs."
)

PLANNER_OUTPUT_REASONING = (
    "OUTPUT FORMAT\n"
    "First write ONE line beginning with \"REASONING:\" describing the current workspace/robot state and why you chose the remaining sequence or [\"DONE\"] (one or two sentences).\n"
    "Then, on the LAST line, output ONLY the valid JSON array of skill ID strings or [\"DONE\"].\n\n"
    "Example:\n"
    "REASONING: The target object is already placed inside the target location in the photo; goal is satisfied.\n"
    '["DONE"]'
)

PLANNER_OUTPUT_TERSE = (
    "OUTPUT FORMAT\n"
    'Return ONLY a valid JSON array of skill ID strings, e.g. ["skill_a", "skill_b"]. '
    "No extra text, no markdown wrappers, no explanation."
)

VERIFY_PROMPT_RULES = (
    "IMPORTANT VERIFICATION GUIDANCE:\n"
    "- If the expected outcome is satisfied in the photo, reply strictly with SUCCESS.\n"
    "- Do NOT expect the robot to be holding an object if the step is marked [positioning/approach, no grasp]. "
    "For such a step, the gripper being correctly positioned relative to its target — with the jaws still open — "
    "is exactly what success looks like.\n"
    "- The expected outcome names specific things that must be VISIBLE. If the gripper or arm is blocking your "
    "view of exactly that spot, you cannot confirm it — that is [unclear], never SUCCESS. Do not assume the "
    "outcome happened just because nothing contradicts it; absence of evidence is not evidence of success.\n"
    "- Your REASONING sentence and your final tag MUST be logically consistent. If your own reasoning says a "
    "required object or state is NOT visible/NOT confirmed, the tag must NOT be SUCCESS — re-read your reasoning "
    "before picking the tag.\n"
    "- You judge ONLY what the camera can show. Independent robot sensors are evaluated separately and may "
    "contradict you; that is expected and useful, so report what you actually see rather than what you think "
    "the robot 'should' have achieved.\n\n"
    "Choose EXACTLY ONE response tag from this list:\n"
    "  SUCCESS            the expected outcome is clearly, visibly achieved\n"
    "  [object_missed]    the robot arm is completely far away or missed the target area entirely\n"
    "  [object_slipped]   an object slipped or dropped during a carrying phase\n"
    "  [target_moved]     the target object shifted out of reach\n"
    "  [unclear]          photo is unclear, blurry, or occluded — INCLUDING the target spot being hidden behind the robot's own arm/gripper\n"
    "  [unknown_failure]  other severe failure\n\n"
    "OUTPUT FORMAT — exactly three lines, in this order:\n"
    "1. ONE line beginning with \"REASONING:\" describing what you actually see in the photo relative to the "
    "expected outcome (one sentence — this is shown to the operator and fed to the re-planner on failure, so it "
    "must say what's wrong, not just repeat the tag).\n"
    "2. ONE line \"GOAL: yes\" or \"GOAL: no\" — is the OVERALL GOAL (not just this step) fully achieved in the "
    "photo right now? Answer 'yes' only if you can actually SEE the finished end state; if unsure, answer 'no'.\n"
    "3. The LAST line: ONLY the tag — it must match what the REASONING line just said.\n\n"
    "Example:\n"
    "REASONING: The gripper is open and hovering well above the target location, not near the object — the arm approached the wrong place.\n"
    "GOAL: no\n"
    "[object_missed]"
)

# [unclear] is deliberately not in this list — it means "take another photo",
# not "the step failed". It is handled separately in _verify().
FAILURE_TAGS = ["[object_missed]", "[object_slipped]", "[target_moved]", "[unknown_failure]"]
UNCLEAR_TAG = "[unclear]"
PLAN_DONE = "DONE"
PLAN_ABORT = "ABORT"


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
             max_tokens: int = 2048) -> str:
        data = self._post("/chat/completions", {
            "model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        })
        choices = data.get("choices") or [{}]
        msg = choices[0].get("message", {})
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        if not content.strip() and reasoning.strip():
            return reasoning
        if reasoning.strip() and content.strip():
            return f"{reasoning}\n{content}"
        return content

    def chat_with_images(self, model: str, user_prompt: str,
                         images_b64: str | list[str] | None = None,
                         system_prompt: str | None = None,
                         temperature: float = 0.1,
                         max_tokens: int = 2048) -> str:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if isinstance(images_b64, str):
            imgs = [images_b64] if images_b64 else []
        elif isinstance(images_b64, list):
            imgs = [i for i in images_b64 if i]
        else:
            imgs = []

        if imgs:
            user_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
            for img in imgs:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img}"}
                })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_prompt})

        return self.chat(model, messages, temperature=temperature, max_tokens=max_tokens)


def parse_json_array(text: str) -> list[str] | None:
    """Parse the model's plan array out of a reply that may contain prose.

    The old implementation took everything between the FIRST '[' and the LAST
    ']', which breaks the moment the model is allowed to explain itself: a
    reply like

        REASONING: the tag [object_missed] was imprecise
        ["grab_cube"]

    would be sliced from '[object_missed]' and fail to parse, taking the whole
    run down with it. Instead, scan for every balanced [...] span, try them
    from the last one backwards, and return the first that parses as a list of
    strings — so prose (including bracketed failure tags) before the actual
    answer is harmless, and the plan is always read from the model's final
    statement rather than its first bracket.
    """
    cleaned = "\n".join(l for l in text.splitlines() if not l.strip().startswith("```")).strip()

    spans: list[str] = []
    depth, start = 0, -1
    for i, ch in enumerate(cleaned):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                spans.append(cleaned[start:i + 1])
                start = -1

    for span in reversed(spans):
        try:
            parsed = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed
    return None


def parse_reasoning_sentence(text: str) -> str:
    """Extract a 1-sentence reasoning/justification from the CEO planner's reply."""
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("REASONING:"):
            res = line[len("REASONING:"):].strip().strip('*"` ')
            if res:
                return res
    return ""


def parse_goal_flag(text: str) -> bool | None:
    """Read the inspector's optional 'GOAL: yes|no' line.

    None means the model didn't answer it (or answered something unparseable),
    which is deliberately indistinguishable from "no opinion" — the caller
    treats that as "no information" and changes nothing. A small local VLM
    dropping one line of a three-line format must not break verification, so
    this never raises and never guesses.
    """
    for line in text.splitlines():
        stripped = line.strip().strip('*"` ')
        if not stripped.upper().startswith("GOAL:"):
            continue
        answer = stripped[len("GOAL:"):].strip().strip('.*"` ').upper()
        if answer.startswith("YES"):
            return True
        if answer.startswith("NO"):
            return False
    return None


# Physical evidence channel (robot's own sensors) — see fuse_evidence().
# UNCLEAR is deliberately distinct from NONE: NONE means "this step type has
# no physical completion signal at all", UNCLEAR means "there IS a reading and
# it landed too close to the threshold to claim either way". Both defer to the
# camera, but only one of them says something went measured-but-inconclusive,
# which is worth telling apart in the run data.
PHYS_CONFIRM, PHYS_DENY, PHYS_NONE, PHYS_UNCLEAR = "CONFIRM", "DENY", "NONE", "UNCLEAR"


def fuse_evidence(phys: str, phys_note: str, vis: str,
                  v_tag: str = "", v_reason: str = "") -> tuple[bool, str, str, str]:
    """Combine the physical and visual verdicts into (success, tag, reason, conflict).

    The two channels measure DIFFERENT propositions and are therefore
    complementary rather than redundant:

      physical — did the robot's own sensors register the completion event
        that defines this step type (protocol B: the jaws are loaded, i.e.
        SOMETHING is held; protocol A: the joints stopped). It cannot say
        WHAT is held, or whether it ended up in the right place.
      visual — does the workspace semantically match the expected outcome
        (the right object, the right location). It cannot reliably judge
        grip force, and loses badly to occlusion when the gripper itself
        hides the very spot being judged.

    So neither may veto the other outright, which is what the earlier
    if/elif chain effectively did (a grasp step failing protocol B was
    marked failed without ever asking the inspector; a settled reset step
    was marked succeeded without asking it either). Rules:

      - a step fails when both channels agree it failed, or when the channel
        that can actually observe the proposition in question says so;
      - the inspector may overrule physical evidence only with a DEFINITE
        tag, never with [unclear] — otherwise an uncertain small VLM would
        invent new failures out of camera-angle ambiguity;
      - any genuine disagreement is returned as `conflict` rather than being
        collapsed into the winning verdict, because "the jaws are loaded but
        the camera sees nothing held" is far more actionable for the planner
        (and far more interesting as thesis data) than a bare tag.

    `vis` is one of SUCCESS / FAIL / UNCLEAR / SKIPPED (inspector switched
    off — the "physical only" ablation) / NOIMG (camera actually broken).
    The last two are kept distinct on purpose: one is an experimental
    condition, the other is a fault.
    """
    if vis in ("SKIPPED", "NOIMG"):
        if phys == PHYS_CONFIRM:
            return True, "SUCCESS", f"Bez inspektora — fyzicky potvrzeno ({phys_note}).", ""
        if phys == PHYS_DENY:
            return False, "[object_missed]", f"Bez inspektora — fyzicky vyvráceno ({phys_note}).", ""
        if vis == "SKIPPED":
            return True, "SKIPPED", "Inspektor vypnutý (skip_inspector) a žádný fyzický důkaz — krok se nekontroloval.", ""
        # Missing camera frame with nothing physical to fall back on is a
        # genuine failure, not a free pass.
        return False, "[no_image]", "Snímek z kamery se nepodařilo získat a fyzický důkaz není k dispozici.", ""

    if phys in (PHYS_NONE, PHYS_UNCLEAR):
        # No usable physical claim — the camera decides on its own. For
        # UNCLEAR that is the whole point: a reading that landed inside the
        # dead-band around the threshold is not evidence, and pretending
        # otherwise is how a mis-tuned threshold silently becomes a verdict.
        reason = v_reason
        if phys == PHYS_UNCLEAR and phys_note:
            reason = f"{v_reason} (fyzika neprůkazná: {phys_note} — rozhodl inspektor ze snímku)"
        return (vis == "SUCCESS"), v_tag, reason, ""

    if phys == PHYS_CONFIRM:
        if vis == "FAIL":
            return False, v_tag, v_reason, (
                f"Fyzika krok potvrzuje ({phys_note}), ale inspektor hlásí konkrétní problém "
                f"{v_tag}: {v_reason}")
        if vis == "SUCCESS":
            return True, "SUCCESS", v_reason, ""
        return True, "SUCCESS", (
            f"Inspektor nerozhodl ({v_reason}) — nese fyzický důkaz ({phys_note})."), ""

    # PHYS_DENY
    if vis == "SUCCESS":
        return True, "SUCCESS", v_reason, (
            f"Fyzika krok nepotvrdila ({phys_note}), ale inspektor jasně vidí splněný výsledek: "
            f"{v_reason}")
    if vis == "FAIL":
        return False, v_tag, v_reason, ""
    return False, "[object_missed]", (
        f"Fyzicky vyvráceno ({phys_note}); inspektor nerozhodl ({v_reason})."), ""


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
        # Last gripper current seen in telemetry. Whether the arm is actually
        # holding something is much more reliable from this than from a vision
        # model squinting at a small object in a top-down photo, so it gets fed
        # into the planner's context.
        self.last_load: float | None = None
        # Idle/resting current sampled by the daemon before any grasp ever ran
        # in this session. "Present_Current" is a raw register value of unknown
        # scale, so treating 20 (or any other number) as an absolute mA
        # threshold is fragile; what's actually reliable is how far the
        # reading has moved from this baseline.
        self.last_baseline: float | None = None
        # Did the current sensor EVER report a non-zero value in this run? On
        # the SO-101 the reading comes from a direct motor-bus register read
        # that may simply not work (it reported a flat 0 on hardware before
        # extract_gripper_load() was rewritten). Without this flag a broken
        # sensor is indistinguishable from "nothing grasped", and the mandatory
        # grasp check below would fail every single grasp step forever.
        self.load_ever_nonzero = False

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
        # Termination protocols are task-specific: a task with no grasping
        # phase has no use for protocol B, and a different gripper or payload
        # needs a different current limit.
        if not cfg.get("protocol_a_enabled", True):
            cmd.append("--no-protocol-a")
        if not cfg.get("protocol_b_enabled", True):
            cmd.append("--no-protocol-b")
        cmd.append(f"--protocol-a.threshold={float(cfg.get('protocol_a_threshold_rad', 0.005))}")
        cmd.append(f"--protocol-a.patience={int(cfg.get('protocol_a_patience', 5))}")
        cmd.append(f"--protocol-b.limit={float(cfg.get('protocol_b_limit_ma', 250))}")
        cmd.append(f"--protocol-b.patience={int(cfg.get('protocol_b_patience', 3))}")
        cmd.append(f"--protocol-b.grace={float(cfg.get('protocol_b_grace_s', 0.75))}")

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
        if self.proc.poll() is not None:
            # _read_output() also sets _ready on EOF (process exit) so a
            # daemon that crashes before ever printing DAEMON_READY (e.g. a
            # broken import) unblocks this wait immediately instead of
            # silently eating the full daemon_start_timeout_s above.
            raise RuntimeError("Daemon skončil dřív, než nahlásil DAEMON_READY (viz log výše).")

    def _read_output(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("[SNAPSHOT] "):
                self._snapshot_b64 = line[len("[SNAPSHOT] "):].strip()
                self._snapshot.set()
            elif line.startswith("[TELEMETRY] "):
                payload = line[len("[TELEMETRY] "):]
                for field in payload.split("|"):
                    field = field.strip()
                    if field.startswith("load:"):
                        try:
                            self.last_load = float(field[len("load:"):])
                            if self.last_load > 0:
                                self.load_ever_nonzero = True
                        except ValueError:
                            pass
                    elif field.startswith("baseline:"):
                        try:
                            self.last_baseline = float(field[len("baseline:"):])
                        except ValueError:
                            pass
                self.emit("telemetry", message=payload)
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
        # Unblock start()'s readiness wait immediately if the process exited
        # (stdout closed) before ever reporting DAEMON_READY, instead of
        # leaving that wait to run out the full daemon_start_timeout_s.
        # start() itself checks proc.poll() right after to tell this apart
        # from a real ready signal and raise a clear error either way.
        self._ready.set()

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

    def run_task(self, task: str, timeout: float, is_grasp: bool = False, is_reset: bool = False) -> str:
        """SET_TASK + wait for TASK_DONE (the task latch). Returns the reason."""
        self._task_done.clear()
        self._done_reason = ""
        cmd = f"SET_TASK:{task}"
        if is_grasp:
            cmd += "|grasp"
        if is_reset:
            cmd += "|reset"
        cmd += f"|timeout={timeout:.1f}"
        self._send(cmd)
        if not self._task_done.wait(timeout + 3.0):
            self._send("STOP")
            return f"timeout po {timeout:.0f} s"
        return self._done_reason

    def snapshot(self, timeout: float = 10.0) -> list[str]:
        for _ in range(3):
            self._snapshot.clear()
            self._snapshot_b64 = ""
            try:
                self._send("SNAP")
            except RuntimeError:
                return []
            if self._snapshot.wait(timeout) and self._snapshot_b64:
                raw = self._snapshot_b64.strip()
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        return [str(v) for v in data.values() if v]
                    elif isinstance(data, list):
                        return [str(v) for v in data if v]
                except json.JSONDecodeError:
                    return [raw] if raw else []
            time.sleep(0.3)
        return []

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


def _dataset_episode_count(repo_id: str) -> int | None:
    """Episodes currently recorded under `repo_id`, or None if unknown/empty.

    Forward reference to `_dataset_stats` below is fine — Python resolves it
    at call time, once the whole module (defined further down in this file)
    has finished loading.
    """
    stats = _dataset_stats(repo_id)
    episodes = (stats or {}).get("episodes")
    return episodes if isinstance(episodes, int) and episodes > 0 else None


def _sized_output_dir(root: str, base_name: str, policy: str, repo_id: str) -> str:
    """Pick between the dataset-size-suffixed and the old flat checkpoint path.

    Recording more episodes into the same repo_id (--resume) and retraining
    used to always write to the same flat `<base_name>_<policy>` directory,
    so a retrain on a grown dataset either collided with (lerobot_train
    refuses to overwrite without --resume) or would have silently orphaned
    the checkpoint trained on the smaller slice. Now a fresh training run
    lands in `<base_name>_<N>ep_<policy>`, sized to the dataset's CURRENT
    episode count, so each dataset size gets its own checkpoint and none of
    them get overwritten.

    Unpinned resolution (here and via baseline_output_dir/step_output_dir)
    prefers that sized checkpoint once it's actually trained, but falls back
    to the flat legacy path as long as THAT is trained and the sized one
    isn't yet — so a checkpoint trained before this convention existed (or
    on a smaller slice you haven't retrained on since) keeps loading without
    having to pin it in "Správa modelů". A pair that's never been trained at
    all picks the sized path, since that's where a fresh training run
    should land.
    """
    legacy = f"{root}/{base_name}_{policy}"
    episodes = _dataset_episode_count(repo_id)
    if not episodes:
        return legacy
    sized = f"{root}/{base_name}_{episodes}ep_{policy}"
    if checkpoint_status(sized)["trained"] or not checkpoint_status(legacy)["trained"]:
        return sized
    return legacy


def baseline_output_dir(cfg: dict) -> str:
    if cfg.get("baseline_policy_path"):
        return cfg["baseline_policy_path"]
    root = cfg.get("output_root") or "outputs/training"
    task_slug = cfg.get("task_slug", "task")
    policy = cfg.get("policy_type", "act")
    return _sized_output_dir(root, task_slug, policy, f"local/{task_slug}")


def step_output_dir(cfg: dict, step_slug: str) -> str:
    for s in cfg.get("steps", []):
        if s.get("slug") == step_slug and s.get("policy_path"):
            return s["policy_path"]
    root = cfg.get("output_root") or "outputs/training"
    task_slug = cfg.get("task_slug", "task")
    policy = cfg.get("policy_type", "act")
    return _sized_output_dir(root, f"{task_slug}_{step_slug}", policy, f"local/{task_slug}_{step_slug}")


def step_catalog(cfg: dict) -> list[dict]:
    """Ordered per-step skills of the configured task."""
    out = []
    for step in cfg.get("steps", []):
        slug = (step.get("slug") or "").strip()
        if slug:
            entry = {"slug": slug,
                     "description": (step.get("description") or "").strip(),
                     "grasp": bool(step.get("grasp")),
                     "reset": bool(step.get("reset"))}
            if step.get("policy_path"):
                entry["policy_path"] = step["policy_path"]
            timeout_s = step.get("timeout_s")
            if timeout_s not in (None, ""):
                try:
                    entry["timeout_s"] = float(timeout_s)
                except (TypeError, ValueError):
                    pass
            verify_hint = (step.get("verify_hint") or "").strip()
            if verify_hint:
                entry["verify_hint"] = verify_hint
            train_steps = step.get("train_steps")
            if train_steps not in (None, ""):
                try:
                    entry["train_steps"] = int(train_steps)
                except (TypeError, ValueError):
                    pass
            out.append(entry)
    return out


def checkpoint_status(output_dir: str, target_steps: int | None = None) -> dict:
    """Whether `<output_dir>/checkpoints/last` resolves to a real, loadable
    checkpoint, and at how many training steps."""
    last = Path(output_dir) / "checkpoints" / "last"
    resolved = last.resolve() if last.exists() else None
    pretrained = (resolved / "pretrained_model") if resolved else None
    trained = bool(pretrained and (pretrained / "config.json").exists())
    steps = None
    if trained:
        try:
            steps = int(resolved.name)
        except ValueError:
            steps = None
    sufficient = trained and (target_steps is None or (steps or 0) >= target_steps)
    return {"path": output_dir, "trained": trained, "steps": steps,
            "target_steps": target_steps, "sufficient": sufficient}


def _dataset_exists_on_disk(repo_id: str) -> bool:
    """Check if a local dataset directory exists on disk.

    record_with_marks.py / lerobot-record always write "local/<name>" to
    LOCAL_DATASETS_DIR — this used to check `.../lerobot/<name>` (missing the
    "local" path segment) and `./local/<name>` relative to wherever this
    process happens to run, neither of which is where the data actually is,
    so every dataset looked "empty" here regardless of what was recorded.
    """
    if repo_id.startswith("local/"):
        name = repo_id[len("local/"):]
        return (LOCAL_DATASETS_DIR / name).exists()
    # Remote repo_ids: assume reachable
    return True


def _dataset_stats(repo_id: str) -> dict | None:
    """{episodes, frames} from meta/info.json, or None if unavailable/not local.

    Frames matter for picking --training.offline_steps: with batch_size B,
    one full pass over the dataset is roughly frames / B steps, so this is
    what you actually want on screen to size a training run, not just the
    episode count."""
    if not repo_id.startswith("local/"):
        return None
    info_file = LOCAL_DATASETS_DIR / repo_id[len("local/"):] / "meta" / "info.json"
    if not info_file.exists():
        return None
    try:
        with open(info_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return {"episodes": meta.get("total_episodes"), "frames": meta.get("total_frames")}
    except Exception:
        return None


def find_available_datasets(cfg: dict, step_slug: str | None = None) -> list[dict]:
    """List all dataset repo_ids available for the task and its steps.

    Returns a list of dicts:
        { repo_id: str, exists: bool, pinned: bool, episodes: int|None, frames: int|None }

    - ``exists``: dataset directory found on disk (see LOCAL_DATASETS_DIR).
      Derived names (local/task_step) are included even if data hasn't been
      collected yet — they serve as the split-target names for split_dataset.py.
    - ``pinned``: manually added by the user via the UI (stored in step.datasets[]).
    """
    task_slug = cfg.get("task_slug", "task")
    seen: dict[str, dict] = {}

    def add(repo_id: str, pinned: bool = False) -> None:
        exists = _dataset_exists_on_disk(repo_id)
        stats = _dataset_stats(repo_id) if exists else None
        if repo_id not in seen:
            seen[repo_id] = {"repo_id": repo_id, "exists": exists, "pinned": pinned,
                              "episodes": (stats or {}).get("episodes"),
                              "frames": (stats or {}).get("frames")}
        elif pinned:
            seen[repo_id]["pinned"] = True

    # 1) Whole-task baseline dataset (always shown — may not exist yet)
    add(f"local/{task_slug}")

    # 2) Per-step derived datasets (always shown — serve as split targets)
    for s in cfg.get("steps", []):
        slug = s.get("slug")
        if slug:
            add(f"local/{task_slug}_{slug}")

    # 3) Scan for any extra local/<task_slug>* datasets not covered above (e.g.
    #    a manually renamed split target). Scoped to THIS project on purpose —
    #    an unscoped scan would pull in every other project's datasets too,
    #    which is confusing at best and a good way to train on the wrong data
    #    at worst.
    if LOCAL_DATASETS_DIR.exists():
        for p in LOCAL_DATASETS_DIR.iterdir():
            if p.is_dir() and (p.name == task_slug or p.name.startswith(f"{task_slug}_")):
                add(f"local/{p.name}")

    # 4) Manually pinned datasets stored in config (always shown even if missing)
    if step_slug:
        step_obj = next((s for s in cfg.get("steps", []) if s.get("slug") == step_slug), None)
        if step_obj:
            for pinned_id in (step_obj.get("datasets") or []):
                if pinned_id:
                    add(pinned_id, pinned=True)
    else:
        for pinned_id in (cfg.get("baseline_datasets") or []):
            if pinned_id:
                add(pinned_id, pinned=True)

    # Sort: pinned first, then exists, then alphabetical
    result = sorted(seen.values(), key=lambda d: (not d["pinned"], not d["exists"], d["repo_id"]))

    # Move preferred step dataset to very top
    if step_slug:
        preferred = f"local/{task_slug}_{step_slug}"
        result = sorted(result, key=lambda d: (d["repo_id"] != preferred, not d["pinned"], not d["exists"], d["repo_id"]))

    return result


def list_trained_checkpoints(cfg: dict, step_slug: str | None = None) -> list[dict]:
    """Scan output_root for all trained checkpoints related to a step or baseline."""
    root = Path(cfg.get("output_root") or "outputs/training")
    task_slug = cfg.get("task_slug", "task")
    default_policy = cfg.get("policy_type", "act")
    
    active_path = None
    if step_slug:
        for s in cfg.get("steps", []):
            if s.get("slug") == step_slug and s.get("policy_path"):
                active_path = s["policy_path"]
    else:
        active_path = cfg.get("baseline_policy_path")
        
    if not active_path:
        active_path = step_output_dir(cfg, step_slug) if step_slug else baseline_output_dir(cfg)

    checkpoints = []
    if root.exists():
        for model_dir in root.glob("*"):
            if not model_dir.is_dir():
                continue
            dir_name = model_dir.name
            
            if step_slug:
                if not (dir_name.startswith(f"{task_slug}_{step_slug}") or f"_{step_slug}_" in dir_name):
                    continue
            else:
                step_slugs = [s.get("slug") for s in cfg.get("steps", []) if s.get("slug")]
                if any(f"_{s}" in dir_name for s in step_slugs if s):
                    continue
                if not dir_name.startswith(task_slug):
                    continue
            
            ckpt_info = checkpoint_status(str(model_dir))
            
            pol_type = default_policy
            for pt in ("act", "diffusion", "vqbet", "smolvla"):
                if dir_name.endswith(f"_{pt}"):
                    pol_type = pt
                    break
                    
            pretrained_cfg = model_dir / "checkpoints" / "last" / "pretrained_model" / "config.json"
            # Fallback guess from the directory name alone (overridden below
            # once we can read the checkpoint's actual dataset_repo_id): strip
            # the trailing "_<policy>" and, if present, the "_<N>ep" size
            # suffix that baseline_output_dir()/step_output_dir() add.
            guess = dir_name[: -(len(pol_type) + 1)] if dir_name.endswith(f"_{pol_type}") else dir_name
            head, _, tail = guess.rpartition("_")
            if tail.endswith("ep") and tail[:-2].isdigit():
                guess = head
            dataset_used = f"local/{guess}"
            if pretrained_cfg.exists():
                try:
                    with open(pretrained_cfg, "r", encoding="utf-8") as f:
                        pdata = json.load(f)
                        if pdata.get("dataset_repo_id"):
                            dataset_used = pdata["dataset_repo_id"]
                        elif pdata.get("repo_id"):
                            dataset_used = pdata["repo_id"]
                except Exception:
                    pass
                    
            ckpt_path = str(model_dir)
            is_active = (active_path and (ckpt_path == active_path or str(Path(ckpt_path).resolve()) == str(Path(active_path).resolve())))
            
            checkpoints.append({
                "name": dir_name,
                "path": ckpt_path,
                "policy_type": pol_type,
                "trained": ckpt_info["trained"],
                "steps": ckpt_info["steps"],
                "dataset": dataset_used,
                "active": bool(is_active)
            })

    return checkpoints


def model_status(cfg: dict) -> dict:
    """Checkpoint status, datasets and checkpoints for baseline and every configured step."""
    default_target = int(cfg.get("train_steps") or 0) or None
    
    baseline_status = checkpoint_status(baseline_output_dir(cfg), default_target)
    baseline_status["datasets"] = find_available_datasets(cfg, None)
    baseline_status["checkpoints"] = list_trained_checkpoints(cfg, None)
    # "pinned": did the user explicitly pick this checkpoint (via the model
    # modal's radio list), as opposed to just getting whatever the
    # naming-convention path happens to resolve to. Separate from "trained" —
    # a pinned checkpoint can still be untrained, and an unpinned one can be.
    baseline_status["pinned"] = bool(cfg.get("baseline_policy_path"))

    steps_dict = {}
    for s in step_catalog(cfg):
        slug = s["slug"]
        s_status = checkpoint_status(step_output_dir(cfg, slug), s.get("train_steps") or default_target)
        s_status["datasets"] = find_available_datasets(cfg, slug)
        s_status["checkpoints"] = list_trained_checkpoints(cfg, slug)
        s_status["pinned"] = bool(s.get("policy_path"))
        steps_dict[slug] = s_status

    return {
        "baseline": baseline_status,
        "steps": steps_dict,
    }


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
        # Set when a re-plan proposes the exact same remaining steps as the
        # attempt that just failed — a second consecutive repeat means the
        # planner has no other strategy, so run() aborts instead of quietly
        # burning the re-plan budget on copies of the same failed plan.
        self._last_replan_was_repeat = False

    def stop(self) -> None:
        self._stop.set()
        if self.daemon:
            self.daemon.stop()

    # -- layer 1: the CEO --------------------------------------------------
    def _build_planner_prompt(self) -> str:
        cfg = self.cfg
        lines = [PLANNER_SYSTEM_PROMPT]
        if cfg.get("scene_description"):
            lines.append("\nENVIRONMENT & SCENE\n" + cfg["scene_description"])
        lines.append(f"\nGOAL: '{cfg.get('task_slug')}' — {cfg.get('task_description', '')}")
        lines.append("\nAVAILABLE SKILLS (use ONLY these skill IDs):")
        for s in step_catalog(cfg):
            grasp_type = " [ends by closing the gripper on the object]" if s.get("grasp") else ""
            reset_type = " [RESET — returns the arm to a known/safe position from any state]" if s.get("reset") else ""
            lines.append(f"- '{s['slug']}': {s['description']}{grasp_type}{reset_type}")
        lines.append("")
        lines.append(PLANNER_OUTPUT_REASONING if cfg.get("planner_reasoning", True)
                     else PLANNER_OUTPUT_TERSE)
        return "\n".join(lines)

    def _gripper_note(self) -> str:
        """One line about what the gripper current says, or '' when unavailable.

        The daemon's Present_Load/Present_Current reading is a raw register
        value of unknown scale, so an absolute mA threshold is fragile.
        Instead this compares the reading against the idle baseline the
        daemon sampled before any grasp ran in this session: a rise of
        holding_limit_ma or more over that baseline indicates active torque
        on an object.
        """
        if not self.cfg.get("gripper_state_in_context", True):
            return ""
        if self.daemon is None or self.daemon.last_load is None:
            return ""
        load = self.daemon.last_load
        baseline = self.daemon.last_baseline or 0.0
        holding_limit = float(self.cfg.get("holding_limit_ma", 20))
        rise = load - baseline

        holding_str = "something appears to be held" if rise >= holding_limit else "nothing appears to be held"
        return f"ROBOT STATE: gripper load {load:.0f}, {rise:+.0f} vs. idle — {holding_str}"

    def _build_initial_context(self, instruction: str, has_image: bool) -> str:
        lines = [f"GOAL: {instruction}", "",
                 "INITIAL WORKSPACE & ROBOT STATE:"]
        note = self._gripper_note()
        if note:
            lines.append(note)
        if has_image:
            lines.append("The attached photo shows the current workspace state at the start of the session.")
            lines.append("FIRST: check if the goal is ALREADY satisfied in the photo (e.g. object is inside target location). If so, answer ONLY [\"DONE\"].")
            lines.append("SECOND: determine what phase the robot and environment are CURRENTLY in. "
                         "If an intermediate phase is ALREADY accomplished (e.g., an object is ALREADY grasped/held in the gripper or aligned), "
                         "do NOT plan skills that approach or grasp the object! Plan ONLY the remaining skills (e.g., transport/move and release) needed from this current state to reach the goal.")
        else:
            lines.append("No photo of the workspace is available; decide from ROBOT STATE.")
        return "\n".join(lines)

    def _build_replan_context(self, instruction: str, step: str, tag: str,
                              reason: str, replans: int, max_replans: int,
                              has_image: bool, insp_reason: str = "",
                              conflict: str = "") -> str:
        """Everything the planner needs to decide what to do next.

        Deliberately does NOT tell it to "start from the failed step" — that
        instruction is what made every re-plan produce the same tail of the
        catalog regardless of what actually happened. The planner gets the
        facts (what ran, what succeeded, how often this step has failed, what
        the gripper reports, the photo) and decides for itself.
        """
        expected = next((s.get("verify_hint") or s.get("description")
                         for s in step_catalog(self.cfg) if s["slug"] == step), step)
        failures_here = sum(1 for r in self.results if r["step"] == step and not r["success"])

        lines = [f"GOAL: {instruction}", "", "PROGRESS THIS RUN:"]
        if not self.results:
            lines.append("  (nothing completed yet)")
        for i, r in enumerate(self.results, 1):
            verdict = "SUCCESS" if r["success"] else f"FAILED {r['tag']}"
            ended = r.get("reason") or "?"
            insp = r.get("insp_reason")
            note = f" — {insp}" if insp else ""
            lines.append(f"  {i}. {r['step']} -> {verdict}  (ended: {ended}){note}")

        insp_line = f"The inspector reported {tag}" + (f": {insp_reason}" if insp_reason else ".")
        lines += ["", f"LAST STEP: '{step}' — should have achieved: {expected}", insp_line]
        if failures_here > 1:
            lines.append(f"This step has now failed {failures_here}x in this run — "
                         "repeating it unchanged is unlikely to work.")
        # A disagreement between the robot's own sensors and the camera says
        # far more about what actually went wrong than either verdict alone
        # (e.g. "the jaws are loaded but the camera sees nothing held" points
        # at gripping the wrong thing, or at an occluded view — two very
        # different fixes). Surface it verbatim instead of collapsing it into
        # the single tag above.
        if conflict:
            lines.append(f"EVIDENCE CONFLICT on this step: {conflict}")
            lines.append("The robot's sensors and the camera disagreed here. Take that into account: "
                         "the sensors know whether the gripper is loaded, the camera knows what is "
                         "actually where. Prefer a next step that would resolve the ambiguity or "
                         "re-establish a known state, rather than blindly repeating the same skill.")
        note = self._gripper_note()
        if note:
            lines.append(note)
        lines.append(f"Re-plan attempt {replans} of {max_replans}.")
        lines.append("")
        if has_image:
            lines.append("The attached photo shows the workspace right now.")
            lines.append("FIRST: check if the goal is ALREADY satisfied in the photo (e.g. the target object is already inside the target location/destination). "
                         "If the goal is satisfied, respond ONLY with [\"DONE\"].")
            lines.append("Otherwise, observe the workspace state carefully (photos + ROBOT STATE + PROGRESS THIS RUN). "
                         "If an intermediate stage is already accomplished or if an object is already held/aligned, "
                         "do NOT repeat earlier skills. Plan ONLY the remaining skills needed from this current state to reach the goal.")
        else:
            lines.append("No photo is available — evaluate ROBOT STATE and PROGRESS THIS RUN to plan the remaining skills to reach the goal.")
        return "\n".join(lines)

    def _create_plan(self, instruction: str, images_b64: list[str] | None = None) -> tuple[list[str], str]:
        """Ask the CEO for a plan. On re-plan calls images_b64 are the same
        snapshots the inspector just judged — the CEO reasons from the failure
        *tag* either way, but the tag alone can't describe anything outside
        its small fixed vocabulary (a second object in the way, the wrong
        thing moved, ...). Without the photo, re-planning is really just
        "guess a fix from one word"; with it, the CEO can react to whatever
        actually changed on the table, not just the closest matching tag.
        """
        if self.cfg.get("skip_planner"):
            plan = [s["slug"] for s in step_catalog(self.cfg)]
            self.emit("log", level="WARN",
                      message="Plánovač přeskočen — použito pevné pořadí kroků.")
            return plan, ""

        if not self.cfg.get("planner_vision", True):
            images_b64 = None

        count_str = f" ({len(images_b64)} snímky kamery)" if images_b64 else ""
        self.emit("log", level="INFO",
                  message=f"CEO plánuje: „{instruction}\"{count_str}")
        system = self._build_planner_prompt()
        model = self.cfg.get("llm_model", "local-llm")

        def ask(with_image: bool) -> str:
            imgs = images_b64 if with_image else None
            return self.lm.chat_with_images(
                model=model,
                user_prompt=instruction,
                images_b64=imgs,
                system_prompt=system,
                temperature=0.1,
                max_tokens=2048
            )

        # A malformed reply (no parseable JSON array anywhere) used to kill
        # the whole run on the spot — one bad completion from a small local
        # model (truncated output, stray prose with no brackets) shouldn't
        # cost the entire trial when the other two layers (daemon, inspector)
        # already get a retry for their equivalent hiccups. One retry, with
        # an explicit correction appended so it's not just asking the same
        # question again and hoping for a different answer.
        original_instruction = instruction
        reply = ""
        for attempt in (1, 2):
            try:
                reply = ask(with_image=True)
            except Exception as e:
                if not images_b64:
                    raise
                self.emit("log", level="WARN",
                          message=f"Plánovač se snímkem selhal ({e}) — zkouším bez snímku "
                                  "(model plánovače asi neumí obraz).")
                reply = ask(with_image=False)

            reasoning = parse_reasoning_sentence(reply)
            plan = parse_json_array(reply)
            self.emit("log", level="INFO", message=f"Surový plán: {plan}")
            if reasoning:
                self.emit("log", level="INFO", message=f"Odůvodnění CEO: „{reasoning}“")
            if plan is not None:
                return plan, reasoning

            if attempt == 2:
                raise RuntimeError(f"CEO nevrátil platné JSON pole ani napodruhé: {reply[:200]}")
            self.emit("log", level="WARN",
                      message="CEO nevrátil platné JSON pole — zkouším znovu s upřesněním formátu.")
            instruction = (original_instruction +
                          "\n\nYour previous reply did not contain a valid JSON array of skill ID "
                          "strings. Follow OUTPUT FORMAT exactly: end your reply with ONLY a JSON "
                          "array on the last line, e.g. [\"skill_a\"] or [\"DONE\"].")

    def _resolve_plan(self, raw_plan: list[str]) -> list[str]:
        """Drop hallucinated IDs and expand the goal into its ordered steps.

        The two sentinels are passed through untouched so the caller can tell
        "the planner deliberately said there is nothing to do / nothing that
        can be done" apart from "the planner produced garbage" — both of which
        used to arrive here as an empty list.
        """
        steps = step_catalog(self.cfg)
        known = {s["slug"] for s in steps}
        goal = self.cfg.get("task_slug", "")

        sentinels = {PLAN_DONE, PLAN_ABORT}
        upper = [i.strip().upper() for i in raw_plan]
        for sentinel in (PLAN_DONE, PLAN_ABORT):
            if sentinel in upper:
                if len(raw_plan) > 1:
                    self.emit("log", level="WARN",
                              message=f"Plánovač vrátil {sentinel} spolu s kroky — "
                                      f"beru jen {sentinel}.")
                return [sentinel]

        resolved: list[str] = []
        for item in raw_plan:
            item = item.strip()
            if item.upper() in sentinels:
                continue
            if item == goal:
                resolved.extend(s["slug"] for s in steps)
            elif item in known:
                resolved.append(item)
            else:
                self.emit("log", level="WARN",
                          message=f"Neznámé ID kroku '{item}' — zahozeno.")
        return resolved

    # -- layer 3: the inspector ───────────────────────────────────────────
    @staticmethod
    def _read_verdict(raw_reply: str) -> tuple[bool, str]:
        """Map the inspector's reply onto a verdict.

        Reads the tag from the reply's LAST line — its final statement, same
        "read from the end" principle as parse_json_array() — since the reply
        now leads with a REASONING line (see VERIFY_PROMPT_RULES) that would
        break the old exact-match "the whole reply == SUCCESS" check. Falls
        back to scanning the whole text in case the model ignores the
        two-line format and puts the tag somewhere else.
        """
        lines = [l.strip() for l in raw_reply.splitlines() if l.strip()]
        last_upper = (lines[-1] if lines else raw_reply).upper()
        for tag in FAILURE_TAGS:
            if tag.upper() in last_upper:
                return False, tag
        if UNCLEAR_TAG.upper() in last_upper:
            return False, UNCLEAR_TAG
        if last_upper.strip('."\'*` ') == "SUCCESS":
            return True, "SUCCESS"

        upper = raw_reply.upper()
        for tag in FAILURE_TAGS:
            if tag.upper() in upper:
                return False, tag
        if UNCLEAR_TAG.upper() in upper:
            return False, UNCLEAR_TAG
        if upper.strip().strip('."\'*` ') == "SUCCESS":
            return True, "SUCCESS"
        return False, "[unknown_failure]"

    def _verify(self, step_slug: str, images_b64: list[str], plan: list[str] | None = None,
                step_index: int = 0, stop_reason: str = "") -> tuple[bool, str, str, bool | None, list[str]]:
        """(success, tag, reasoning, goal_satisfied, images_used).

        goal_satisfied is the inspector's read on the OVERALL task, not this
        step: True/False when it answered, None when it didn't (see
        parse_goal_flag) — callers must treat None as "no information".

        images_used is returned because an [unclear] verdict makes this method
        take a FRESH snapshot and re-ask; the caller must keep that newer
        photo, otherwise everything downstream (the re-plan context, the
        planner's own vision) would keep reasoning about the stale frame that
        was already judged too ambiguous to decide on.
        """
        cfg = self.cfg
        catalog = step_catalog(cfg)
        step_cfg = next((s for s in catalog if s["slug"] == step_slug), {})
        step_desc = step_cfg.get("description") or step_slug
        expected = step_cfg.get("verify_hint") or step_desc

        lines = [
            "You are the visual inspector of a robotic manipulation system.",
            "Your task is to inspect the attached photo(s) of the workspace after a step completed.\n",
        ]
        if cfg.get("scene_description"):
            lines.append(f"SCENE & ENVIRONMENT:\n{cfg['scene_description']}\n")

        lines.append(f"OVERALL GOAL: '{cfg.get('task_slug')}' — {cfg.get('task_description', '')}\n")

        if plan:
            lines.append("ACTIVE PLAN CREATED BY CEO PLANNER:")
            for i, p_slug in enumerate(plan, 1):
                p_cfg = next((s for s in catalog if s["slug"] == p_slug), {})
                p_desc = p_cfg.get("description") or p_slug
                grasp_note = " [grasps object]" if p_cfg.get("grasp") else " [positioning/approach, no grasp]"
                if p_cfg.get("reset"):
                    grasp_note += " [RESET skill]"
                if i - 1 < step_index:
                    status = "[COMPLETED PREVIOUSLY]"
                elif i - 1 == step_index:
                    status = "[JUST EXECUTED - VERIFY THIS NOW]"
                else:
                    status = "[PENDING LATER IN PLAN]"
                lines.append(f"  {i}. '{p_slug}' ({p_desc}){grasp_note} -> {status}")
            lines.append("")
        else:
            lines.append("TASK SKILLS CATALOG (ALL STEPS IN TASK):")
            for i, s in enumerate(catalog, 1):
                grasp_note = " [grasps object]" if s.get("grasp") else " [positioning/approach, no grasp]"
                if s.get("reset"):
                    grasp_note += " [RESET skill]"
                lines.append(f"  {i}. '{s['slug']}' ({s['description']}){grasp_note}")
            lines.append("")

        total_steps = len(plan) if plan else len(catalog)
        lines.append(f"STEP JUST EXECUTED (STEP {step_index + 1} OF {total_steps}): '{step_slug}' ({step_desc})")
        lines.append(f"EXPECTED OUTCOME TO VERIFY NOW: {expected}\n")

        # Physical grounding for the photo — same signals the CEO planner
        # already gets (see _gripper_note), just never previously reached the
        # inspector even though it is the one deciding SUCCESS/FAILED. A step
        # that ended by TIMEOUT never got a completion signal from the
        # controller (no Protocol A settle, no Protocol B grasp) — the photo
        # was taken at an arbitrary clock tick, possibly mid-motion, so a
        # "looks about right" read deserves less confidence than for a step
        # that ended because the robot itself signaled it was done.
        evidence_lines = []
        if stop_reason:
            ended_on_signal = ("Protokol A" in stop_reason) or ("Protokol B" in stop_reason)
            evidence_lines.append(f"- Step ended because: {stop_reason}.")
            if not ended_on_signal:
                evidence_lines.append(
                    "  This was NOT a completion signal from the robot/controller (no settling, "
                    "no grasp detected) — just the clock running out. The photo may show the arm "
                    "mid-motion rather than at rest. Weigh 'looks approximately right' accordingly.")
        gripper_note = self._gripper_note()
        if gripper_note:
            evidence_lines.append(f"- {gripper_note}")
        if evidence_lines:
            lines.append("PHYSICAL SENSOR EVIDENCE (from the robot, not the photo — use it to "
                         "disambiguate what the camera can't show, e.g. an object hidden behind "
                         "the gripper):")
            lines.extend(evidence_lines)
            lines.append("")

        lines.append(VERIFY_PROMPT_RULES)

        prompt = "\n".join(lines)

        model = self.cfg.get("vlm_model", "local-vlm")
        for attempt in (1, 2):
            reply = self.lm.chat_with_images(model=model, user_prompt=prompt, images_b64=images_b64, temperature=0.1, max_tokens=1024)
            raw_reply = reply.strip()
            self.emit("log", level="INFO",
                      message=f"VLM inspektor ({model}) odpovídá: „{raw_reply}\"")
            success, tag = self._read_verdict(raw_reply)
            reasoning = parse_reasoning_sentence(raw_reply)
            goal_done = parse_goal_flag(raw_reply)
            if reasoning:
                self.emit("log", level="INFO", message=f"Odůvodnění inspektora: „{reasoning}“")

            # [unclear] means "I cannot tell from this photo", not "it failed".
            # A fresh snapshot is far cheaper than a re-plan, so take one and
            # ask once more before treating it as a failure.
            if tag != UNCLEAR_TAG or attempt == 2 or self.daemon is None:
                break
            self.emit("log", level="WARN",
                      message="Inspektor nedokázal ze snímků rozhodnout — nový snímek.")
            fresh = self.daemon.snapshot()
            if not fresh:
                # Bez nového snímku by druhý dotaz jen zopakoval tentýž obrázek
                # a stál další volání VLM se zaručeně stejnou odpovědí.
                break
            images_b64 = fresh
            self.emit("snapshot", images=fresh, step=step_slug)
        return success, tag, reasoning, goal_done, images_b64

    # -- the loop ----------------------------------------------------------
    def run(self, instruction: str) -> dict:
        cfg = self.cfg
        max_replans = int(cfg.get("max_replans", 5))
        replans = 0
        started = time.time()
        self.results = []

        try:
            self.emit("state", state="PLANNING")

            initial_images: list[str] = []
            catalog = step_catalog(cfg)
            if catalog and cfg.get("planner_vision", True) and not cfg.get("skip_planner"):
                try:
                    self.daemon = Daemon(cfg, self.emit)
                    self.daemon.start(step_output_dir(cfg, catalog[0]["slug"]))
                    initial_images = self.daemon.snapshot()
                    if initial_images:
                        self.emit("snapshot", images=initial_images, step="(výchozí scéna)")
                except Exception as e:
                    self.emit("log", level="WARN",
                              message=f"Výchozí snímek scény se nepodařilo pořídit ({e}) — "
                                      "plánuji bez něj.")
                    # Drop the process, not just the reference — an orphaned
                    # daemon keeps holding the serial port and the cameras.
                    if self.daemon:
                        self.daemon.stop()
                    self.daemon = None

            raw_plan, ceo_reasoning = self._create_plan(
                self._build_initial_context(instruction, bool(initial_images)),
                images_b64=initial_images or None)
            plan = self._resolve_plan(raw_plan)
            self.emit("plan", steps=plan, reasoning=ceo_reasoning)

            if plan == [PLAN_DONE]:
                self.emit("log", level="INFO",
                          message="Plánovač vyhodnotil, že cíl je už splněný — nic se nespouští.")
                return self._finish(True, started)
            if plan == [PLAN_ABORT]:
                raise RuntimeError("Plánovač označil úlohu za neproveditelnou z výchozího stavu.")
            if not plan:
                self.emit("log", level="WARN", message="Plán je prázdný — konec.")
                return self._finish(True, started)

            index = 0
            while index < len(plan):
                if self._stop.is_set():
                    self.emit("log", level="WARN", message="Běh zastaven uživatelem.")
                    return self._finish(False, started)

                step = plan[index]
                step_cfg = next((s for s in step_catalog(cfg) if s["slug"] == step), {})
                policy_path = step_output_dir(cfg, step)
                is_grasp = bool(step_cfg.get("grasp"))
                is_reset = bool(step_cfg.get("reset"))
                # Per-step timeout written by compute_step_timeouts.py;
                # falls back to episode_time_s when the script hasn't run.
                step_timeout = float(
                    step_cfg.get("timeout_s")
                    or cfg.get("episode_time_s", 20))
                self.emit("state", state="EXECUTING")
                self.emit("step", index=index, total=len(plan), step=step, phase="start",
                          policy=policy_path)

                # 1)+2) the muscles: one daemon, weights swapped per step, task
                # latch bounded by step_timeout. One retry with a fresh daemon
                # process if the existing one died mid-run (e.g. a wedged
                # serial port) — a single hardware hiccup shouldn't zero out
                # an otherwise fine trial.
                reason = None
                for attempt in (1, 2):
                    try:
                        if self.daemon is None:
                            self.daemon = Daemon(cfg, self.emit)
                            self.daemon.start(policy_path)
                        else:
                            self.emit("log", level="INFO",
                                      message=f"Hot-swap modelu na krok '{step}'.")
                            self.daemon.set_policy(policy_path)
                        reason = self.daemon.run_task(step, step_timeout, is_grasp, is_reset)
                        break
                    except (RuntimeError, OSError) as e:
                        # OSError (e.g. BrokenPipeError from _send()'s
                        # stdin.write()) is a real daemon-communication
                        # failure of the exact same "wedged serial port"
                        # shape as the RuntimeError this loop was built to
                        # retry — just raised by a different code path (the
                        # process dying between _send()'s liveness poll and
                        # the write itself) — so it needs the same retry.
                        if attempt == 2:
                            raise
                        self.emit("log", level="ERROR",
                                  message=f"Daemon selhal ({e}) — restartuji a zkouším "
                                          f"krok '{step}' znovu.")
                        self.daemon.stop()
                        self.daemon = None
                self.emit("step", index=index, step=step, phase="executed", reason=reason)

                # 3) physical validation + visual inspector
                self.emit("state", state="VERIFYING")
                images = self.daemon.snapshot()
                if images:
                    self.emit("snapshot", images=images, step=step)

                # ── Evidence fusion ──────────────────────────────────────
                # Both channels are always evaluated; fuse_evidence() combines
                # them (see its docstring for why neither may veto the other).
                phys, phys_note = PHYS_NONE, ""

                # Protocol B applies to grasp steps — but only when the sensor
                # demonstrably works. If no non-zero current has been seen even
                # once in this run, a flat zero means "no reading", not
                # "nothing grasped"; enforcing it then fails every grasp step
                # and burns the whole re-plan budget on a run that cannot
                # possibly succeed. In that case fall back to vision and say so.
                grasp_check_applies = is_grasp and cfg.get("protocol_b_enabled", True)
                if grasp_check_applies and not self.daemon.load_ever_nonzero:
                    grasp_check_applies = False
                    self.emit("log", level="ERROR",
                              message="Zátěž gripperu je celý běh nulová — čidlo nejspíš nic "
                                      "nevrací, takže fyzické ověření úchopu nelze použít. "
                                      "Krok posuzuje jen inspektor. Zkontroluj protokol B "
                                      "a čtení registru zátěže.")
                if grasp_check_applies:
                    if "Protokol B" in (reason or ""):
                        # Firing already required the rise to be sustained AND
                        # settled (see PROTOCOL_B_* in inference_daemon.py), so
                        # this direction is the strong inference.
                        phys, phys_note = PHYS_CONFIRM, "protokol B: čelisti registrují sevření"
                    else:
                        # NOT firing is the weak inference: it also happens when
                        # the threshold is merely mis-tuned. If the final rise
                        # landed just short of the limit, that is a measurement
                        # too close to call, not a denial — say so and let the
                        # camera decide instead of manufacturing a failure out
                        # of a threshold this project has already had to retune
                        # several times.
                        phys, phys_note = PHYS_DENY, "protokol B: zátěž gripperu nepřešla práh nárůstu"
                        limit = float(cfg.get("protocol_b_limit_ma", 250) or 0)
                        band = limit * float(cfg.get("protocol_b_deadband_frac", 0.25) or 0)
                        if limit > 0 and band > 0 and self.daemon.last_load is not None:
                            rise = self.daemon.last_load - (self.daemon.last_baseline or 0.0)
                            if rise >= limit - band:
                                phys = PHYS_UNCLEAR
                                phys_note = (f"nárůst {rise:.0f} je těsně pod prahem {limit:.0f} "
                                             f"(pásmo nejistoty {band:.0f})")
                elif is_reset and cfg.get("protocol_a_enabled", True):
                    if "Protokol A" in (reason or ""):
                        phys, phys_note = PHYS_CONFIRM, "protokol A: klouby se zastavily"
                    # A reset that timed out proves nothing either way — the
                    # arm may still be home — so it stays PHYS_NONE and the
                    # inspector decides alone.

                # Visual channel. SKIPPED (inspector deliberately off, the
                # "physical only" ablation) is deliberately distinct from
                # NOIMG (camera broken) — the first is an experimental
                # condition, the second is a real fault.
                v_success, v_tag, v_reason, goal_done = False, "", "", None
                if cfg.get("skip_inspector"):
                    vis = "SKIPPED"
                elif images:
                    # `images` is reassigned on purpose: on an [unclear]
                    # verdict _verify() re-snapshots, and the re-plan below
                    # must reason about that fresher frame, not the stale one.
                    v_success, v_tag, v_reason, goal_done, images = self._verify(
                        step, images, plan=plan, step_index=index, stop_reason=reason or "")
                    vis = "SUCCESS" if v_success else ("UNCLEAR" if v_tag == UNCLEAR_TAG else "FAIL")
                else:
                    vis = "NOIMG"

                success, tag, insp_reason, conflict = fuse_evidence(
                    phys, phys_note, vis, v_tag, v_reason)

                if tag == "[no_image]":
                    self.emit("log", level="ERROR",
                              message="Snímek z kamery se nepodařilo získat — krok "
                                      "označen jako neúspěšný.")
                self.emit("log", level="INFO",
                          message=f"Ověření kroku '{step}': fyzika={phys}"
                                  + (f" ({phys_note})" if phys_note else "")
                                  + f", inspektor={vis}"
                                  + (f" {v_tag}" if v_tag else ""))
                if conflict:
                    self.emit("log", level="WARN", message=f"Rozpor důkazů u kroku '{step}': {conflict}")

                att_num = len(self.results) + 1
                # phys/vis/conflict are recorded per attempt on purpose: the
                # thesis compares an orchestrated scheme against a monolithic
                # one, and "how often did the two evidence channels disagree,
                # and who was right" is exactly the kind of raw data that
                # cannot be reconstructed afterwards from a single verdict.
                self.results.append({"attempt": att_num, "replan": replans, "step": step,
                                     "success": success, "tag": tag, "reason": reason,
                                     "insp_reason": insp_reason,
                                     "phys": phys, "vis": vis, "conflict": conflict,
                                     "goal_seen_by_vlm": goal_done})
                self.emit("step", index=index, step=step, phase="verified",
                          success=success, tag=tag, reason=reason, attempt=att_num,
                          insp_reason=insp_reason, conflict=conflict)

                if success:
                    # The inspector is the layer with eyes, so it is also the
                    # one best placed to notice the overall goal is already
                    # satisfied — possibly earlier than the plan expected (a
                    # step can achieve the end state as a side effect). Only
                    # honoured when this step also succeeded, so a single
                    # confused "GOAL: yes" cannot end a run on its own, and
                    # recorded in the run summary so these runs stay
                    # auditable/filterable when the results are analysed.
                    if goal_done:
                        self.emit("log", level="INFO",
                                  message="Inspektor potvrdil splnění celkového cíle — běh končí, "
                                          "aniž by se dojel zbytek plánu.")
                        return self._finish(True, started, goal_early_exit=True)
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
                previous_remaining = plan[index:]
                context = self._build_replan_context(
                    instruction, step, tag, reason or "", replans, max_replans,
                    has_image=bool(images), insp_reason=insp_reason or "",
                    conflict=conflict)
                raw_plan, ceo_reasoning = self._create_plan(context, images_b64=images or None)
                plan = self._resolve_plan(raw_plan)
                self.emit("plan", steps=plan, replan=replans, reasoning=ceo_reasoning)

                if plan == [PLAN_DONE]:
                    self.emit("log", level="INFO",
                              message="Plánovač po selhání vyhodnotil, že cíl je přesto splněný.")
                    return self._finish(True, started)
                if plan == [PLAN_ABORT]:
                    raise RuntimeError(
                        f"Plánovač označil stav po selhání kroku '{step}' ({tag}) "
                        "za nezotavitelný.")
                plan_replaced_by_fallback = False
                if not plan:
                    # Robust fallback: retry from the failed step onwards
                    all_slugs = [s["slug"] for s in step_catalog(cfg)]
                    if step in all_slugs:
                        plan = all_slugs[all_slugs.index(step):]
                        plan_replaced_by_fallback = True
                        self.emit("log", level="INFO",
                                  message=f"Záložní re-plán: opakuji od kroku '{step}' -> {plan}")

                # Code-level loop guard: a re-plan only counts as a genuine new
                # strategy if it actually differs from what was just tried and
                # failed. A small local planner can (and in practice does)
                # propose the exact same remaining steps again despite the
                # prompt saying that's unlikely to work — prompt wording alone
                # doesn't reliably fix that, so this is a model-agnostic
                # backstop: one repeat is tolerated (logged), a second one in
                # a row ends the run with a clear reason instead of silently
                # spending the whole re-plan budget on copies of one attempt.
                if plan == previous_remaining:
                    if self._last_replan_was_repeat:
                        raise RuntimeError(
                            f"Plánovač navrhl u kroku '{step}' podruhé za sebou identický plán "
                            f"po selhání ({tag}) — nemá zjevně jinou strategii k dispozici, běh "
                            "ukončen místo tichého opakování.")
                    self._last_replan_was_repeat = True
                    self.emit("log", level="WARN",
                              message=f"Plánovač po selhání navrhl stejný plán jako předtím pro "
                                      f"krok '{step}' — beru na vědomí; při dalším identickém "
                                      "opakování běh ukončím.")
                else:
                    self._last_replan_was_repeat = False

                # Only re-emit when the fallback above actually replaced the
                # plan. The planner's own plan was already emitted (with its
                # reasoning) right after _resolve_plan, so emitting again
                # unconditionally sent the UI a second, identical 'plan' event
                # per re-plan — pure duplication in the live view.
                if plan_replaced_by_fallback:
                    self.emit("plan", steps=plan, replan=replans)
                index = 0
                if not plan:
                    raise RuntimeError("Re-plán vrátil prázdný plán.")

            # Reaching here means the *current* plan was walked to completion
            # by successful increments only (the while-loop's only way out
            # besides raising) — so the run succeeded. self.results still
            # holds every attempt, including ones from discarded pre-replan
            # plans; ANDing over all of them (the old behaviour) punished a
            # run for a failure that re-planning had already fixed, which is
            # exactly the resilience this scheme is supposed to get credit
            # for. Per-attempt detail for diagnostics stays in self.results.
            return self._finish(True, started)

        except Exception as e:
            self.emit("state", state="ERROR")
            self.emit("log", level="ERROR", message=str(e))
            return self._finish(False, started, error=str(e))
        finally:
            if self.daemon:
                self.daemon.stop()
                self.daemon = None

    def _finish(self, success: bool, started: float, error: str = "",
                goal_early_exit: bool = False) -> dict:
        summary = {
            "success": success,
            "error": error,
            "duration_s": round(time.time() - started, 1),
            # True when the run ended because the inspector reported the
            # overall goal already satisfied, rather than by walking the plan
            # to its end — kept in the raw data so these runs can be told
            # apart (or filtered out) during analysis.
            "goal_early_exit": goal_early_exit,
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
            atomic_write_text(runs / name, json.dumps(payload, indent=2, ensure_ascii=False))
            self.emit("log", level="INFO", message=f"Záznam běhu uložen: runs/{name}")
        except Exception as e:
            self.emit("log", level="WARN", message=f"Záznam běhu se nepodařilo uložit: {e}")
