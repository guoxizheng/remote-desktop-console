#!/usr/bin/env bash
# 局域网远程桌面控制台 管理脚本
# 用法: ./start.sh [start|stop|restart|status|logs]
# 可选环境变量: RDC_PORT=8389  RDC_TOKEN=你的令牌  RDC_NO_TERMINAL=1(禁用终端)
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$DIR/data/server.pid"
PORT="${RDC_PORT:-8389}"
TOKEN="${RDC_TOKEN:-}"
EXTRA=""
[ -n "${RDC_NO_TERMINAL:-}" ] && EXTRA="--no-terminal"

start() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "已在运行 (PID $(cat "$PIDFILE"))"
    return 0
  fi
  mkdir -p "$DIR/data"
  if [ -n "$TOKEN" ]; then
    nohup python3 "$DIR/server.py" --port "$PORT" --token "$TOKEN" $EXTRA >> "$DIR/data/server.log" 2>&1 &
  else
    nohup python3 "$DIR/server.py" --port "$PORT" $EXTRA >> "$DIR/data/server.log" 2>&1 &
  fi
  echo $! > "$PIDFILE"
  sleep 1
  if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "已启动 (PID $(cat "$PIDFILE"))"
    echo "端口: $PORT   日志: data/server.log"
    echo "请用浏览器打开: http://<NAS的局域网IP>:$PORT"
  else
    echo "启动失败, 请查看 data/server.log"
    rm -f "$PIDFILE"
  fi
}

stop() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")"
    rm -f "$PIDFILE"
    echo "已停止"
  else
    echo "未在运行"
    rm -f "$PIDFILE"
  fi
}

status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "运行中 (PID $(cat "$PIDFILE")), 端口 $PORT"
  else
    echo "未在运行"
  fi
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -n 50 -f "$DIR/data/server.log" ;;
  *)       echo "用法: $0 [start|stop|restart|status|logs]"; exit 1 ;;
esac
