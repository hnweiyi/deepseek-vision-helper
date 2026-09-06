#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""额度计数 + 频率控制。

- 额度：本地 JSON 按自然日计数（全局 + 单模型），调用前主动检查，超限熔断。
- 频率：目标组（默认魔搭）调用间做最小间隔限速，降低短时 429 概率。

仅依赖 Python 标准库，Python 3.8 兼容。
"""
import json
import threading
import time
from datetime import date


class Quota:
    def __init__(self, cfg, state_file):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.group = str(cfg.get("group", "modelscope"))
        self.global_limit = int(cfg.get("global_daily", 2000))
        self.per_model_limit = int(cfg.get("per_model_daily", 500))
        self.reserve_ratio = float(cfg.get("reserve_ratio", 0.2))
        self.min_interval = float(cfg.get("min_interval_sec", 5.0))
        self.global_effective = int(self.global_limit * (1.0 - self.reserve_ratio))
        self.per_model_effective = int(self.per_model_limit * (1.0 - self.reserve_ratio))
        self.state_file = state_file
        self._lock = threading.Lock()
        # 目标组内为串行调用，单线程访问，无需加锁
        self._last_call = 0.0
        self.state = self._load()

    def _load(self):
        today = date.today().isoformat()
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            s = {}
        if s.get("date") != today:
            s = {"date": today, "global_used": 0, "models": {}}
        s.setdefault("models", {})
        return s

    def _save(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
        except Exception:
            pass

    def check(self, provider_name, group):
        """调用前检查额度。返回 (ok, reason)。非目标组或未启用恒放行。"""
        if not self.enabled or group != self.group:
            return (True, "")
        with self._lock:
            if self.state["global_used"] >= self.global_effective:
                return (False, "全局额度已尽(%d/%d)" % (self.state["global_used"], self.global_effective))
            used = self.state["models"].get(provider_name, 0)
            if used >= self.per_model_effective:
                return (False, "模型额度已尽(%d/%d)" % (used, self.per_model_effective))
        return (True, "")

    def consume(self, provider_name, group):
        """发起一次目标组调用后记账。"""
        if not self.enabled or group != self.group:
            return
        with self._lock:
            self.state["global_used"] = self.state.get("global_used", 0) + 1
            self.state["models"][provider_name] = self.state["models"].get(provider_name, 0) + 1
            self._save()

    def remaining(self):
        with self._lock:
            return max(0, self.global_effective - self.state.get("global_used", 0))

    def throttle(self, group):
        """目标组调用前限速：保证两次调用之间的最小间隔。"""
        if not self.enabled or group != self.group or self.min_interval <= 0:
            return
        now = time.monotonic()
        wait = self.min_interval - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
