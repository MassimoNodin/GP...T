# Selective opponent processing: Gate A result

## Status

**Blocked for primary review; not ready for acceptance.** The final permitted T1
rerun still has one failing assertion. T2 was not run. The remaining failure is
in `test_P06_traffic_exclusions_dedup_coincidence_and_equal_nearest`: its fixture
leaves one valid car, and the contract says that a single other car can be both
the nearest car ahead and behind. The implementation returns both reasons; the
test currently expects only `traffic_ahead`. The T1 rerun limit is exhausted, so
there is no post-correction T1 run in this handoff.

## Changed paths and baseline work

New Gate A paths:

- `f1_engineer/processing/detail_policy.py`
- `f1_engineer/processing/detail_demand.py`
- `tests/test_detail_policy.py`
- `tests/test_detail_pipeline.py`
- `tests/test_detail_evidence.py`
- `tests/test_detail_demands.py`
- `tests/test_detail_api.py`
- `docs/selective-processing-implementation-result.md`

Modified allowed paths:

- `f1_engineer/pipeline.py`: keyword-only profile, post-inventory selection before detailed observation construction, and selection/row diagnostics.
- `f1_engineer/processing/coordinator.py`: profile configuration and recovery check, persisted selection changes, demand mutation journaling, target snapshots, deferred readiness, and owner status.
- `f1_engineer/processing/evidence.py`: additive `detail` attempt metadata and early deferred-attempt rejection.
- `f1_engineer/processing/runtime.py`: opt-in profile, bounded demand queue, owner command service, and detail status.
- `f1_engineer/api/app.py`: profile-specific evidence path and detail target/task endpoints.
- `f1_engineer/cli.py`: `api --detail-profile {full,demand_v1}` with `full` default.

Baseline dirty work was recorded before editing. `evidence.py` already had the
protected 32 MiB evidence-read limit; that change remains. `runtime.py` already
had the protected isolated Linux receiver and independent-thread behavior; those
changes remain. The pre-existing edits in `docs/migration-baseline.md`,
`tests/test_session_evidence.py`, every `web/` path, `f1_engineer/udp/isolated.py`,
`scripts/benchmark_receiver_isolation.py`, `tests/test_isolated_udp_receiver.py`,
and `unused-evidence.sqlite3` were preserved. The contract was not edited.

No existing test, dependency, receiver setting, deployment file, or production
database was changed. `sessions/car_lap_inventory.py` was not modified.

## Test-ID mapping

| IDs | Test function(s) |
| --- | --- |
| P01 | `test_P01_race_uses_position_order_not_physical_distance` |
| P02 | `test_P02_race_field_edges_and_single_car` (three position cases) |
| P03 | `test_P03_lapped_and_pitting_race_neighbor_remains_but_inactive_does_not` |
| P04 | `test_P04_invalid_duplicate_conflicted_and_unassessable_suppress_auto` (two player-position cases) |
| P05 | `test_P05_every_practice_qualifying_shootout_uses_circular_traffic` (each traffic enum) |
| P06 | `test_P06_traffic_exclusions_dedup_coincidence_and_equal_nearest` (**final assertion fails as described above**) |
| P07 | `test_P07_tt_and_unknown_have_no_automatic_selection` (TT, unknown, missing) |
| P08 | `test_P08_invalid_geometry_suppresses_traffic_but_keeps_task` |
| P09 | `test_P09_task_overlap_deduplicates_and_invalid_tenure_is_never_selected` |
| P10 | `test_P10_selection_order_and_reason_order_are_stable` (22 and 24 slots) |
| P11 | `test_P11_full_profile_preserves_all_car_rows_and_player_sample_records` |
| P12 | `test_P12_demand_profile_selects_three_and_keeps_all_field_tenures` |
| P13 | `test_P13_suppressed_car_observations_are_never_constructed` |
| P14 | `test_P14_current_roster_identity_replacement_invalidates_old_task_binding` |
| P15 | `test_P15_player_record_parity_between_full_and_demand_profiles` |
| P16 | `test_P16_neighbor_swap_applies_on_next_processed_frame_without_stale_car` |
| P17 | `test_P17_duplicate_conflicting_lap_packets_keep_player_and_missing_channel_diagnostics` |
| P18 | `test_P18_format_and_roster_shape_changes_clear_old_associations` (22→24 and 24→22) |
| P19 | `test_P19_thousand_stable_frames_construct_exact_rows_and_only_emit_selection_change` |
| P20 | `test_P20_four_task_targets_are_bounded_at_seven_and_release_does_not_fallback` |
| E01 | `test_E01_full_readiness_chunks_records_unchanged_and_detail_is_additive` |
| E02 | `test_E02_suppressed_attempt_is_deferred_and_raw_journal_matches_full` |
| E03 | `test_E03_partial_activation_and_reselection_do_not_stitch_laps` |
| E04 | `test_E04_gap_quarantine_keeps_precedence_and_saved_reports_immutable` |
| E05 | `test_E05_fault_recovery_keeps_single_attempt_publication` (five fault stages) |
| E06 | `test_E06_profile_mismatch_fails_and_legacy_missing_profile_is_full` |
| D01 | `test_D01_queued_is_not_active_and_effective_sequence_is_owner_applied` |
| D02 | `test_D02_renewal_and_identity_conflict_preserve_active_task` |
| D03 | `test_D03_task_command_and_terminal_snapshots_are_bounded_and_duplicate_posts_coalesce` |
| D04 | `test_D04_release_of_one_overlapping_task_preserves_other_active_task` |
| D05 | `test_D05_fake_clock_expiry_and_renewal_at_deadline` |
| D06 | `test_D06_new_activation_records_its_own_coverage_boundary` |
| D07 | `test_D07_stale_session_or_binding_is_rejected_without_retargeting` |
| D08 | `test_D08_interruption_terminates_active_task_without_changing_other_task` |
| D09 | `test_D09_journal_replay_uses_recorded_task_mutation_not_current_clock` |
| D10 | `test_D10_owner_revalidates_target_after_it_disappears` |
| D11 | `test_D11_concurrent_enqueue_and_read_only_snapshot_are_lock_safe` |
| D12 | `test_D12_shutdown_resolves_queued_commands_without_worker_retention` |
| A01 | `test_A01_cli_profile_default_explicit_and_evidence_path_separation` |
| A02 | `test_A02_post_delete_control_auth_and_app_wide_get_auth` |
| A03 | `test_A03_invalid_ids_unexpected_json_and_bare_slot_or_name_rejected` |
| A04 | `test_A04_targets_are_current_bounded_and_tasks_report_queue_then_owner_state` |
| A05 | `test_A05_unavailable_unknown_and_terminal_release_behaviors` |
| A06 | `test_A06_deferred_evidence_uses_existing_422_envelope` |
| A07 | `test_A07_runtime_status_has_bounded_counts_and_policy_row_meanings` |

