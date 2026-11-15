#!/bin/bash
set -euo pipefail
TASK_MONITOR_DIR="$(cd "$(dirname "$0")" && pwd)"
TASK_SSH_DIR="$(mktemp -d /tmp/datn-monitor-ssh.XXXXXX)"
TASK_SERVER_PID=""
cleanup() {
  ssh -S "$TASK_SSH_DIR/control" -O exit pi@100.103.159.73 >/dev/null 2>&1 || true
  if [ -n "$TASK_SERVER_PID" ]; then
    kill "$TASK_SERVER_PID" >/dev/null 2>&1 || true
    wait "$TASK_SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -f "$TASK_SSH_DIR/control"
  rmdir "$TASK_SSH_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM
echo "Kết nối trạng thái Pi. Nếu được hỏi, nhập mật khẩu SSH của Pi tại cửa sổ này."
ssh -M -S "$TASK_SSH_DIR/control" -f -N \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:18765:127.0.0.1:8765 pi@100.103.159.73
if ! curl --noproxy '*' --silent --fail http://127.0.0.1:8787/health | /usr/bin/python3 -c 'import json,sys;sys.exit(json.load(sys.stdin).get("service")!="datn-mac-monitor")' 2>/dev/null; then
  /usr/bin/python3 "$TASK_MONITOR_DIR/monitor.py" --port 8787 &
  TASK_SERVER_PID="$!"
fi
for TASK_ATTEMPT in 1 2 3 4 5 6 7 8 9 10; do
  if curl --noproxy '*' --silent --fail http://127.0.0.1:8787/health >/dev/null; then break; fi
  sleep 0.3
done
open 'http://127.0.0.1:8787'
echo "Dashboard: http://127.0.0.1:8787"
echo "Giữ cửa sổ này mở. Nhấn Ctrl+C để đóng kết nối giám sát."
while ssh -S "$TASK_SSH_DIR/control" -O check pi@100.103.159.73 >/dev/null 2>&1; do
  if [ -n "$TASK_SERVER_PID" ] && ! kill -0 "$TASK_SERVER_PID" 2>/dev/null; then break; fi
  sleep 3
done
