# collabfs

Collaborative network file system: several clients edit **one shared text file** on a server at the same time, with a clear, deterministic strategy for conflicting edits.
Computer Networks mini project (Project 4). Python 3.10+, **standard library only** (asyncio TCP sockets, length-prefixed JSON messages, difflib; deployed with systemd, firewalld and journald). Nothing needs to be installed with pip.

## How it works
- **One server** keeps the document (a list of lines plus a version number) and is the single source of truth.
- **Clients** connect over TCP, get a copy of the document, and send edits such as "replace lines 3-4 with this text, based on version 7".
- **Conflict strategy (optimistic locking):** the server applies an edit only if no one changed those lines since the version it was based on.
  - Edits on **different lines** at the same time are all kept (the server shifts the line numbers of the later edit).
  - Edits on the **same lines** at the same time: the first to reach the server wins; the other client is told `CONFLICT`, sees a diff of what changed, and redoes its edit. Nothing is lost silently.
- **Reconnect:** a client that loses its connection reconnects by itself and receives the current document. The server saves the document to a file and reloads it after a restart.

## Quick start (three terminals on one machine)
```bash
git clone https://github.com/0xmonali/collabfs.git
cd collabfs

# terminal 1: server
python3 -m server.server --host 0.0.0.0 --port 9000 --state state.json

# terminal 2 and 3: clients (use any distinct names; for other machines add --host <server-ip>)
python3 -m client.cli --port 9000 --name alice
python3 -m client.cli --port 9000 --name bob
```
Client commands (line numbers start at 1):

| Command | Meaning |
|---|---|
| `show` | print the document |
| `a text` | append a line |
| `r N text` | replace line N |
| `i N text` | insert before line N |
| `d N [M]` | delete line N (or N to M) |
| `sync` | fetch the latest document |
| `help`, `quit` | help, exit |

## Automated demo
```bash
python3 -m demo.demo_client --embedded
```
Starts a temporary server and plays three clients: edits on different lines, a staged same-line conflict, and a disconnect/reconnect. It ends with `CONVERGED` when all clients see the same document. The manual demo steps are in `demo/demo_script.md`.

## Tests
```bash
python3 -m unittest discover -s tests -t . -v
```
All tests must print `OK`. The list of tests, plus the manual test checklist, is in `docs/test_cases.md`.

## Deployment on Linux (systemd + firewalld + journald)
```bash
sudo ./deploy/install.sh
journalctl -u collabfs -f
```
Details and checks: `docs/DEPLOYMENT.md`.

## Repository layout
```
collabfs/
├── common/protocol.py     message framing (4-byte length + JSON), shared by server and client
├── server/
│   ├── document.py        the document, version check, overlap detection and line shifting
│   └── server.py          asyncio server: connections, broadcast, saving, logging
├── client/cli.py          terminal client with automatic reconnect and conflict diff
├── deploy/                systemd unit and install script (firewalld rule included)
├── demo/                  scripted demo client and live demo script
├── tests/                 automated tests
└── docs/                  design decisions, test cases, deployment guide
```

## Documentation
- `docs/design_decisions.md`: every design choice, why it was made, and which alternatives were rejected, plus what the system does and does not guarantee.
- `docs/test_cases.md`: automated and manual test cases.
- `docs/DEPLOYMENT.md`: install, verify and troubleshoot the service.
- `demo/demo_script.md`: step-by-step live demo.

## Team
- Monali Das: server, conflict strategy, deployment
- Tanishq Srivastava: message protocol, client, demo client
- Ananyaa Das: tests, test cases, demo script
