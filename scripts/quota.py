#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""额度计数 + 频率控制 + 瞬态失败冷却。

- 日额度：本地 JSON 按自然日计数（全局 + 单模型），只对 quota_track 开启的组生效；
  旧版单值 quota.group 自动按 track_groups=["<group>"] 兼容。
- 月额度：按自然月累计各供应商响应 usage 上报的 token；组级 monthly_token_limit>0 才熔断
  （0 = 无限制，如魔搭/智谱/agnes 免费接口；internai 官方按月免费额度，耗尽会扣余额，故建议配置）。
- 频率：按组最小间隔限速（组配置覆盖全局），降低短时 429 概率。
- 冷却：记录各供应商冷却到期时间，供 analyze.py 做 429/5xx 熔断换路；
  日/月切换只重置额度，不清除冷却。

仅依赖 Python 标准库，Python 3.8 兼容。
"""
import json
import threading
import time
from datetime import date


def _this_month():
    return time.strftime("%Y-%m")


class Quota:
    def __init__(self, cfg, state_file, groups_cfg=None, members=None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", True))
        legacy = cfg.get("group")
        track = cfg.get("track_groups")
        if not track:
            track = [legacy] if legacy else ["modelscope"]
        self.track_groups = [str(x) for x in track]
        self.global_limit = int(cfg.get("global_daily", 2000))
        self.per_model_limit = int(cfg.get("per_model_daily", 500))
        self.reserve_ratio = float(cfg.get("reserve_ratio", 0.2))
        self.min_interval = float(cfg.get("min_interval_sec", 5.0))
        self.global_effective = int(self.global_limit * (1.0 - self.reserve_ratio))
        self.per_model_effective = int(self.per_model_limit * (1.0 - self.reserve_ratio))
        self.state_file = state_file
        self.groups_cfg = groups_cfg if isinstance(groups_cfg, dict) else {}
        self.members = members if isinstance(members, dict) else {}
        self._lock = threading.Lock()
        self._last_call = {}
        self.state = self._load()

    # ---------- 组行为查询 ----------
    def _gcfg(self, g):
        gc = self.groups_cfg.get(g)
        return gc if isinstance(gc, dict) else {}

    def daily_track(self, g):
        """该组是否计入本地自然日额度池。"""
        gc = self._gcfg(g)
        if "quota_track" in gc:
            return self.enabled and bool(gc.get("quota_track"))
        return self.enabled and (str(g) in self.track_groups)

    def monthly_limit(self, g):
        """该组月度 token 免费额度上限；0 = 不限制。"""
        gc = self._gcfg(g)
        try:
            return max(0, int(gc.get("monthly_token_limit", 0) or 0))
        except Exception:
            return 0

    def interval_for(self, g):
        """组级最小调用间隔；组未配置时回退全局。"""
        gc = self._gcfg(g)
        if gc.get("min_interval_sec") is not None:
            try:
                v = float(gc.get("min_interval_sec"))
                if v >= 0:
                    return v
            except Exception:
                pass
        return self.min_interval

    def members_of(self, g):
        m = self.members.get(g)
        return m if isinstance(m, list) else []

    # ---------- 状态读写 ----------
    def _load(self):
        today = date.today().isoformat()
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            s = {}
        if not isinstance(s, dict):
            s = {}
        old_cooldowns = s.get("cooldowns", {}) if isinstance(s.get("cooldowns"), dict) else {}
        if s.get("date") != today:
            s = {"date": today, "global_used": 0, "models": {}, "cooldowns": old_cooldowns}
        s.setdefault("models", {})
        s.setdefault("cooldowns", {})
        months = s.get("months")
        if not isinstance(months, dict):
            months = {}
        keys = sorted(months.keys())
        if len(keys) > 2:  # 只保留最近两个自然月，避免 state 无限膨胀
            months = {k: months[k] for k in keys[-2:]}
        s["months"] = months
        return s

    def _save(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------- 日额度 ----------
    def check(self, provider_name, group):
        if not self.enabled:
            return (True, "")
        if self.daily_track(group):
            with self._lock:
                if self.state["global_used"] >= self.global_effective:
                    return (False, "全局日额度已尽(%d/%d)" % (self.state["global_used"], self.global_effective))
                used = self.state["models"].get(provider_name, 0)
                if used >= self.per_model_effective:
                    return (False, "模型日额度已尽(%d/%d)" % (used, self.per_model_effective))
        lim = self.monthly_limit(group)
        if lim > 0:
            used = self._month_used(group)
            if used >= lim:
                return (False, "组月token额度已尽(%d/%d)" % (used, lim))
        return (True, "")

    def consume(self, provider_name, group):
        if not self.daily_track(group):
            return
        with self._lock:
            self.state["global_used"] = self.state.get("global_used", 0) + 1
            self.state["models"][provider_name] = self.state["models"].get(provider_name, 0) + 1
            self._save()

    def consume_tokens(self, provider_name, group, tokens):
        """调用成功后按响应 usage 上报 token；仅对设置月限的组有意义。"""
        if not self.enabled or self.monthly_limit(group) <= 0:
            return
        try:
            tokens = int(tokens)
        except Exception:
            return
        if tokens <= 0:
            return
        with self._lock:
            ym = _this_month()
            months = self.state.setdefault("months", {})
            m = months.setdefault(ym, {})
            m[provider_name] = m.get(provider_name, 0) + tokens
            self._save()

    def remaining(self):
        with self._lock:
            return max(0, self.global_effective - self.state.get("global_used", 0))

    # ---------- 月额度 ----------
    def _month_used(self, group, ym=None):
        names = self.members_of(group)
        if not names:
            return 0
        ym = ym or _this_month()
        with self._lock:
            months = self.state.get("months", {})
            m = months.get(ym, {})
            return sum(int(m.get(n, 0)) for n in names)

    def month_usage(self, group):
        """返回当月组内各成员已消耗 token 明细。"""
        ym = _this_month()
        with self._lock:
            months = self.state.get("months", {})
            m = months.get(ym, {})
            out = {}
            for n in self.members_of(group):
                v = m.get(n)
                if v:
                    out[n] = int(v)
            return out

    def month_remaining(self, group):
        lim = self.monthly_limit(group)
        if lim <= 0:
            return None
        return max(0, lim - self._month_used(group))

    # ---------- 频率控制 ----------
    def throttle(self, group):
        interval = self.interval_for(group)
        if not self.enabled or interval <= 0:
            return
        now = time.monotonic()
        last = self._last_call.get(group, 0.0)
        wait = interval - (now - last)
        if wait > 0:
            time.sleep(wait)
        self._last_call[group] = time.monotonic()

    # ---------- 冷却 ----------
    def cooldown_remaining(self, name):
        if not name:
            return 0.0
        with self._lock:
            cooldowns = self.state.setdefault("cooldowns", {})
            expires = cooldowns.get(name)
            if expires is None:
                return 0.0
            try:
                remaining = float(expires) - time.time()
            except Exception:
                remaining = 0.0
            if remaining <= 0:
                cooldowns.pop(name, None)
                self._save()
                return 0.0
            return remaining

    def set_cooldown(self, name, seconds):
        if not name:
            return
        try:
            seconds = max(0.0, float(seconds))
        except Exception:
            return
        with self._lock:
            self.state.setdefault("cooldowns", {})[name] = time.time() + seconds
            self._save()

    def cooldowns_snapshot(self):
        out = {}
        with self._lock:
            now = time.time()
            for name, expires in list(self.state.setdefault("cooldowns", {}).items()):
                try:
                    remaining = float(expires) - now
                except Exception:
                    remaining = 0.0
                if remaining > 0:
                    out[name] = remaining
                else:
                    self.state["cooldowns"].pop(name, None)
        return out
