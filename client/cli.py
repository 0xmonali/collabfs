"""PHASES 3 + 5 + 6 (owner: Tanishq) - terminal client with automatic reconnect.

  python -m client.cli --host 127.0.0.1 --port 9000 --name alice
Commands (line numbers start at 1):  show | r N text | i N text | a text | d N [M] | sync | help | quit
 * the server tells us the APPLIED line range in every ack/update, so our copy stays exact even when the
   server shifted our edit past other people's edits (phase 8)
 * on a conflict we re-fetch the document and print a difflib diff of what changed (phase 5)
 * if the connection drops we reconnect by ourselves (0.5s, 1s, 2s ... max 5s) and show the current document (phase 6)
"""
import argparse
import asyncio
import difflib
import sys

from common import protocol as P

HELP = "show | r N text (replace) | i N text (insert before N) | a text (append) | d N [M] (delete) | sync | lag N (demo: fake slow network) | quit"
EDIT_COMMANDS = ("a", "r", "i", "d")


class Client:
    def __init__(self, name):
        self.name, self.lines, self.version = name, [], 0
        self.writer, self.pending, self.req = None, {}, 0
        self.diff_base = None            # our text before a conflicting edit, to diff against after the re-fetch
        self.quit = False
        self.lag = 0.0                   # demo only: delay other people's updates to fake a slow network

    def send(self, msg):
        self.writer.write(P.encode_message(msg))

    def show(self):
        print("\n".join(f"{i + 1:>3} | {t}" for i, t in enumerate(self.lines)) or "(empty document)")

    def print_diff(self, old, new):
        diff = list(difflib.unified_diff(old, new, "before", "server now", lineterm="", n=1))
        print("\n".join("    " + d for d in diff) if diff else "    (no difference)")

    def handle(self, m):
        t = m["type"]
        if t == P.SNAPSHOT:
            self.lines, self.version = list(m["lines"]), m["version"]
            if self.diff_base is not None:
                print(f"* document refreshed (v{self.version}). What changed since you started your edit:")
                self.print_diff(self.diff_base, self.lines)
                print("  Redo your edit if it still makes sense.")
                self.diff_base = None
            else:
                print(f"* document at version {self.version}")
                self.show()
        elif t == P.UPDATE:
            s, e, new = m["start_line"], m["end_line"], m["new_lines"]
            if m["version"] == self.version + 1:
                self.lines[s:e] = new
                self.version = m["version"]
                print(f"* {m['author']} changed lines {s + 1}-{max(e, s + len(new))} (v{self.version})")
            elif m["version"] > self.version + 1:
                print("* missed an update, re-fetching")
                self.send({"type": P.SYNC})
        elif t == P.ACK:
            entry = self.pending.pop(m["req_id"], None)
            if entry is None:
                return
            new = entry[2]
            s, e = m["start_line"], m["end_line"]            # the range the server APPLIED
            if m["version"] == self.version + 1:
                self.lines[s:e] = new
                self.version = m["version"]
                note = " (an earlier edit landed first; yours was moved to the right place)" if m.get("rebased") else ""
                print(f"ok (v{self.version}){note}")
            elif m["version"] > self.version + 1:
                print(f"ok (v{m['version']}) - accepted; others edited meanwhile, refreshing your copy")
                self.send({"type": P.SYNC})
        elif t == P.REJECT:
            entry = self.pending.pop(m["req_id"], None)
            if m["reason"] in ("conflict", "stale_version"):
                print("CONFLICT: someone else changed those lines first, so your edit was NOT applied. Re-fetching...")
                self.diff_base = entry[3] if entry else list(self.lines)
                self.send({"type": P.SYNC})
            else:
                print(f"REJECTED ({m['reason']}): {m.get('detail', '')}")
        elif t == P.ERROR:
            print(f"server error: {m.get('reason')}")

    def edit(self, start, end, new):
        self.req += 1
        self.pending[self.req] = (start, end, new, list(self.lines))
        self.send({"type": P.EDIT, "req_id": self.req, "base_version": self.version,
                   "start_line": start, "end_line": end, "new_lines": new})

    def command(self, text):
        """Run one typed command. Returns False to quit."""
        parts, n = text.strip().split(None, 2), len(self.lines)
        if not parts:
            return True
        cmd = parts[0].lower()
        try:
            if cmd in ("quit", "q", "exit"):
                return False
            if cmd in EDIT_COMMANDS + ("sync",) and self.writer is None:
                print("not connected - nothing was sent. Waiting for the server to come back...")
            elif cmd == "lag":               # demo only
                self.lag = float(parts[1]) if len(parts) > 1 else 0.0
                print(f"* demo: updates from others are now delayed by {self.lag}s" if self.lag else "* demo lag off")
            elif cmd == "help":
                print(HELP)
            elif cmd == "show":
                self.show()
            elif cmd == "sync":
                self.send({"type": P.SYNC})
            elif cmd == "a":
                body = text.strip().split(None, 1)
                self.edit(n, n, [body[1] if len(body) > 1 else ""])
            elif cmd in ("r", "i"):
                line, body = int(parts[1]), (parts[2] if len(parts) > 2 else "")
                if cmd == "r" and 1 <= line <= n:
                    self.edit(line - 1, line, [body])
                elif cmd == "i" and 1 <= line <= n + 1:
                    self.edit(line - 1, line - 1, [body])
                else:
                    print("line number out of range")
            elif cmd == "d":
                first = int(parts[1])
                last = int(parts[2]) if len(parts) > 2 else first
                if 1 <= first <= last <= n:
                    self.edit(first - 1, last, [])
                else:
                    print("range out of bounds")
            else:
                print("unknown command; type help")
        except (ValueError, IndexError):
            print("usage error; type help")
        return True


async def amain(args):
    c = Client(args.name)
    loop = asyncio.get_running_loop()

    async def read_commands():
        while not c.quit:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line or not c.command(line):
                c.quit = True
                if c.writer:
                    c.writer.close()
                return

    stdin_task = asyncio.create_task(read_commands())
    print(HELP)
    delay = 0.5
    while not c.quit:
        try:
            reader, writer = await asyncio.open_connection(args.host, args.port)
        except OSError:
            print(f"* cannot reach the server, retrying in {delay:.1f}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5)
            continue
        c.writer, delay = writer, 0.5
        c.send({"type": P.HELLO, "client_id": args.name})
        try:
            while True:
                m = await P.read_message(reader)
                if m is None:
                    break
                if c.lag and m["type"] == P.UPDATE:      # demo only: deliver other people's edits late
                    asyncio.get_running_loop().call_later(c.lag, c.handle, m)
                else:
                    c.handle(m)
        except (P.ProtocolError, ConnectionError, OSError) as e:
            print(f"* connection problem: {e}")
        finally:
            c.writer = None
            writer.close()
            if c.pending:
                print("* an edit was in flight when the connection dropped - type 'show' to check whether it landed")
                c.pending.clear()
        if not c.quit:
            print("* disconnected, reconnecting...")
            await asyncio.sleep(delay)
    stdin_task.cancel()


def main():
    ap = argparse.ArgumentParser(description="CollabFS client")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--name", required=True, help="letters, digits, _ . - (max 32)")
    asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    main()