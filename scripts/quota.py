#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""额度计数 + 频率控制 + 瞬态失败冷却。

- 日额度（组级双限值，0 = 不限且不计）：
  * daily_group_limit：全组/自然日，组内所有 provider 共享一个组池；
  * daily_model_limit：单 provider/自然日；
  两个限值都套用 reserve_ratio 缓冲（熔断生效值 = 限值 x (1 - reserve_ratio)）。
- 月额度：按自然月累计各 provider 响应 usage 上报的 token；组级 monthly_token_limit>0 才熔断。
- 频率：按组最小间隔限速（组配置覆盖全局）。
- 冷却：记录各 provider 冷却到期时间，供 analyze.py 做 429/5xx 熔断换路；
  日/月切换只重置额度，不清除冷却。

状态文件结构（.quota_state.json）：
  {"date": "YYYY-MM-DD",
   "group_used": {"<组>": n},          # 组池自然日计数
   "models": {"<provider>": n},        # 单 provider 自然日计数
   "months": {"YYYY-MM": {"<provider>": tokens}},
   "cooldowns": {"<provider>": unix_ts}}
旧版 global_used 单池会在加载时自动并入首个有日限额的组。

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
        self.reserve_ratio = float(cfg.get("reserve_ratio", 0.2))
        self.min_interval = float(cfg.get("min_interval_sec", 5.0))
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

    def _int0(self, v):
        try:
            return max(0, int(v or 0))
        except Exception:
            return 0

    def daily_group_limit(self, g):
        """全组/自然日限额；0 = 不限制且不计。"""
        return self._int0(self._gcfg(g).get("daily_group_limit"))

    def daily_model_limit(self, g):
        """单 provider/自然日限额；0 = 不限制且不计。"""
        return self._int0(self._gcfg(g).get("daily_model_limit"))

    def daily_active(self, g):
        """该组是否启用本地日计数（任一限值 > 0）。"""
        return self.daily_group_limit(g) > 0 or self.daily_model_limit(g) > 0

    def group_effective(self, g):
        """组池熔断生效值（套 reserve_ratio 缓冲）。"""
        lim = self.daily_group_limit(g)
        return int(lim * (1.0 - self.reserve_ratio)) if lim > 0 else 0

    def model_effective(self, g):
        """单 provider 熔断生效值（套 reserve_ratio 缓冲）。"""
        lim = self.daily_model_limit(g)
        return int(lim * (1.0 - self.reserve_ratio)) if lim > 0 else 0

    def monthly_limit(self, g):
        """该组月度 token 免费额度上限；0 = 不限制。"""
        return self._int0(self._gcfg(g).get("monthly_token_limit"))

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
        old_months = s.get("months", {}) if isinstance(s.get("months"), dict) else {}
        if s.get("date") != today:
            s = {"date": today, "group_used": {}, "models": {}, "cooldowns": old_cooldowns, "months": old_months}
        # 一次性迁移：旧 global_used 单池 -> group_used（归首个有日限额的组）
        if "group_used" not in s:
            s["group_used"] = {}
            if "global_used" in s:
                try:
                    n = int(s.get("global_used", 0) or 0)
                except Exception:
                    n = 0
                if n > 0:
                    target = None
                    for g in sorted(self.members.keys()):
                        if self.daily_group_limit(g) > 0:
                            target = g
                            break
                    if target is None and "modelscope" in self.members:
                        target = "modelscope"
                    if target:
                        s["group_used"][target] = s["group_used"].get(target, 0) + n
                s.pop("global_used", None)
        s.setdefault("group_used", {})
        if not isinstance(s["group_used"], dict):
            s["group_used"] = {}
        s.setdefault("models", {})
        if not isinstance(s["models"], dict):
            s["models"] = {}
        s.setdefault("cooldowns", {})
        if not isinstance(s["cooldowns"], dict):
            s["cooldowns"] = {}
        months = s.get("months")
        if not isinstance(months, dict):
            months = {}
        keys = sorted(months.keys())
        if len(keys) > 2:  # 只保留最近两个自然月
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
        gl = self.daily_group_limit(group)
        if gl > 0:
            with self._lock:
                used = self.state["group_used"].get(group, 0)
                eff = self.group_effective(group)
                if used >= eff:
                    return (False, "组日额度已尽(%d/%d)" % (used, gl))
        ml = self.daily_model_limit(group)
        if ml > 0:
            with self._lock:
                used = self.state["models"].get(provider_name, 0)
                eff = self.model_effective(group)
                if used >= eff:
                    return (False, "模型日额度已尽(%d/%d)" % (used, ml))
        lim = self.monthly_limit(group)
        if lim > 0:
            used = self._month_used(group)
            if used >= lim:
                return (False, "组月token额度已尽(%d/%d)" % (used, lim))
        return (True, "")

    def consume(self, provider_name, group):
        gl = self.daily_group_limit(group)
        ml = self.daily_model_limit(group)
        if gl <= 0 and ml <= 0:
            return
        with self._lock:
            changed = False
            if gl > 0:
                self.state["group_used"][group] = self.state["group_used"].get(group, 0) + 1
                changed = True
            if ml > 0:
                self.state["models"][provider_name] = self.state["models"].get(provider_name, 0) + 1
                changed = True
            if changed:
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

    def group_used(self, group):
        with self._lock:
            return int(self.state["group_used"].get(group, 0))

    def group_remaining(self, group):
        eff = self.group_effective(group)
        if eff <= 0:
            return None
        return max(0, eff - self.group_used(group))

    def model_used(self, provider_name):
        with self._lock:
            return int(self.state["models"].get(provider_name, 0))

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

    def clear_cooldown(self, name):
        """清除单个 provider 的冷却记录。"""
        if not name:
            return
        with self._lock:
            cooldowns = self.state.setdefault("cooldowns", {})
            if name in cooldowns:
                cooldowns.pop(name, None)
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
