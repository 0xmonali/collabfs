# Test cases

Automated: `python3 -m unittest discover -s tests -t . -v` (all must say OK). Owner: Ananyaa.

This list is produced from the test descriptions in `tests/`.

## Document rules: version check, shifting, conflicts (unit)

| ID | Test | What it proves |
|---|---|---|
| T-01 | `BasicTests.test_bad_ranges` | Negative, inverted and out-of-bounds ranges are invalid_range. |
| T-02 | `BasicTests.test_future_version_rejected` | A base_version newer than the server's is malformed. |
| T-03 | `BasicTests.test_history_missing_means_stale` | A document loaded at version 5 has no history, so base 4 cannot be shifted: stale_version. |
| T-04 | `BasicTests.test_line_too_long` | A line longer than the limit is limit_exceeded. |
| T-05 | `BasicTests.test_malformed` | Wrong types, newlines inside a line, no-op edits and bool-as-int are malformed_edit. |
| T-06 | `BasicTests.test_rejected_edit_leaves_state_untouched` | A rejected edit never changes the lines or the version. |
| T-07 | `BasicTests.test_replace_insert_delete` | Replace, insert and delete work, bump the version by 1 and report the applied range. |
| T-08 | `ShiftingTests.test_edit_based_on_v0_shifted_through_two_later_edits` | An edit based on version 0 is shifted correctly through two later edits. |
| T-09 | `ShiftingTests.test_insert_above_earlier_edit` | Insert above an earlier edit keeps its line numbers. |
| T-10 | `ShiftingTests.test_insert_after_shrinking_replace` | Insert below an edit that removed a line moves up by one. |
| T-11 | `ShiftingTests.test_insert_at_start_of_replaced_block` | Insert at the first line of a replaced block stays before it. |
| T-12 | `ShiftingTests.test_insert_inside_replaced_block_conflicts` | Insert strictly inside a replaced block conflicts. |
| T-13 | `ShiftingTests.test_inserts_at_same_point` | Two inserts at the same point: the later one goes after the earlier one. |
| T-14 | `ShiftingTests.test_partial_overlap_conflicts` | Partly overlapping replaces conflict. |
| T-15 | `ShiftingTests.test_replace_above_earlier_edit` | A replace entirely above an earlier edit is unchanged. |
| T-16 | `ShiftingTests.test_replace_below_delete_shifts_up` | A replace below an earlier delete moves up by the number of deleted lines. |
| T-17 | `ShiftingTests.test_replace_below_insert_shifts_down` | A replace below an earlier insert moves down by the number of inserted lines. |
| T-18 | `ShiftingTests.test_replace_containing_earlier_insert_conflicts` | A replace range that contains the point of an earlier insert conflicts. |
| T-19 | `ShiftingTests.test_replace_ending_where_insert_happened` | A replace that ends exactly where an earlier insert happened is unchanged. |
| T-20 | `ShiftingTests.test_same_line_conflicts` | Two replaces of the same line conflict: first to arrive wins. |

## Server: edits, conflicts, reconnect, restart, robustness (real TCP)

| ID | Test | What it proves |
|---|---|---|
| T-21 | `EditTests.test_different_lines_both_accepted_and_shifted` | A stale edit on different lines is accepted and moved to the right place; the ack shows the new range. |
| T-22 | `EditTests.test_edit_acked_and_broadcast` | An accepted edit is acked to the author (with the applied range) and pushed to the other client. |
| T-23 | `EditTests.test_same_line_conflict_first_wins_then_retry` | Two edits to the same line from the same version: first wins, second gets conflict, re-fetches, retries. |
| T-24 | `EditTests.test_three_clients_same_line_one_winner` | 3 clients replace the same line at once: exactly one wins and all clients converge. |
| T-25 | `EditTests.test_three_clients_same_point_all_kept` | 3 clients insert at the same point at once: all 3 edits are kept and all clients end identical. |
| T-26 | `ReconnectTests.test_document_survives_server_restart` | The document and its version are saved on shutdown and reloaded on start. |
| T-27 | `ReconnectTests.test_edit_after_restart_is_accepted` | After a restart a client that continues from the saved version can still edit. |
| T-28 | `ReconnectTests.test_reconnect_gets_latest_document` | A client that disconnects and comes back sees the current document. |
| T-29 | `RobustnessTests.test_corrupt_state_file_refuses_to_load` | A corrupt state file stops the server instead of being silently overwritten. |
| T-30 | `RobustnessTests.test_first_message_must_be_hello` | Anything other than a valid hello first gets an error. |
| T-31 | `RobustnessTests.test_garbage_frame_does_not_kill_server` | An absurd frame length closes only that connection. |
| T-32 | `RobustnessTests.test_malformed_edit_rejected_connection_survives` | Garbage edit fields get a reject and the connection keeps working. |

