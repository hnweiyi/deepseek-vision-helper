# -*- coding: utf-8 -*-
"""shijuefenxi 交互式修改工具：输入明文 key，自动读取配置、加密并保存。

用法:
  python set_key.py                     # 交互式选择供应商并修改
  python set_key.py --config <path>     # 指定配置文件

交互流程：列出供应商 -> 选序号 -> 选要改的项 -> 输入新值 -> 自动保存。
改 key 时用隐藏输入（不回显），并需输入两次确认。
model 支持候选数组：多个候选用英文逗号分隔，保存为数组（404 自动降级到下一候选）。
"""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

if sys.version_info < (3, 8):
    raise SystemExit("需要 Python 3.8 或更高版本，当前是 %s。请升级 Python 后重试。" % ".".join(map(str, sys.version_info[:3])))

try:
    from dpapi import encrypt_text
except Exception:
    def encrypt_text(s):
        raise SystemExit("DPAPI 仅支持 Windows")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR.parent / "config.json"


def _ensure_config_from_example(path):
    """config.json 不存在时，按 config.example.json 自动生成空白配置（api_key 置空）。

    返回 True 表示本次自动创建了新配置；返回 False 表示无需/无法自动创建。
    """
    p = Path(path)
    if p.exists():
        return False
    example = p.with_name("config.example.json")
    if not example.exists():
        return False
    try:
        with example.open("r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        return False
    for pr in cfg.get("providers", []):
        pr["api_key"] = ""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except Exception:
        return False
    return True

def load_config(path):
    p = Path(path)
    if not p.exists():
        if _ensure_config_from_example(path):
            print("[提示] 未找到 config.json，已按 config.example.json 自动生成空白配置，请填写各供应商 api_key。")
        else:
            raise SystemExit("找不到配置文件: %s" % p)
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)
def save_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def mask(v):
    if not v:
        return "(未设置)"
    if v.startswith("dpapi:"):
        return "dpapi:...%s (已加密)" % v[-4:]
    return ("%s...%s" % (v[:4], v[-4:])) if len(v) > 10 else "(明文，较短)"


def model_display(model):
    if isinstance(model, list):
        return ", ".join(str(x) for x in model)
    return str(model) if model else ""


def parse_model(v):
    parts = [x.strip() for x in v.split(",") if x.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser(description="shijuefenxi 交互式修改工具")
    ap.add_argument("--config", default=None, help="配置文件路径，默认取脚本旁 config.json")
    args = ap.parse_args(argv)

    path = args.config or os.environ.get("VISION_CONFIG") or str(DEFAULT_CONFIG)
    cfg = load_config(path)
    providers = cfg.get("providers", [])

    print("当前供应商：")
    for i, p in enumerate(providers, 1):
        print("  [%d] %-16s group=%-10s model=%-30s enabled=%-5s key=%s" % (
            i, p.get("name"), p.get("group", ""), model_display(p.get("model", "")), p.get("enabled", True), mask(p.get("api_key", ""))))

    sel = input("选择要修改的序号或名字（回车取消）: ").strip()
    if not sel:
        print("已取消。")
        return 0
    idx = None
    if sel.isdigit():
        idx = int(sel) - 1
    else:
        names = [p.get("name") for p in providers]
        if sel in names:
            idx = names.index(sel)
    if idx is None or idx < 0 or idx >= len(providers):
        raise SystemExit("无效选择: %s" % sel)

    p = providers[idx]
    print("\n修改: %s (model=%s)" % (p.get("name"), model_display(p.get("model", ""))))
    print("当前 key: %s" % mask(p.get("api_key", "")))
    print("要改什么？")
    print("  [1] api_key（输入明文，自动加密保存）")
    print("  [2] base_url（接口地址）")
    print("  [3] model（模型名；多个候选用英文逗号分隔）")
    print("  [4] enabled（启用/禁用，直接切换）")
    choice = input("选择（默认 1）: ").strip() or "1"

    if choice == "1":
        k1 = getpass.getpass("输入新 key 明文（输入时不回显）: ").strip()
        if not k1:
            print("未输入，已取消。")
            return 0
        k2 = getpass.getpass("再输一次确认: ").strip()
        if k1 != k2:
            raise SystemExit("两次输入不一致，未保存。")
        p["api_key"] = encrypt_text(k1)
        p["enabled"] = True
        save_config(path, cfg)
        print("已保存并加密 -> %s" % path)
        print("新 key: %s" % mask(p["api_key"]))
    elif choice == "2":
        cur = p.get("base_url", "")
        v = input("新的 base_url（当前: %s，回车跳过）: " % cur).strip()
        if v:
            p["base_url"] = v
            save_config(path, cfg)
            print("已保存 base_url = %s" % v)
        else:
            print("未修改。")
    elif choice == "3":
        cur = model_display(p.get("model", ""))
        v = input("新的 model（当前: %s，回车跳过；多个候选用英文逗号）: " % cur).strip()
        if v:
            p["model"] = parse_model(v)
            save_config(path, cfg)
            print("已保存 model = %s" % model_display(p["model"]))
        else:
            print("未修改。")
    elif choice == "4":
        cur = p.get("enabled", True)
        p["enabled"] = not cur
        save_config(path, cfg)
        print("已切换 enabled = %s" % p["enabled"])
    else:
        print("无效选择。")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
