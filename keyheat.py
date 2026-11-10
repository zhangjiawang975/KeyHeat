#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KeyHeat - Windows 键盘热力图

用 SetWindowsHookEx(WH_KEYBOARD_LL) 全局统计每个按键被按下的次数，
只记录「虚拟键码 + 计数」，不记录按键顺序、不记录任何文本内容。
统计结果保存在 %APPDATA%\\KeyHeat\\stats.json，界面用本地网页实时渲染。

用法:
    python keyheat.py                # 启动并自动打开浏览器（默认端口 49271）
    keyheat.py --port 9000           # 指定端口
    keyheat.py --no-open             # 不自动开浏览器
    keyheat.py --no-tray             # 不显示托盘图标
"""

import argparse
import atexit
import json
import os
import sys
import threading
import time
import webbrowser

from common import DATA_DIR, DATA_FILE, DEFAULT_PORT, INSTANCE_FILE, VERSION, log
from server import Handler, find_running_instance, make_server, pick_port
from store import Stats
from trayicon import TrayIcon
from updater import Updater, cleanup_old_files, start_cleanup_old, wait_pid_exit
from winhook import KeyHook


def main():
    ap = argparse.ArgumentParser(description="Windows 键盘热力图")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 %d" % DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--no-tray", action="store_true", help="不显示托盘图标")
    ap.add_argument("--no-update", action="store_true", help="本次启动不自动检查更新")
    ap.add_argument("--repo", default="", metavar="OWNER/NAME", help="更新来源仓库，默认用内置值")
    ap.add_argument("--update-api", default="", metavar="URL",
                    help="更新接口地址模板（内网镜像/测试用，默认 GitHub API）")
    ap.add_argument("--takeover", type=int, default=0, metavar="PID",
                    help="更新后重启用：等指定进程退出并接管端口")
    args = ap.parse_args()

    cleanup_old_files()
    start_cleanup_old()

    # 自动更新后重启：旧进程还占着端口，等它退干净再继续
    if args.takeover:
        ok = wait_pid_exit(args.takeover, 20000)
        log("[info] 接管模式：等待旧进程 %d %s" % (args.takeover, "已退出" if ok else "超时，继续启动"))

    # 单实例：已经在跑就只把面板打开，避免两个钩子重复计数 + 抢写同一个数据文件
    # 接管模式（更新后重启）例外：已经等过旧进程，再拦一次会导致更新后没有进程在跑
    running = None if args.takeover else find_running_instance()
    if running:
        url = "http://127.0.0.1:%d/" % running
        log("已有实例在运行（端口 %d），直接打开面板" % running)
        if not args.no_open:
            webbrowser.open(url)
        return 0

    stats = Stats(DATA_FILE)
    hook = KeyHook(stats.add)
    hook_ok = hook.start()
    if not hook_ok:
        log("[error] 键盘钩子安装失败：面板仍可打开，但不会计数（可尝试以管理员身份运行）")

    tray = {"obj": None}

    # 后台定时落盘 + 刷新托盘提示
    def saver():
        while True:
            time.sleep(5)
            stats.save()
            t = tray["obj"]
            if t is not None and t.ok:
                t.set_tooltip(t._base_tip())

    threading.Thread(target=saver, name="saver", daemon=True).start()

    def shutdown():
        hook.stop()
        stats.save()

    atexit.register(shutdown)

    Handler.stats = stats
    Handler.hook_ok = hook_ok

    port = pick_port(args.port)
    srv = make_server(("127.0.0.1", port))
    url = "http://127.0.0.1:%d/" % port

    # 供「退出程序」按钮调用：必须从别的线程 shutdown
    def request_quit():
        log("[info] 收到退出请求，正在保存并退出")
        threading.Timer(0.25, srv.shutdown).start()

    Handler.quit_cb = staticmethod(request_quit)

    # 自动更新
    updater = Updater(current=VERSION, repo=args.repo or None,
                      on_quit=request_quit, api_base=args.update_api)
    Handler.updater = updater
    if not args.no_update:
        updater.check_async(delay=6)

    # 托盘图标（失败也不影响统计）
    if not args.no_tray:
        ti = TrayIcon(url, stats, request_quit)
        if ti.start():
            ti.set_tooltip(ti._base_tip())
            tray["obj"] = ti
        else:
            log("[warn] 托盘不可用，继续以无界面方式运行")

    # 记录 pid + 端口，下次启动据此判断是否已经在跑（无窗口模式下用户看不到进程）
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INSTANCE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "app": "keyheat",
                    "pid": os.getpid(),
                    "port": port,
                    "url": url,
                    "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                f,
                ensure_ascii=False,
            )
    except Exception as exc:
        log("[warn] 写入实例信息失败：%s" % exc)

    log(
        "启动完成 | v%s | 端口 %d | 数据 %s | 钩子 %s"
        % (VERSION, port, DATA_FILE, "正常" if hook_ok else "失败")
    )
    if sys.stdout is not None:
        print("=" * 52)
        print("  KeyHeat · Windows 键盘热力图  v%s" % VERSION)
        print("=" * 52)
        print("  面板地址 : %s" % url)
        print("  数据文件 : %s" % DATA_FILE)
        print(
            "  监听状态 : %s"
            % (
                "运行中（仅统计按键次数，不记录输入内容）"
                if hook_ok
                else "失败（试试以管理员身份运行）"
            )
        )
        print("  托盘图标 : %s" % ("已创建（右键有菜单）" if tray["obj"] else "不可用"))
        print("  退出方式 : 托盘右键「退出程序」，或面板上的「退出程序」")
        print("=" * 52)
        sys.stdout.flush()

    if not args.no_open:
        threading.Timer(0.9, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hook.stop()
        if tray["obj"] is not None:
            tray["obj"].stop()
        stats.save()
        srv.server_close()
        # 只清理属于自己的实例记录（Windows 上偶发被占用，重试几次）
        for attempt in range(3):
            try:
                with open(INSTANCE_FILE, "r", encoding="utf-8") as f:
                    owner = int(json.load(f).get("pid") or 0)
                if owner == os.getpid():
                    os.remove(INSTANCE_FILE)
                break
            except FileNotFoundError:
                break
            except Exception as exc:
                if attempt == 2:
                    log("[warn] 清理实例信息失败：%r" % (exc,))
                else:
                    time.sleep(0.12)
        log("[info] 已停止，数据已保存。")
    return 0



if __name__ == "__main__":
    sys.exit(main())
