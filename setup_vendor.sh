#!/usr/bin/env bash
# 一次性部署脚本: 下载运行所需的二进制组件到 vendor/
# (仓库不包含二进制, 以保持轻量; 目标系统需为 Debian/Ubuntu 系)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="$DIR/vendor"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

MIRRORS=(
  "https://deb.debian.org/debian"
  "https://ftp.debian.org/debian"
  "http://ftp.debian.org/debian"
)

RDP_VER="2.11.7+dfsg1-6~deb12u1"
VNC_VER="0.9.14+dfsg-1+deb12u2"
X11VNC_VER="0.9.16-9"

for bin in curl dpkg; do
  command -v "$bin" >/dev/null || { echo "缺少 $bin, 请先安装"; exit 1; }
done

mkdir -p "$VENDOR/rdp" "$VENDOR/vnc"

fetch() {  # $1=池内相对路径  $2=解压目录
  local path="$1" dest="$2" f url ok=0
  f="$TMP/$(basename "$path")"
  for m in "${MIRRORS[@]}"; do
    url="$m/$path"
    echo "  下载 $(basename "$path")"
    if curl -fsSL --retry 2 --connect-timeout 10 --max-time 180 -o "$f" "$url"; then
      ok=1
      break
    fi
    echo "    镜像失败, 换下一个…"
  done
  [ "$ok" = 1 ] || { echo "下载失败: $path"; exit 1; }
  dpkg -x "$f" "$dest"
}

echo ">>> FreeRDP $RDP_VER (RDP 客户端 + 库)"
fetch "pool/main/f/freerdp2/freerdp2-x11_${RDP_VER}_amd64.deb" "$VENDOR/rdp"
fetch "pool/main/f/freerdp2/libfreerdp-client2-2_${RDP_VER}_amd64.deb" "$VENDOR/rdp"
fetch "pool/main/f/freerdp2/libfreerdp2-2_${RDP_VER}_amd64.deb" "$VENDOR/rdp"
fetch "pool/main/f/freerdp2/libwinpr2-2_${RDP_VER}_amd64.deb" "$VENDOR/rdp"

echo ">>> x11vnc $X11VNC_VER + libvncserver (标准 RFB 输出)"
fetch "pool/main/x/x11vnc/x11vnc_${X11VNC_VER}_amd64.deb" "$VENDOR/vnc"
fetch "pool/main/libv/libvncserver/libvncserver1_${VNC_VER}_amd64.deb" "$VENDOR/vnc"
fetch "pool/main/libv/libvncserver/libvncclient1_${VNC_VER}_amd64.deb" "$VENDOR/vnc"

echo ""
echo ">>> 完成. 检查系统依赖:"
python3 - <<'PY'
import shutil
for name, hint in (("Xvnc", "kasmvnc / tigervnc-standalone-server"),
                   ("Xvfb", "xvfb"), ("python3", "")):
    if shutil.which(name):
        print("  [OK] %s" % name)
    else:
        print("  [缺少] %s (可 apt install %s)" % (name, hint))
PY
echo ">>> 启动: ./start.sh start"
