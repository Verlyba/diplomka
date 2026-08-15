# NOTES

Running log for the automated daily maintenance routine on this repo. Open
tasks at the top; dated entries below, newest first.

## Open tasks

- **[design decision needed] Setup page's "Uložit" can silently revert
  config.json edits made outside that browser tab — e.g. the still-open
  page can wipe out per-step `timeout_s` values just written by
  `compute_step_timeouts.py --apply`.** `web/setup.js` loads `cfg` into page
  memory once (`loadConfig()`), never refreshes it, and "Uložit" POSTs the
  whole in-memory object. `server.py` `save_config()`'s non-overwrite path
  does `merged = load_config(); merged.update(cfg)` — a shallow update, so
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
  `server.py` `create_project()` ~L240-248: the new project's config starts
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
  `orchestrator.py` `Orchestrator.run()` ~L1008-1016 vs. `_resolve_plan()`
  ~L858-865. `_resolve_plan()`'s own docstring says the `DONE`/`ABORT`
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
  analogous empty-plan case *after a re-plan* (~L1140-1150) already does the
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
  `orchestrator.py` `list_trained_checkpoints()` ~L618-626. Directory naming
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
  (`d.name == task_slug or d.name.startswith(f"{task_slug}_")`) and
  `orchestrator.py` `find_available_datasets()`'s disk-scan branch (same
  pattern). If one project's `task_slug` is a prefix of another's, the
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
  `max_replans`, `llm_timeout_s`, `daemon_start_timeout_s` are read by
  `orchestrator.py` but have no `data-key` input anywhere in `web/index.html`
  — only editable by hand-editing `config.json`. Flagging in case that's not
  intentional; not changing anything since adding form fields is a UI feature
  decision, not a bug fix.

- **[uncertain, low priority] VLM inspector verdict parsing checks failure
  tags via substring match over the whole reply, before requiring an exact
  "SUCCESS".** `orchestrator.py` `_read_verdict()` ~L895-906: `FAILURE_TAGS`
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
  ~L367-386 and the `_policy_event`/`_task_done` handling in `run_task()`/
  `set_policy()` ~L341-365: responses are matched to calls purely by shared
  mutable state (e.g. `_snapshot`/`_snapshot_b64` cleared then awaited), with
  no sequence ID. If a previous call's response arrives late — after that
  call already timed out and gave up — a later call could consume it as its
  own. Whether this is reachable depends on daemon responsiveness under real
  hardware/camera latency; flagging as a latent race, not a confirmed one.

- **[uncertain, low priority] Mark timestamps in `record_with_marks.py` may
  be systematically ~1 frame late.** `record_with_marks.py` ~L93-94/182-185:
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
