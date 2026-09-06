# -*- coding: utf-8 -*-
"""shijuefenxi key 管理工具：加密 key 或把 config.json 里的明文 key 一键转成 DPAPI 加密。

用法:
  python encrypt_key.py "明文key"                          # 输出 dpapi:... 加密串
  python encrypt_key.py --encrypt-config [config路径]       # 把 config 里所有明文 key 就地加密
  python encrypt_key.py --set <name> "明文key" [--config 路径]  # 设置某供应商 key（加密后写入）
  python encrypt_key.py --decrypt "dpapi:..."               # 解密校验
"""
import argparse
import json
import os
import sys
from pathlib import Path

if sys.version_info < (3, 8):
    raise SystemExit("需要 Python 3.8 或更高版本，当前是 %s。请升级 Python 后重试。" % ".".join(map(str, sys.version_info[:3])))


try:
    from dpapi import encrypt_text, decrypt_text, is_encrypted
except Exception:
    def encrypt_text(s):
        raise SystemExit("DPAPI 仅支持 Windows")

    def decrypt_text(s):
        return s

    def is_encrypted(s):
        return False

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR.parent / "config.json"


def load_config(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit("找不到配置文件: %s" % p)
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="shijuefenxi key 加解密工具")
    ap.add_argument("plain", nargs="?", help="要加密的明文 key")
    ap.add_argument("--encrypt-config", action="store_true", help="把 config.json 里所有明文 key 就地加密")
    ap.add_argument("--set", metavar="NAME", help="设置某个供应商的 key")
    ap.add_argument("--decrypt", metavar="DPAPI", help="解密一个 dpapi: 串做校验")
    ap.add_argument("--config", default=None, help="配置文件路径")
    args = ap.parse_args(argv)

    if args.decrypt:
        print(decrypt_text(args.decrypt))
        return 0

    if args.encrypt_config:
        path = args.config or os.environ.get("VISION_CONFIG") or str(DEFAULT_CONFIG)
        cfg = load_config(path)
        changed = 0
        for p in cfg.get("providers", []):
            k = p.get("api_key", "")
            if k and not is_encrypted(k):
                p["api_key"] = encrypt_text(k)
                changed += 1
        if changed:
            save_config(path, cfg)
            print("已加密 %d 个 key -> %s" % (changed, path))
        else:
            print("没有需要加密的明文 key")
        return 0

    if args.set:
        path = args.config or os.environ.get("VISION_CONFIG") or str(DEFAULT_CONFIG)
        plain = args.plain
        if not plain:
            raise SystemExit("--set 需要提供明文 key")
        cfg = load_config(path)
        found = False
        for p in cfg.get("providers", []):
            if p.get("name") == args.set:
                p["api_key"] = encrypt_text(plain)
                p["enabled"] = True
                found = True
        if not found:
            raise SystemExit("找不到供应商: %s" % args.set)
        save_config(path, cfg)
        print("已设置并加密 %s 的 key -> %s" % (args.set, path))
        return 0

    if args.plain:
        print(encrypt_text(args.plain))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())