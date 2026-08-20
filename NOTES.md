# NOTES

Running log for the automated daily maintenance routine on this repo. Open
tasks at the top; dated entries below, newest first.

## Open tasks

- **[protocol semantics, needs a human decision] Protocol A's "settled" counter
  isn't reset on an observation/inference failure, so it can inflate from
  stale data instead of a real hold.** `inference_daemon.py` ~L725-763. In the
  `RUNNING` branch, `predict_and_act()` failing (camera read error, transient
  serial error, etc.) is caught by `except Exception as e: log.warning(...)`
  with no other effect — `joints`/`target` are left exactly as they were on
  the previous tick. Immediately after, `deltas = np.abs(target[:n_pos] -
  joints[:n_pos])` and `settled = settled + 1 if ... else 0` (~L744-746) run
  unconditionally on those stale values. If deltas were already under
  threshold on the last successful tick, every subsequent *failed* tick keeps
  incrementing `settled` off the same frozen numbers — Protocol A
  (`settled >= PROTOCOL_A_PATIENCE`, default 5 frames ≈167ms @30fps) can then
  fire purely from a string of read/inference failures, not from the arm
  actually having reached and held its target. Notably asymmetric with
  Protocol B: `load` is explicitly reset to `0.0` at the top of every tick
  (~L696), so a failure naturally suppresses Protocol B (`rise = load -
  baseline` goes negative) but nothing analogous resets/pauses Protocol A's
  settled-counter on failure — suggesting this may be an oversight rather
  than intentional. Whether the fix should be "reset `settled = 0` on
  exception" or "skip the termination-protocol check entirely for that tick"
  is a protocol-semantics call (changes exactly when/how a step ends under
  hardware flakiness), so left alone per the "never change protocol A/B
  thresholds/semantics on a guess" rule rather than picked unilaterally.
  Trigger: repeated `robot.get_observation()`/inference exceptions while a
  step is `RUNNING`, with the arm already near its target when the failures
  start. Not confirmed against a real hardware failure log — flagging the
  code-level asymmetry, not a reproduced incident.

- **[design decision needed] Setup page's "Uložit" can silently revert
  config.json edits made outside that browser tab — e.g. the still-open
  page can wipe out per-step `timeout_s` values just written by
  `compute_step_timeouts.py --apply`.** `web/setup.js` loads `cfg` into page
  memory once (`loadConfig()`), never refreshes it, and "Uložit" POSTs the
  whole in-memory object. `server.py` `save_config()`'s non-overwrite path
  (now ~L331, merge at L337-338 — drifted only from unrelated insertions
  earlier in the file; same logic) does `merged = load_config();
  merged.update(cfg)` — a shallow update, so
  `merged["steps"]` is wholesale replaced by whatever `steps` array the
  browser still has in memory, silently dropping any `timeout_s` (or other
  step field) that was written to `config.json` by another process since
  the page was loaded. Concretely: open Setup → split dataset → run
  `compute_step_timeouts.py --apply` in a terminal (writes `steps[].timeout_s`
  straight to `config.json`) → back in the still-open tab, tweak an unrelated
  field and click "Uložit" → the just-computed timeouts are gone, and the
  next orchestration run silently falls back every step to the flat
  `episode_time_s` — exactly the truncation problem
  `compute_step_timeouts.py` exists to fix, with no error either side. Fixed
  the other half of this (see 2026-08-14 entry: `compute_step_timeouts.py`
  now also mirrors into `projects/<slug>.json`, so merely *switching
  projects* no longer reverts the timeouts) but did not touch this
  Save-button trigger — a correct fix means either re-fetching config
  immediately before every save (risks clobbering unsaved form edits made in
  the meantime) or deep-merging `steps[]` by slug (risks reviving
  intentionally-deleted steps/fields), and picking between those is a UX
  design call, not a mechanical bug fix.

- **[uncertain, low priority] `web/setup.js`'s `arg()` quoting doesn't
  protect against PowerShell `$`/backtick expansion in free-text fields.**
  `web/setup.js` ~L179-184: `arg()` wraps values containing whitespace/braces/
  quotes in double quotes and its comment claims this "works in PowerShell,
  cmd.exe and bash" — but PowerShell expands `$variable`/`` `escapes `` even
  inside double-quoted strings, unlike cmd.exe/bash. A `task_description` or
  step description containing `$` (e.g. "kostka za $5") would have that
  substring silently expanded (usually to empty, since such a variable is
  normally undefined) when the generated `record_with_marks.py` command is
  pasted into PowerShell, changing the dataset's per-frame task-annotation
  text with no error anywhere. Plausible but requires specific characters in
  free-text fields to trigger, and a real cross-shell fix isn't mechanical —
  PowerShell/cmd/bash have incompatible quoting rules and the app
  deliberately generates one command line for all three — so left alone
  pending a decision on which shell to prioritize or whether to special-case
  PowerShell quoting.

