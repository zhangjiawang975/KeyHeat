#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""托盘图标：纯 ctypes 调 Shell_NotifyIcon，无第三方依赖。"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import webbrowser

from common import APP_DIR, LRESULT, kernel32, log, shell32, user32


# --------------------------------------------------------------------------- #
# 托盘图标（纯 ctypes 调 Shell_NotifyIcon，不引入第三方依赖）
# --------------------------------------------------------------------------- #
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", GUID), ("hBalloonIcon", wt.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wt.HWND
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.LoadImageW.argtypes = [
    wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT,
]
user32.LoadImageW.restype = ctypes.c_void_p
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.ExtractIconExW.argtypes = [
    wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.HICON), ctypes.POINTER(wt.HICON), wt.UINT,
]
shell32.ExtractIconExW.restype = wt.UINT

kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HINSTANCE
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = ctypes.c_void_p


class TrayIcon:
    """在独立线程里跑一个隐藏窗口 + 托盘图标。任何异常都不影响计数主流程。"""

    def __init__(self, url, stats, on_quit):
        self.url = url
        self.stats = stats
        self.on_quit = on_quit
        self.hwnd = None
        self.ok = False
        self.failed = None
        self._thread = None
        self._proc = None        # 必须持有 WNDPROC 引用，否则会被回收导致崩溃
        self._nid = None
        self._tip = "KeyHeat"
        self._ready = threading.Event()

    # ---- 对外 ----
    def start(self):
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        return self.ok

    def set_tooltip(self, text):
        self._tip = text[:120]
        if not self.ok or not self._nid:
            return
        try:
            self._nid.szTip = self._tip
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
        except Exception:
            pass

    def stop(self):
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    # ---- 内部 ----
    def _load_icon(self):
        path = os.path.join(APP_DIR, "icon.ico")
        if os.path.exists(path):
            h = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                return h
        # 退路：从 exe 自身的图标资源取
        large, small = wt.HICON(), wt.HICON()
        if shell32.ExtractIconExW(sys.executable, 0, ctypes.byref(large),
                                  ctypes.byref(small), 1):
            return large if large.value else small.value
        return user32.LoadIconW(None, ctypes.c_wchar_p(32512))  # IDI_APPLICATION

    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            cls = "KeyHeatTrayWnd"
            self._proc = WNDPROC(self._wndproc)
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
            wc.cbClsExtra = 0
            wc.cbWndExtra = 0
            wc.hInstance = hinst
            wc.hIcon = 0
            wc.hCursor = 0
            wc.hbrBackground = 0
            wc.lpszMenuName = None
            wc.lpszClassName = cls
            if not user32.RegisterClassW(ctypes.byref(wc)):
                raise OSError("RegisterClass 失败: %d" % ctypes.get_last_error())
            # 隐藏的顶层窗口（不用 message-only，否则 SetForegroundWindow 会失效）
            self.hwnd = user32.CreateWindowExW(
                0, cls, "KeyHeat", 0x80000000, 0, 0, 0, 0, None, None, hinst, None  # WS_POPUP
            )
            if not self.hwnd:
                raise OSError("CreateWindow 失败: %d" % ctypes.get_last_error())

            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = self._load_icon()
            nid.szTip = self._tip
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                raise OSError("Shell_NotifyIcon 失败: %d" % ctypes.get_last_error())
            self._nid = nid
            self.ok = True
            self._ready.set()
            log("[info] 托盘图标已创建")

            msg = wt.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self.failed = exc
            log("[warn] 托盘图标不可用（不影响统计）：%s" % exc)
        finally:
            try:
                if self._nid is not None:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                    self._nid = None
            except Exception:
                pass
            self.ok = False
            self._ready.set()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam == WM_RBUTTONUP:
                    self._show_menu()
                elif lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._open_panel()
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as exc:
            log("[warn] 托盘消息处理异常：%s" % exc)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _open_panel(self):
        try:
            webbrowser.open(self.url)
        except Exception:
            pass

    def _show_menu(self):
        paused = bool(self.stats.paused)
        menu = user32.CreatePopupMenu()
        try:
            user32.AppendMenuW(menu, MF_STRING, 1, "打开面板")
            user32.AppendMenuW(menu, MF_STRING, 2, "继续统计" if paused else "暂停统计")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, 3, "退出程序")
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x, pt.y, 0, self.hwnd, None,
            )
            user32.PostMessageW(self.hwnd, 0, 0, 0)  # WM_NULL，让菜单正确消失
        finally:
            user32.DestroyMenu(menu)
        if cmd == 1:
            self._open_panel()
        elif cmd == 2:
            self.stats.set_paused(not paused)
            self.stats.save()
            self.set_tooltip(self._base_tip())
        elif cmd == 3:
            log("[info] 托盘菜单请求退出")
            self.on_quit()

    def _base_tip(self):
        try:
            d = self.stats.snapshot("today")
            state = "已暂停" if d["paused"] else "记录中"
            return "KeyHeat · %s · 今日 %d 次" % (state, d["today_total"])
        except Exception:
            return "KeyHeat"
