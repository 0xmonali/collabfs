# Demo script (about 6 minutes) - owner: Ananyaa (steps), Tanishq (demo_client.py), Monali (explanations)

Setup: start the server on one laptop (`python3 -m server.server --host 0.0.0.0 --port 9000 --state state.json`, or the systemd service). Three people connect: `python3 -m client.cli --host <ip> --port 9000 --name alice` (and `bob`, `carol`).
Safety net if anything fails: `python3 -m demo.demo_client --embedded` plays the whole story automatically.

| Step | Do | Say |
|---|---|---|
| 1. Shared document | alice: `a # Notes`, `a intro: TBD`, `a plan: TBD`, `a risks: TBD` | "Every client sees the edit within a moment. The server is the single source of truth." |
| 2. Different lines at the same time | alice `r 2 intro by alice`, bob `r 4 risks by bob`, typed and sent together | "Both are kept. The server shifted the later edit's line numbers only because the edits did not overlap." |
| 3. Same line at the same time | alice `r 3 plan A`, bob `r 3 plan B`, sent together | "Exactly one wins: the first to reach the server. The other sees CONFLICT and a diff. Nothing is lost silently." |
| 4. Recover | the loser types `r 3 plan B (merged)` | "On fresh text, it is accepted." |
| 5. Disconnect | carol closes her terminal; alice and bob add lines; carol reconnects with the same name | "A snapshot on reconnect gives her the current document." |
| 6. Server restart | restart the server (or `sudo systemctl restart collabfs`) | "Clients reconnect by themselves; the document was saved to disk." |
| 7. Deployment | `systemctl status collabfs`, `journalctl -u collabfs -f`, `firewall-cmd --list-ports` | "systemd restarts it after a crash, firewalld opens one port, journald keeps the log." |

Likely questions: why optimistic locking and not locks? why not merge text automatically? what if two edits arrive at the same instant? what do you guarantee and not guarantee? Answers are in `docs/design_decisions.md` (D4, D7, D9, "consistency guarantees").