- **[uncertain, low priority] `create_project()`'s field whitelist may drop
  more settings than intended when starting a new project.**
  `server.py` `create_project()` ~L279 (drifted from ~L240 only due to
  unrelated insertions earlier in the file; same logic): the new project's config starts
  from `dict(DEFAULT_CONFIG)`, then only a fixed whitelist of keys (python,
  device, robot/teleop/camera fields, `fps`, `lm_url`/`llm_model`/`vlm_model`,
  the protocol A/B settings) is copied over from the currently active
  project. Everything else — `output_root`, `policy_type`, `train_steps`,
  `batch_size`, `save_freq`, `data_strategy`, `max_replans`, `planner_vision`,
  `planner_reasoning`, `gripper_state_in_context` — silently resets to
  `DEFAULT_CONFIG` for every new project, even though these read more like
  machine-wide preferences (where to put training output, how the planner
  behaves) than per-task data. A user who tuned e.g. `output_root` or
  `planner_vision` once and then adds a second task/project for the same
  robot would get those reset without any indication. Could be intentional
  (each project's training config is meant to be independent) or an
  oversight in the whitelist — didn't want to guess at which behavior the
  thesis author wants, so left the whitelist as-is and flagging here instead.

- **[affects success accounting, needs a human decision] A garbage initial
  plan (all step IDs hallucinated) is recorded as a successful run.**
  `orchestrator.py` `Orchestrator.run()` ~L1041-1043 vs. `_resolve_plan()`
  ~L883 (both drifted only from unrelated insertions earlier in the file;
  same logic). `_resolve_plan()`'s own docstring says the `DONE`/`ABORT`
  sentinels exist specifically so the caller "can tell 'the planner
  deliberately said there is nothing to do / nothing that can be done' apart
  from 'the planner produced garbage'-both of which used to arrive here as an
  empty list." But `run()` doesn't act on that distinction: `if not plan:`
  (empty list — i.e. every step ID the LLM returned was unknown/hallucinated
  and dropped) is handled identically to `plan == [PLAN_DONE]` — both call
  `self._finish(True, started)`. So a planning call that produced zero usable
  steps gets written to `runs/*.json` as a **successful** run even though the
  robot never moved and nothing was verified. This looks like a real bug
  against the code's own stated intent, but fixing it changes what counts as
  "success" for these runs — i.e. it would alter the thesis's success-rate
  data, which is explicitly out of scope for an unattended fix. Note the
  analogous empty-plan case *after a re-plan* (~L1157-1177) already does the
  right thing (falls back to resuming from the failed step, or raises if that
  also fails) — only the *initial* planning call's empty-plan handling has
  this gap.

- **[protocol semantics, needs a human decision] Protocol A can preempt
  Protocol B on grasp steps before contact is ever detected.**
  `inference_daemon.py` ~L744-763: Protocol A's "settled" check
  (`elif use_triggers and use_protocol_a and settled >= PROTOCOL_A_PATIENCE`)
  is *not* gated on `active_is_grasp`, unlike Protocol B
  (`use_protocol_b and active_is_grasp and rise > PROTOCOL_B_LOAD_LIMIT`).
  The gripper joint is explicitly excluded from the Protocol A delta
  (`n_pos = max(joints.size - 1, 1)`), so on a grasp step the arm can be
  judged "settled" (5 consecutive frames ≈ 167ms @ 30fps by default) while
  the gripper is still mid-close and has not yet made contact — Protocol A
  fires first and the step ends before Protocol B ever gets a chance.
  In `orchestrator.py` the mandatory grasp check
  (`protocol_b_ok = not grasp_check_applies or ("Protokol B" in (reason or ""))`)
  then hard-fails the step as `[object_missed]` without asking the VLM
  inspector — regardless of whether the grasp was actually succeeding. This
  could systematically doom grasp steps and burn the re-plan budget on
  trials that never got a fair chance to trip Protocol B. Whether Protocol A
  should be suppressed while `active_is_grasp`, given a longer patience for
  grasp steps, or left as-is (maybe it's an intentional timeout-of-last-resort)
  is a call for the thesis author to make deliberately — not something to
  silently patch, per the "never change protocol A/B thresholds/semantics on
  a guess" rule.

- **[risky to mechanically fix] Checkpoint attribution uses unanchored
  substring matching, can attribute a checkpoint to the wrong step.**
  `orchestrator.py` `list_trained_checkpoints()` ~L619 (unchanged). Directory naming
  convention is `{task_slug}_{step_slug}_{policy_type}` (see
  `step_output_dir`/`baseline_output_dir`, ~L429-441). The step-lookup branch
  does `dir_name.startswith(f"{task_slug}_{step_slug}")` (no trailing `_`)
  OR `f"_{step_slug}_" in dir_name`. If one step's slug is a prefix of
  another's (e.g. steps `move` and `move_to_box`), a checkpoint dir for
  `move_to_box` (`{task}_move_to_box_{policy}`) also satisfies both branches
  when looking up checkpoints for step `move`, so it can appear in `move`'s
  checkpoint list / fine-tune-base dropdown / get picked as "active" for the
  wrong step. The baseline branch (excluding dirs matching any step slug) has
  the same substring-boundary problem in reverse. A correct fix needs to
  match the naming convention's segment boundaries precisely (e.g. require
  the known policy-type suffix and an exact middle segment) without breaking
  the loose fallback matching that may exist on purpose (e.g. to find
  checkpoints trained under an since-renamed task_slug) — didn't want to
  guess at the intended fallback behavior, so left alone. Test case to
  reproduce: set two step slugs where one is a prefix of the other (e.g.
  `grab` and `grab_tight`), train checkpoints for both, and check whether
  `list_trained_checkpoints("grab")` in `orchestrator.py` includes the
  `grab_tight` checkpoint dir.
  **2026-08-15 update:** the same root cause (unanchored `task_slug`
  prefix matching) also affects *dataset* visibility, in two more spots —
  not just checkpoint attribution: `server.py` `list_local_datasets()`
  (`d.name == task_slug or d.name.startswith(f"{task_slug}_")`, now L372) and
  `orchestrator.py` `find_available_datasets()`'s disk-scan branch (now L553,
  same pattern). If one project's `task_slug` is a prefix of another's, the
  Setup page's dataset overview and the model modal's "available datasets"
  list both leak the other project's dataset dirs into the current
  project's view. Same fix, same risk of breaking an intentional
  loose-match fallback — left alone for the same reason.

- **[informational, low priority] Dead API endpoints.** `GET /api/lmstudio`
  and `GET /api/runs` are implemented in `server.py` but no `web/*.js` file
  calls them — no UI exists to browse past runs or query live LM Studio
  models. Not a bug, just unused surface; leaving as-is since adding a UI for
  these would be a feature, not a fix.

- **[informational, low priority] Some config keys are config.json-only.**
  `max_replans` (`server.py` `DEFAULT_CONFIG` L128), `llm_timeout_s`
  (`orchestrator.py` L721), `daemon_start_timeout_s` (`orchestrator.py` L314)
  are read by `orchestrator.py` but have no `data-key` input anywhere in
  `web/index.html` — confirmed 2026-08-19 they're genuinely absent from both
  `DEFAULT_CONFIG` and `web/config.js`'s `DEFAULTS`, not just missing a form
  field — only editable by hand-editing `config.json`. Flagging in case that's not
  intentional; not changing anything since adding form fields is a UI feature
  decision, not a bug fix.

- **[uncertain, low priority] VLM inspector verdict parsing checks failure
  tags via substring match over the whole reply, before requiring an exact
  "SUCCESS".** `orchestrator.py` `_read_verdict()` ~L921 (drifted from ~L895
  only due to unrelated insertions earlier in the file; same logic): `FAILURE_TAGS`
  are matched with `tag.upper() in upper` against the entire raw reply,
  checked before the strict `upper.strip(...) == "SUCCESS"` branch. If a
  verbose/CoT-style local VLM reply mentions a bracketed tag string while
  explaining why that failure *doesn't* apply (but still concludes success),
  the step could be misclassified as that failure. Whether this actually
  happens depends on how strictly the configured local VLM follows "reply
  strictly with SUCCESS" — plausible but not observed/confirmed against real
  replies, so not touching the parsing logic on a guess.

- **[uncertain, low priority] No request/response correlation on the
  `Daemon` stdin/stdout protocol.** `orchestrator.py` `Daemon.snapshot()`
  ~L392 (drifted from ~L367 only due to unrelated insertions earlier in the
  file; same logic) and the `_policy_event`/`_task_done` handling in `run_task()`/
  `set_policy()` ~L341-365: responses are matched to calls purely by shared
  mutable state (e.g. `_snapshot`/`_snapshot_b64` cleared then awaited), with
  no sequence ID. If a previous call's response arrives late — after that
  call already timed out and gave up — a later call could consume it as its
  own. Whether this is reachable depends on daemon responsiveness under real
  hardware/camera latency; flagging as a latent race, not a confirmed one.

- **[uncertain, low priority] Mark timestamps in `record_with_marks.py` may
  be systematically ~1 frame late.** `record_with_marks.py` ~L205 (drifted
  only because the unrelated atomic-write fix touched `_persist()`'s write
  mechanics just above it, 2026-08-19; the counter itself is untouched):
  `_STATE["frames"]` is incremented at the *start* of the wrapped
  `add_frame()`, so by the time frame index 0 has been written, the counter
  already reads 1, and a mark taken right then computes `t = 1/fps` rather
  than `0/fps`. The module's own docstring states marks are meant to share
  the same time axis as the dataset's `timestamp` column (frame 0 -> t=0), so
  this looks like a real off-by-one — but marks are human-key-press-triggered
  and already coarse (the bias is one frame, ~33ms @30fps), and it's not
  certain which reading `split_dataset.py`'s bisect actually wants w.r.t.
  precision at a segment edge. Small enough and touches recorded-data timing
  closely enough that I didn't want to change it without the thesis author
  confirming the intended semantics.

