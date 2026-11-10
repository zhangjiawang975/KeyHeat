#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 HTTP 服务：面板页面 + 各接口。"""

import json
import os
import socket
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from autostart import autostart_enabled, set_autostart
from common import DEFAULT_PORT, INSTANCE_FILE, UI_FILE, log


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    stats = None  # type: Stats
    hook_ok = True
    quit_cb = None
    updater = None
    _ui_cache = {"mtime": 0.0, "body": b""}

    def log_message(self, fmt, *args):  # 静音访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _ui(self):
        try:
            mtime = os.path.getmtime(UI_FILE)
        except OSError:
            self._send(500, "ui.html 缺失", "text/plain; charset=utf-8")
            return
        if mtime != self._ui_cache["mtime"]:
            with open(UI_FILE, "rb") as f:
                self._ui_cache["body"] = f.read()
            self._ui_cache["mtime"] = mtime
        self._send(200, self._ui_cache["body"], "text/html; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/ui.html"):
            self._ui()
        elif path == "/api/ping":
            self._json({"app": "keyheat", "pid": os.getpid(), "hook_ok": bool(self.hook_ok)})
        elif path == "/api/stats":
            qs = parse_qs(urlparse(self.path).query)
            rng = (qs.get("range") or ["today"])[0]
            if rng not in ("today", "7d", "all"):
                rng = "today"
            data = self.stats.snapshot(rng)
            data["hook_ok"] = bool(self.hook_ok)
            data["pid"] = os.getpid()
            data["autostart"] = autostart_enabled()
            if self.updater is not None:
                st = self.updater.status()
                data["version"] = st["version"]
                data["update"] = {
                    "state": st["state"],
                    "latest": (st["latest"] or {}).get("version"),
                    "progress": st["progress"],
                    "error": st["error"],
                    "frozen": st["frozen"],
                }
            self._json(data)
        elif path == "/api/update":
            if self.updater is None:
                self._json({"state": "idle", "error": "未启用更新检查"})
            else:
                self._json(self.updater.status())
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/control":
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        action = payload.get("action")
        if action == "pause":
            self.stats.set_paused(True)
        elif action == "resume":
            self.stats.set_paused(False)
        elif action == "check_update":
            if self.updater is None:
                self._json({"ok": False, "error": "未启用更新检查"}, 400)
                return
            self.updater.check_async()
            self._json({"ok": True})
            return
        elif action == "do_update":
            if self.updater is None:
                self._json({"ok": False, "error": "未启用更新检查"}, 400)
                return
            started = self.updater.install_async()
            self._json({"ok": started})
            return
        elif action == "set_autostart":
            set_autostart(bool(payload.get("on")))
            self._json({"ok": True, "autostart": autostart_enabled()})
            return
        elif action == "set_layout":
            ok = self.stats.set_layout(str(payload.get("id") or ""))
            self.stats.save()
            self._json({"ok": ok, "layout": self.stats.layout})
            return
        elif action == "reset_today":
            self.stats.reset_today()
        elif action == "reset_all":
            self.stats.reset_all()
        elif action == "quit":
            # 先把数据落盘并回包，再异步停掉服务（shutdown 不能在 serve_forever 线程里调）
            self.stats.save()
            self._json({"ok": True, "quitting": True})
            try:
                # 注意：必须从类上取，否则实例访问会把普通函数绑定成方法，多传一个 self
                cb = type(self).quit_cb
                if cb is not None:
                    cb()
            except Exception as exc:
                log("[warn] 触发退出失败：%s" % exc)
            return
        else:
            self._json({"ok": False, "error": "unknown action"}, 400)
            return
        self.stats.save()
        self._json({"ok": True, "paused": self.stats.paused})





def make_server(addr, handler=Handler):
    srv = ThreadingHTTPServer(addr, handler)
    srv.daemon_threads = True
    return srv


def pick_port(start):
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("找不到可用端口，请用 --port 指定")


def ping(port, timeout=0.6):
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:%d/api/ping" % port, timeout=timeout
        ) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "keyheat"
    except Exception:
        return False


def find_running_instance():
    """返回已在运行的实例端口；没有则返回 None。

    设环境变量 KEYHEAT_ALLOW_MULTI=1 可跳过检查（调试用，会重复计数）。
    """
    if os.environ.get("KEYHEAT_ALLOW_MULTI") == "1":
        return None
    try:
        with open(INSTANCE_FILE, "r", encoding="utf-8") as f:
            port = int(json.load(f).get("port") or 0)
        if port and ping(port):
            return port
    except Exception:
        pass
    if ping(DEFAULT_PORT):
        return DEFAULT_PORT
    return None