Some mapped tests are narrower than the contract’s full assertions. In
particular, P12 uses a 22-slot pipeline fixture; P18 covers format/slot-count
changes rather than every listed lifecycle boundary; E03 does not establish a
later continuously selected lap; E05 checks duplicate publication IDs but not
full recovered selection/chunk/readiness equivalence; D06 and D09 are queue-level
checks rather than complete live coordinator recovery/coverage exercises; and
A06 checks the trace 422 envelope but not a selected comparison flow. Primary
review should treat those as remaining acceptance gaps.

## Validation history

Exact authorized commands were used from the repository root with the existing
`.venv` interpreter.

| Command | Run | Result | Duration |
| --- | ---: | --- | ---: |
| T0 baseline | 1 | 130 passed, 1 warning | 114.17s |
| T1 new tests | 1 | 23 passed, 46 failed, 2 warnings | 6.98s |
| T1 new tests | rerun 1 | 66 passed, 3 failed, 2 warnings | 8.01s |
| T1 new tests | rerun 2 | 68 passed, 1 failed, 2 warnings | 7.53s |
| T2 regression | not run | Stopped at the contract’s rerun limit with T1 still failing | — |

`git diff --check` reported no whitespace errors; Git printed line-ending
conversion warnings for the pre-existing and modified CRLF/LF files. `git diff
--stat` and `git status --short` were inspected. No other tests or checks were run.

## Synthetic examples and evidence

Examples use only the synthetic IDs in the prescribed tests:

- Queued request: `POST /api/v2/session-evidence/detail-tasks` with
  `{"task_id":"task-a","session_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","binding_id":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}`
  returns HTTP 202 and `command_state: "queued"`.
- Active request: after owner application, GET for `task-a` reports `state:
  "active"`, an activation ordinal/sequence, and `coverage_state:
  "waiting_for_complete_lap"`; a published attempt within that activation moves
  coverage to `available`.
- Expired request: injected-clock expiry produces `state: "expired"` and
  `reason: "detail_task_expired"` at the owner mutation sequence.
- Rejected request: a stale target produces `state: "rejected"` and
  `reason: "detail_target_not_current"`; player bindings are rejected with
  `detail_target_is_player`.
- Deferred evidence: GET trace returns the existing HTTP 422 error envelope with
  `reason: "trace_not_selected"` for a safely owned zero-row omitted attempt.

The passing P12 assertion currently demonstrates three detailed rows for a
22-slot race frame; P20 checks the seven-car ceiling with four task targets and
two automatic neighbors. E02 compares full/demand journal metadata for the same
synthetic stream and asserts a suppressed zero-row attempt is deferred. E03
asserts partial selection stays deferred. E05 runs five injected fault points
and checks attempt IDs are not duplicated. These are deterministic test counts,
not speed or losslessness evidence. The required 24-slot three-row integration
assertion and full recovery equivalence still need primary review/verification.

## Limitations and handoff

The profile is opt-in and not deployed. Historical backfill, UI changes, and LLM
integration are not included. Raw journal retention and disk growth are
unchanged. Windows tests cannot prove Linux receiver losslessness.

No out-of-scope edits, tests, dependencies, or production actions were made. No
branch was created; nothing was staged, committed, or pushed. Gate A is **not**
marked ready, and this handoff does not authorize deployment or Gate B/C.
