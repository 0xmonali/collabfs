# Deployment (phase 9): systemd + firewalld + journald

Needs a Linux machine with systemd, firewalld and Python 3.10+. (WSL only works if systemd is enabled there: run `systemctl status`; if it errors, use a real Linux machine or VM.)
On Ubuntu install firewalld first: `sudo apt install firewalld && sudo systemctl enable --now firewalld`.

## Install
```bash
git clone https://github.com/0xmonali/collabfs.git
cd collabfs
sudo ./deploy/install.sh
```
It creates a `collabfs` user, copies `server/` and `common/` to `/opt/collabfs`, installs and starts the service, and opens TCP port 9000 in firewalld. The document is kept in `/var/lib/collabfs/state.json`.

## Check it works (tick D1 to D7 in `docs/test_cases.md`)
| What | Command | Expected |
|---|---|---|
| Service running | `systemctl status collabfs` | `active (running)` |
| Listening | `ss -tlnp \| grep 9000` | python3 on `0.0.0.0:9000` |
| Port open | `sudo firewall-cmd --list-ports` | contains `9000/tcp` |
| Live log | `journalctl -u collabfs -f` | client connect/disconnect and ACCEPT/REJECT lines |
| Warnings only | `journalctl -u collabfs -p warning` | only warnings and errors |
| Connect | `python3 -m client.cli --host <server-ip> --port 9000 --name alice` | document appears |
| Crash drill | `sudo kill -9 $(systemctl show -p MainPID --value collabfs)` | restarts in about 2 s, document restored |
| Restart | `sudo systemctl restart collabfs` | log says state saved; clients reconnect |
| Boot | `systemctl is-enabled collabfs` | `enabled` |

Find the server's address with `hostname -I`. Other laptops on the same network use it as `--host`.

## Update after a code change
Run `sudo ./deploy/install.sh` again (it restarts the service).

## Problems
- Another machine cannot connect: check `firewall-cmd --list-ports`, `ss -tlnp`, and that the laptops are on the same network.
- Service will not start: `journalctl -u collabfs -n 50`. A corrupt `state.json` stops the server on purpose; move the file away to start with an empty document.
- Remove everything: `sudo systemctl disable --now collabfs; sudo rm /etc/systemd/system/collabfs.service; sudo rm -r /opt/collabfs /var/lib/collabfs; sudo firewall-cmd --permanent --remove-port=9000/tcp; sudo firewall-cmd --reload`.

The unit file and install script were checked for syntax but not run on a real host: do D1 to D7 once before the demo.
