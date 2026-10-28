#!/usr/bin/env bash
# Install CollabFS as a systemd service and open its port in firewalld. Run as root from the repo root:
#   sudo ./deploy/install.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./deploy/install.sh"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
PORT=9000

id collabfs &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin collabfs
install -d -m 0755 /opt/collabfs
rm -rf /opt/collabfs/server /opt/collabfs/common
cp -r "$SRC/server" "$SRC/common" /opt/collabfs/
find /opt/collabfs -name __pycache__ -prune -exec rm -rf {} +
install -m 0644 "$SRC/deploy/collabfs.service" /etc/systemd/system/collabfs.service
systemctl daemon-reload
systemctl enable collabfs
systemctl restart collabfs

if command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port="${PORT}/tcp"
  firewall-cmd --reload
  echo "firewalld: opened ${PORT}/tcp"
else
  echo "WARNING: firewalld is not running, so port ${PORT}/tcp was NOT opened" >&2
fi
sleep 1
systemctl --no-pager status collabfs || true
echo "Done. Live logs: journalctl -u collabfs -f"
