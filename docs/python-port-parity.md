# Python Port Parity Audit

This document is the consolidated parity ledger for the Python rewrite. The
function map is generated from the frozen `adversarial-ai-coding.sh` function
declarations, in source order. A row is complete only when it names a concrete
Python replacement or an `intentionally-dropped(...)` rationale.

## Function map

| Bash function | Python replacement |
|---|---|
| `alias_env_or_default` | `config.Settings.from_env` |
| `list_run_state_ids` | `runstate.list_run_state_ids` |
| `release_run_lock` | `runstate.RunState.release_lock` |
| `print_resume_hint` | `cli._print_resume_hint` |
| `on_workflow_exit` | `cli._abort_message` and `cli.main` exception mapping |
| `install_run_traps` | `intentionally-dropped(Python try/except/finally handles WorkflowAbort, KeyboardInterrupt, and lock release)` |
| `acquire_run_lock` | `runstate.RunState.acquire_lock` |
| `parse_resume_conf` | `runstate.load_snapshot` |
| `write_resume_conf` | `runstate.snapshot_values` plus `runstate.write_snapshot` |
| `resume_check_immutable` | `runstate.check_immutable` |
| `resume_load` | `runstate.RunState.resume` plus the resume block in `cli.main` |
| `init_run_state` | `runstate.RunState.create` |
| `usage` | `cli.USAGE` and the no-argument branch in `cli.main` |
| `need` | `agents.validate_agents` and the git-work-tree check in `cli.main`; jq is intentionally no longer checked |
| `validate_engines` | `agents.validate_agents` |
| `notify` | `agents.notify` and `workflow.WorkflowContext.notify` |
| `metric` | `archive.RunArchive.metric` |
| `generated_at` | `archive.generated_at` |
| `safe_slug` | `archive.safe_slug` |
| `is_builtin_engine` | `agents.is_builtin_agent` |
| `resolve_model_args` | `agents.resolve_model_args` |
| `csv_row` | `archive.csv_row` |
| `write_csv_row` | `archive.write_csv_row` |
| `metrics_summary` | `archive.metrics_summary` |
| `art_path` | `archive.RunArchive.art_path` |
| `write_meta` | `archive.RunArchive.write_meta` |
| `archive_snapshot` | `archive.RunArchive.archive_snapshot` |
| `archive_text` | `archive.RunArchive.archive_text` |
| `prompt_file_instruction` | `prompts.prompt_file_instruction` |
| `prompt_template_path` | `prompts.prompt_template_path` |
| `read_prompt_template` | `prompts.read_prompt_template` |
| `render_prompt` | `prompts.render_prompt` |
| `archive_task` | `archive.RunArchive.archive_task` |
| `archive_git_state` | `archive.RunArchive.archive_git_state` |
| `abs_path` | `pathlib.Path.resolve` in `cli.main` |
| `establish_run_archive` | `archive.establish_run_archive` |
| `write_run_metadata` | `archive.RunArchive.write_run_metadata` |
| `write_log_metadata` | `archive.RunArchive.write_log_metadata` |
| `log_section` | `archive.RunArchive.log_section` |
| `init_live_state` | `runstate.init_live_state` |
| `engine_model` | `agents.agent_model` |
| `verdict_approved` | `review.verdict_approved` |
| `is_rate_limited` | `ratelimit.is_rate_limited` |
| `parse_reset_wait` | `ratelimit.parse_reset_wait` |
| `human_duration` | `ratelimit.human_duration` |
| `detect_gate` | `gates.detect_gate` |
| `detect_build_gate` | `gates.detect_build_gate` |
| `protected_violations` | `gitops.protected_violations` |
| `plan_tasks` | `runstate.plan_tasks` |
| `ensure_task_queue` | `runstate.ensure_task_queue` |
| `pop_task_queue` | `runstate.pop_task_queue` |
| `mark_plan_task_done` | `runstate.mark_plan_task_done` |
| `restore_or_record_acceptance_base` | `runstate.restore_or_record_acceptance_base` |
| `normalize_dual_spec_decision` | `dual_spec.normalize_dual_spec_decision` |
| `dual_spec_owner_slot` | `dual_spec.dual_spec_owner_slot` |
| `engine_for_slot` | `dual_spec.agent_for_slot` |
| `reviewer_slot_for_owner_slot` | `dual_spec.reviewer_slot_for_owner_slot` |
| `set_spec_roles_from_slot` | `workflow.set_spec_roles_from_slot` |
| `candidate_spec_for_slot` | `dual_spec.candidate_spec_for_slot` |
| `collect_review_suggestions_enabled` | `workflow.WorkflowContext.collect_review_suggestions` |
| `dual_spec_final_review_scope` | `dual_spec.dual_spec_final_review_scope` |
| `write_spec_merge_request_template` | `dual_spec.write_spec_merge_request_template` |
| `merge_request_has_content` | `dual_spec.merge_request_has_content` |
| `apply_dual_spec_decision` | `dual_spec.apply_dual_spec_decision` |
| `dual_spec_preflight` | `dual_spec.dual_spec_preflight` |
| `write_agents_section` | `prompts.write_agents_section` |
| `bootstrap_agents_md` | `prompts.bootstrap_agents_md` |
| `w_claude` | `agents._worker_claude` through `agents.run_worker` |
| `w_codex` | `agents._worker_codex` through `agents.run_worker` |
| `w_agy` | `agents._worker_agy` through `agents.run_worker` |
| `generic_engine_args` | `agents.generic_agent_args` |
| `run_generic_engine` | `agents._run_generic` |
| `w_generic` | `agents._run_generic` through `agents.run_worker` |
| `worker_fn_for_engine` | `agents.run_worker` dispatcher |
| `archive_engine_attempt` | `archive.RunArchive.archive_agent_attempt` |
| `engine_call` | `ratelimit.agent_call` |
| `work` | `workflow.work` |
| `check_protected` | `workflow.check_protected` |
| `review_prompt` | `review.compose_review_prompt` using the `review` template |
| `verdict_file_instr` | `review.compose_review_prompt` using the `verdict-file-instruction` template |
| `compose_review_prompt` | `review.compose_review_prompt` |
| `r_claude` | `agents._reviewer_claude` through `agents.run_reviewer` |
| `r_codex` | `agents._reviewer_codex` through `agents.run_reviewer` |
| `r_agy` | `agents._reviewer_agy` through `agents.run_reviewer` |
| `r_generic` | `agents._run_generic` through `agents.run_reviewer` |
| `reviewer_fn_for_engine` | `agents.run_reviewer` dispatcher |
| `collect_suggestions` | `review.collect_suggestions` |
| `show_blockers` | `review.show_blockers` |
| `run_review` | `review.run_review` |
| `gate_loop` | `gates.gate_loop` |
| `stage_done` | `runstate.RunState.stage_done` |
| `begin_stage` | `workflow.begin_stage` |
| `end_stage` | `workflow.end_stage` |
| `review_loop` | `review.review_loop` |
| `commit_work` | `workflow.commit_work` |
| `ensure_committed` | `gitops.ensure_committed` |
| `commit_if_dirty` | `workflow.commit_if_dirty` |
| `human_gate_spec` | `workflow.human_gate_spec` |
| `run_candidate_spec_review` | `dual_spec.run_candidate_spec_review` |
| `write_spec_comparison_index` | `dual_spec.write_spec_comparison_index` |
| `write_dual_spec_decision_file` | `dual_spec.write_dual_spec_decision_file` |
| `human_gate_dual_spec_decision` | `dual_spec.human_gate_dual_spec_decision` |
| `restore_dual_spec_decision` | `dual_spec.restore_dual_spec_decision` |
| `run_dual_spec_spec_stage` | `dual_spec.run_dual_spec_spec_stage` |
| `finish` | `workflow.finish` |
| `verify_last_head` | `gitops.verify_last_head` |
| `resume_workspace` | `gitops.resume_workspace` |
| `setup_workspace` | `gitops.setup_workspace` |
| `main` | `cli.main` startup plus `workflow.run_workflow` stage orchestration |

