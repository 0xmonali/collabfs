"""PHASES 4 + 7 + 8 (owner: Monali) - the shared document and the V2 concurrency rule (Option B).

State = list of lines + ONE integer version for the whole file (+ a short history of applied edits).
Rule  = optimistic locking with SAFE REBASE:
  * base_version == current           -> apply
  * base_version  < current (stale)   -> look at every edit applied since base_version:
        - none of them touched the lines this edit targets  -> shift the line numbers past them, apply  (phases 7+8)
        - one of them overlaps the target lines             -> reject "conflict" (first edit to reach the server wins)
  * history no longer available       -> reject "stale_version" (client re-fetches)
Only line NUMBERS move; no text is ever merged, so a wrong merge cannot corrupt content.
"""
import json
import os
from collections import deque

MAX_LINES = 100_000
MAX_LINE_LEN = 10_000
HISTORY_LIMIT = 1000


class EditRejected(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def transform(s: int, e: int, ps: int, pe: int, n: int):
    """Move the incoming range [s,e) past an EARLIER applied edit that replaced [ps,pe) by n lines.
    Returns (s, e), or None if the two edits overlap (conflict)."""
    delta = n - (pe - ps)                      # how many lines the earlier edit added (+) or removed (-)
    if s == e:                                 # incoming is an insert
        if s < ps:
            return s, e                        # above the earlier edit: unchanged
        if s == ps == pe:
            return s + n, e + n                # both inserted at the same point: later arrival goes after
        if s == ps < pe:
            return s, e                        # at the start of a replaced block: stays before it
        if ps < s < pe:
            return None                        # inside a replaced block: conflict
        return s + delta, e + delta            # below the earlier edit: shift
    if e <= ps:
        return s, e                            # entirely above the earlier edit: unchanged
    if s >= pe:
        return s + delta, e + delta            # entirely below: shift
    return None                                # overlap: conflict


class Document:
    def __init__(self, lines=None, version: int = 0):
        self.lines = list(lines or [])
        self.version = version
        self.history = deque(maxlen=HISTORY_LIMIT)     # (version_after, start, end, n_new_lines)

    def snapshot(self) -> dict:
        return {"version": self.version, "lines": list(self.lines)}

    def apply_edit(self, base_version, start_line, end_line, new_lines):
        """Apply one edit atomically. Returns (new_version, applied_start, applied_end, rebased)
        or raises EditRejected."""
        # 1. shape checks
        if not (_is_int(base_version) and _is_int(start_line) and _is_int(end_line)):
            raise EditRejected("malformed_edit", "base_version/start_line/end_line must be integers")
        if not isinstance(new_lines, list) or not all(isinstance(x, str) for x in new_lines):
            raise EditRejected("malformed_edit", "new_lines must be a list of strings")
        if any("\n" in x or "\r" in x for x in new_lines):
            raise EditRejected("malformed_edit", "a line must not contain newline characters")
        if any(len(x) > MAX_LINE_LEN for x in new_lines):
            raise EditRejected("limit_exceeded", "line too long")
        if base_version < 0 or start_line < 0 or end_line < start_line:
            raise EditRejected("invalid_range", f"[{start_line},{end_line}) is not a valid range")
        if start_line == end_line and not new_lines:
            raise EditRejected("malformed_edit", "no-op edit")
        # 2. concurrency rule
        s, e, rebased = start_line, end_line, False
        if base_version > self.version:
            raise EditRejected("malformed_edit", "base_version is newer than the server's")
        if base_version < self.version:
            missed = [h for h in self.history if h[0] > base_version]
            if len(missed) != self.version - base_version:
                raise EditRejected("stale_version", f"base {base_version}, current {self.version}; history unavailable")
            for _, ps, pe, n in missed:                          # oldest -> newest
                moved = transform(s, e, ps, pe, n)
                if moved is None:
                    raise EditRejected("conflict", f"lines [{start_line},{end_line}) were changed by another edit since version {base_version}")
                s, e = moved
            rebased = True
        # 3. range check on the current text
        if not (0 <= s <= e <= len(self.lines)):
            raise EditRejected("invalid_range", f"[{s},{e}) outside 0..{len(self.lines)}")
        if len(self.lines) - (e - s) + len(new_lines) > MAX_LINES:
            raise EditRejected("limit_exceeded", "document too large")
        # 4. apply (no await anywhere here => atomic)
        self.lines[s:e] = new_lines
        self.version += 1
        self.history.append((self.version, s, e, len(new_lines)))
        return self.version, s, e, rebased

    # ---- saving / loading (phase 6: survive a server restart) ----
    def to_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "Document":
        """Missing file -> empty document. Corrupt file -> raise (never silently overwrite data)."""
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not (_is_int(data.get("version")) and isinstance(data.get("lines"), list)
                and all(isinstance(x, str) for x in data["lines"])):
            raise ValueError(f"state file {path!r} is corrupt")
        return cls(data["lines"], data["version"])
