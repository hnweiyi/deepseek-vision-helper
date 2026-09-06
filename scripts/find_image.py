# -*- coding: utf-8 -*-
"""查找最近新增的图片文件（用于“贴图但没给路径”时自动定位）。

用法:
  python find_image.py [目录...] [--since 分钟] [--count N] [--recursive]
默认在常见目录里找最近 15 分钟内修改的图片，按时间倒序输出完整路径。
"""
import argparse
import os
import sys
import time
from pathlib import Path

if sys.version_info < (3, 8):
    raise SystemExit("需要 Python 3.8 或更高版本，当前是 %s。请升级 Python 后重试。" % ".".join(map(str, sys.version_info[:3])))


IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".jfif"}

DEFAULT_ROOTS = [
    os.environ.get("TEMP", ""),
    os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Pictures", "Screenshots"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Pictures"),
    os.path.join(os.path.expanduser("~"), ".codex", "tmp"),
    os.path.join(os.path.expanduser("~"), ".codex", ".tmp"),
]


def scan(root, since_ts, recursive, acc):
    root = Path(root)
    if not root.exists():
        return
    it = root.rglob("*") if recursive else root.glob("*")
    for p in it:
        try:
            if p.is_file() and p.suffix.lower() in IMG_EXTS and p.stat().st_mtime >= since_ts:
                acc.append((p.stat().st_mtime, str(p)))
        except OSError:
            continue


def main(argv=None):
    ap = argparse.ArgumentParser(description="查找最近新增的图片文件")
    ap.add_argument("roots", nargs="*", help="要扫描的目录（默认用常见目录）")
    ap.add_argument("--since", type=int, default=15, help="只找最近 N 分钟内修改的图（默认15）")
    ap.add_argument("--count", type=int, default=1, help="最多输出几张（默认1）")
    ap.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    args = ap.parse_args(argv)

    roots = args.roots or [r for r in DEFAULT_ROOTS if r]
    since_ts = time.time() - args.since * 60
    acc = []
    for r in roots:
        try:
            scan(r, since_ts, args.recursive, acc)
        except Exception as e:
            sys.stderr.write("[warn] 扫描失败 %s: %s\n" % (r, e))
    acc.sort(reverse=True)
    for _ts, path in acc[:args.count]:
        print(path)
    return 0 if acc else 1


if __name__ == "__main__":
    raise SystemExit(main())