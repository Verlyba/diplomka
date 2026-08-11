# NOTES

Running log for the automated daily maintenance routine on this repo. Open
tasks at the top; dated entries below, newest first.

## Open tasks

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