## 2026-08-20

Housekeeping: `HEAD` was detached at `832fa7f` (yesterday's commit) with
local `main` stale at `31058a1`; `git fetch origin main` confirmed
`origin/main` was already at `832fa7f` too — checked out `main` and
fast-forwarded it to match, no data at risk (same recurring pattern as every
prior day). No commits had landed since yesterday's review, so today was
another fresh-angle pass rather than picking up new history.

Ran the test suite (`tests/test_split.py`, `test_merge.py`, `test_marks.py`)
for the first time in this routine's history — all three currently fail to
even import in this container (`ModuleNotFoundError: lerobot`/`numpy`,
`numpy` installable but `lerobot` is not, being LeRobot itself). This is a
sandbox-dependency gap, not a repo bug — noting it only so a future run
doesn't re-discover the same "can't run tests here" result from scratch;
the real target machine (with the conda env from `DEFAULT_CONFIG.python`)
presumably has both installed.

Re-verified all 8 standing open items are still present in current code —
no drift beyond what's already noted. Dispatched two parallel fresh-angle
review passes deliberately split by file group to cover more ground:
(1) `orchestrator.py` + `inference_daemon.py` — re-plan/step bookkeeping,
`_verify()`'s retry loop, `Daemon` process-lifecycle edge cases beyond what
prior days already fixed, Protocol A/B numeric-comparison edge cases;
(2) `server.py` + all of `web/` — request-body validation gaps, response-
shape↔fetch() parity, DOM-id wiring, numeric-input edge cases in `setup.js`.
Also independently traced the `_config_lock`/`atomic_write_json` interaction
added over the last two days for correctness (all 5 atomic-write call sites
compose correctly with the locking; no new issue) and confirmed the
`EventBus.subscribe()` race fix from yesterday is correct by re-deriving the
happens-before argument by hand.

Found and fixed (narrow, mechanical, no protocol/scoring impact — each
verified with a live repro before and after):

- `inference_daemon.py`: **`QUIT` used `os._exit(0)` from a background
  thread, which skips the `finally: robot.disconnect()` cleanup in
  `__main__` entirely** (~L579, ~L792-799). `os._exit()` terminates the
  process at the OS level without unwinding any thread's call stack,
  including the main thread running `main()`'s `while True:` loop — which
  has no other exit path. Since `Daemon.stop()` (`orchestrator.py`) sends
  `QUIT` as the *normal* end of every run (success, failure, or user-stop),
  this meant `robot.disconnect()` was skipped on essentially every
  orchestrated run, not just crashes — whatever LeRobot's `disconnect()`
  does beyond closing the OS file descriptor (releasing a lock, disabling
  torque) never ran. Replaced the direct `os._exit(0)` with a
  `threading.Event` (`_quit_requested`) that `stdin_reader()` sets and
  `main()`'s loop checks at the top of every tick, `return`ing normally so
  `__main__`'s `finally` actually executes. Verified live: spawned the
  daemon in simulated mode, sent `QUIT` over stdin, confirmed clean exit
  (rc=0) in ~0.16s — well inside `Daemon.stop()`'s existing 5s
  `proc.wait(timeout=5)`, so no user-visible behavior change beyond the
  cleanup now actually running. Pure process-shutdown-hygiene fix, same
  class as the already-landed `Daemon.stop()`-before-abandoning fix
  (2026-08-18) — does not touch protocol thresholds, timing, or any
  in-`RUNNING`-state logic.
- `orchestrator.py`: **`Daemon.start()` could block for the full
  `daemon_start_timeout_s` (default 180s) even when the subprocess crashed
  instantly** (`start()` ~L314, `_read_output()` ~L317-357). `_read_output()`
  only sets `self._ready` on a `DAEMON_READY` status line; if
  `inference_daemon.py` dies before ever printing it (e.g. a broken
  import — plausible given the file's own unguarded `import numpy as np`
  at module top, unlike the guarded `torch`/`lerobot` imports beside it),
  the `for line in self.proc.stdout:` loop just ends and logs "Daemon
  ukončen." with no effect on the `_ready.wait()` blocking `start()` — so a
  failure that's apparent in milliseconds could take the full timeout to
  surface, up to 3× per run (once for the initial-snapshot daemon, twice
  more in the per-step retry loop). Made `_read_output()` also set
  `_ready` when its loop ends (stdout EOF = process exited), and added a
  `self.proc.poll()` check right after `start()`'s wait returns to tell a
  real ready-signal apart from this early-exit case and raise a clear error
  immediately either way. Verified live: pointed `cfg["python"]` at a binary
  that exits instantly with no stdout — `start()` now raises in ~0ms instead
  of hanging for the configured timeout. Pure fail-fast robustness; the
  normal-startup path (`DAEMON_READY` printed, process still running) is
  unaffected.
- `orchestrator.py`: **the per-step daemon retry loop only caught
  `RuntimeError`, missing `BrokenPipeError`/`OSError` from the exact same
  failure class it was built to retry** (~L1082-1099). The loop's own
  comment says it exists so "a single hardware hiccup [a wedged serial
  port] shouldn't zero out an otherwise fine trial," and `Daemon._send()`
  does raise `RuntimeError` when its own liveness check
  (`self.proc.poll()`) finds the process already dead — but if the process
  dies in the gap between that check and the following
  `self.proc.stdin.write(...)`, `write()` raises `BrokenPipeError`
  (an `OSError` subclass) instead, which fell through the `except
  RuntimeError:` entirely and killed the whole run instead of getting the
  intended one-shot restart-and-retry. Widened the catch to
  `except (RuntimeError, OSError)`. Narrow timing race, not reproduced
  against a live hardware failure, but the fix is a direct, low-risk
  widening of an existing retry's exception coverage to match its own
  stated purpose — doesn't change retry count, timeout, or Protocol A/B
  semantics.
- `server.py`: **any POST body that was valid JSON but not a JSON object
  (a bare array, number, string, or `null`) crashed the connection instead
  of getting a clean error.** `_read_body()` (~L523-536) only rejected
  unparseable JSON; every POST route handler then does `body.get(...)`
  unconditionally, so e.g. posting `[1,2,3]` to `/api/projects/select`
  raised `AttributeError: 'list' object has no attribute 'get'` *outside*
  that route's own `try/except`, which `ThreadingHTTPServer` surfaces as a
  server-side traceback and a dropped connection (`RemoteDisconnected` on
  the client) rather than a JSON error response — the same failure class
  the 2026-08-19 entry fixed for three GET endpoints, but these seven POST
  routes weren't covered since the crash happens before their own
  try/except is even reached. Not reachable through normal use of
  `web/setup.js` (always sends real objects), so a robustness/defense gap
  rather than a currently-visible UI bug. Fixed at the single source —
  `_read_body()` now returns `None` (→ the existing clean 400 path) for any
  parsed-but-non-dict JSON, protecting all current and future POST routes
  at once instead of patching each call site. Verified live: POSTing
  `[1,2,3]` now returns a clean `400 {"ok": false, "error": "Tělo
  požadavku není platný JSON."}` instead of a reset connection.