## Known deliberate divergences

| Divergence | Rationale | Pinning test |
|---|---|---|
| `resets at` and `resets on` absolute quota messages are parsed | The bash regex branches were unreachable; accepting real provider wording prevents false exponential backoff. | `tests/test_ratelimit_parsing.py::test_resets_at_absolute_date_parses`, `::test_resets_on_absolute_date_parses` |
| Clock rollover uses the next local wall-clock occurrence | `datetime` preserves the intended local clock across day and DST boundaries instead of blindly adding an epoch-day. | `tests/test_ratelimit_parsing.py::test_past_clock_time_rolls_to_tomorrow` |
| AM/PM hours are range checked | Python must reject hour 0 and values above 12 just as GNU `date` did. | `tests/test_ratelimit_parsing.py::test_out_of_range_hour_falls_through`, `::test_hour_zero_clock_falls_through`, `::test_hour_zero_absolute_date_returns_none` |
| Metrics ignore short CSV rows and keep first-seen stage ordering | A damaged trailing row cannot crash finish output, and ordering is stable instead of depending on awk associative iteration. | `tests/test_archive_helpers.py::test_metrics_summary_ignores_short_rows_and_keeps_first_seen_order` |
| Resume snapshots and ledgers use schema-tagged JSON | JSON is data-only, rejects unknown schemas/keys, and safely replaces the unreleased line-oriented formats. | `tests/test_runstate_snapshot.py::test_write_parse_roundtrip_keeps_spaces_and_quotes`, `::test_unknown_key_is_rejected`; `tests/test_runstate_crossstage.py::test_record_stage_and_head_checkpoint` |
| Snapshot values may contain newlines; `task_arg` persists only its first line | JSON safely preserves values that the bash format rejected, while the display-only task argument remains one line. | `tests/test_runstate_snapshot.py::test_newline_values_round_trip` |
| Gate commands use the platform shell | `subprocess.run(..., shell=True)` uses `cmd.exe` on Windows; detected commands only require portable `&&` chaining. | `tests/test_gates.py::test_run_shell_uses_platform_shell` |
| Agent argv0 is resolved with `shutil.which` | Windows CLI installations commonly expose `.cmd` shims that `Popen` needs resolved explicitly. | `tests/test_agents.py::test_generic_worker_resolves_argv0_with_shutil_which` |
| Successful Claude output that is not valid structured JSON is handled leniently | Preserve the useful text and session semantics rather than making a successful CLI process fatal solely because its envelope was malformed. | `tests/test_agents.py::test_claude_worker_invalid_json_success_keeps_session` |
| Human gates read interactive stdin instead of `/dev/tty` | Cross-platform Python has no portable `/dev/tty`; non-interactive stdin fails closed with actionable wording. | `tests/test_stageflow.py::test_default_ask_rejects_noninteractive_stdin`, `tests/test_dual_spec.py::test_preflight_requires_human_gate_and_tty` |
| jq is not a runtime requirement | Verdict and snapshot JSON are parsed with the standard library, removing an otherwise unnecessary executable dependency. | `tests/test_cli.py::test_startup_does_not_require_jq` |
| `setup_workspace` returns the selected path instead of changing process directory | The CLI owns the single `chdir`, keeping git helpers explicit and testable. | `tests/test_gitops.py::test_setup_workspace_branch_mode`, `::test_setup_workspace_worktree_mode`, `::test_setup_workspace_no_branch_mode` |
| Task-file paths use `Path.resolve` | This is the cross-platform replacement for the bash `abs_path` helper. | `tests/test_cli.py::test_task_file_argument_is_read` |

