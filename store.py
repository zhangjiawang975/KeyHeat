#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计数据：内存计数 + 按天明细 + JSON 持久化。"""

import json
import os
import threading
import time
from collections import deque

from common import DEFAULT_LAYOUT, LAYOUT_IDS, log


# --------------------------------------------------------------------------- #
# 统计存储
# --------------------------------------------------------------------------- #
def today_key():
    return time.strftime("%Y-%m-%d")


class Stats:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.all = {}        # vk -> 累计次数
        self.days = {}       # "YYYY-MM-DD" -> {vk: 次数}
        self.session = {}    # 本次运行
        self.recent = deque()  # 用于计算 KPM 的单调时钟时间戳
        self.paused = False
        self.layout = DEFAULT_LAYOUT
        self.dirty = False
        self.started = time.monotonic()
        self._load()

    # -- 持久化 ------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return
        except Exception as exc:  # 数据损坏时不要影响启动
            log("[warn] 历史数据读取失败：%s" % exc)
            return
        try:
            lay = str(d.get("layout") or "")
            if lay in LAYOUT_IDS:
                self.layout = lay
            self.all = {int(k): int(v) for k, v in (d.get("all") or {}).items()}
            self.days = {
                str(day): {int(k): int(v) for k, v in (m or {}).items()}
                for day, m in (d.get("days") or {}).items()
            }
        except Exception as exc:
            log("[warn] 历史数据格式异常：%s" % exc)

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            payload = {
                "version": 1,
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "layout": self.layout,
                "all": {str(k): v for k, v in self.all.items()},
                "days": {
                    day: {str(k): v for k, v in m.items()}
                    for day, m in self.days.items()
                },
            }
            self.dirty = False
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
        except Exception as exc:
            log("[warn] 保存失败：%s" % exc)

    # -- 写入 --------------------------------------------------------------
    def add(self, vk, ts):
        if self.paused:
            return
        day = today_key()
        with self.lock:
            self.all[vk] = self.all.get(vk, 0) + 1
            bucket = self.days.setdefault(day, {})
            bucket[vk] = bucket.get(vk, 0) + 1
            self.session[vk] = self.session.get(vk, 0) + 1
            self.recent.append(ts)
            self.dirty = True

    # -- 查询 --------------------------------------------------------------
    def snapshot(self, rng="today"):
        now = time.monotonic()
        with self.lock:
            if rng == "all":
                counts = dict(self.all)
            elif rng == "7d":
                counts = {}
                for i in range(7):
                    day = time.strftime(
                        "%Y-%m-%d", time.localtime(time.time() - i * 86400)
                    )
                    for k, v in self.days.get(day, {}).items():
                        counts[k] = counts.get(k, 0) + v
            else:
                counts = dict(self.days.get(today_key(), {}))

            while self.recent and now - self.recent[0] > 60:
                self.recent.popleft()
            kpm = len(self.recent)

            data = {
                "counts": {str(k): v for k, v in counts.items()},
                "total_all": sum(self.all.values()),
                "today_total": sum(self.days.get(today_key(), {}).values()),
                "session_total": sum(self.session.values()),
                "kpm": kpm,
                "paused": self.paused,
                "active_days": len([d for d, m in self.days.items() if m]),
                "uptime": int(now - self.started),
                "range": rng,
                "layout": self.layout,
            }
        return data

    # -- 控制 --------------------------------------------------------------
    def set_layout(self, layout):
        if layout not in LAYOUT_IDS:
            return False
        with self.lock:
            self.layout = layout
            self.dirty = True
        return True

    def set_paused(self, value):
        with self.lock:
            self.paused = bool(value)
            self.recent.clear()
        return self.paused

    def reset_today(self):
        with self.lock:
            self.days.pop(today_key(), None)
            self.recent.clear()
            self.dirty = True

    def reset_all(self):
        with self.lock:
            self.all.clear()
            self.days.clear()
            self.session.clear()
            self.recent.clear()
            self.dirty = True
