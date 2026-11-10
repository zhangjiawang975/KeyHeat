#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共部分：路径、常量、日志、Win32 DLL 句柄。"""

import ctypes
import os
import sys
import threading
import time

if os.name != "nt":
    sys.exit("KeyHeat 仅支持 Windows")

# 用 pythonw 或打包成 exe（--noconsole）时，标准流是 None，print 会抛 AttributeError
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

VERSION = "1.1.0"

# 自动更新检查的仓库（改成你自己的仓库即可；tag 形如 v1.2.0，资产放 KeyHeat.exe）
UPDATE_REPO = "zhangjiawang975/KeyHeat"

# PyInstaller 单文件模式下，资源被解到临时目录 sys._MEIPASS
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    APP_DIR = BASE_DIR

DATA_DIR = os.path.join(os.environ.get("APPDATA") or APP_DIR, "KeyHeat")
DATA_FILE = os.path.join(DATA_DIR, "stats.json")
INSTANCE_FILE = os.path.join(DATA_DIR, "instance.json")
LOG_FILE = os.path.join(DATA_DIR, "keyheat.log")
UI_FILE = os.path.join(APP_DIR, "ui.html")

# 端口选在 IANA 动态段(49152-65535)，不是任何已知服务的默认端口，避免抢别人的
DEFAULT_PORT = 49271
DEFAULT_LAYOUT = "y75"
LAYOUT_IDS = {"y75", "k68", "k61", "tkl", "full"}

# 过滤长按自动重复：同一按键 45ms 内的连续 down 事件视为一次
DEDUPE_WINDOW = 0.045

# 三个常用的 Win32 DLL 在这里统一创建，各模块共享（argtypes 各自设置）
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
LRESULT = ctypes.c_ssize_t


# --------------------------------------------------------------------------- #
# 日志：用 pythonw 无窗口启动时 sys.stdout 是 None，print 会直接抛异常
# --------------------------------------------------------------------------- #
_log_lock = threading.Lock()


def log(msg):
    with _log_lock:
        try:
            if sys.stdout is not None:
                print(msg)
                sys.stdout.flush()
        except Exception:
            pass
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 512 * 1024:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write("-- log rotated %s --\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except Exception:
            pass