- `server.py`: **`POST /api/run` had no exception handling**, unlike every
  other POST route (~L719-725, before the fix). `start_run()` itself has no
  try/except and does two unguarded `body.get(...)` calls — same
  `AttributeError` mechanism as above — and could also raise from
  `orch.Orchestrator(cfg, bus.publish)` construction; any of that crashed
  the connection instead of the `{"ok": false, "error": ...}` shape
  `web/run.js`'s start handler already expects (`run.js` does correctly
  re-enable the start button on a hard `fetch()` failure too, so this
  wasn't a stuck-UI bug, just a misleading "server neběží" message for a
  real error). Wrapped in the same `try/except Exception as e:
  self._send_json({"ok": False, "error": str(e)}, status=500)` pattern used
  everywhere else.
- `web/setup.js`: **clearing any top-level numeric config field silently
  saved it as `0`.** `readForm()` (~L68-74) ran `Number(el.value)` for every
  `type="number"` input on every `input`/`change` event, and
  `Number('') === 0` in JS — so momentarily blanking a field like
  `episode_time_s`, `train_steps`, `batch_size`, or either camera's
  width/height/fps while retyping it (or leaving it blank by accident, then
  clicking "Uložit" or tabbing away) wrote `0` into that field of `cfg`,
  which then flowed straight into the generated shell commands
  (`--batch_size=0`, etc.) and, if saved, into `config.json` with no
  validation on either side. Notably, the sibling per-step "Kroků tréninku"
  field already handles this correctly by `delete`ing the key on empty
  input (~L125-130) instead of defaulting to 0 — proving the failure mode
  was already known for that one field but the shared `readForm()` path was
  missed. A concrete downstream effect: `orchestrator.py`'s
  `model_status()` computes `int(cfg.get("train_steps") or 0) or None` —
  once `train_steps` is saved as `0`, every checkpoint's done/not-done (✓/✗)
  badge in the Setup UI stops reflecting real training progress. Fixed by
  skipping the assignment entirely when the field is blank (keeping the
  last known-good value in `cfg` instead of clobbering it with `0`) —
  simpler and lower-risk here than the per-step field's delete-key approach,
  since these top-level fields don't have a "no override, fall back to
  something else" semantics for an absent key the way the per-step field
  does.

All six fixes verified independently: `ast.parse`/`node --check` on every
touched file, plus a live repro-before/confirm-after for each of the five
that have one (the sixth, the retry-loop `OSError` widening, is a timing
race not independently reproducible without real hardware — verified by
code tracing only, per its own entry above).

Did not turn up any new open task today — the six findings above all met
the bar for a safe, narrow, mechanical fix and were applied directly;
nothing surfaced that needed a design/protocol-semantics call left
unresolved.

## 2026-08-19

Housekeeping: `main` was already clean and tracking `origin/main` at
`31058a1` — no detached-HEAD/stale-ref issue today, unlike every prior day.
No commits had landed since yesterday's review, so today was another
fresh-angle pass rather than picking up new history.

Re-verified all 8 standing open items against current code (dispatched
review). All still present, substance unchanged — a few line numbers
drifted, but only because today's own fixes below added code earlier in the
same files, not from any external change; updated the line references
in-place above rather than duplicating the items here.

Dispatched a review pass aimed at angles not yet covered: crash-safety of
every JSON write in the repo (write-then-rename vs. direct overwrite),
numpy-into-`json.dumps()` TypeError risk, SSE event-stream thread-safety
beyond the already-fixed config-write locking, exception-handling coverage
across `server.py`'s remaining endpoints, a full fresh re-read of
`web/style.css`/`web/orchestrace.html`, and a fresh hand re-derivation of
`compute_step_timeouts.py`/`split_dataset.py`'s boundary arithmetic against
edge cases (zero-duration step, single-step task, a mark exactly on a frame
boundary).

Found and fixed (narrow, mechanical, no protocol/scoring impact):

