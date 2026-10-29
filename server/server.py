"""PHASES 2, 5, 6 (owner: Monali) - asyncio server: hello/snapshot, ack/update/reject broadcast, state file.

Run:  python -m server.server --host 0.0.0.0 --port 9000 --state state.json
One asyncio event loop; the edit handler has no `await`, so two edits can never interleave.
"""
import argparse
import asyncio
import contextlib
import logging
import os
import re
import tempfile

from common import protocol as P
from .document import Document, EditRejected

log = logging.getLogger("collabfs")
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def write_atomic(path, text):
    """Write to a temp file, then rename: a crash can never leave a half-written state file."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class CollabServer:
    def __init__(self, host="127.0.0.1", port=9000, state_path=None, save_interval=1.0):
        self.state_path, self.save_interval = state_path, save_interval
        self.doc = Document.load(state_path) if state_path else Document()
        self.host, self._port = host, port
        self.sessions: dict[str, asyncio.StreamWriter] = {}
        self._server = None
        self._saver = None
        self._stopping = None
        self._dirty = False

    @property
    def port(self):
        return self._server.sockets[0].getsockname()[1]

    async def start(self):
        self._server = await asyncio.start_server(self._handle_client, self.host, self._port)
        if self.state_path:
            self._stopping = asyncio.Event()
            self._saver = asyncio.create_task(self._save_loop())
        log.info("listening on %s:%d (document v%d, %d lines)", self.host, self.port,
                 self.doc.version, len(self.doc.lines))

    async def stop(self):
        self._server.close()
        for w in list(self.sessions.values()):
            w.close()
        if self._saver:
            self._stopping.set()
            await self._saver          # let a save that is already running finish: never cancel a write half-way
            await self._flush()        # then write whatever is still unsaved
        await self._server.wait_closed()
        log.info("server stopped at version %d", self.doc.version)

    async def _flush(self):
        if self._dirty and self.state_path:
            self._dirty = False
            try:
                await asyncio.to_thread(write_atomic, self.state_path, self.doc.to_json())
            except BaseException:
                self._dirty = True
                raise

    async def _save_loop(self):          # saves at most once per second, off the event loop
        while not self._stopping.is_set():
            with contextlib.suppress(asyncio.TimeoutError):      # wake up every save_interval, or at once on stop
                await asyncio.wait_for(self._stopping.wait(), self.save_interval)
            try:
                await self._flush()
            except OSError:
                log.exception("could not save state; will retry")

    def _send(self, writer, msg):
        if not writer.is_closing():
            writer.write(P.encode_message(msg))

    def _broadcast(self, msg, exclude=None):
        for w in list(self.sessions.values()):
            if w is not exclude:
                self._send(w, msg)

    async def _handle_client(self, reader, writer):
        client_id = None
        try:
            hello = await asyncio.wait_for(P.read_message(reader), 10)
            if (not hello or hello["type"] != P.HELLO or not isinstance(hello.get("client_id"), str)
                    or not CLIENT_ID_RE.match(hello["client_id"])):
                self._send(writer, {"type": P.ERROR, "reason": "first message must be hello with a valid client_id"})
                return
            client_id = hello["client_id"]
            if client_id in self.sessions:                      # same id reconnecting: drop the old socket
                self.sessions[client_id].close()
            self.sessions[client_id] = writer
            log.info("client %s connected", client_id)
            self._send(writer, {"type": P.SNAPSHOT, **self.doc.snapshot()})   # also what a reconnect gets
            while True:
                msg = await P.read_message(reader)
                if msg is None:
                    break
                self._dispatch(client_id, writer, msg)
        except P.ProtocolError as e:
            log.warning("protocol error from %s: %s", client_id, e)
            self._send(writer, {"type": P.ERROR, "reason": str(e)})
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            if client_id and self.sessions.get(client_id) is writer:
                del self.sessions[client_id]
                log.info("client %s disconnected", client_id)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _dispatch(self, client_id, writer, msg):
        t = msg["type"]
        if t == P.EDIT:
            self._on_edit(client_id, writer, msg)
        elif t == P.SYNC:
            self._send(writer, {"type": P.SNAPSHOT, **self.doc.snapshot()})
        elif t == P.PING:
            self._send(writer, {"type": P.PONG})
        else:
            self._send(writer, {"type": P.ERROR, "reason": f"unknown message type {t!r}"})

    def _on_edit(self, client_id, writer, msg):
        req_id = msg.get("req_id")
        try:
            version, s, e, rebased = self.doc.apply_edit(msg.get("base_version"), msg.get("start_line"),
                                                         msg.get("end_line"), msg.get("new_lines"))
        except EditRejected as err:
            log.info("REJECT %s from %s (%s)", err.reason, client_id, err.detail)
            self._send(writer, {"type": P.REJECT, "req_id": req_id, "reason": err.reason,
                                "detail": err.detail, "current_version": self.doc.version})
            return
        new = msg["new_lines"]
        self._dirty = True
        log.info("ACCEPT v%d from %s: lines [%d,%d) -> %d line(s)%s", version, client_id, s, e, len(new),
                 " (shifted)" if rebased else "")
        # the ACK and the UPDATE carry the APPLIED line range, which can differ from what the client sent
        self._send(writer, {"type": P.ACK, "req_id": req_id, "version": version,
                            "start_line": s, "end_line": e, "rebased": rebased})
        self._broadcast({"type": P.UPDATE, "version": version, "author": client_id,
                         "start_line": s, "end_line": e, "new_lines": new}, exclude=writer)


class JournaldFormatter(logging.Formatter):
    """Prefix syslog priority so `journalctl -p warning` works; journald adds the timestamp."""
    PRIO = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4, logging.ERROR: 3, logging.CRITICAL: 2}

    def format(self, record):
        return f"<{self.PRIO.get(record.levelno, 6)}>" + super().format(record)


async def _run(args):
    import signal
    srv = CollabServer(args.host, args.port, args.state)
    await srv.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):          # SIGTERM = what systemd sends on stop
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await srv.stop()


def main():
    ap = argparse.ArgumentParser(description="CollabFS server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--state", default=None, help="file that keeps the document across restarts")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    handler = logging.StreamHandler()
    if os.environ.get("JOURNAL_STREAM"):                 # running under systemd
        handler.setFormatter(JournaldFormatter("%(name)s: %(message)s"))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=args.log_level.upper(), handlers=[handler])
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()