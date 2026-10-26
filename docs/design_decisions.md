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

## V1 consistency guarantees
Guaranteed: no silently lost/corrupted edit (each is applied once or explicitly rejected); all clients converge to the server's state; same-version races have exactly one winner; a reconnecting client gets the current document; bad frames/messages never crash the server.
Not guaranteed: automatic merging (loser retries manually); real-time delivery; persistence across a server restart (V2+).
