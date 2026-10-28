# Design decisions (running log) — add an entry for every choice: Choice / Why / Rejected alternatives / Trade-off

## V1
**D1 Python asyncio, single event loop.** The edit handler has no `await`, so two edits can never interleave: no locks needed. Rejected: threads (locks, races), multiprocessing (shared state needs IPC). Trade-off: one core (fine for a few clients).

**D2 Plain TCP.** Reliable ordered stream. Rejected: UDP (we would rebuild reliability; a lost edit breaks "no silent loss"), HTTP/WebSocket (not in the synopsis stack).

**D3 Framing = 4-byte length + JSON.** TCP has no message boundaries; the prefix says how many bytes to wait for and allows a 1 MiB cap before allocating. Rejected: newline-delimited (no size check until the newline), fixed-size (wasteful), binary formats (extra dependencies).

**D4 Concurrency = optimistic locking.** An edit carries the version it was based on; the server applies it only if that equals the current version. Rejected: pessimistic locks (a disconnected client can block everyone), last-write-wins (silently loses edits), OT/CRDT (too complex to build and defend; synopsis excludes character-level merge).

**D5 One integer version per file (Option A).** Server is the only authority, so a version vector collapses to one counter. Trade-off in V1: *any* stale edit is rejected, even on different lines (V2 fixes this with overlap detection, Option B).

**D6 Edit = line-range replace `[start,end) -> new_lines`.** One message shape covers insert, delete and replace. Rejected: whole-file replace (overwrites everyone), char-level ops (needs OT/CRDT).

**D7 Conflict handling = reject and resync.** First edit to reach the server wins; the other gets `reject(stale_version)`, the client re-fetches (`sync`) and the user redoes the edit. Deterministic, nothing lost silently.

**D8 Server is the single source of truth; reconnect = `hello` returns a full snapshot.** Always correct however long a client was away.

## V2

**D9 Safe rebase: overlap detection + line shifting (Option B, phases 7+8).** V1 rejected *every* stale edit, even when someone typed on a different line, which makes multi-user editing feel broken. V2 keeps the last 1000 applied edits on the server as `(version, start, end, number_of_new_lines)`. A stale edit is checked against every edit applied after its base version: if none overlaps the lines it targets, its line numbers are shifted past them and it is applied; if one overlaps, it is rejected as `conflict` (first edit to reach the server wins). Same-point inserts keep arrival order. Only line numbers move, no text is merged, so a wrong merge cannot corrupt content. Rejected: keep strict V1 (too many false conflicts); per-line version vector (more state and messages, awkward with inserts/deletes); OT/CRDT (too complex to build and defend; synopsis excludes character-level merge); auto-merging text with difflib (a wrong merge silently corrupts text). Trade-off: bounded history (a client more than 1000 versions behind gets `stale_version` and re-fetches).

**D10 The server tells everyone the APPLIED line range.** `ack` and `update` carry `start_line`, `end_line` (and `rebased` in the ack), which can differ from what the client sent. Clients apply that range, so every copy stays identical. Rejected: clients recompute the shifting themselves (duplicate logic, risk of drift).

**D11 Reconnect = connect again with the same name; the `hello` reply is a full snapshot (phase 6).** The client retries with growing delays (0.5 s, 1 s, 2 s, ... max 5 s). Edits typed while offline are refused with a clear message, not queued: a queued edit would be stale by the time the server returns. If an edit was in flight when the connection dropped, the client says so and the user checks with `show`. Rejected: silently queue offline edits; give every edit an id so a retry cannot apply twice (would give exactly-once, left for future work).

**D12 The document survives a server restart.** The server saves `state.json` (document + version) by writing a temporary file and renaming it, at most once per second in the background, and once more on SIGTERM. A corrupt file stops the server instead of being overwritten. Rejected: saving on every edit (slower); a database (not in the synopsis stack). Trade-off: up to about 1 second of edits can be lost on a hard crash or power cut.

**D13 difflib shows the loser what changed.** After a `conflict` the client re-fetches and prints a unified diff between its text when it started the edit and the server's text now, so the user can redo the edit. Rejected: merging automatically (see D9).

**D14 Deployment (phase 9).** `systemd` starts the server at boot, restarts it after a crash (`Restart=on-failure`), stops it gracefully with SIGTERM (so the state is saved), runs it as an unprivileged user with its own state directory and basic sandboxing. `firewalld` opens only TCP 9000. `journald` collects the server's log output automatically; under systemd the log lines carry a priority prefix so `journalctl -u collabfs -p warning` works. Rejected: nohup/screen (no supervision or boot start), cron `@reboot` (no restart, no logs), running as root, writing our own log files (journald already does it).

## V2 consistency guarantees
Guaranteed:
- No silently lost or corrupted edit: each edit is applied once or explicitly rejected.
- All clients converge to the server's document (ordered updates, re-fetch on a gap, snapshot on reconnect).
- Edits on different lines at the same time are all kept, and land on the right text (D9).
- Edits on the same lines: exactly one wins (the first to reach the server), the other gets `conflict` plus a diff.
- A reconnecting client gets the current document; the document survives a server restart.
- Bad frames, bad messages and slow or hostile clients cannot crash the server or corrupt the document.

Not guaranteed:
- Automatic merging inside the same lines (the loser redoes the edit).
- Real-time delivery (updates arrive after network delay).
- The last ~1 second of edits on a hard crash or power cut (D12).
- That an edit in flight when the connection dropped was applied or not: the client tells the user to check (D11).
- Security: no login or encryption; firewalld limits who can reach the port.
