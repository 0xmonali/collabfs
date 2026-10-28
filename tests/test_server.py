"""PHASE 3/5 tests (owner: Ananyaa) - real server on a free port, raw protocol clients."""
import asyncio
import struct
import unittest

from common.protocol import encode_message, read_message
from server.server import CollabServer


class Raw:
    def __init__(self, r, w):
        self.r, self.w = r, w

    @classmethod
    async def connect(cls, port, name):
        c = cls(*await asyncio.open_connection("127.0.0.1", port))
        await c.send(type="hello", client_id=name)
        c.snapshot = await c.recv("snapshot")
        return c

    async def send(self, **m):
        self.w.write(encode_message(m))
        await self.w.drain()

    async def recv(self, *types):
        while True:
            m = await asyncio.wait_for(read_message(self.r), 2)
            if m["type"] in types:
                return m

    async def edit(self, base, s, e, lines, req=1):
        await self.send(type="edit", req_id=req, base_version=base, start_line=s, end_line=e, new_lines=lines)
        return await self.recv("ack", "reject")

    async def sync(self):
        await self.send(type="sync")
        return await self.recv("snapshot")


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.srv = CollabServer("127.0.0.1", 0)
        await self.srv.start()
        self.port, self.clients = self.srv.port, []

    async def asyncTearDown(self):
        for c in self.clients:
            c.w.close()
        await self.srv.stop()

    async def raw(self, name):
        c = await Raw.connect(self.port, name)
        self.clients.append(c)
        return c

    async def test_edit_acked_and_broadcast(self):
        """An accepted edit is acked to the author and pushed to the other client."""
        a, b = await self.raw("alice"), await self.raw("bob")
        ack = await a.edit(0, 0, 0, ["hello"])
        self.assertEqual((ack["type"], ack["version"]), ("ack", 1))
        upd = await b.recv("update")
        self.assertEqual((upd["version"], upd["author"], upd["new_lines"]), (1, "alice", ["hello"]))

    async def test_stale_edit_rejected_then_resync_then_retry(self):
        """Second editor with an old version is rejected, re-fetches, retries and succeeds."""
        a, b = await self.raw("alice"), await self.raw("bob")
        await a.edit(0, 0, 0, ["one"])
        r = await b.edit(0, 0, 1, ["BOB"])
        self.assertEqual((r["type"], r["reason"], r["current_version"]), ("reject", "stale_version", 1))
        snap = await b.sync()
        self.assertEqual((snap["version"], snap["lines"]), (1, ["one"]))
        self.assertEqual((await b.edit(1, 0, 1, ["BOB"], req=2))["type"], "ack")

    async def test_three_simultaneous_clients_one_winner_all_converge(self):
        """3 clients edit from the same version at once: exactly one wins, all end identical."""
        cs = [await self.raw(n) for n in ("c1", "c2", "c3")]
        res = await asyncio.gather(*(c.edit(0, 0, 0, [f"from {i}"]) for i, c in enumerate(cs)))
        self.assertEqual(sorted(r["type"] for r in res), ["ack", "reject", "reject"])
        snaps = [await c.sync() for c in cs]
        self.assertTrue(all(s == snaps[0] for s in snaps))

    async def test_reconnect_gets_latest_document(self):
        """A client that disconnects and comes back sees the current document."""
        a, b = await self.raw("alice"), await self.raw("bob")
        await a.edit(0, 0, 0, ["x"])
        a.w.close()
        await b.recv("update")
        await b.edit(1, 1, 1, ["y"])
        a2 = await self.raw("alice")
        self.assertEqual((a2.snapshot["version"], a2.snapshot["lines"]), (2, ["x", "y"]))

    async def test_garbage_frame_does_not_kill_server(self):
        """An absurd frame length closes only that connection."""
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(struct.pack("!I", 0xFFFFFFFF))
        await w.drain()
        await r.read()
        w.close()
        self.assertEqual((await self.raw("alice")).snapshot["version"], 0)

    async def test_first_message_must_be_hello(self):
        """Anything other than a valid hello first gets an error."""
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(encode_message({"type": "edit"}))
        await w.drain()
        self.assertEqual((await read_message(r))["type"], "error")
        w.close()


if __name__ == "__main__":
    unittest.main()
