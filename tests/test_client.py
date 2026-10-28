"""Phases 3, 5, 6 client tests (owner: Ananyaa, with Tanishq) - the Client class, no network needed."""
import contextlib
import io
import json
import unittest

from client.cli import Client


class FakeWriter:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(json.loads(data[4:].decode()))

    def close(self):
        pass


def make_client(lines=("a", "b", "c"), version=3):
    c = Client("tester")
    c.lines, c.version, c.writer = list(lines), version, FakeWriter()
    return c


def run(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args)
    return result, buf.getvalue()


class ClientTests(unittest.TestCase):
    def test_edit_command_sends_correct_message(self):
        """'r 2 X' sends an edit replacing line index 1 based on the current version."""
        c = make_client()
        run(c.command, "r 2 X")
        m = c.writer.sent[-1]
        self.assertEqual((m["type"], m["base_version"], m["start_line"], m["end_line"], m["new_lines"]),
                         ("edit", 3, 1, 2, ["X"]))

    def test_ack_uses_the_range_the_server_applied(self):
        """We edit line 4 while someone inserts a line above; the ack says the edit landed on index 4, and our copy matches."""
        c = make_client(["l0", "l1", "l2", "l3"], 2)
        run(c.command, "r 4 B3")                                   # we send start=3, end=4 based on version 2
        rid = c.writer.sent[-1]["req_id"]
        run(c.handle, {"type": "update", "version": 3, "author": "alice", "start_line": 0, "end_line": 0, "new_lines": ["top"]})
        _, out = run(c.handle, {"type": "ack", "req_id": rid, "version": 4, "start_line": 4, "end_line": 5, "rebased": True})
        self.assertEqual((c.lines, c.version), (["top", "l0", "l1", "l2", "B3"], 4))
        self.assertIn("ok (v4)", out)

    def test_update_in_order_is_applied(self):
        """An update with version = ours + 1 is applied."""
        c = make_client()
        _, out = run(c.handle, {"type": "update", "version": 4, "author": "bob", "start_line": 1, "end_line": 2, "new_lines": ["B"]})
        self.assertEqual((c.lines, c.version), (["a", "B", "c"], 4))
        self.assertIn("bob changed", out)

    def test_update_gap_triggers_resync(self):
        """An update that skips a version makes the client ask for a fresh copy."""
        c = make_client()
        run(c.handle, {"type": "update", "version": 9, "author": "bob", "start_line": 0, "end_line": 1, "new_lines": ["x"]})
        self.assertEqual(c.writer.sent[-1]["type"], "sync")
        self.assertEqual(c.lines, ["a", "b", "c"])

    def test_conflict_reject_refetches_and_prints_diff(self):
        """A conflict reject asks for a fresh copy; the snapshot then prints a difflib diff of what changed."""
        c = make_client()
        run(c.command, "r 2 MINE")
        rid = c.writer.sent[-1]["req_id"]
        _, out = run(c.handle, {"type": "reject", "req_id": rid, "reason": "conflict", "detail": "", "current_version": 4})
        self.assertIn("CONFLICT", out)
        self.assertEqual(c.writer.sent[-1]["type"], "sync")
        _, out = run(c.handle, {"type": "snapshot", "version": 4, "lines": ["a", "THEIRS", "c"]})
        self.assertIn("-b", out)
        self.assertIn("+THEIRS", out)

    def test_other_reject_just_prints_reason(self):
        """Non-conflict rejects (e.g. invalid_range) are shown and do not trigger a re-fetch."""
        c = make_client()
        run(c.command, "r 2 X")
        rid = c.writer.sent[-1]["req_id"]
        n = len(c.writer.sent)
        _, out = run(c.handle, {"type": "reject", "req_id": rid, "reason": "invalid_range", "detail": "bad", "current_version": 3})
        self.assertIn("REJECTED (invalid_range)", out)
        self.assertEqual(len(c.writer.sent), n)

    def test_offline_edit_is_refused_not_lost_silently(self):
        """With no connection an edit command says it was not sent."""
        c = make_client()
        c.writer = None
        _, out = run(c.command, "a hello")
        self.assertIn("not connected", out)

    def test_bad_commands_do_not_crash(self):
        """Out-of-range numbers, missing arguments and unknown commands print a message."""
        c = make_client()
        for cmd in ("r 9 x", "d 0", "i 9 x", "r", "d x", "dance"):
            _, out = run(c.command, cmd)
            self.assertTrue(out.strip(), cmd)
        self.assertEqual(c.writer.sent, [])

    def test_quit(self):
        """quit makes command() return False."""
        c = make_client()
        self.assertFalse(c.command("quit"))


if __name__ == "__main__":
    unittest.main()
