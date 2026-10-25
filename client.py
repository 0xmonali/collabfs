"""
client.py

Collaborative Network File System - Client
Computer Networks Mini Project
Authors: Tanishq, Monali, Ananyaa

Usage:  python3 client.py [--host 127.0.0.1] [--port 5555] [--user NAME]
        (host/port can also come from COLLABFS_HOST / COLLABFS_PORT)

Two things happen at once on the client, which is why it is built around a
background "listener" task and not a simple request/reply loop:

  1. the user typing commands and waiting for the server's reply
  2. the server pushing unsolicited "file_updated" / "file_deleted"
     broadcasts whenever another client saves or deletes a file we have
     open, which can arrive at any moment

Both kinds of message arrive on the same TCP connection, in the order the
server sent them. The listener reads every incoming message in that order
and does two jobs with it: it keeps the local cache (self.watched) up to
date, and -- if the message is a reply rather than a broadcast -- hands it
to the command that is waiting. Updating the cache *in the listener, in
arrival order* (and never letting a revision number go backwards) is what
keeps the cache correct even when a reply and a broadcast arrive together.

Beyond the basics this client also:
  * reconnects automatically if the connection drops, then re-opens every
    file it had open and reports what changed while it was away
  * on a rejected save, re-applies the user's change on top of the newer
    server version with a three-way line merge (merge.py). Non-overlapping
    changes are merged and re-submitted; overlapping ones are a conflict:
    the server's version stays and the user's text is saved locally, never
    thrown away.

The Client class has no input()/print() calls of its own beyond self.out, so
tests and demo.py can drive it directly; the command prompt is at the bottom.
"""

import argparse
import asyncio
import codecs
import os
import socket
import sys
import threading
import time

from merge import merge3
from protocol import ProtocolError, recv_msg, send_msg

