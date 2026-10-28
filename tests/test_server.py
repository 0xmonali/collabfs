"""Phases 2, 5, 6 tests (owner: Ananyaa) - real server on a free port, raw protocol clients."""
import asyncio
import os
import struct
import tempfile
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


class ServerCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.clients = []
        await self.boot()

    async def boot(self, port=0):
        self.srv = CollabServer("127.0.0.1", port, state_path=self.state, save_interval=0.05)
        await self.srv.start()
        self.port = self.srv.port

    async def asyncTearDown(self):
        for c in self.clients:
            c.w.close()
        await self.srv.stop()
        self.tmp.cleanup()

    async def raw(self, name):
        c = await Raw.connect(self.port, name)
        self.clients.append(c)
        return c


class EditTests(ServerCase):
    async def test_edit_acked_and_broadcast(self):
        """An accepted edit is acked to the author (with the applied range) and pushed to the other client."""
        a, b = await self.raw("alice"), await self.raw("bob")
        ack = await a.edit(0, 0, 0, ["hello"])
        self.assertEqual((ack["type"], ack["version"], ack["start_line"], ack["end_line"], ack["rebased"]),
                         ("ack", 1, 0, 0, False))
        upd = await b.recv("update")
        self.assertEqual((upd["version"], upd["author"], upd["new_lines"]), (1, "alice", ["hello"]))

    async def test_same_line_conflict_first_wins_then_retry(self):
        """Two edits to the same line from the same version: first wins, second gets conflict, re-fetches, retries."""
        a, b = await self.raw("alice"), await self.raw("bob")
        await a.edit(0, 0, 0, ["one", "two", "three"])
        r1 = await a.edit(1, 1, 2, ["ALICE"], req=2)
        r2 = await b.edit(1, 1, 2, ["BOB"])
        self.assertEqual(r1["type"], "ack")
        self.assertEqual((r2["type"], r2["reason"], r2["current_version"]), ("reject", "conflict", 2))
        snap = await b.sync()
        self.assertEqual((snap["version"], snap["lines"]), (2, ["one", "ALICE", "three"]))
        self.assertEqual((await b.edit(2, 1, 2, ["BOB"], req=2))["type"], "ack")

    async def test_different_lines_both_accepted_and_shifted(self):
        """A stale edit on different lines is accepted and moved to the right place; the ack shows the new range."""
        a, b = await self.raw("alice"), await self.raw("bob")
        await a.edit(0, 0, 0, ["l0", "l1", "l2", "l3"])
        r1 = await a.edit(1, 0, 1, ["A0", "A0b"], req=2)          # alice grows the document above bob's target
        r2 = await b.edit(1, 3, 4, ["B3"])                         # bob still works on version 1
        self.assertEqual((r1["rebased"], r2["rebased"], r2["start_line"], r2["end_line"]), (False, True, 4, 5))
        self.assertEqual((await a.sync())["lines"], ["A0", "A0b", "l1", "l2", "B3"])

    async def test_three_clients_same_point_all_kept(self):
        """3 clients insert at the same point at once: all 3 edits are kept and all clients end identical."""
        cs = [await self.raw(n) for n in ("c1", "c2", "c3")]
        res = await asyncio.gather(*(c.edit(0, 0, 0, [f"from {i}"]) for i, c in enumerate(cs)))
        self.assertEqual([r["type"] for r in res], ["ack"] * 3)
        snaps = [await c.sync() for c in cs]
        self.assertTrue(all(s == snaps[0] for s in snaps))
        self.assertEqual((snaps[0]["version"], sorted(snaps[0]["lines"])), (3, ["from 0", "from 1", "from 2"]))

    async def test_three_clients_same_line_one_winner(self):
        """3 clients replace the same line at once: exactly one wins and all clients converge."""
        cs = [await self.raw(n) for n in ("c1", "c2", "c3")]
        await cs[0].edit(0, 0, 0, ["base"])
        res = await asyncio.gather(*(c.edit(1, 0, 1, [f"from {i}"], req=2) for i, c in enumerate(cs)))
        self.assertEqual(sorted(r["type"] for r in res), ["ack", "reject", "reject"])
        snaps = [await c.sync() for c in cs]
        self.assertTrue(all(s == snaps[0] for s in snaps))
        self.assertEqual(snaps[0]["version"], 2)


class ReconnectTests(ServerCase):
    async def test_reconnect_gets_latest_document(self):
        """A client that disconnects and comes back sees the current document."""
        a, b = await self.raw("alice"), await self.raw("bob")
        await a.edit(0, 0, 0, ["x"])
        a.w.close()
        await b.recv("update")
        await b.edit(1, 1, 1, ["y"])
        a2 = await self.raw("alice")
        self.assertEqual((a2.snapshot["version"], a2.snapshot["lines"]), (2, ["x", "y"]))

    async def test_document_survives_server_restart(self):
        """The document and its version are saved on shutdown and reloaded on start."""
        a = await self.raw("alice")
        await a.edit(0, 0, 0, ["keep me"])
        await self.srv.stop()
        await self.boot()
        a2 = await self.raw("alice")
        self.assertEqual((a2.snapshot["version"], a2.snapshot["lines"]), (1, ["keep me"]))

    async def test_edit_after_restart_is_accepted(self):
        """After a restart a client that continues from the saved version can still edit."""
        a = await self.raw("alice")
        await a.edit(0, 0, 0, ["one"])
        await self.srv.stop()
        await self.boot()
        a2 = await self.raw("alice")
        self.assertEqual((await a2.edit(1, 1, 1, ["two"], req=2))["version"], 2)


class RobustnessTests(ServerCase):
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

    async def test_malformed_edit_rejected_connection_survives(self):
        """Garbage edit fields get a reject and the connection keeps working."""
        a = await self.raw("alice")
        await a.send(type="edit", req_id=1, base_version="x", start_line=None, end_line=[], new_lines=5)
        self.assertEqual((await a.recv("reject"))["reason"], "malformed_edit")
        self.assertEqual((await a.edit(0, 0, 0, ["fine"], req=2))["type"], "ack")

    async def test_corrupt_state_file_refuses_to_load(self):
        """A corrupt state file stops the server instead of being silently overwritten."""
        await self.srv.stop()
        with open(self.state, "w") as f:
            f.write('{"version": "x", "lines": 3}')
        with self.assertRaises(ValueError):
            CollabServer("127.0.0.1", 0, state_path=self.state)
        os.remove(self.state)
        await self.boot()


if __name__ == "__main__":
    unittest.main()