- **No JSON write anywhere in the codebase was crash-safe.** Every
  JSON-writing call site — `server.py`'s `save_config()` (→ `config.json`
  and `projects/<slug>.json`), `ensure_projects_dir()`, `create_project()`;
  `compute_step_timeouts.py --apply`'s two writes; `orchestrator.py`'s
  `_save_run()` (→ `runs/<timestamp>.json`, the raw thesis data);
  `record_with_marks.py`'s `_persist()` (→ `<dataset>.marks.json`, called
  after every keypress during a live recording — its own docstring already
  claimed "survives a crash mid-recording," a claim the code didn't actually
  keep); `merge_datasets.py`'s marks-sidecar write — wrote straight to the
  target path with `write_text()`/`json.dump()`. A crash or kill mid-write on
  a long-running robot-control app like this could leave any of these
  truncated/corrupt, most damagingly `config.json`, which every subsequent
  run reads. Added a small `atomic_write_json`/`atomic_write_text` helper to
  each of the 5 affected files: write to a temp file in the same directory
  via `tempfile.mkstemp()`, `chmod` it to `0o644` (mkstemp defaults to
  owner-only `0600`, which would otherwise have silently made every
  rewritten file less readable than the plain `open(path, "w")` it
  replaces — verified empirically against this environment's `022` umask),
  then swap it in with `os.replace()` (atomic on Windows too, unlike
  `os.rename()`). Verified all 5 files still parse and did a live
  write-then-read-back + permission-bits round trip. First half of this fix
  (`server.py`, `compute_step_timeouts.py`) landed as commit `4cc9764`
  mid-session (an auto-commit mechanism in this environment persisted it
  before the full review pass finished, matching the pattern noted in prior
  days' entries); the rest (`orchestrator.py`, `record_with_marks.py`,
  `merge_datasets.py`) landed together with this entry. Pure I/O-mechanics
  change — no data format, default value, or protocol logic touched.
- `server.py`: **`EventBus`'s SSE subscribe/history sequence had a race that
  could double-deliver one event to a reconnecting browser tab.**
  `_stream_events()` called `bus.subscribe()` (register the queue) and,
  separately, `bus.history()` (snapshot the log so a reload doesn't lose the
  run) — two independently-locked operations. `publish()` appends-to-history
  and snapshots-subscribers atomically under one lock; if a `publish()`
  landed in the gap between `subscribe()` and `history()`, that event ended
  up in both the history replay *and* the newly-registered queue — a
  duplicated log/plan/step event in a (re)connecting tab. Merged `subscribe()`
  into one atomic method that registers the queue and snapshots history
  under a single lock hold, returning both together; `history()` removed as
  now-unused. Verified with a standalone test (events published before/after
  `subscribe()` each land in exactly one of history-snapshot or queue, never
  both). UI/log-only, same class as the already-fixed 2026-08-12 duplicate-
  "plan"-event bug — no change to `self.results` or success/failure
  accounting.
- `server.py`: **three more endpoints lacked the try/except pattern the
  2026-08-16 fix established for `/api/models`.** `GET /api/projects`,
  `GET /api/datasets/local`, and `POST /api/config` (used on every "Uložit"
  click) had no exception handling, so a failure inside them (e.g. a project
  file becoming unreadable, or a dataset dir vanishing mid-`iterdir()` from a
  concurrent delete) would 500 with a raw traceback / reset connection
  instead of a clean JSON response. Wrapped all three in the same
  `try/except Exception as e: self._send_json({"ok": False, "error": str(e)},
  status=500)` pattern already used elsewhere.
- `web/config.js`: **`saveConfig()` never actually checked whether the save
  succeeded — needed together with the `/api/config` fix above.** It
  returned `true` unconditionally once `fetch()` resolved, checking neither
  `resp.ok` nor the body — before today's fix, a server-side exception in
  `save_config()` reset the connection with no response at all, which *did*
  make `fetch()` throw, so the existing "Uloženo do prohlížeče. Server
  neběží…" fallback happened to fire correctly by accident. Adding the
  try/except above turns that same failure into a clean HTTP 500 with a JSON
  body, which `fetch()` does *not* throw on — so wrapping `/api/config` alone
  would have flipped a real save failure into a falsely-reported "Uloženo do
  config.json" success message, worse than the raw error it replaced. Fixed
  by having `saveConfig()` `return resp.ok` instead of unconditional `true`;
  unchanged on the normal-success path. Landing this together with the
  `/api/config` try/except is why it's one entry, not two.

Angles that turned up nothing new (verified fresh rather than assumed):
numpy-into-`json.dumps()` TypeError risk (traced every `json.dumps`/
`json.dump` call site in `server.py`/`orchestrator.py`/`inference_daemon.py`
— the first two never import numpy at all, and `inference_daemon.py`'s one
JSON write only ever holds plain `str`, so no numpy scalar can leak in); a
full fresh read of `web/style.css` (551 lines) and `web/orchestrace.html`
(218 lines, including re-checking its Protocol A/B numeric claims against
current defaults — still accurate); hand re-derivation of
`compute_step_timeouts.py`/`split_dataset.py`'s boundary arithmetic against
zero-duration-step, single-step-task, and mark-exactly-on-a-frame-boundary
cases — all correctly handled, no new off-by-one beyond the already-logged,
unrelated marks-timestamp item.

Did not turn up any new open task today — everything found either got fixed
directly (all judged narrow/low-risk/mechanical) or was already covered by
an existing open item.

## 2026-08-18

Housekeeping: local `main` was again a stale ref behind a detached `HEAD`
that actually held yesterday's commit (`2804286`); `git fetch origin main`
confirmed `origin/main` was already at `2804286` too — reset local `main` to
track it, no data at risk (now a standing daily pattern in this environment,
noted only for continuity, not because anything went wrong). No commits
landed since yesterday's review, so today picked up with a fresh-angle pass
rather than new history.

Dispatched a review pass deliberately aimed at angles not yet covered by the
7 prior days' passes: Windows path-separator correctness across all `.py`/
`.js` files, `server.py`'s static-file path-traversal containment check, a
full key-by-key diff of `server.py` `DEFAULT_CONFIG` vs. `web/config.js`
`DEFAULTS` vs. `inference_daemon.py` argparse defaults, subprocess/process
lifecycle handling around `orchestrator.py`'s `Daemon` class, UTF-8/encoding
correctness across every file I/O and subprocess call given the Czech-text
UI, and a fresh re-check of dataset-tooling argparse flags against what
`web/setup.js` generates.

Found and fixed (narrow, mechanical, no protocol/scoring impact):

- `inference_daemon.py`: `sys.stdout`/`sys.stderr` were reconfigured to UTF-8
  at startup (with a comment explaining this is needed so Windows doesn't
  fall back to the console codepage, e.g. cp1250, for the Czech text sent
  through the pipe) but `sys.stdin` — read by `stdin_reader()`'s
  `for line in sys.stdin:` loop, which receives `SET_TASK:<step description>`
  commands from `orchestrator.py` — was never reconfigured, even though
  `orchestrator.py`'s `Popen(...)` explicitly writes to that pipe with
  `encoding="utf-8"`. On Windows this is a real encode/decode mismatch: a
  step/task description with any character outside cp1250 risks a silent
  `UnicodeDecodeError` inside `stdin_reader()`'s loop (uncaught there, which
  would kill command intake for the rest of the run), and even in-range
  Czech diacritics risk silent mojibake in `active_task` and the
  `TASK_STARTED`/`TASK_DONE` status lines. Added `sys.stdin` to the same
  reconfigure loop as stdout/stderr — same fix shape already applied there,
  just the input side that was missed.
- `server.py`: `delete_dataset_episodes()`'s `subprocess.run(...)` call for
  `lerobot_edit_dataset` (used by the `/api/datasets/delete-episodes`
  endpoint) had no `encoding=` argument, unlike the sibling `Popen` in
  `orchestrator.py` which explicitly passes `encoding="utf-8",
  errors="replace"`. With `text=True` and no explicit encoding, Python
  decodes the child's captured stdout/stderr using the platform's default
  locale encoding — on Windows, not UTF-8 — under strict error handling.
  `lerobot_edit_dataset`'s video-reindexing step (the function's own
  docstring notes it can run long) plausibly emits Unicode progress-bar
  characters, which would raise `UnicodeDecodeError` inside
  `subprocess.run()` itself, before the function's own `returncode` handling
  ever runs — surfacing as an opaque codec error to the browser instead of
  the real delete-episodes outcome. Added `encoding="utf-8",
  errors="replace"` to match the existing pattern used elsewhere in the
  codebase for the same class of subprocess call.
