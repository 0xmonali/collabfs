"""Phases 4, 7, 8 tests (owner: Ananyaa) - the V2 rule: stale edits are shifted if they do not overlap, else conflict."""
import unittest

from server.document import Document, EditRejected


def outcome(doc, *args):
    try:
        doc.apply_edit(*args)
        return "accepted"
    except EditRejected as e:
        return e.reason


class BasicTests(unittest.TestCase):
    def doc(self):
        return Document(["a", "b", "c"], version=5)

    def test_replace_insert_delete(self):
        """Replace, insert and delete work, bump the version by 1 and report the applied range."""
        d = self.doc()
        self.assertEqual(d.apply_edit(5, 1, 2, ["B"]), (6, 1, 2, False))
        d.apply_edit(6, 3, 3, ["d"])
        d.apply_edit(7, 0, 2, [])
        self.assertEqual((d.lines, d.version), (["c", "d"], 8))

    def test_rejected_edit_leaves_state_untouched(self):
        """A rejected edit never changes the lines or the version."""
        d = self.doc()
        self.assertEqual(outcome(d, 4, 0, 1, ["x"]), "stale_version")
        self.assertEqual(outcome(d, 5, 0, 9, ["x"]), "invalid_range")
        self.assertEqual((d.lines, d.version), (["a", "b", "c"], 5))

    def test_history_missing_means_stale(self):
        """A document loaded at version 5 has no history, so base 4 cannot be shifted: stale_version."""
        self.assertEqual(outcome(self.doc(), 4, 0, 1, ["x"]), "stale_version")

    def test_future_version_rejected(self):
        """A base_version newer than the server's is malformed."""
        self.assertEqual(outcome(self.doc(), 9, 0, 1, ["x"]), "malformed_edit")

    def test_bad_ranges(self):
        """Negative, inverted and out-of-bounds ranges are invalid_range."""
        for s, e in [(-1, 1), (2, 1), (0, 4), (4, 4)]:
            self.assertEqual(outcome(self.doc(), 5, s, e, ["x"]), "invalid_range")

    def test_malformed(self):
        """Wrong types, newlines inside a line, no-op edits and bool-as-int are malformed_edit."""
        d = self.doc()
        self.assertEqual(outcome(d, 5, 0, 1, "nope"), "malformed_edit")
        self.assertEqual(outcome(d, 5, 0, 1, ["a\nb"]), "malformed_edit")
        self.assertEqual(outcome(d, 5, 1, 1, []), "malformed_edit")
        self.assertEqual(outcome(d, True, 0, 1, ["x"]), "malformed_edit")

    def test_line_too_long(self):
        """A line longer than the limit is limit_exceeded."""
        self.assertEqual(outcome(self.doc(), 5, 0, 1, ["x" * 10_001]), "limit_exceeded")


class ShiftingTests(unittest.TestCase):
    """Every row of the shifting table. Document starts at version 0 with 10 lines; `prior` is applied
    first, then `incoming` is applied based on version 0 (so it is stale)."""
    def second(self, prior, incoming):
        d = Document([f"l{i}" for i in range(10)])
        d.apply_edit(0, *prior)
        try:
            _, s, e, rebased = d.apply_edit(0, *incoming)
            return s, e, rebased
        except EditRejected as ex:
            return ex.reason

    def test_insert_above_earlier_edit(self):
        """Insert above an earlier edit keeps its line numbers."""
        self.assertEqual(self.second((5, 6, ["X"]), (2, 2, ["Y"])), (2, 2, True))

    def test_inserts_at_same_point(self):
        """Two inserts at the same point: the later one goes after the earlier one."""
        self.assertEqual(self.second((3, 3, ["X", "X2"]), (3, 3, ["Y"])), (5, 5, True))

    def test_insert_at_start_of_replaced_block(self):
        """Insert at the first line of a replaced block stays before it."""
        self.assertEqual(self.second((3, 5, ["X"]), (3, 3, ["Y"])), (3, 3, True))

    def test_insert_inside_replaced_block_conflicts(self):
        """Insert strictly inside a replaced block conflicts."""
        self.assertEqual(self.second((3, 6, ["X"]), (4, 4, ["Y"])), "conflict")

    def test_insert_after_shrinking_replace(self):
        """Insert below an edit that removed a line moves up by one."""
        self.assertEqual(self.second((3, 5, ["X"]), (5, 5, ["Y"])), (4, 4, True))

    def test_replace_above_earlier_edit(self):
        """A replace entirely above an earlier edit is unchanged."""
        self.assertEqual(self.second((6, 7, ["X"]), (1, 3, ["Y"])), (1, 3, True))

    def test_replace_ending_where_insert_happened(self):
        """A replace that ends exactly where an earlier insert happened is unchanged."""
        self.assertEqual(self.second((3, 3, ["X"]), (1, 3, ["Y"])), (1, 3, True))

    def test_replace_below_insert_shifts_down(self):
        """A replace below an earlier insert moves down by the number of inserted lines."""
        self.assertEqual(self.second((1, 1, ["A", "B"]), (4, 6, ["Y"])), (6, 8, True))

    def test_replace_below_delete_shifts_up(self):
        """A replace below an earlier delete moves up by the number of deleted lines."""
        self.assertEqual(self.second((0, 3, []), (5, 6, ["Y"])), (2, 3, True))

    def test_partial_overlap_conflicts(self):
        """Partly overlapping replaces conflict."""
        self.assertEqual(self.second((3, 5, ["X"]), (4, 7, ["Y"])), "conflict")

    def test_same_line_conflicts(self):
        """Two replaces of the same line conflict: first to arrive wins."""
        self.assertEqual(self.second((3, 4, ["X"]), (3, 4, ["Y"])), "conflict")

    def test_replace_containing_earlier_insert_conflicts(self):
        """A replace range that contains the point of an earlier insert conflicts."""
        self.assertEqual(self.second((4, 4, ["X"]), (2, 6, ["Y"])), "conflict")

    def test_edit_based_on_v0_shifted_through_two_later_edits(self):
        """An edit based on version 0 is shifted correctly through two later edits."""
        d = Document([f"l{i}" for i in range(10)])
        d.apply_edit(0, 0, 0, ["top"])
        d.apply_edit(1, 8, 9, ["mid"])
        _, s, e, rebased = d.apply_edit(0, 9, 10, ["end"])
        self.assertEqual((s, e, rebased, d.lines[-1]), (10, 11, True, "end"))


if __name__ == "__main__":
    unittest.main()