## Acceptance record

Parity gate 3 is complete. Both the quota-gated fresh real-agent E2E and the
deliberate Ctrl-C/resume procedure from Plan 6 Task 3 passed.

- Date and operator: 2026-07-12, Arthur (human-run)
- Models and arguments: Claude `sonnet` with `--effort=low`; Codex `gpt-5.5`
  with `-c model_reasoning_effort=low`
- Full E2E run id and result: **PASS**, run `20260712-000544`; pytest reported
  `1 passed, 1 deselected in 1319.97s`; workspace
  `C:\tmp\aicoding-full-e2e-20260712-000526`; completed marker present, lock
  absent, and `strutil/strutil_test.go` recorded as the protected test
- Interrupted run id and write-code interrupt point: **PASS**, run
  `20260712-003327`, interrupted during `write-code`; workspace
  `C:\tmp\aicoding-interrupt-e2e-20260712-003305`
- Exit 130, one resume hint, and released lock: **PASS**, exit `130`, lock
  absent after interruption
- Resume skipped completed stages without repeated spec/plan calls: **PASS**,
  resume archive `20260712-003327-2` logged skips for `write-spec`,
  `commit-spec`, `write-implementation-plan`, and `write-acceptance-tests`;
  its metrics contain only `write-code` and `final-review-and-fixes`
- Completion marker and completed-run refusal: **PASS**, resume exited `0`,
  completed marker present, lock absent, and a subsequent resume exited `1`
  with `already completed; nothing to resume`
- Overall parity gate 3: **PASS**