- `orchestrator.py`: `Orchestrator.run()` had two places
  (~L990-1000, initial-snapshot daemon; ~L1043-1060, per-step retry loop)
  that abandon `self.daemon` — setting it to `None` after a failure — without
  ever calling the daemon's own `.stop()` first. `Daemon.stop()` (sends
  QUIT, waits, kills on timeout) is implemented correctly and *is* called at
  the end of a normal/aborted run, but neither of these mid-run failure
  paths reaches it, so the just-spawned `inference_daemon.py` subprocess
  (which may hold the robot's serial port and camera handles) is left
  running, unreferenced, forever. The per-step retry loop's own comment
  literally names "a wedged serial port" as the scenario it's guarding
  against — meaning the most likely trigger for this path is exactly the
  case where leaking the old process is most damaging: the freshly-spawned
  replacement daemon's own hardware `open()` can then fail too, because the
  abandoned process is still holding the device. Added a `self.daemon.stop()`
  call at both sites before discarding the reference — pure process hygiene,
  does not touch protocol thresholds, config defaults, or success/failure
  accounting.
- `server.py`: `_serve_static()`'s path-traversal containment check used an
  unanchored string-prefix comparison (`str(target).startswith(str(WEB_DIR
  .resolve()))`), which would also accept a sibling directory whose name
  merely starts with `web` (e.g. a future `web-old/` or, on Windows'
  case-insensitive filesystem, `WEB2/`) — no such directory exists today so
  this isn't currently exploitable, but the check itself was doing the wrong
  thing. Switched to `target.is_relative_to(WEB_DIR.resolve())` (safe here —
  the project already uses PEP 604 `X | None` syntax elsewhere, so it's
  already on Python 3.10+, well past `is_relative_to`'s 3.9 floor). No
  behavior change for any legitimate request; purely correct containment
  logic for a check that was already intended to do exactly this.

Angles that turned up nothing new (verified fresh rather than assumed):
Windows path-separator handling (backend is consistently `pathlib`-based,
and the few forward-slash string paths in `orchestrator.py` are handed to
either `pathlib.Path()` or CLI tools that also use `pathlib`, so `/` is fine
on Windows too); full default-value diff across `server.py`/`config.js`/
`inference_daemon.py` (fully in sync, including everything fixed in prior
passes — the only non-`DEFAULT_CONFIG`-backed fallback found,
`cfg.get("task_slug", "task")` vs. the real default `"pick_and_place"`, is
dead in every real path since `load_config()` always merges
`DEFAULT_CONFIG` first, and isn't a numeric/threshold value, so not
reportable the way the previously-fixed 250-vs-280 case was); dataset-
tooling argparse flags vs. what `web/setup.js` generates (all match, re-
derived fresh from source rather than trusted from prior notes).

Did not re-verify all 7 standing open items line-by-line today (this pass
targeted new-bug-hunting on fresh angles, not re-confirmation); no commits
landed since yesterday that could have invalidated any of them.

## 2026-08-17

Housekeeping: local `main` was again a detached-`HEAD` situation with a stale
cached `remotes/origin/main` ref (showing `0ff9fb4`, many commits behind) —
`git fetch origin main` confirmed `origin/main` was actually already at
`dd13b92` (yesterday's commit), so the local ref was simply stale, not
actually behind; reset local `main` to track it. No commits landed since
yesterday's review, so today was another fresh deep pass rather than picking
up new history. No data was ever at risk in any of these housekeeping steps
(recorded here only because it's now a 6-day recurring pattern in this
environment, not because anything went wrong).

Dispatched a review pass deliberately aimed at angles prior passes covered
more lightly, rather than re-walking the same files top-to-bottom again:
`split_dataset.py`/`compute_step_timeouts.py` boundary-arithmetic correctness
worked out by hand (bisect convention, cut-list construction vs. per-step
duration), `inference_daemon.py`'s full stdin/stdout command protocol
cross-checked command-by-command and status-line-by-status-line against
`orchestrator.py`'s `Daemon` class, `server.py`'s dataset-management
endpoints (`pin`/`unpin`/`delete`/`delete-episodes`/`local`) traced
end-to-end against every corresponding `fetch()` in `web/setup.js` (body
keys, query params, response fields), `web/run.js`'s SSE `handleEvent()`
cross-checked against every `self.emit(...)` call site and payload shape in
`orchestrator.py`, and `measure_gripper_current.py`/`reset_homing.py` read
fresh for correctness bugs in isolation (not just cross-file coupling, as
prior passes checked).

**No new bugs found.** Every angle above checked out consistent with stated
intent — no off-by-ones in the boundary/timeout math, no command/param-name
or payload-shape mismatches in either protocol (daemon stdin/stdout or SSE),
no endpoint/param drift between `server.py` and `web/setup.js`. One item
surfaced during the review that isn't reportable as a bug either way and
isn't logged as an open task: whether `measure_gripper_current.py`'s
`'o'`→100.0 / `'c'`→0.0 open/closed convention actually matches this
hardware's real calibration can't be determined from the repo alone (it's a
physical-calibration question, not a code-logic one) — noting it here only
so a future pass doesn't waste time re-deriving the same "no evidence either
way" conclusion.

Did not re-verify all 12 standing open items line-by-line today (the
dispatched pass targeted new-bug-hunting in specific under-scrutinized areas,
not re-confirmation of already-logged ones); no commits landed since
yesterday that could have invalidated any of them, so low risk they've
silently drifted. Recommend the next pass either re-verify the full list
once for hygiene or start treating items unchanged for many consecutive days
as stable unless a related file actually changes.

No code changes today — nothing found met the bar for a safe, narrow,
mechanical fix.

## 2026-08-16

Housekeeping: local `main` was again a detached-`HEAD`/stale-ref situation
(same recurring pattern as every prior day) but pointed at the correct
commit already (`87b2815`, matching `origin/main`) — reset local `main` to
track it, no data at risk, no commits landed since yesterday's review.

Dispatched a fresh deep-review pass focused on areas covered more lightly
before: `server.py`'s HTTP/concurrency handling (exception paths, whether
any shared state is mutated without a lock, file-write atomicity),
`web/orchestrace.html`/`web/style.css` in full, `inference_daemon.py`'s full
stdin/stdout protocol and error paths beyond just Protocol A/B,
`server.py`↔`web/*.js` endpoint/param cross-check, and unit/off-by-one
scanning across timeout and threshold arithmetic. Re-confirmed all 10
standing open items are unaffected by this pass (not re-verified line by
line today — no commits landed to invalidate them).

Found and fixed (narrow, mechanical, no protocol/scoring impact):

- `server.py`: config-mutating request handlers had a real lost-update race
  under concurrent requests. `ThreadingHTTPServer` runs each connection on
  its own thread; `save_config()`, `select_project()`, `create_project()`,
  `delete_project()`, and the `/api/datasets/pin` and `/api/datasets/unpin`
  POST handlers all did `load_config()` → mutate a dict in memory →
  `save_config()` (full-file overwrite) with no locking at all (only
  `RunState` had a lock, and that guards run-start/stop, not config I/O).
  Two requests landing close together — e.g. two Setup tabs open, or a
  double-click on "Přidat dataset" — could interleave so the second
  `save_config()` call overwrote the first's change entirely, silently
  dropping it from `config.json`/`projects/<slug>.json` with no error on
  either side. This is a distinct mechanism from the already-logged "stale
  browser `cfg` on Save" item (that one is one tab's in-memory copy going
  stale over time; this is genuinely concurrent server-side writes racing on
  the same file). Added a module-level `_config_lock = threading.RLock()`
  and wrapped the read-modify-write body of each of those five call sites in
  `with _config_lock:` (RLock so `select_project()` etc. can call
  `save_config()` internally without deadlocking). Purely serializes
  concurrent writes — no change to any default, single-request behavior, or
  file format.
- `server.py`: `GET /api/models` had no exception handling, unlike its
  sibling `GET /api/lmstudio` right above it (~L539-547 before the fix),
  which wraps its call in `try/except` and returns a structured
  `{"ok": false, "error": ...}`. If `orch.model_status()` (or anything it
  calls — `checkpoint_status()`, `list_trained_checkpoints()`) raised for any
  reason, the exception propagated out of `do_GET` uncaught: a traceback to
  the server console and a broken connection instead of a normal HTTP
  response. `web/setup.js`'s `refreshModelStatus()` already tolerates both a
  non-OK status and a hard fetch failure (`if (!resp.ok) return` /
  `catch (_) { return; }`), so this was a robustness gap rather than a
  currently-visible bug. Wrapped the one line in the same try/except pattern
  as `/api/lmstudio`, returning `{"ok": false, "error": ...}` with a 500
  status on failure.

Found but NOT fixed — logged as a new open task above rather than a guess:
`inference_daemon.py`'s Protocol A "settled" counter isn't reset when
`predict_and_act()` raises during a `RUNNING` step — the stale `joints`/
`target` from the last successful tick keep feeding the settled-check, so a
run of read/inference failures can trip Protocol A even though the arm never
actually finished moving. Notably asymmetric with Protocol B's `load`, which
*is* explicitly zeroed every tick and so naturally self-suppresses on
failure. Whether to reset `settled` or skip the check entirely on a failed
tick changes step-termination timing under hardware flakiness — a protocol
call for the thesis author, not a mechanical fix, so left alone per the
standing "never touch protocol A/B semantics on a guess" rule.

No new UI/backend field mismatches, endpoint mismatches, or off-by-one/unit
errors turned up in the areas swept this round (`web/orchestrace.html`,
`web/style.css`, endpoint↔fetch cross-check, timeout/threshold unit
arithmetic) beyond what's already logged.

## 2026-08-15

Housekeeping: same recurring pattern as prior days — local `main` was a
stale ref (pointed at `0ff9fb4`) behind a detached `HEAD` that actually held
yesterday's commit (`98aadea`); a `git fetch origin main` showed
`origin/main` was already at `98aadea` too (the local remote-tracking ref was
just stale), so this was a plain fast-forward of local `main`, no data at
risk. No commits landed since yesterday's review, so today was another fresh
deep pass rather than picking up new history.

Re-verified all 9 standing open items against current code via a dispatched
review pass — all still present as described, no drift. Focused this pass's
new scrutiny on areas covered more lightly before: `web/run.js`'s full SSE
event handling, `server.py`'s dataset-management endpoints (pin/unpin,
delete, delete-episodes, create/select/delete project), and the standalone
scripts `measure_gripper_current.py`/`reset_homing.py` (confirmed these are
hand-run tools with no cross-file coupling to break). Also re-read
`inference_daemon.py` and `orchestrator.py` in full for anything the last 4
passes may have missed.

