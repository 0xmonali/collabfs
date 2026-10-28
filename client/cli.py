"""PHASE 3 + client side of PHASE 5 (owner: Tanishq) - minimal terminal client.

  python -m client.cli --host 127.0.0.1 --port 9000 --name alice
Commands (line numbers start at 1):  show | r N text | i N text | a text | d N [M] | sync | help | quit
When the server rejects an edit (someone else got there first) the client re-fetches the document
and tells you to redo the edit. It never overwrites silently.
"""
import argparse
import asyncio
import sys

from common import protocol as P

HELP = "show | r N text (replace) | i N text (insert before N) | a text (append) | d N [M] (delete) | sync | quit"


class Client:
    def __init__(self, name):
        self.name, self.lines, self.version = name, [], 0
        self.writer, self.pending, self.req = None, {}, 0

    def send(self, msg):
        self.writer.write(P.encode_message(msg))

    def show(self):
        print("\n".join(f"{i + 1:>3} | {t}" for i, t in enumerate(self.lines)) or "(empty document)")

    def handle(self, m):
        t = m["type"]
        if t == P.SNAPSHOT:
            self.lines, self.version = list(m["lines"]), m["version"]
            print(f"* document at version {self.version}")
            self.show()
        elif t == P.UPDATE:
            if m["version"] == self.version + 1:
                self.lines[m["start_line"]:m["end_line"]] = m["new_lines"]
                self.version = m["version"]
                print(f"* {m['author']} changed lines {m['start_line'] + 1}-{max(m['end_line'], m['start_line'] + len(m['new_lines']))} (v{self.version})")
            elif m["version"] > self.version + 1:
                print("* missed an update, re-fetching")
                self.send({"type": P.SYNC})
        elif t == P.ACK:
            start, end, new = self.pending.pop(m["req_id"], (0, 0, []))
            if m["version"] == self.version + 1:
                self.lines[start:end] = new
                self.version = m["version"]
                print(f"ok (v{self.version})")
            else:
                self.send({"type": P.SYNC})
        elif t == P.REJECT:
            self.pending.pop(m["req_id"], None)
            print(f"REJECTED ({m['reason']}): {m.get('detail', '')}")
            if m["reason"] == "stale_version":
                print("  someone edited first - re-fetching the latest; redo your edit if it still makes sense")
                self.send({"type": P.SYNC})
        elif t == P.ERROR:
            print(f"server error: {m.get('reason')}")

    def edit(self, start, end, new):
        self.req += 1
        self.pending[self.req] = (start, end, new)
        self.send({"type": P.EDIT, "req_id": self.req, "base_version": self.version,
                   "start_line": start, "end_line": end, "new_lines": new})

    def command(self, text):
        """Returns False to quit."""
        parts, n = text.strip().split(None, 2), len(self.lines)
        if not parts:
            return True
        cmd = parts[0].lower()
        try:
            if cmd in ("quit", "q", "exit"):
                return False
            if cmd == "help":
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
    try:
        reader, c.writer = await asyncio.open_connection(args.host, args.port)
    except OSError as e:
        sys.exit(f"cannot connect to {args.host}:{args.port}: {e}")
    c.send({"type": P.HELLO, "client_id": args.name})

    async def receive():
        while True:
            try:
                m = await P.read_message(reader)
            except P.ProtocolError as e:
                print(f"protocol error: {e}")
                break
            if m is None:
                print("* server closed the connection")
                break
            c.handle(m)
    task = asyncio.create_task(receive())
    loop = asyncio.get_running_loop()
    print(HELP)
    while not task.done():
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line or not c.command(line):
            break
    c.writer.close()
    task.cancel()


def main():
    ap = argparse.ArgumentParser(description="CollabFS client (V1)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--name", required=True, help="letters, digits, _ . - (max 32)")
    asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    main()
