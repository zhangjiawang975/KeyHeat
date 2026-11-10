#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动更新：检查 GitHub Release → 下载 → 校验 → 原地替换 → 重启。

设计取舍（对照 TeslaVideo 的 updater.js）：
- TeslaVideo 是 Electron 解压包，得先退出、再解压覆盖整个目录，所以用 PowerShell 脚本等进程退出；
- KeyHeat 是 PyInstaller 单文件 exe，Windows 允许**重命名正在运行的 exe**，
  于是可以直接「自己改名 → 新文件顶上 → 拉起新进程 → 自己退出」，不需要外部脚本。
- 新进程启动时用 --takeover 等旧进程真正退出（端口要等旧的释放）。
"""

import ctypes.wintypes as wt
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from common import DATA_DIR, UPDATE_REPO, VERSION, kernel32, log

API_LATEST = "https://api.github.com/repos/%s/releases/latest"
USER_AGENT = "KeyHeat-Updater"
TIMEOUT = 20
MIN_EXE_BYTES = 1 << 20  # 小于 1MB 的一定不是我们的 exe

# 只认这些资产名：KeyHeat.exe / KeyHeat-1.2.0-win-x64.exe 之类
ASSET_RE = re.compile(r"^KeyHeat.*\.exe$", re.I)

# GitHub 未认证 API 只有 60 次/小时/IP，所以：本地缓存 + 6 小时节流；
# 带 If-None-Match 的 304 响应不计入配额，命中缓存时几乎零消耗。
CACHE_FILE = os.path.join(DATA_DIR, "update.json")
CHECK_INTERVAL = 6 * 3600

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
SYNCHRONIZE = 0x00100000

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def parse_version(text):
    """'v1.2.3' / '1.2.3' / '1.2.3-beta' → (1, 2, 3)；解析失败返回 None。"""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(remote, local):
    a, b = parse_version(remote), parse_version(local)
    if not a or not b:
        return False
    return a > b


def wait_pid_exit(pid, timeout_ms=20000):
    """等某个进程结束（用于接管：等旧实例释放端口）。"""
    try:
        h = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not h:
            return True
        try:
            return kernel32.WaitForSingleObject(h, timeout_ms) == 0
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return True


class Updater:
    """状态机：idle → checking → (uptodate | available) → downloading → ready。"""

    def __init__(self, current=VERSION, repo=UPDATE_REPO, on_quit=None, api_base=""):
        self.current = current
        self.repo = repo or UPDATE_REPO
        self.api = api_base or API_LATEST
        self.on_quit = on_quit
        self.lock = threading.Lock()
        self.state = "idle"
        self.latest = None      # {version, notes, url, size, digest, page}
        self.progress = 0.0
        self.error = None
        self.checked_at = 0
        self._busy = False

    # ---- 对外 ----
    def status(self):
        with self.lock:
            return {
                "version": self.current,
                "repo": self.repo,
                "state": self.state,
                "latest": self.latest,
                "progress": round(self.progress, 3),
                "error": self.error,
                "frozen": bool(getattr(sys, "frozen", False)),
                "checked_at": self.checked_at,
            }

    def check_async(self, delay=0, force=False):
        threading.Thread(target=self._check_job, args=(delay, force), daemon=True).start()

    def install_async(self):
        if self._busy:
            return False
        self._busy = True
        threading.Thread(target=self._install_job, daemon=True).start()
        return True

    # ---- 内部 ----
    def _set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _check_job(self, delay, force=False):
        if delay:
            time.sleep(delay)
        with self.lock:
            if self.state in ("checking", "downloading", "ready"):
                return
            self.state = "checking"
            self.error = None

        cache = self._load_cache()
        fresh = cache.get("checked_at") and (time.time() - cache["checked_at"] < CHECK_INTERVAL)
        if fresh and not force and cache.get("payload"):
            log("[info] 使用缓存的更新信息（%d 分钟前检查过）"
                % int((time.time() - cache["checked_at"]) / 60))
            self._apply(cache["payload"], cache["checked_at"])
            return

        try:
            payload, checked_at = self._fetch_latest(cache.get("etag") or "")
        except urllib.error.HTTPError as exc:
            if exc.code == 403 or exc.code == 429:
                msg = "检查太频繁，GitHub 暂时限流了，稍后再试"
            elif exc.code == 404:
                msg = "仓库 %s 还没有发布 release" % self.repo
            else:
                msg = "HTTP %d" % exc.code
            log("[warn] 检查更新失败：%s" % msg)
            self._set(state="error", error=msg)
            return
        except Exception as exc:
            log("[warn] 检查更新失败：%s" % exc)
            self._set(state="error", error=str(exc))
            return
        self._save_cache({"etag": payload and payload.pop("_etag", ""), "payload": payload, "checked_at": checked_at})
        self._apply(payload, checked_at)

    def _apply(self, info, checked_at):
        self.checked_at = checked_at or time.time()
        if not info:
            self._set(state="error", error="release 中没有找到 KeyHeat*.exe")
            return
        if is_newer(info["version"], self.current):
            log("[info] 发现新版本 %s（当前 %s）" % (info["version"], self.current))
            self._set(state="available", latest=info)
        else:
            self._set(state="uptodate", latest=info)

    def _load_cache(self):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _save_cache(self, data):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _fetch_latest(self, etag=""):
        """返回 (info, checked_at)；命中 304 时用缓存里的 payload。"""
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if etag:
            headers["If-None-Match"] = etag
        req = urllib.request.Request(self.api % self.repo, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read().decode("utf-8")
                new_etag = r.headers.get("ETag") or ""
        except urllib.error.HTTPError as exc:
            if exc.code == 304:               # 没变化，不消耗配额
                cache = self._load_cache()
                return cache.get("payload"), time.time()
            raise
        rel = json.loads(raw)
        assets = rel.get("assets") or []
        asset = None
        for a in assets:                     # 优先完全同名的
            if a.get("name", "").lower() == "keyheat.exe":
                asset = a
                break
        if asset is None:
            for a in assets:
                if ASSET_RE.match(a.get("name", "")):
                    asset = a
                    break
        if asset is None:
            return None, time.time()
        return {
            "_etag": new_etag,
            "version": str(rel.get("tag_name") or "").lstrip("vV"),
            "notes": (rel.get("body") or "").strip()[:2000],
            "url": asset.get("browser_download_url"),
            "size": int(asset.get("size") or 0),
            "digest": asset.get("digest") or "",
            "page": rel.get("html_url") or "",
            "name": asset.get("name"),
            "published": rel.get("published_at") or "",
        }, time.time()

    def _download(self, info, dest):
        req = urllib.request.Request(info["url"], headers={"User-Agent": USER_AGENT})
        total = info.get("size") or 0
        got = 0
        sha = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(dest, "wb") as f:
            if not total:
                total = int(r.headers.get("Content-Length") or 0)
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                got += len(chunk)
                if total:
                    self._set(progress=min(1.0, got / total))
        return got, sha.hexdigest()

    def _install_job(self):
        try:
            with self.lock:
                info = self.latest
                if self.state not in ("available", "error") or not info:
                    self.state = "available" if info else "error"
                    self.error = self.error or "没有可用的更新信息"
                    return
                self.state = "downloading"
                self.progress = 0.0
                self.error = None

            if not getattr(sys, "frozen", False):
                raise RuntimeError("源码模式不支持自动替换，请用打包好的 KeyHeat.exe")

            exe = os.path.abspath(sys.executable)
            folder = os.path.dirname(exe)
            try:
                test = os.path.join(folder, ".writetest")
                with open(test, "w") as f:
                    f.write("1")
                os.remove(test)
            except OSError:
                raise RuntimeError("没有写入权限：%s" % folder)

            new_path = exe + ".new"
            old_path = exe + ".old"
            try:
                got, sha = self._download(info, new_path)
                if info.get("size") and got != info["size"]:
                    raise RuntimeError("下载不完整：%d/%d 字节" % (got, info["size"]))
                if got < MIN_EXE_BYTES:
                    raise RuntimeError("文件太小，不像正常的 exe")
                with open(new_path, "rb") as f:
                    if f.read(2) != b"MZ":
                        raise RuntimeError("下载到的不是可执行文件")
                digest = (info.get("digest") or "").lower()
                if digest.startswith("sha256:") and digest.split(":", 1)[1] != sha:
                    raise RuntimeError("SHA-256 校验不通过")
                log("[info] 更新包已下载并校验通过（%d 字节）" % got)

                # 原地替换：运行中的 exe 可以改名，但不能被覆盖
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass
                os.replace(exe, old_path)
                try:
                    os.replace(new_path, exe)
                except Exception:
                    os.replace(old_path, exe)   # 回滚
                    raise
            except Exception:
                for p in (new_path,):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                raise

            self._set(state="ready", progress=1.0)
            log("[info] 已替换为 %s，准备重启" % info["version"])
            ok = self._spawn(exe)
            if not ok:
                self._set(state="error", error="新版本已就位，但自动重启失败，请手动打开程序")
                log("[warn] 重启失败，已替换文件，等用户手动启动")
                return
            time.sleep(0.4)
            if self.on_quit:
                self.on_quit()
        except Exception as exc:
            log("[warn] 更新失败：%s" % exc)
            self._set(state="error", error=str(exc))
        finally:
            self._busy = False

    def _spawn(self, exe):
        # 必须沿用原来的启动参数（--port 等），否则重启后会跑到别的端口/丢掉配置
        cleaned, skip = [], False
        for a in sys.argv[1:]:
            if skip:
                skip = False
                continue
            if a == "--takeover":
                skip = True
                continue
            if a.startswith("--takeover="):
                continue
            cleaned.append(a)
        args = [exe] + cleaned + ["--takeover", str(os.getpid())]
        for flags in (
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            0,
        ):
            try:
                subprocess.Popen(
                    args,
                    close_fds=True,
                    creationflags=flags,
                    cwd=os.path.dirname(exe),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except OSError as exc:
                log("[warn] 启动新版本失败(flags=%d)：%s" % (flags, exc))
        return False


def cleanup_old_files():
    """清理上次更新留下的 KeyHeat.exe.old / .new。

    注意：更新后重启时，旧进程可能还没完全退出，而 .old 正是它自己的镜像、被系统锁着，
    所以这里只是"试一次"，失败很正常——由 start_cleanup_old() 反复重试直到成功。
    """
    try:
        exe = os.path.abspath(sys.executable)
    except Exception:
        return 0
    if not getattr(sys, "frozen", False):
        return 0
    removed = 0
    for suffix in (".old", ".new"):
        p = exe + suffix
        try:
            if os.path.exists(p):
                os.remove(p)
                removed += 1
                log("[info] 已清理旧文件 %s" % os.path.basename(p))
        except OSError:
            pass
    return removed


def start_cleanup_old(max_seconds=120, interval=5):
    """后台重试清理，直到旧进程退出、文件可删为止。"""
    if not getattr(sys, "frozen", False):
        return

    def job():
        deadline = time.time() + max_seconds
        while time.time() < deadline:
            if cleanup_old_files() == 0:
                exe = os.path.abspath(sys.executable)
                if not any(os.path.exists(exe + s) for s in (".old", ".new")):
                    return
            time.sleep(interval)

    threading.Thread(target=job, name="cleanup-old", daemon=True).start()