Found and fixed (narrow, mechanical, no protocol/scoring impact):

- `web/setup.js`: the "Doučit (fine-tune)" base-checkpoint dropdown in the
  model modal (`openModelModal()`, populated ~L1009-1023) set each
  `<option>`'s value to `ckpt.path` — the bare training-output directory
  returned by `orchestrator.py`'s `list_trained_checkpoints()` (e.g.
  `outputs/training/pick_and_place_grab_cube_act`), which has no
  `config.json` directly in it; the loadable model actually lives at
  `<that_dir>/checkpoints/last/pretrained_model`. The generated
  `lerobot_train --policy.path=...` command consumed this dropdown value
  verbatim (`updateModalCmdPreview()` ~L1099-1103) — whose own *fallback*
  branch (used only when nothing is selected) already appended the correct
  `/checkpoints/last/pretrained_model` suffix, proving the code already knew
  stock `lerobot_train` needs the nested path and doesn't get
  `inference_daemon.py`'s `resolve_policy_dir()` dual-path tolerance. Since
  browsers auto-select a dropdown's first option, this broke fine-tune
  command generation by default the moment any checkpoint existed — the
  normal, encouraged way to fine-tune produced a copy-pasteable command that
  would fail to load the base model, while leaving the dropdown untouched
  (the fallback path) worked. Fixed by appending the same
  `/checkpoints/last/pretrained_model` suffix where the dropdown option
  value is built, so both branches agree. Confirmed `finetuneBaseSelect` is
  used solely for this one purpose before changing it — not reused
  elsewhere for something that wants the bare directory (e.g. the "active
  checkpoint" radio buttons and `policy_path` assignments elsewhere in the
  same file correctly keep using bare `ckpt.path`, since
  `inference_daemon.py` tolerates both spellings there).

Found but NOT fixed — extended an existing open item rather than adding a
new one: the same unanchored `task_slug` prefix-matching pattern behind the
already-logged checkpoint-attribution bug also turned up in two more spots
(`server.py` `list_local_datasets()` and `orchestrator.py`
`find_available_datasets()`'s disk-scan branch), leaking one project's
dataset listing into another's if one `task_slug` is a prefix of the other.
Logged as a 2026-08-15 update under the existing checkpoint-substring-match
item above, same reasoning for leaving it alone (risk of breaking an
intentional loose-match fallback without knowing the intended semantics).

## 2026-08-14

Housekeeping: local `main` was again a stale ref (pointed at `0ff9fb4`) behind
a detached `HEAD` that actually held yesterday's commit (`cc7b0aa`);
`origin/main` already had it — fast-forwarded local `main` to match, no data
at risk. No commits landed since yesterday's review, so today was another
fresh deep pass rather than picking up new history.

Re-verified all 9 standing open items against current code — all still
present as described, none touched.

Dispatched a focused review of the areas prior passes covered more lightly:
`server.py`'s dataset-management endpoints, `web/config.js`, `web/run.js`'s
SSE handling, `compute_step_timeouts.py`, `split_dataset.py`, and the
dataset-management/multi-project UI added in `0ff9fb4` (newer code, less
scrutiny so far). Cross-checked argparse flags in the dataset-tooling
scripts against the commands `web/setup.js` generates, and every DOM id the
modal/project/dataset UI in `setup.js` looks up against `index.html`.

Found and fixed (narrow, mechanical, no protocol/scoring impact):

- `compute_step_timeouts.py`: `--apply` wrote the computed `steps[].timeout_s`
  straight to `config.json` only, bypassing `server.py`'s `save_config()` —
  which is the one place that normally keeps `config.json` and
  `projects/<task_slug>.json` in sync on every write. Because
  `select_project()` reloads `config.json` wholesale from the project file,
  simply switching to a different project and back in the Setup UI (no Save
  click needed) silently reverted the just-computed timeouts back to
  whatever was in `projects/<slug>.json`. Made `--apply` mirror its write
  into the matching `projects/<slug>.json` too, restoring the same
  config.json/project-file sync invariant every other write path already
  has. This does not fully close the bug — see the new open task above for
  the remaining Setup-page "Uložit" trigger, which is a design call rather
  than a mechanical fix.
- `server.py`: `delete_project()`'s fallback (picking a new active project
  after deleting the currently-active one) used an unsorted
  `PROJECTS_DIR.glob("*.json")`, so which project became active depended on
  filesystem directory-entry order rather than the alphabetical order the
  project dropdown (`list_projects()`, which does `sorted(...)`) otherwise
  implies. Sorted it to match.

Found but NOT fixed — logged as two new open tasks above: the Setup page's
stale in-memory `cfg` can still silently overwrite fresh on-disk config
(including timeouts) on Save (needs a UX design call between re-fetch-before-
save and deep-merge), and `web/setup.js`'s command-quoting doesn't protect
against PowerShell `$`/backtick expansion in free-text fields (a real
cross-shell fix isn't mechanical given the app intentionally generates one
command line for PowerShell/cmd/bash alike).

## 2026-08-13

Housekeeping: local `main` was again a stale ref behind a detached `HEAD`
that actually held yesterday's commit (`2a691ad`); `origin/main` already had
it (fast-forward only, no data at risk) — reset local `main` to match.
`git log` showed no commits since `2a691ad`, so there was nothing new to
review from prior runs — this was a fresh deep pass over files the last two
days already covered, looking for anything missed.

Re-checked every standing open item against the current code: all six are
still present exactly as described (Protocol A/B grasp preemption,
checkpoint substring matching, the empty-initial-plan success bug, dead
`/api/lmstudio` + `/api/runs` endpoints, config-only fields, VLM tag
substring matching, daemon stdin/stdout correlation, marks off-by-one). No
new evidence changed the confidence on any of them, so none were touched.

Read in full and re-verified line-by-line: `inference_daemon.py`,
`orchestrator.py` (all ~1200 lines, including the parts not quoted in past
entries — `Daemon` lifecycle, `_verify()`'s retry-on-`[unclear]` loop,
`_resolve_plan()`'s sentinel handling, `list_trained_checkpoints()`,
`model_status()`), `server.py` (config/project/dataset endpoints),
`web/config.js`, `web/run.js`, `web/setup.js` (all ~1185 lines, including the
model/dataset management modal), `split_dataset.py`,
`compute_step_timeouts.py`, `merge_datasets.py` (confirmed yesterday's
boundary-append fix is correct and complete), `record_with_marks.py`,
`measure_gripper_current.py`, `reset_homing.py`. Cross-checked every
`data-key` in `web/index.html` against `DEFAULT_CONFIG`/`DEFAULTS` and every
element ID `setup.js` looks up against the modal/project/dataset markup in
`index.html` — no drift found beyond what's already logged as open tasks.
Also checked `web/orchestrace.html`'s static explainer text (re-plan count,
Protocol A/B thresholds) against current config defaults — still accurate
after yesterday's fix.