DEFAULT_HOST = os.environ.get("COLLABFS_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("COLLABFS_PORT", "5555"))

CONNECT_TIMEOUT = 5          # seconds to wait when opening a TCP connection
REQUEST_TIMEOUT = 30         # seconds to wait for the server's reply before assuming the link is dead
READY_TIMEOUT = 30           # how long a command waits for an in-progress reconnect
RECONNECT_DELAYS = (0.5, 1, 2, 3, 5)   # back-off between reconnect attempts (last value repeats)
MAX_MERGE_ROUNDS = 5         # how many times to re-merge if other people keep saving in between
CONFLICT_DIR = "conflicts"   # where text the server couldn't accept is kept


class ConnectionLost(Exception):
    """The link to the server dropped (or never answered) during a request."""


class Client:
    def __init__(self, host: str, port: int, username: str, out=print, conflict_dir: str = CONFLICT_DIR):
        self.host, self.port, self.username = host, port, username
        self.out = out                       # where messages for the user go
        self.conflict_dir = conflict_dir
        self.prompt: str | None = None       # if set, re-printed after an asynchronous notice

        # filename -> {"content": str, "revision": int} for files we have
        # open. Local cache only -- the server's copy is always the real one.
        self.watched: dict[str, dict] = {}

        self._reader = None
        self._writer = None
        self._replies: asyncio.Queue | None = None
        self._inflight: dict | None = None   # the request whose reply we're waiting for
        self._lock = asyncio.Lock()          # one request at a time on the connection
        self._transport_up = False           # a live TCP connection exists
        self._ready = asyncio.Event()        # ...and we've re-synced, so commands may run
        self._listener_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._closing = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect for the first time (raises OSError if the server can't be
        reached) and start the background task that reconnects if needed."""
        await self._connect_once()
        self._ready.set()
        self._supervisor_task = asyncio.create_task(self._supervise())

    async def close(self) -> None:
        self._closing = True
        self._ready.clear()
        if self._supervisor_task:
            self._supervisor_task.cancel()
            await asyncio.gather(self._supervisor_task, return_exceptions=True)
        await self._teardown()
        if self._listener_task:
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

    async def drop_connection(self) -> None:
        """Close the socket abruptly (as a network failure would). The
        supervisor notices and reconnects. Used by tests/demo."""
        if self._writer:
            self._writer.transport.abort()

    async def _connect_once(self) -> None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT)
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                pass
        self._reader, self._writer = reader, writer
        self._replies = asyncio.Queue()      # fresh queue: nothing stale from an old connection
        self._inflight = None
        self._transport_up = True
        self._listener_task = asyncio.create_task(self._listen(reader, self._replies))

    async def _teardown(self) -> None:
        self._transport_up = False
        writer, self._writer = self._writer, None
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass

    async def _supervise(self) -> None:
        """Background task: whenever the listener ends (connection lost),
        keep trying to reconnect, then re-sync every open file."""
        while not self._closing:
            await asyncio.shield(self._listener_task)    # returns when the connection is gone
            if self._closing:
                return
            self._ready.clear()
            await self._teardown()
            self._notice("[!] connection to the server was lost -- trying to reconnect...")

            attempt = 0
            while not self._closing:
                await asyncio.sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
                attempt += 1
                try:
                    await self._connect_once()
                    await self._resync()
                except (OSError, asyncio.TimeoutError, ConnectionLost):
                    await self._teardown()
                    continue
                self._ready.set()
                self._notice("[+] reconnected to the server")
                break

    async def _resync(self) -> None:
        """After reconnecting: re-open every file we had open. The server
        re-registers us as a watcher and returns its current state; we compare
        that with what we had cached and tell the user what changed."""
        for filename, old in list(self.watched.items()):
            reply = await self._request_raw({"type": "open", "filename": filename})
            if reply["type"] == "error":
                self.watched.pop(filename, None)
                self._notice(f"[notice] '{filename}' no longer exists on the server "
                             f"(deleted while you were disconnected)")
                continue
            # (the listener already stored the new state in self.watched)
            new_rev = reply["revision"]
            if new_rev > old["revision"]:
                self._notice(f"[notice] '{filename}' changed while you were disconnected "
                             f"(revision {old['revision']} -> {new_rev}); your copy is now up to date")
            elif new_rev < old["revision"]:
                path = self._save_unsent(filename, old["content"], "newer than the server's copy")
                self._notice(f"[notice] the server's '{filename}' is OLDER than your copy "
                             f"(server reset?). Server state adopted; your copy was kept in {path}")

    # ------------------------------------------------------------------
    # Receiving: the listener
    # ------------------------------------------------------------------

    async def _listen(self, reader, replies: asyncio.Queue) -> None:
        """Runs for the life of one connection. Reads every incoming message,
        in order; updates the cache; passes replies to the waiting command."""
        try:
            while True:
                try:
                    msg = await recv_msg(reader)
                except ProtocolError:
                    msg = None
                if msg is None:
                    break
                kind = msg.get("type")
                if kind == "file_updated":
                    self._on_file_updated(msg)
                elif kind == "file_deleted":
                    self._on_file_deleted(msg)
                else:
                    self._on_reply(msg)
                    replies.put_nowait(msg)
        finally:
            self._transport_up = False
            replies.put_nowait(None)          # wakes a command that is waiting for a reply

    def _remember(self, filename: str, content: str, revision: int, *, force: bool = False) -> None:
        """Update the cache, but never let a file's revision go backwards
        (unless force=True, used for an explicit 'open', which is by
        definition the server's current state)."""
        current = self.watched.get(filename)
        if force or (current is not None and revision >= current["revision"]):
            self.watched[filename] = {"content": content, "revision": revision}

    def _on_reply(self, msg: dict) -> None:
        """Cache bookkeeping for replies, done here so it happens in the same
        order as broadcasts (see the module docstring)."""
        kind = msg.get("type")
        if kind == "file_data":
            self._remember(msg["filename"], msg["content"], msg["revision"], force=True)
        elif kind == "edit_ok" and self._inflight and self._inflight.get("type") == "edit":
            self._remember(msg["filename"], self._inflight["content"], msg["revision"])
        elif kind == "edit_rejected":
            self._remember(msg["filename"], msg["current_content"], msg["current_revision"])
        elif kind == "delete_ok":
            self.watched.pop(msg["filename"], None)

    def _on_file_updated(self, msg: dict) -> None:
        name, rev = msg["filename"], msg["revision"]
        current = self.watched.get(name)
        if current is None or rev <= current["revision"]:
            return                            # not open here, or an update we already have
        self._remember(name, msg["content"], rev)
        text = f"[notice] '{name}' was updated by {msg.get('edited_by', '?')} (now revision {rev})"
        if len(msg["content"].split("\n")) <= 15:          # short files: show the new text right away
            text += f"\n--- {name} ---\n" + (msg["content"] or "(empty file)") + "\n--- end ---"
        self._notice(text)

    def _on_file_deleted(self, msg: dict) -> None:
        if self.watched.pop(msg["filename"], None) is not None:
            self._notice(f"[notice] '{msg['filename']}' was deleted by {msg.get('deleted_by', '?')}")

    def _notice(self, text: str) -> None:
        """Print something that arrived asynchronously, then restore the prompt."""
        self.out("\n" + text if self.prompt else text)
        if self.prompt:
            print(self.prompt, end="", flush=True)

    # ------------------------------------------------------------------
    # Sending: requests
    # ------------------------------------------------------------------

    async def _wait_ready(self, timeout: float = READY_TIMEOUT) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            raise ConnectionLost("not connected to the server (still retrying)") from None

    async def request(self, msg: dict) -> dict:
        """Send one message and wait for its reply. Broadcasts never land
        here -- the listener handles those itself."""
        await self._wait_ready()
        return await self._request_raw(msg)

    async def _request_raw(self, msg: dict) -> dict:
        async with self._lock:
            if not self._transport_up or self._writer is None:
                raise ConnectionLost("not connected")
            msg = {**msg, "username": self.username}
            self._inflight = msg
            try:
                await send_msg(self._writer, msg)
                reply = await asyncio.wait_for(self._replies.get(), REQUEST_TIMEOUT)
            except asyncio.TimeoutError:
                await self.drop_connection()              # no answer: treat the link as dead
                raise ConnectionLost("the server did not answer in time") from None
            except ProtocolError as exc:                      # e.g. text too large for one message
                return {"type": "error", "reason": f"cannot send: {exc}"}
            except OSError as exc:
                raise ConnectionLost(str(exc)) from exc
            finally:
                self._inflight = None
            if reply is None:
                raise ConnectionLost("connection closed")
            return reply

    async def _idempotent(self, msg: dict) -> dict:
        """For requests that are safe to repeat (list, open): if the link
        drops mid-request, wait for the reconnect and ask again once."""
        try:
            return await self.request(msg)
        except ConnectionLost:
            return await self.request(msg)

    # ------------------------------------------------------------------
    # Commands. Each returns a short status so tests can check it.
    # ------------------------------------------------------------------

    async def list_files(self) -> list | None:
        try:
            reply = await self._idempotent({"type": "list"})
        except ConnectionLost as exc:
            self.out(f"error: {exc}")
            return None
        if not reply["files"]:
            self.out("(no files on server)")
        for entry in reply["files"]:
            mark = " [open]" if entry["filename"] in self.watched else ""
            self.out(f"  {entry['filename']}  (revision {entry['revision']}){mark}")
        return reply["files"]

    async def create(self, filename: str, content: str = "") -> bool:
        try:
            reply = await self.request({"type": "create", "filename": filename, "content": content})
        except ConnectionLost as exc:
            self.out(f"error: connection lost before the server replied ({exc}); "
                     f"run 'list' to see whether '{filename}' was created")
            return False
        if reply["type"] != "create_ok":
            self.out(f"error: {reply.get('reason', reply)}")
            return False
        self.out(f"created '{filename}' (revision {reply['revision']})")
        return True

    async def open(self, filename: str) -> dict | None:
        """Opening always fetches the server's current content and revision --
        never a locally cached guess. Reconnecting uses the same call, which
        is why a reconnected client is caught up by construction."""
        try:
            reply = await self._idempotent({"type": "open", "filename": filename})
        except ConnectionLost as exc:
            self.out(f"error: {exc}")
            return None
        if reply["type"] != "file_data":
            self.out(f"error: {reply.get('reason', reply)}")
            return None
        self.out(f"--- {filename} (revision {reply['revision']}) ---")
        self.out(reply["content"] if reply["content"] else "(empty file)")
        self.out("--- end ---")
        return self.watched.get(filename)

    async def delete(self, filename: str) -> bool:
        try:
            reply = await self.request({"type": "delete", "filename": filename})
        except ConnectionLost as exc:
            self.out(f"error: connection lost before the server replied ({exc}); "
                     f"run 'list' to see whether '{filename}' was deleted")
            return False
        if reply["type"] != "delete_ok":
            self.out(f"error: {reply.get('reason', reply)}")
            return False
        self.out(f"deleted '{filename}'")
        return True

    def snapshot(self, filename: str) -> dict | None:
        """The state an edit will be based on. Take it BEFORE the user starts
        typing, not after: if someone else's update arrives mid-typing (the
        listener updates the cache in the background), our save must still be
        judged against what we actually saw when we started editing -- which
        is exactly the conflict case we want to detect, not paper over."""
        entry = self.watched.get(filename)
        return dict(entry) if entry else None

    async def edit(self, filename: str, new_content: str) -> str:
        """Replace the whole file's content, starting from the current cache."""
        base = self.snapshot(filename)
        if base is None:
            self.out(f"'{filename}' isn't open -- use 'open {filename}' first")
            return "not_open"
        return await self.save_edit(filename, base, new_content)

    async def save_edit(self, filename: str, base: dict, new_content: str) -> str:
        """
        Try to save `new_content`, which the user wrote starting from `base`
        ({"content", "revision"}). Returns one of:
          "saved"      - accepted as-is
          "merged"     - first rejected, then merged with newer changes and accepted
          "unchanged"  - nothing to save (no change, or the server already has it)
          "conflict"   - overlapping change by someone else; server keeps theirs,
                         our text was saved to a local file
          "failed"     - could not be saved (text kept in a local file)
        """
        base_rev, base_text, mine = base["revision"], base["content"], new_content
        if mine == base_text:
            self.out("no changes to save")
            return "unchanged"

        merged_once = False
        reconnect_waits = 0
        for _ in range(MAX_MERGE_ROUNDS + 3):
            try:
                reply = await self.request({
                    "type": "edit", "filename": filename,
                    "base_revision": base_rev, "content": mine,
                })
            except ConnectionLost as exc:
                # The server may or may not have applied it. Retrying is safe:
                # if it did, the retry is rejected and the merge below sees
                # that the server already contains our text.
                reconnect_waits += 1
                if reconnect_waits > 3:
                    path = self._save_unsent(filename, mine, f"connection lost: {exc}")
                    self.out(f"error: could not reach the server ({exc}). Your text was kept in {path}")
                    return "failed"
                self.out("connection lost while saving -- will retry once reconnected")
                continue

            kind = reply["type"]
            if kind == "edit_ok":
                if merged_once:
                    self.out(f"saved '{filename}' (revision {reply['revision']}) -- merged with the other changes")
                    return "merged"
                self.out(f"saved '{filename}' (revision {reply['revision']})")
                return "saved"

            if kind == "edit_rejected":
                theirs, theirs_rev = reply["current_content"], reply["current_revision"]
                who = reply.get("last_edited_by") or "someone else"
                self.out(f"REJECTED: your copy (revision {base_rev}) is out of date -- the server is at "
                         f"revision {theirs_rev} (last saved by {who}); trying to merge your change...")
                merged, conflicts = merge3(base_text, mine, theirs)
                if merged is None:
                    return self._report_conflict(filename, mine, theirs, theirs_rev, conflicts)
                if merged == theirs:
                    self.out(f"nothing to save: the server already contains your change (revision {theirs_rev})")
                    return "unchanged"
                self.out("your change doesn't overlap with the other changes -- merged; saving the result")
                base_rev, base_text, mine = theirs_rev, theirs, merged   # new base = what we merged onto
                merged_once = True
                continue

            # any other reply is an error (e.g. the file was deleted meanwhile)
            path = self._save_unsent(filename, mine, reply.get("reason", "error"))
            self.out(f"error: {reply.get('reason', reply)}. Your text was kept in {path}")
            return "failed"

        path = self._save_unsent(filename, mine, "too many concurrent changes")
        self.out(f"gave up: the file kept changing under us. Your text was kept in {path}")
        return "failed"

    def _report_conflict(self, filename, mine, theirs, theirs_rev, conflicts) -> str:
        path = self._save_unsent(filename, mine, "conflict")
        self.out(f"CONFLICT: your change overlaps another client's change to '{filename}'.")
        for c in conflicts:
            first, last = c.base_start + 1, max(c.base_end, c.base_start + 1)
            where = f"line {first}" if first == last else f"lines {first}-{last}"
            self.out(f"  around {where}:")
            self.out("    server has : " + (" / ".join(c.theirs) if c.theirs else "(nothing)"))
            self.out("    you wrote  : " + (" / ".join(c.mine) if c.mine else "(nothing)"))
        self.out(f"The server keeps its version (first save wins). Your text was saved to {path}")
        self.out(f"Local copy is now at revision {theirs_rev}; run 'edit {filename}' to redo your change.")
        return "conflict"

    def _save_unsent(self, filename: str, text: str, why: str) -> str:
        """Keep text the server did not accept, so it is never lost."""
        try:
            os.makedirs(self.conflict_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            base = f"{filename}.{self.username}.{stamp}.unsaved"
            path = os.path.join(self.conflict_dir, base)
            n = 1
            while os.path.exists(path):          # two saves in the same second
                n += 1
                path = os.path.join(self.conflict_dir, f"{base}.{n}")
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            return path
        except OSError:
            self.out(f"(could not write a local file; here is your text, reason: {why})")
            self.out(text)
            return "(printed above)"


# ---------------------------------------------------------------------------
# Command prompt
# ---------------------------------------------------------------------------

HELP = """\
Commands:
  list              list files on the server
  create <name>     create a new empty file
  open <name>       open (or refresh) a file -- needed before editing it
  edit <name>       replace an open file's content, with conflict checking
  delete <name>     delete a file
  help              show this message
  quit              disconnect
"""


class StdinLines:
    """Reads stdin on a background daemon thread and feeds lines to the event
    loop. A plain input() inside the event loop would freeze the listener (no
    broadcasts while typing); and a thread that is stuck in input() would keep
    the process from exiting on Ctrl+C -- a daemon thread doesn't."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._queue: asyncio.Queue = asyncio.Queue()
        threading.Thread(target=self._pump, args=(loop,), daemon=True).start()

    def _pump(self, loop):
        put = lambda item: loop.call_soon_threadsafe(self._queue.put_nowait, item)
        try:
            # Read the raw file descriptor (not sys.stdin's buffered wrapper):
            # a daemon thread blocked inside the buffered reader can make
            # interpreter shutdown abort with a "could not acquire lock" error.
            fd = sys.stdin.fileno()
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            pending = ""
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                pending += decoder.decode(chunk)
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    put(line.rstrip("\r"))
            if pending:
                put(pending)
        except (OSError, ValueError):
            pass
        put(None)                                   # EOF

    async def read(self, prompt: str):
        print(prompt, end="", flush=True)
        return await self._queue.get()


async def cmd_edit(client: Client, lines: StdinLines, filename: str) -> None:
    base = client.snapshot(filename)
    if base is None:
        print(f"'{filename}' isn't open -- use 'open {filename}' first")
        return
    saved_prompt, client.prompt = client.prompt, None   # don't re-print "> " in the middle of typing
    print(f"Current content (revision {base['revision']}):")
    print(base["content"] if base["content"] else "(empty file)")
    print("Enter the new content. Finish with a line containing only EOF")
    typed = []
    while True:
        line = await lines.read("")
        if line is None or line == "EOF":
            break
        typed.append(line)
    client.prompt = saved_prompt
    await client.save_edit(filename, base, "\n".join(typed))


async def main(args) -> int:
    loop = asyncio.get_running_loop()
    lines = StdinLines(loop)

    username = args.user
    if username is None:
        username = ((await lines.read("Enter your username: ")) or "").strip() or "anonymous"

    client = Client(args.host, args.port, username)
    try:
        await client.start()
    except (OSError, asyncio.TimeoutError) as exc:
        print(f"Could not connect to {args.host}:{args.port}: {exc}")
        return 1
    print(f"Connected to server at {args.host}:{args.port} as '{username}'")
    print(HELP)
    client.prompt = "> "

    try:
        while True:
            line = await lines.read("> ")
            if line is None:
                break
            parts = line.strip().split(maxsplit=1)
            if not parts:
                continue
            cmd, arg = parts[0], (parts[1].strip() if len(parts) > 1 else "")

            if cmd in ("quit", "exit"):
                break
            elif cmd == "help":
                print(HELP)
            elif cmd == "list":
                await client.list_files()
            elif cmd == "create" and arg:
                await client.create(arg)
            elif cmd == "open" and arg:
                await client.open(arg)
            elif cmd == "edit" and arg:
                await cmd_edit(client, lines, arg)
            elif cmd == "delete" and arg:
                await client.delete(arg)
            else:
                print("unknown command or missing filename -- type 'help'")
    finally:
        client.prompt = None
        await client.close()
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Collaborative Network File System client")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"server address (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"server port (default {DEFAULT_PORT})")
    p.add_argument("--user", default=None, help="username (asked for if omitted)")
    return p.parse_args(argv)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(parse_args())))
    except KeyboardInterrupt:
        print("\nbye")
