"""PHASE 4 tests (owner: Ananyaa) - the V1 rule: only an edit based on the CURRENT version is accepted."""
import unittest

from server.document import Document, EditRejected


def outcome(doc, *args):
    try:
        doc.apply_edit(*args)
        return "accepted"
    except EditRejected as e:
        return e.reason


class DocumentTests(unittest.TestCase):
    def doc(self):
        return Document(["a", "b", "c"], version=5)

    def test_replace_insert_delete(self):
        """Replace, insert and delete work and bump the version by 1."""
        d = self.doc()
        self.assertEqual(d.apply_edit(5, 1, 2, ["B"]), 6)
        d.apply_edit(6, 3, 3, ["d"])
        d.apply_edit(7, 0, 2, [])
        self.assertEqual((d.lines, d.version), (["c", "d"], 8))

    def test_stale_edit_rejected_state_untouched(self):
        """An edit based on an old version is rejected and changes nothing."""
        d = self.doc()
        self.assertEqual(outcome(d, 4, 0, 1, ["x"]), "stale_version")
        self.assertEqual((d.lines, d.version), (["a", "b", "c"], 5))

    def test_future_version_rejected(self):
        """A base_version newer than the server's is malformed."""
        self.assertEqual(outcome(self.doc(), 9, 0, 1, ["x"]), "malformed_edit")

    def test_bad_ranges(self):
        """Negative, inverted and out-of-bounds ranges are invalid_range."""
        for s, e in [(-1, 1), (2, 1), (0, 4), (4, 4)]:
            self.assertEqual(outcome(self.doc(), 5, s, e, ["x"]), "invalid_range")

    def test_malformed(self):
        """Wrong types, newlines inside a line, and no-op edits are malformed_edit."""
        d = self.doc()
        self.assertEqual(outcome(d, 5, 0, 1, "nope"), "malformed_edit")
        self.assertEqual(outcome(d, 5, 0, 1, ["a\nb"]), "malformed_edit")
        self.assertEqual(outcome(d, 5, 1, 1, []), "malformed_edit")
        self.assertEqual(outcome(d, True, 0, 1, ["x"]), "malformed_edit")

    def test_line_too_long(self):
        """A line longer than the limit is limit_exceeded."""
        self.assertEqual(outcome(self.doc(), 5, 0, 1, ["x" * 10_001]), "limit_exceeded")


if __name__ == "__main__":
    unittest.main()
