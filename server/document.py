"""PHASE 4 (owner: Monali) - the shared document and the V1 concurrency rule (Option A).

State = list of lines + ONE integer version for the whole file.
Rule  = optimistic locking: an edit is applied ONLY if base_version == current version.
        Otherwise it is rejected ("stale_version") and the client re-fetches and retries.
V2 will replace the strict check with overlap detection + line shifting (Option B).
"""


class EditRejected(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class Document:
    MAX_LINES = 100_000
    MAX_LINE_LEN = 10_000

    def __init__(self, lines=None, version: int = 0):
        self.lines = list(lines or [])
        self.version = version

    def snapshot(self) -> dict:
        return {"version": self.version, "lines": list(self.lines)}

    def apply_edit(self, base_version, start_line, end_line, new_lines) -> int:
        """Apply one edit atomically. Returns the new version or raises EditRejected."""
        if not (_is_int(base_version) and _is_int(start_line) and _is_int(end_line)):
            raise EditRejected("malformed_edit", "base_version/start_line/end_line must be integers")
        if not isinstance(new_lines, list) or not all(isinstance(x, str) for x in new_lines):
            raise EditRejected("malformed_edit", "new_lines must be a list of strings")
        if any("\n" in x or "\r" in x for x in new_lines):
            raise EditRejected("malformed_edit", "a line must not contain newline characters")
        if any(len(x) > self.MAX_LINE_LEN for x in new_lines):
            raise EditRejected("limit_exceeded", "line too long")
        if base_version > self.version:
            raise EditRejected("malformed_edit", "base_version is newer than the server's")
        if base_version < self.version:                       # <-- the whole V1 conflict rule
            raise EditRejected("stale_version", f"base {base_version}, current {self.version}")
        if not (0 <= start_line <= end_line <= len(self.lines)):
            raise EditRejected("invalid_range", f"[{start_line},{end_line}) outside 0..{len(self.lines)}")
        if start_line == end_line and not new_lines:
            raise EditRejected("malformed_edit", "no-op edit")
        if len(self.lines) - (end_line - start_line) + len(new_lines) > self.MAX_LINES:
            raise EditRejected("limit_exceeded", "document too large")
        self.lines[start_line:end_line] = new_lines            # no await here => atomic
        self.version += 1
        return self.version
