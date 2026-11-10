#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局键盘钩子：WH_KEYBOARD_LL + 独立消费线程。"""

import ctypes
import ctypes.wintypes as wt
import queue
import threading
import time

from common import DEDUPE_WINDOW, LRESULT, kernel32, log, user32


# --------------------------------------------------------------------------- #
# 全局键盘钩子
# --------------------------------------------------------------------------- #

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.GetCurrentThreadId.restype = wt.DWORD


class KeyHook:
    """在独立线程里挂 WH_KEYBOARD_LL，回调只把事件塞进队列，绝不阻塞。"""

    def __init__(self, sink):
        self.sink = sink
        self.events = queue.SimpleQueue()
        self.ready = threading.Event()
        self.running = False
        self.ok = False
        self._thread_id = 0
        self._proc = None

    # hook 回调（运行在消息循环线程）
    def _callback(self, n_code, w_param, l_param):
        if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            try:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self.events.put((int(kb.vkCode), time.monotonic()))
            except Exception:
                pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _hook_loop(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        self._proc = HOOKPROC(self._callback)  # 必须持有引用，否则被回收会崩
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not hook:
            log("[error] 安装键盘钩子失败，错误码 %d" % ctypes.get_last_error())
            self.ready.set()
            return
        self.ok = True
        self.ready.set()
        msg = wt.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)

    def _consume_loop(self):
        last_vk, last_ts = -1, 0.0
        while self.running:
            try:
                vk, ts = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            # 长按自动重复去重
            if vk == last_vk and ts - last_ts < DEDUPE_WINDOW:
                last_ts = ts
                continue
            last_vk, last_ts = vk, ts
            try:
                self.sink(vk, ts)
            except Exception:
                pass

    def start(self):
        self.running = True
        threading.Thread(target=self._hook_loop, name="hook", daemon=True).start()
        threading.Thread(target=self._consume_loop, name="consume", daemon=True).start()
        self.ready.wait(3.0)
        return self.ok

    def stop(self):
        self.running = False
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