## Client: applying updates, conflicts, offline edits (unit)

| ID | Test | What it proves |
|---|---|---|
| T-33 | `ClientTests.test_ack_uses_the_range_the_server_applied` | We edit line 4 while someone inserts a line above; the ack says the edit landed on index 4, and our copy matches. |
| T-34 | `ClientTests.test_bad_commands_do_not_crash` | Out-of-range numbers, missing arguments and unknown commands print a message. |
| T-35 | `ClientTests.test_conflict_reject_refetches_and_prints_diff` | A conflict reject asks for a fresh copy; the snapshot then prints a difflib diff of what changed. |
| T-36 | `ClientTests.test_edit_command_sends_correct_message` | 'r 2 X' sends an edit replacing line index 1 based on the current version. |
| T-37 | `ClientTests.test_offline_edit_is_refused_not_lost_silently` | With no connection an edit command says it was not sent. |
| T-38 | `ClientTests.test_other_reject_just_prints_reason` | Non-conflict rejects (e.g. invalid_range) are shown and do not trigger a re-fetch. |
| T-39 | `ClientTests.test_quit` | quit makes command() return False. |
| T-40 | `ClientTests.test_update_gap_triggers_resync` | An update that skips a version makes the client ask for a fresh copy. |
| T-41 | `ClientTests.test_update_in_order_is_applied` | An update with version = ours + 1 is applied. |

## Manual tests (run on real laptops, tick when done)

| ID | Steps | Expected | Done |
|---|---|---|---|
| S1 | Start server, connect alice | alice sees `document at version 0` | [ ] |
| S2 | alice `a hello` | `ok (v1)` | [ ] |
| S3 | connect bob | bob sees `1 \| hello` | [ ] |
| S4 | bob `r 1 HELLO` | bob `ok (v2)`; alice sees `bob changed lines 1-1` | [ ] |
| S5 | alice and bob type edits to the SAME line at the same moment | one `ok`, the other `CONFLICT` + diff; both end with the same text | [ ] |
| S6 | alice edits line 1 while bob edits line 5 at the same moment | both `ok`; bob's may say it was moved to the right place | [ ] |
| S7 | Close bob's terminal, alice adds a line, reopen bob with the same name | bob sees the current document | [ ] |
| S8 | Stop the server (Ctrl+C) with clients open, start it again with the same `--state` file | clients print `reconnecting...`, then show the saved document; edits work again | [ ] |
| S9 | `r 99 x`, `d 0`, `foo`, `a` with no text | friendly message, nothing crashes | [ ] |
| S10 | Run `python3 -m demo.demo_client --embedded` | ends with `CONVERGED` | [ ] |
| D1 | Deploy: `sudo ./deploy/install.sh` | `systemctl status collabfs` is active (running) | [ ] |
| D2 | `ss -tlnp \| grep 9000` | python3 listening on 0.0.0.0:9000 | [ ] |
| D3 | `sudo firewall-cmd --list-ports` | contains `9000/tcp` | [ ] |
| D4 | `journalctl -u collabfs -f` while two clients edit | connect / ACCEPT / REJECT lines appear | [ ] |
| D5 | `sudo kill -9 $(systemctl show -p MainPID --value collabfs)` | service restarts in about 2 s, document restored (at most ~1 s of edits lost) | [ ] |
| D6 | `sudo systemctl restart collabfs` | log says server stopped at version N, clients reconnect, nothing lost | [ ] |
| D7 | `sudo reboot`, then connect a client | service came up by itself | [ ] |

Automated tests: **41**.
