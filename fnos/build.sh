#!/usr/bin/env bash
# 构建飞牛 fnOS 应用包 (.fpk)
# 用法: ./build.sh
# 环境变量: FNPACK=/path/to/fnpack 指定打包工具
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$DIR")"
BUILD="$DIR/build"

echo ">>> 1/4 生成图标"
python3 "$DIR/gen_icons.py"

echo ">>> 2/4 组装打包目录"
rm -rf "$BUILD"
mkdir -p "$BUILD"
cp -r "$DIR/src/." "$BUILD/"

APP="$BUILD/app"
cp "$ROOT/server.py" "$APP/server.py"
cp -r "$ROOT/web" "$APP/web"
if [ -f "$ROOT/start.sh" ]; then cp "$ROOT/start.sh" "$APP/start.sh"; fi
if [ -f "$ROOT/setup_vendor.sh" ]; then cp "$ROOT/setup_vendor.sh" "$APP/setup_vendor.sh"; fi
if [ -f "$ROOT/README.md" ]; then cp "$ROOT/README.md" "$APP/README.md"; fi
if [ -f "$ROOT/LICENSE" ]; then cp "$ROOT/LICENSE" "$APP/LICENSE"; fi

if [ -d "$ROOT/vendor" ]; then
    cp -r "$ROOT/vendor" "$APP/vendor"
    echo "    已内置 vendor/ (FreeRDP + x11vnc, 约 $(du -sh "$ROOT/vendor" | cut -f1))"
else
    echo "    [警告] 未找到 vendor/, 包内不含 RDP 组件" >&2
fi

chmod +x "$APP/start.sh" "$APP/setup_vendor.sh" 2>/dev/null || true
chmod +x "$BUILD/cmd/"* 2>/dev/null || true

echo ">>> 3/4 获取 fnpack"
FNPACK="${FNPACK:-$DIR/fnpack}"
if [ ! -x "$FNPACK" ]; then
    echo "    下载 fnpack 1.2.3 ..."
    curl -fsSL --retry 3 --max-time 120 -o "$FNPACK" \
        https://static2.fnnas.com/fnpack/fnpack-1.2.3-linux-amd64
    chmod +x "$FNPACK"
fi

VER=$(grep -E '^version' "$BUILD/manifest" | head -1 | sed 's/.*=[[:space:]]*//')

echo ">>> 4/4 打包 (版本 $VER)"
( cd "$BUILD" && "$FNPACK" build )

OUT="$DIR/remote-desktop-console-${VER}.fpk"
mv "$BUILD/remote-desktop-console.fpk" "$OUT"
ls -lh "$OUT"
echo ""
echo ">>> 完成: $OUT"
echo ">>> 安装: 飞牛 fnOS 应用中心 -> 右上角 -> 手动安装 -> 选择该 .fpk 文件"
