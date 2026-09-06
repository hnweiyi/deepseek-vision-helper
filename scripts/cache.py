#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""答案缓存（零依赖，仅标准库）。

- 只缓存视觉模型成功返回的文字，绝不缓存原始图片或 api_key。
- JSON 文件结构：entries{key:{text,ts,elapsed}} + order[]（简单 LRU + TTL）。
- 线程安全：所有读写加锁；IO 异常一律静默降级为 miss，不影响主流程。
- 默认缓存文件放在 config.json 同目录，名为 .cache_vision.json；也可用 cache.dir 指定目录。

仅依赖 Python 标准库，Python 3.8 兼容。
"""
import json
import threading
import time
from pathlib import Path


class VisionCache:
    def __init__(self, cfg=None, config_dir=None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.ttl = max(0.0, float(cfg.get("ttl_seconds", 3600)))
        self.max_entries = int(cfg.get("max_entries", 200))
        self.dir = str(cfg.get("dir", "") or "").strip()
        self.config_dir = str(config_dir or "").strip()
        self._lock = threading.RLock()
        self._file = self._resolve_file()
        self._state = {"entries": {}, "order": []}
        if self.enabled:
            self._load()

    def _resolve_file(self):
        if self.dir:
            base = Path(self.dir)
        elif self.config_dir:
            base = Path(self.config_dir)
        else:
            base = Path(".")
        return str(base / ".cache_vision.json")

    def _load(self):
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            return
        if not isinstance(s, dict):
            return
        entries = s.get("entries", {})
        order = s.get("order", [])
        if not isinstance(entries, dict) or not isinstance(order, list):
            return
        clean = {}
        for k, v in entries.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            text = v.get("text")
            if not isinstance(text, str):
                continue
            clean[str(k)] = {"text": text, "ts": float(v.get("ts", 0.0) or 0.0), "elapsed": float(v.get("elapsed", 0.0) or 0.0)}
        self._state = {"entries": clean, "order": [str(x) for x in order if str(x) in clean]}

    def _save(self):
        try:
            if self.dir or self.config_dir:
                Path(self._file).parent.mkdir(parents=True, exist_ok=True)
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False)
        except Exception:
            pass

    def _cleanup_locked(self):
        now = time.time()
        entries = self._state["entries"]
        expired = []
        for k, v in entries.items():
            try:
                if self.ttl > 0 and now - float(v.get("ts", 0.0)) > self.ttl:
                    expired.append(k)
            except Exception:
                expired.append(k)
        for k in expired:
            entries.pop(k, None)
        self._state["order"] = [k for k in self._state["order"] if k in entries]

    def get(self, key):
        """命中返回 entry 字典（text/ts/elapsed），未命中或 IO 异常返回 None。"""
        if not self.enabled or not key:
            return None
        with self._lock:
            self._cleanup_locked()
            entry = self._state["entries"].get(str(key))
            if entry is None:
                return None
            k = str(key)
            order = [x for x in self._state["order"] if x != k]
            order.append(k)
            self._state["order"] = order
            return dict(entry)

    def put(self, key, text, elapsed=0.0):
        """缓存成功文字；空文字不缓存；IO 异常静默。"""
        if not self.enabled or not key or not isinstance(text, str) or not text:
            return
        if self.max_entries <= 0:
            return
        k = str(key)
        with self._lock:
            self._cleanup_locked()
            self._state["entries"][k] = {"text": text, "ts": time.time(), "elapsed": float(elapsed or 0.0)}
            order = [x for x in self._state["order"] if x != k]
            order.append(k)
            while len(order) > self.max_entries:
                victim = order.pop(0)
                if victim != k:
                    self._state["entries"].pop(victim, None)
            self._state["order"] = order
            self._save()

    def stats(self):
        """返回缓存统计信息（供 --doctor 展示）。"""
        with self._lock:
            return {
                "enabled": self.enabled,
                "file": self._file,
                "entries": len(self._state["entries"]),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl,
            }
