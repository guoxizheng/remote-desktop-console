#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成飞牛 fnOS 应用图标 (ICON.PNG 64x64 / ICON_256.PNG 256x256 + 入口图标)。

纯标准库实现: 3 倍超采样 + 面积平均下采样, 得到平滑的圆角矩形图标。
"""
import struct
import zlib
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "src"


def write_png(path, w, h, rgba_rows):
    raw = b"".join(b"\x00" + bytes(row) for row in rgba_rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 9)) +
           chunk(b"IEND", b""))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(png)


def rounded(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = x0 + r if x < x0 + r else (x1 - r if x > x1 - r else x)
    cy = y0 + r if y < y0 + r else (y1 - r if y > y1 - r else y)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def segment(x, y, x0, y0, x1, y1, t):
    """点到线段的距离 <= t"""
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return (x - x0) ** 2 + (y - y0) ** 2 <= t * t
    u = ((x - x0) * dx + (y - y0) * dy) / L2
    u = max(0.0, min(1.0, u))
    px, py = x0 + u * dx, y0 + u * dy
    return (x - px) ** 2 + (y - py) ** 2 <= t * t


def render(size):
    SS = 3
    S = size * SS
    buf = bytearray(S * S * 4)

    top = (79, 140, 255)      # #4f8cff
    bottom = (123, 92, 255)   # #7b5cff
    white = (240, 245, 255)

    def put(x, y, rgb, a=255):
        if 0 <= x < S and 0 <= y < S:
            i = (y * S + x) * 4
            buf[i:i + 4] = bytes((rgb[0], rgb[1], rgb[2], a))

    # 画布归一化坐标
    def n(v):
        return int(v * S)

    m = 0.02
    r = 0.21
    x0, y0 = n(m), n(m)
    x1, y1 = n(1 - m) - 1, n(1 - m) - 1

    for y in range(S):
        t = (y / max(1, S - 1))
        bg = (int(top[0] + (bottom[0] - top[0]) * t),
              int(top[1] + (bottom[1] - top[1]) * t),
              int(top[2] + (bottom[2] - top[2]) * t))
        for x in range(S):
            if rounded(x, y, x0, y0, x1, y1, n(r)):
                put(x, y, bg)

    # 显示器外框 + 屏幕
    mx0, my0 = n(0.20), n(0.22)
    mx1, my1 = n(0.80), n(0.585)
    mr = n(0.055)
    frame = n(0.038)
    for y in range(my0, my1 + 1):
        for x in range(mx0, mx1 + 1):
            if rounded(x, y, mx0, my0, mx1, my1, mr):
                inner = rounded(x, y, mx0 + frame, my0 + frame,
                                mx1 - frame, my1 - frame, max(1, mr - frame))
                if not inner:
                    put(x, y, white)
                else:
                    put(x, y, (30, 42, 70))   # 屏幕深色

    # 支架
    sx0, sx1 = n(0.465), n(0.535)
    for y in range(n(0.585), n(0.665)):
        for x in range(sx0, sx1):
            put(x, y, white)
    # 底座
    bx0, bx1 = n(0.37), n(0.63)
    for y in range(n(0.665), n(0.705)):
        for x in range(bx0, bx1):
            if rounded(x, y, bx0, n(0.665), bx1, n(0.705), n(0.018)):
                put(x, y, white)

    # 屏幕里的终端提示符 >_
    th = n(0.020)
    cx0, cy0 = n(0.315), n(0.345)
    cx1, cy1 = n(0.395), n(0.445)
    for y in range(n(0.31), n(0.48)):
        for x in range(n(0.29), n(0.56)):
            if (segment(x, y, cx0, cy0, cx1, cy1, th) or
                    segment(x, y, cx0, cy1 + (cy1 - cy0), cx1, cy1, th) or
                    (n(0.43) <= x <= n(0.55) and n(0.415) <= y <= n(0.445))):
                put(x, y, (240, 245, 255))

    # 下采样
    rows = []
    for oy in range(size):
        row = bytearray(size * 4)
        for ox in range(size):
            r_sum = g_sum = b_sum = a_sum = 0
            for dy in range(SS):
                for dx in range(SS):
                    i = ((oy * SS + dy) * S + ox * SS + dx) * 4
                    a = buf[i + 3]
                    r_sum += buf[i] * a
                    g_sum += buf[i + 1] * a
                    b_sum += buf[i + 2] * a
                    a_sum += a
            n_px = SS * SS
            a = a_sum // n_px
            if a_sum > 0:
                row[ox * 4 + 0] = r_sum // a_sum
                row[ox * 4 + 1] = g_sum // a_sum
                row[ox * 4 + 2] = b_sum // a_sum
            row[ox * 4 + 3] = a
        rows.append(row)
    return rows


def main():
    for size, name in ((64, "ICON.PNG"), (256, "ICON_256.PNG")):
        rows = render(size)
        write_png(SRC / name, size, size, rows)
        write_png(SRC / "app" / "ui" / "images" /
                  ("icon_%d.png" % size), size, size, rows)
        print("生成", name, "%dx%d" % (size, size))


if __name__ == "__main__":
    main()
