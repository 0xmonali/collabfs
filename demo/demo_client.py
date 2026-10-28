"""PHASE 10 (owner: Tanishq) - scripted demo with 3 clients, no typing needed.

  python3 -m demo.demo_client --port 9000          # against a running server
  python3 -m demo.demo_client --embedded           # starts its own temporary server
Shows: (1) edits on different lines at the same moment, (2) a staged same-line conflict,
(3) a disconnect and reconnect.  Exit code 0 only if every client ends with the same document.
"""
import argparse
import asyncio
import sys

from common.protocol import encode_message, read_message


class Raw:
    def __init__(self, name, host, port):
        self.name, self.host, self.port = name, host, port

    async def connect(self):
        self.r, self.w = await asyncio.open_connection(self.host, self.port)
        await self.send(type="hello", client_id=self.name)
        self.snap = await self.recv("snapshot")

    async def send(self, **m):
        self.w.write(encode_message(m))
        await self.w.drain()

    async def recv(self, *types):
        while True:
            m = await asyncio.wait_for(read_message(self.r), 3)
            if m["type"] in types:
                return m

    async def edit(self, base, s, e, lines, req):
        await self.send(type="edit", req_id=req, base_version=base, start_line=s, end_line=e, new_lines=lines)
        return await self.recv("ack", "reject")

    async def sync(self):
        await self.send(type="sync")
        return await self.recv("snapshot")


def show(title, snap):
    print(f"   {title} (v{snap['version']})")
    for i, t in enumerate(snap["lines"]):
        print(f"     {i + 1:>2} | {t}")


async def run(host, port):
    a, b, c = (Raw(n, host, port) for n in ("alice", "bob", "carol"))
    for x in (a, b, c):
        await x.connect()
    v = a.snap["version"]
    print("\n=== 0. Alice writes the first draft")
    r = await a.edit(v, len(a.snap["lines"]), len(a.snap["lines"]),
                     ["# Notes", "intro: TBD", "plan: TBD", "risks: TBD", "owner: TBD"], 1)
    base = r["version"]
    show("document", await b.sync())

    print("\n=== 1. Alice, Bob and Carol edit DIFFERENT lines at the same moment (all from v%d)" % base)
    n = len((await a.sync())["lines"]) - 5          # offset if the document already had lines
    res = await asyncio.gather(a.edit(base, n + 1, n + 2, ["intro: by alice"], 2),
                               b.edit(base, n + 3, n + 4, ["risks: by bob"], 2),
                               c.edit(base, n + 4, n + 5, ["owner: carol"], 2))
    for name, x in zip(("alice", "bob", "carol"), res):
        print(f"   {name}: {x['type']}" + (" (moved to the right place)" if x.get("rebased") else ""))
    snap = await a.sync()
    show("all three edits survived", snap)
    base = snap["version"]

    print("\n=== 2. Alice and Bob rewrite the SAME line at the same moment")
    res = await asyncio.gather(a.edit(base, n + 2, n + 3, ["plan: alice's plan"], 3),
                               b.edit(base, n + 2, n + 3, ["plan: bob's plan"], 3))
    for name, x in zip(("alice", "bob"), res):
        print(f"   {name}: {x['type']}" + (f" ({x['reason']}) - the other edit reached the server first" if x["type"] == "reject" else ""))
    loser = b if res[1]["type"] == "reject" else a
    snap = await loser.sync()
    show(f"{loser.name} re-fetches", snap)
    r = await loser.edit(snap["version"], n + 2, n + 3, [snap["lines"][n + 2] + f" (+ {loser.name}'s note)"], 4)
    print(f"   {loser.name} redoes the edit on the fresh text: {r['type']}")

    print("\n=== 3. Carol's connection drops, the others keep editing, Carol reconnects")
    c.w.close()
    await asyncio.sleep(0.1)
    snap = await a.sync()
    await a.edit(snap["version"], len(snap["lines"]), len(snap["lines"]), ["added while carol was offline"], 5)
    await c.connect()
    show("carol after reconnect", c.snap)

    snaps = [await x.sync() for x in (a, b, c)]
    ok = all(s == snaps[0] for s in snaps)
    print("\n=== RESULT:", "all three clients see the same document - CONVERGED" if ok else "MISMATCH - BUG")
    for x in (a, b, c):
        x.w.close()
    return 0 if ok else 1


async def amain(args):
    if args.embedded:
        from server.server import CollabServer
        srv = CollabServer("127.0.0.1", 0)
        await srv.start()
        try:
            return await run("127.0.0.1", srv.port)
        finally:
            await srv.stop()
    return await run(args.host, args.port)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--embedded", action="store_true", help="start a temporary server inside the demo")
    sys.exit(asyncio.run(amain(ap.parse_args())))


if __name__ == "__main__":
    main()
