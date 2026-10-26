"""PHASE 2 skeleton + PHASE 5 broadcast (owner: Monali; Tanishq reviews / co-owns phase 5).

Run:  python -m server.server --host 0.0.0.0 --port 9000
One asyncio event loop; the edit handler has no `await`, so two edits can never interleave.
"""
import argparse
import asyncio
import contextlib
import logging
import re

from common import protocol as P
from .document import Document, EditRejected

log = logging.getLogger("collabfs")
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


class CollabServer:
    def __init__(self, host="127.0.0.1", port=9000):
        self.doc = Document()
        self.host, self._port = host, port
        self.sessions: dict[str, asyncio.StreamWriter] = {}
        self._server = None

    @property
    def port(self):
        return self._server.sockets[0].getsockname()[1]

    async def start(self):
        self._server = await asyncio.start_server(self._handle_client, self.host, self._port)
        log.info("listening on %s:%d", self.host, self.port)

    async def stop(self):
        self._server.close()
        for w in list(self.sessions.values()):
            w.close()
        await self._server.wait_closed()

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
        s, e, new = msg.get("start_line"), msg.get("end_line"), msg.get("new_lines")
        try:
            version = self.doc.apply_edit(msg.get("base_version"), s, e, new)
        except EditRejected as err:
            log.info("REJECT %s from %s (%s)", err.reason, client_id, err.detail)
            self._send(writer, {"type": P.REJECT, "req_id": req_id, "reason": err.reason,
                                "detail": err.detail, "current_version": self.doc.version})
            return
        log.info("ACCEPT v%d from %s: lines [%d,%d) -> %d line(s)", version, client_id, s, e, len(new))
        self._send(writer, {"type": P.ACK, "req_id": req_id, "version": version})
        self._broadcast({"type": P.UPDATE, "version": version, "author": client_id,
                         "start_line": s, "end_line": e, "new_lines": new}, exclude=writer)


async def _run(args):
    import signal
    srv = CollabServer(args.host, args.port)
    await srv.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await srv.stop()


def main():
    ap = argparse.ArgumentParser(description="CollabFS server (V1)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