Found one new item, logged above as low priority/uncertain rather than
fixed: `server.py`'s `create_project()` only carries a fixed whitelist of
hardware/model fields over from the current project into a newly created
one; several settings that read more like machine-wide preferences
(`output_root`, `planner_vision`, `max_replans`, etc.) silently reset to
`DEFAULT_CONFIG` instead. Could well be intentional per-project isolation,
so not changed on a guess.

Fixed: nothing this round — no new narrow, unambiguous, low-risk bug turned
up that wasn't already either fixed on a previous day or blocked on a
judgment call already logged above.

## 2026-08-12

Picked up from yesterday's open-task list plus a fresh pass. Housekeeping
first: local `main` had fallen behind a detached `HEAD` that actually held
yesterday's 4 commits (`09cedb7`..`9d39f05`) — fast-forwarded `main` to match;
`origin/main` already had them, so no data was actually at risk, just a stale
local ref.

Re-checked both standing open items from 2026-08-11 (Protocol A/B grasp
preemption, checkpoint substring matching) — both still present, still
correctly left alone pending a human decision.

Reviewed fresh, in more depth than the first pass: `record_with_marks.py`,
`split_dataset.py`, `merge_datasets.py`, `compute_step_timeouts.py` (dataset
tooling), `server.py` + all of `web/` (backend/frontend field and endpoint
matching), and `orchestrator.py`'s planner/inspector/re-plan loop.

Fixed (safe, narrow, no protocol/scoring-behavior change):

- `inference_daemon.py`: the module's own `PROTOCOL_B_LOAD_LIMIT` default
  (used only as the `--protocol-b.limit` argparse default) was still 250,
  while `server.py`'s `DEFAULT_CONFIG`, `web/config.js`'s `DEFAULTS`, and
  `orchestrator.py`'s cfg fallback (aligned yesterday in `454335b`) all use
  280. `orchestrator.py` always passes `--protocol-b.limit` explicitly on
  every daemon launch, so this was dead in every real code path — same shape
  as yesterday's fix, just the sibling file it missed. Aligned to 280.
- `merge_datasets.py`: when a per-step dataset was missing a given episode
  (e.g. that episode was deleted from one step's dataset but not the
  others), the `continue` that skipped copying its (nonexistent) frames also
  skipped appending that step's seam to the episode's `boundaries` list —
  silently producing a `boundaries` list shorter than `len(steps)-1` for
  that episode. `split_dataset.py` does catch the resulting count mismatch
  and drops the whole episode on re-split, so it wasn't silently-wrong data,
  but it was a real, avoidable loss of that episode. Restructured so the
  boundary is always appended for that seam (as a zero-length segment when
  the step contributed no frames), regardless of whether the step had
  frames for this episode.
- `orchestrator.py`: the re-plan path emitted a `"plan"` SSE event twice per
  re-plan — once unconditionally right after `_resolve_plan()` (~L1130,
  with the LLM's reasoning), and once more (~L1147) that was written to look
  like it belonged to the `if not plan:` fallback block but was actually a
  sibling statement at the same indent, so it fired on every re-plan
  regardless of whether the empty-plan fallback triggered. `web/run.js`
  treats every `"plan"` event with `replan` set as a new re-plan attempt, so
  this produced two duplicate "re-plan #N" entries in the run's progress
  summary and log for every real re-plan (the second with blank reasoning).
  UI/log-only — `self.results` and the re-plan counter were never affected.
  Moved the second emit inside the `if not plan:` block so it only fires
  when the fallback plan substitution actually changes what was already
  emitted.

Found but NOT fixed — logged above as new open tasks. The most significant
one: an initial plan that resolves to empty (every step ID hallucinated) is
currently recorded as a *successful* run, which looks like a genuine bug
against the code's own documented intent but touches success-rate accounting
directly, so it's left for a deliberate decision rather than an unattended
fix. The rest are lower-confidence/lower-impact items flagged for awareness.

## 2026-08-11

First run — no prior NOTES.md existed, so this covered the whole codebase
from scratch rather than picking up an open item list.

Reviewed: `inference_daemon.py`, `orchestrator.py`, `server.py`, and the
`web/` frontend (`setup.js`, `config.js`, `run.js`, `index.html`,
`orchestrace.html`) for correctness bugs and backend/frontend mismatches
(config fields, API endpoints, defaults). Dataset tooling
(`record_with_marks.py`, `split_dataset.py`, `merge_datasets.py`,
`compute_step_timeouts.py`) was read but no issues found there this round.

Fixed (safe, narrow, no protocol/behavior change for any currently-working
setup):

- `web/setup.js`: the `#boundaries` placeholder (example fixed-split
  boundary values) was computed once in `fillForm()` from the step count at
  page load, but never recomputed by `render()`, which runs on every
  add/remove/edit of a step. Adding or removing a step without reloading the
  page left the placeholder showing boundary values for the *old* step count.
  Since the "fixed boundaries" split command falls back to the placeholder
  text when the `#boundaries` field is left empty, a user who added a step
  and then copied the split command without noticing could pass the wrong
  number of `--boundaries` values to `split_dataset.py`, which silently
  skips every episode whose boundary count doesn't match — producing empty
  per-step datasets with exit code 0. Moved the placeholder computation into
  `render()` so it always reflects the current step count.
- `web/index.html` / `web/setup.js`: the "Vrátit výchozí" (reset to
  defaults) button had no event listener at all — a visibly dead control.
  Wired it to reset `cfg` to a deep copy of `DEFAULTS` (deep copy so editing
  steps afterwards can't mutate the shared `DEFAULTS.steps` array and
  corrupt future resets) and re-render the form.
- `web/orchestrace.html`: two places in the static explainer text said the
  planner gets "max 3×" re-plan attempts / "run ends in error after three
  failures", but `max_replans` defaults to 5 in `server.py`,
  `web/config.js`, and is used as 5 in `orchestrator.py`. Pure doc-text
  fix, no behavior change.
- `orchestrator.py`: the in-code fallback defaults used only when
  `protocol_b_limit_ma` / `holding_limit_ma` are absent from `cfg`
  (250 / 20) disagreed with the actual shipped defaults in
  `server.py`'s `DEFAULT_CONFIG` and `web/config.js`'s `DEFAULTS` (280 / 50).
  `server.py`'s `load_config()` always merges `DEFAULT_CONFIG` under
  `config.json`, so these fallbacks are dead in every real code path today
  (orchestrator.py is only ever invoked with a cfg built by
  `load_config()`) — this doesn't change behavior for any current run, it
  just removes a latent trap for future direct/standalone use of
  `Orchestrator`. Aligned both fallback literals to match the declared
  defaults (280 / 50).

Found but NOT fixed — logged above as open tasks, because they either touch
protocol A/B semantics or need a judgment call about intended matching
behavior that isn't safe to guess at.
