#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开机自启：写 HKCU 的 Run 键。"""

import os
import sys

from common import BASE_DIR, log


# --------------------------------------------------------------------------- #
# 开机自启（写 HKCU 的 Run 键，可读回校验）
# --------------------------------------------------------------------------- #
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "KeyHeat"


def autostart_command():
    """返回应该写进 Run 键的命令行。"""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    script = os.path.join(BASE_DIR, "keyheat.py")
    exe = sys.executable
    pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pw):
        exe = pw
    return '"%s" "%s"' % (exe, script)


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
            return bool(val)
    except Exception:
        return False


def set_autostart(on):
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
                log("[info] 已开启开机自启：%s" % autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                    log("[info] 已关闭开机自启")
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        log("[warn] 设置开机自启失败：%s" % exc)
        return False
