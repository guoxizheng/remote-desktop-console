#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网远程桌面控制台 (LAN Remote Desktop Console)
单文件、纯 Python 标准库, 零依赖。
内置 WebSocket->TCP 桥接 + 内嵌 noVNC 前端。
用法: python3 server.py [--port 8389] [--bind 0.0.0.0] [--token SECRET]
"""

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

BASE = Path(__file__).resolve().parent
WEB_DIR = BASE / "web"
DATA_DIR = BASE / "data"
HOSTS_FILE = DATA_DIR / "hosts.json"

CFG = {"port": 8389, "bind": "0.0.0.0", "token": None}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ---------------------------------------------------------------- 日志

LOG_LINES = deque(maxlen=500)
LOG_LOCK = threading.Lock()


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    with LOG_LOCK:
        LOG_LINES.append(line)
    print(line, flush=True)


def recent_logs(n=200):
    with LOG_LOCK:
        return list(LOG_LINES)[-n:]


# ---------------------------------------------------------------- 主机存储


def load_hosts():
    try:
        with open(HOSTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_hosts(hosts):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HOSTS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hosts, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HOSTS_FILE)


def get_host(host_id):
    for h in load_hosts():
        if h.get("id") == host_id:
            return h
    return None


def host_public(h):
    d = dict(h)
    d["has_vnc_password"] = bool(d.get("vnc_password"))
    return d


# ---------------------------------------------------------------- 工具检测


def _which_xfreerdp():
    local = BASE / "vendor" / "rdp" / "usr" / "bin" / "xfreerdp"
    if local.is_file() and os.access(str(local), os.X_OK):
        return str(local)
    return shutil.which("xfreerdp")


def _which_x11vnc():
    local = BASE / "vendor" / "vnc" / "usr" / "bin" / "x11vnc"
    if local.is_file() and os.access(str(local), os.X_OK):
        return str(local)
    return shutil.which("x11vnc")


def detect_tools():
    tools = {
        "xfreerdp": bool(_which_xfreerdp()),
        "Xvnc": bool(shutil.which("Xvnc")),
        "Xvfb": bool(shutil.which("Xvfb")),
        "x11vnc": bool(_which_x11vnc()),
    }
    tools["rdp_ready"] = (tools["xfreerdp"] and tools["x11vnc"]
                          and (tools["Xvnc"] or tools["Xvfb"]))
    return tools


# ---------------------------------------------------------------- RDP 会话

RDP_SESSIONS = {}
RDP_LOCK = threading.Lock()


def _free_vnc_port(start=5911):
    for p in range(start, start + 80):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            pass
        finally:
            s.close()
    raise RuntimeError("没有可用的 VNC 端口")


def _free_display(start=90):
    for n in range(start, start + 30):
        if not Path("/tmp/.X%d-lock" % n).exists():
            return n
    raise RuntimeError("没有可用的显示号")


def _rdp_env():
    env = dict(os.environ)
    paths = []
    for sub in ("rdp", "vnc"):
        lib = BASE / "vendor" / sub / "usr" / "lib" / "x86_64-linux-gnu"
        if lib.is_dir():
            paths.append(str(lib))
    if paths:
        env["LD_LIBRARY_PATH"] = ":".join(paths) + ":" + env.get("LD_LIBRARY_PATH", "")
    # FreeRDP 需要可写的 HOME 来创建证书存储/配置目录
    home = DATA_DIR / "home"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    (home / ".cache").mkdir(parents=True, exist_ok=True)
    return env


def _wait_port(host, port, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            s = socket.create_connection((host, port), timeout=1)
            s.close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def _kill_proc(p):
    if not p:
        return
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except Exception:
        try:
            p.terminate()
        except Exception:
            pass
    try:
        p.wait(timeout=3)
    except Exception:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass


def rdp_session_list():
    with RDP_LOCK:
        out = []
        for k, v in list(RDP_SESSIONS.items()):
            out.append({"host_id": k, "name": v["name"],
                        "display": v["display"], "vnc_port": v["vnc_port"],
                        "elapsed": int(time.time() - v["started"]),
                        "alive": v["xfreerdp"].poll() is None})
        return out


def rdp_start(host):
    hid = host["id"]
    with RDP_LOCK:
        if hid in RDP_SESSIONS:
            v = RDP_SESSIONS[hid]
            return {"ok": True, "vnc_port": v["vnc_port"],
                    "display": v["display"], "already": True}
        t = detect_tools()
        if not t["rdp_ready"]:
            return {"ok": False, "error": "缺少 RDP 组件: xfreerdp=%s, Xvnc=%s"
                    % (t["xfreerdp"], t["Xvnc"])}
        user = (host.get("rdp_user") or "").strip()
        pwd = host.get("rdp_password") or ""
        if not user or not pwd:
            return {"ok": False, "error": "请先在主机设置中填写 RDP 用户名和密码"}
        rdp_port = int(host.get("rdp_port") or 3389)
        try:
            _s = socket.create_connection((host["ip"], rdp_port), timeout=3)
            _s.close()
        except OSError as e:
            return {"ok": False, "error": "目标 %s:%d 不可达 (%s)"
                    % (host["ip"], rdp_port, e)}
        logf = open(DATA_DIR / ("rdp_%s.log" % hid), "ab")
        xvnc = rdp = x11vnc = None
        try:
            display = _free_display()
            rfb_port = _free_vnc_port()
            env = _rdp_env()
            xvnc_bin = shutil.which("Xvnc")
            xvfb_bin = shutil.which("Xvfb")
            if xvnc_bin:
                probe = _free_vnc_port(rfb_port + 1)
                xvnc = subprocess.Popen(
                    [xvnc_bin, ":%d" % display,
                     "-geometry", "1600x900", "-depth", "24",
                     "-SecurityTypes", "None", "-rfbport", str(probe),
                     "-localhost", "-ac", "-noWebsocket"],
                    stdout=logf, stderr=logf, start_new_session=True)
                if not _wait_port("127.0.0.1", probe):
                    raise RuntimeError("Xvnc 启动失败, 详见 data/rdp_%s.log" % hid)
            elif xvfb_bin:
                xvnc = subprocess.Popen(
                    [xvfb_bin, ":%d" % display, "-screen", "0", "1600x900x24",
                     "-ac"], stdout=logf, stderr=logf, start_new_session=True)
                lock = Path("/tmp/.X%d-lock" % display)
                t0 = time.time()
                while time.time() - t0 < 8 and not lock.exists():
                    time.sleep(0.2)
                if not lock.exists():
                    raise RuntimeError("Xvfb 启动失败, 详见 data/rdp_%s.log" % hid)
            else:
                raise RuntimeError("缺少虚拟显示器 Xvnc/Xvfb")
            # 标准 RFB 输出 (KasmVNC 自带 RFB 不兼容 noVNC, 用 x11vnc 转一道)
            x11vnc = subprocess.Popen(
                [_which_x11vnc(), "-display", ":%d" % display,
                 "-rfbport", str(rfb_port), "-localhost", "-nopw",
                 "-shared", "-forever", "-noxdamage", "-quiet"],
                env=env, stdout=logf, stderr=logf, start_new_session=True)
            if not _wait_port("127.0.0.1", rfb_port):
                raise RuntimeError("x11vnc 启动失败, 详见 data/rdp_%s.log" % hid)
            env["DISPLAY"] = ":%d" % display
            args = [_which_xfreerdp(),
                    "/v:%s:%d" % (host["ip"], int(host.get("rdp_port") or 3389)),
                    "/u:%s" % user, "/p:%s" % pwd, "/f", "/cert:ignore"]
            rdp = subprocess.Popen(args, env=env, stdout=logf, stderr=logf,
                                   start_new_session=True)
            time.sleep(2.5)
            if rdp.poll() is not None:
                raise RuntimeError("xfreerdp 已退出 (账号密码错误/目标拒绝), 详见 data/rdp_%s.log" % hid)
            RDP_SESSIONS[hid] = {"name": host["name"], "display": display,
                                 "vnc_port": rfb_port, "xvnc": xvnc,
                                 "x11vnc": x11vnc, "xfreerdp": rdp,
                                 "started": time.time()}
            log("RDP 会话启动: %s -> Xvnc :%d + x11vnc :%d (VNC 127.0.0.1:%d)"
                % (host["name"], display, rfb_port, rfb_port))
            return {"ok": True, "vnc_port": rfb_port, "display": display}
        except Exception as e:
            _kill_proc(rdp)
            _kill_proc(x11vnc)
            _kill_proc(xvnc)
            logf.flush()
            detail = ""
            try:
                with open(DATA_DIR / ("rdp_%s.log" % hid), "r",
                          encoding="utf-8", errors="replace") as f:
                    lines = [x for x in f.read().strip().splitlines() if "ERROR" in x]
                    detail = lines[-1][:200] if lines else ""
            except Exception:
                pass
            msg = str(e)
            if detail:
                msg += " | " + detail
            log("RDP 启动失败: %s -> %s" % (host["name"], msg))
            return {"ok": False, "error": msg}


def rdp_stop(hid):
    with RDP_LOCK:
        info = RDP_SESSIONS.pop(hid, None)
    if not info:
        return {"ok": True, "stopped": False}
    _kill_proc(info.get("xfreerdp"))
    _kill_proc(info.get("x11vnc"))
    _kill_proc(info.get("xvnc"))
    log("RDP 会话已停止: %s" % info["name"])
    return {"ok": True, "stopped": True}



# ---------------------------------------------------------------- 活跃桥接

ACTIVE = {}
ACTIVE_LOCK = threading.Lock()


def active_list():
    with ACTIVE_LOCK:
        out = []
        for k, v in list(ACTIVE.items()):
            d = dict(v)
            d["elapsed"] = int(time.time() - d["start"])
            out.append(d)
        return out


# ---------------------------------------------------------------- WebSocket


def _read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def ws_accept(key):
    return base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_send_frame(sock, payload, opcode=0x2):
    hdr = bytearray([0x80 | (opcode & 0x0F)])
    n = len(payload)
    if n < 126:
        hdr.append(n)
    elif n < 65536:
        hdr.append(126)
        hdr += struct.pack(">H", n)
    else:
        hdr.append(127)
        hdr += struct.pack(">Q", n)
    sock.sendall(bytes(hdr) + payload)
def ws_read_frame(sock, frag):
    """读取一条完整消息(处理分片/控制帧)。
    返回 (opcode, payload); close 返回 (8, payload); 断连返回 None。
    frag: [opcode, bytearray] 未完成分片状态
    """
    while True:
        hdr = _read_exact(sock, 2)
        b0, b1 = hdr[0], hdr[1]
        opcode = b0 & 0x0F
        fin = b0 & 0x80
        masked = b1 & 0x80
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _read_exact(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _read_exact(sock, 8))[0]
        mask = _read_exact(sock, 4) if masked else b""
        payload = _read_exact(sock, ln) if ln else b""
        if masked and payload:
            payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        if opcode == 0x8:
            return (0x8, payload)
        if opcode == 0x9:
            ws_send_frame(sock, payload, 0xA)
            continue
        if opcode == 0xA:
            continue
        if opcode == 0x0:
            if not frag[0]:
                continue
            frag[1] += payload
            if fin:
                op, data = frag[0], bytes(frag[1])
                frag[0], frag[1] = None, bytearray()
                return (op, data)
            continue
        if fin:
            return (opcode, payload)
        frag[0] = opcode
        frag[1] = bytearray(payload)


def relay_ws(ws_sock, tcp_sock, info):
    """WS <-> TCP 双向转发, 任一端断开即结束"""
    frag = [None, bytearray()]
    closed = {"tcp": False}

    def tcp_to_ws():
        try:
            while not closed["tcp"]:
                data = tcp_sock.recv(65536)
                if not data:
                    break
                for i in range(0, len(data), 128 * 1024):
                    ws_send_frame(ws_sock, data[i:i + 128 * 1024], 0x2)
                with info["lock"]:
                    info["tx"] += len(data)
        except Exception:
            pass
        finally:
            closed["tcp"] = True

    t = threading.Thread(target=tcp_to_ws, daemon=True)
    t.start()
    try:
        while True:
            r = ws_read_frame(ws_sock, frag)
            if r is None:
                break
            op, data = r
            if op == 0x8:
                try:
                    ws_send_frame(ws_sock, b"", 0x8)
                except Exception:
                    pass
                break
            tcp_sock.sendall(data)
            with info["lock"]:
                info["rx"] += len(data)
    except Exception:
        pass
    finally:
        try:
            tcp_sock.close()
        except Exception:
            pass
        t.join(timeout=1)
        try:
            ws_sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            ws_sock.close()
        except Exception:
            pass
        with ACTIVE_LOCK:
            ACTIVE.pop(info["key"], None)
        log("桥接结束: %s -> %s:%d (浏览器->目标 %d KB, 目标->浏览器 %d KB)"
            % (info["name"], info["target"], info["target_port"],
               info["rx"] // 1024, info["tx"] // 1024))
# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RDConsole/1.0"

    def log_message(self, fmt, *args):
        p = urlparse(self.path).path
        if p.startswith(("/api/", "/ws/")):
            log("HTTP %s %s -> %s" % (self.command, p, getattr(self, "_code", "?")))

    def finish(self):
        try:
            super().finish()
        except Exception:
            pass

    def _send_json(self, obj, code=200):
        self._code = code
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, msg, code=400):
        self._send_json({"ok": False, "error": msg}, code)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def _token_ok(self):
        if not CFG["token"]:
            return True
        q = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        if q and q == CFG["token"]:
            return True
        m = re.search(r"rdc_token=([^;]+)", self.headers.get("Cookie", ""))
        return bool(m and m.group(1) == CFG["token"])

    def _need_token(self):
        self._send_error_json("访问需要令牌 (token)", 401)
        return False

    # ---------- 路由 ----------

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/ws/vnc/"):
            return self._handle_ws(path)
        if path.startswith("/api/"):
            if not self._token_ok():
                return self._need_token()
            return self._api_get(path)
        return self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._send_error_json("not found", 404)
        if not self._token_ok():
            return self._need_token()
        return self._api_post(path)

    def do_PUT(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/hosts/"):
            return self._send_error_json("not found", 404)
        if not self._token_ok():
            return self._need_token()
        host_id = path.rsplit("/", 1)[-1]
        data = self._read_json()
        if data is None:
            return self._send_error_json("JSON 格式错误", 400)
        h = get_host(host_id)
        if not h:
            return self._send_error_json("主机不存在", 404)
        for k in ("name", "ip", "vnc_port", "rdp_port", "vnc_password",
                  "rdp_user", "rdp_password"):
            if k in data:
                h[k] = data[k]
        h["vnc_port"] = int(h.get("vnc_port") or 5900)
        h["rdp_port"] = int(h.get("rdp_port") or 3389)
        h["ip"] = re.sub(r"^https?://", "", str(h.get("ip", "")),
                         flags=re.I).rstrip("/").split("/")[0]
        hosts = [x for x in load_hosts() if x["id"] != host_id] + [h]
        save_hosts(hosts)
        log("更新主机: %s" % h.get("name"))
        self._send_json({"ok": True, "host": host_public(h)})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/hosts/"):
            return self._send_error_json("not found", 404)
        if not self._token_ok():
            return self._need_token()
        host_id = path.rsplit("/", 1)[-1]
        h = get_host(host_id)
        if not h:
            return self._send_error_json("主机不存在", 404)
        hosts = [x for x in load_hosts() if x["id"] != host_id]
        save_hosts(hosts)
        log("删除主机: %s" % h.get("name"))
        self._send_json({"ok": True})

    # ---------- API ----------

    def _api_get(self, path):
        if path == "/api/hosts":
            self._send_json({"ok": True, "hosts": [host_public(h) for h in load_hosts()]})
        elif path == "/api/status":
            self._send_json({"ok": True, "tools": detect_tools(),
                             "active": active_list(),
                             "rdp_sessions": rdp_session_list(),
                             "token_set": bool(CFG["token"]),
                             "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        elif path == "/api/logs":
            self._send_json({"ok": True, "logs": recent_logs(200)})
        else:
            self._send_error_json("not found", 404)

    def _api_post(self, path):
        if path == "/api/hosts":
            data = self._read_json()
            if data is None:
                return self._send_error_json("JSON 格式错误", 400)
            ip = str(data.get("ip", "")).strip()
            ip = re.sub(r"^https?://", "", ip, flags=re.I).rstrip("/").split("/")[0]
            name = str(data.get("name", "")).strip() or ip
            if not ip:
                return self._send_error_json("ip 不能为空", 400)
            h = {
                "id": uuid.uuid4().hex[:8],
                "name": name,
                "ip": ip,
                "vnc_port": int(data.get("vnc_port") or 5900),
                "rdp_port": int(data.get("rdp_port") or 3389),
                "vnc_password": str(data.get("vnc_password") or ""),
                "rdp_user": str(data.get("rdp_user") or ""),
                "rdp_password": str(data.get("rdp_password") or ""),
                "created": time.strftime("%Y-%m-%d %H:%M"),
            }
            hosts = load_hosts()
            hosts.append(h)
            save_hosts(hosts)
            log("添加主机: %s (%s)" % (name, ip))
            self._send_json({"ok": True, "host": host_public(h)}, 201)
        elif path.endswith("/rdp/start"):
            host_id = path.split("/")[-3]
            h = get_host(host_id)
            if not h:
                return self._send_error_json("主机不存在", 404)
            res = rdp_start(h)
            self._send_json(res, 200 if res.get("ok") else 400)
        elif path.endswith("/rdp/stop"):
            host_id = path.split("/")[-3]
            self._send_json(rdp_stop(host_id))
        elif path.endswith("/test"):
            host_id = path.split("/")[-2]
            h = get_host(host_id)
            if not h:
                return self._send_error_json("主机不存在", 404)
            res = {}
            for label, port in (("vnc", h.get("vnc_port", 5900)),
                                ("rdp", h.get("rdp_port", 3389))):
                t0 = time.time()
                try:
                    s = socket.create_connection((h["ip"], int(port)), timeout=3)
                    s.close()
                    res[label] = {"ok": True, "ms": int((time.time() - t0) * 1000)}
                except Exception as e:
                    res[label] = {"ok": False, "error": str(e)}
            self._send_json({"ok": True, "test": res})
        else:
            self._send_error_json("not found", 404)

    # ---------- WebSocket 桥接 ----------

    def _handle_ws(self, path):
        host_id = path.rsplit("/", 1)[-1].split("?")[0]
        if not self._token_ok():
            return self._send_error_json("访问需要令牌", 401)
        h = get_host(host_id)
        if not h:
            return self._send_error_json("主机不存在", 404)
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or "websocket" not in (self.headers.get("Upgrade") or "").lower():
            return self._send_error_json("需要 WebSocket 升级", 400)
        with RDP_LOCK:
            sess = RDP_SESSIONS.get(host_id)
        if sess:
            target_ip, target_port = "127.0.0.1", sess["vnc_port"]
            label = h["name"] + " (RDP)"
        else:
            target_ip, target_port = h["ip"], int(h["vnc_port"])
            label = h["name"]
        try:
            tcp = socket.create_connection((target_ip, target_port), timeout=5)
        except Exception as e:
            return self._send_error_json("无法连接目标 %s:%s (%s)" % (
                target_ip, target_port, e), 502)
        resp = ("HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n") % ws_accept(key)
        self.wfile.write(resp.encode())
        self.wfile.flush()
        self._code = 101
        try:
            tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        info = {"key": uuid.uuid4().hex[:8], "name": label,
                "target": target_ip, "target_port": target_port,
                "start": time.time(), "rx": 0, "tx": 0,
                "lock": threading.Lock()}
        with ACTIVE_LOCK:
            ACTIVE[info["key"]] = info
        log("桥接开始: %s -> %s:%d" % (label, target_ip, target_port))
        self.close_connection = True
        relay_ws(self.connection, tcp, info)

    # ---------- 静态文件 ----------

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        fp = (WEB_DIR / unquote(path.lstrip("/"))).resolve()
        try:
            fp.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._send_error_json("forbidden", 403)
        if fp.is_dir():
            fp = fp / "index.html"
        if not fp.is_file():
            return self._send_error_json("not found", 404)
        ctype, _ = mimetypes.guess_type(str(fp))
        if fp.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif fp.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif fp.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        body = fp.read_bytes()
        self._code = 200
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _cleanup_stale_rdp():
    """清理上次异常退出遗留的 RDP 虚拟显示器/VNC/客户端进程(只匹配本程序特征)"""
    for pattern in ("Xvnc .* -noWebsocket", "vendor/vnc/usr/bin/x11vnc",
                    "vendor/rdp/usr/bin/xfreerdp", "Xvfb :9"):
        try:
            out = subprocess.run(["pgrep", "-f", pattern],
                                 capture_output=True, text=True, timeout=5).stdout
            for pid in out.split():
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    log("清理残留进程: pid %s (%s)" % (pid, pattern))
                except Exception:
                    pass
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="LAN Remote Desktop Console")
    ap.add_argument("--port", type=int, default=8389)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--token", default=None, help="访问令牌 (可选)")
    a = ap.parse_args()
    CFG["port"], CFG["bind"], CFG["token"] = a.port, a.bind, a.token
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_rdp()

    if not (WEB_DIR / "novnc" / "vnc.html").is_file():
        log("警告: 未找到 noVNC 前端 (%s/novnc/vnc.html)" % WEB_DIR)
    tools = detect_tools()
    log("启动: http://%s:%d  (RDP: %s)" % (
        "NAS-IP" if CFG["bind"] == "0.0.0.0" else CFG["bind"], CFG["port"],
        "就绪 (xfreerdp + Xvnc)" if tools["rdp_ready"] else
        "不可用 (缺少 xfreerdp/Xvnc)"))
    srv = ThreadingHTTPServer((CFG["bind"], CFG["port"]), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("已停止")


if __name__ == "__main__":
    main()
