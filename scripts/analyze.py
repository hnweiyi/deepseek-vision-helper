#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shijuefenxi 视觉分析：本地/远程图片 -> 视觉模型 -> 结构化中文描述（半自动路由引擎）。

用法:
  python analyze.py <图片路径或URL...> [-f 关注点] [--task 类型] [--provider 名称]
                    [--json] [--config 路径] [--max-size N] [--auto-trim] [--brief]
                    [--no-downscale] [--multi] [--serial] [--no-task-check]

任务类型 --task：general / ocr / error / ui / chart / compare / document /
                 math_stem / detail / video / unknown
  主模型先根据用户需求粗判 task（明确则直接定，模糊则 general）；脚本会用 Agnes
  校验 task 是否正确，错误则自动重新设定并重跑一次。

特性:
  - 路由完全由 config 驱动（default_chain / overrides / multi_tasks），代码不硬编码模型或顺序。
  - 按厂商分组（modelscope / glm / agnes）自动路由；失败按原因分类降级。
  - 关键任务（multi_tasks）自动多模型并发 + agnes 汇总成一份。
  - 魔搭额度本地按日计数（全局 + 单模型）主动熔断，并做最小间隔限速。
  - 本地/远程图片统一安全校验；api_key 支持明文或 dpapi: 加密串。
  - 仅依赖 Python 标准库；PIL 可选；Python 3.8 兼容。
"""
import argparse
import base64
import io
import json
import mimetypes
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if sys.version_info < (3, 8):
    raise SystemExit("需要 Python 3.8 或更高版本，当前是 %s。请升级 Python 后重试。" % ".".join(map(str, sys.version_info[:3])))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from safe_net import safe_download, validate_url, verify_image, sniff_image, SafeNetError  # noqa: E402
from quota import Quota  # noqa: E402

DEFAULT_CONFIG = SCRIPT_DIR.parent / "config.json"

try:
    from PIL import Image, ImageChops
    HAS_PIL = True
    try:
        _RESAMPLE = Image.Resampling.LANCZOS
    except AttributeError:
        _RESAMPLE = Image.LANCZOS
except Exception:
    HAS_PIL = False
    _RESAMPLE = None
    ImageChops = None

try:
    from dpapi import decrypt_text as _decrypt_key
except Exception:
    def _decrypt_key(v):
        return v

from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

TASK_MAX_SIZE = {
    "general": 1568, "ocr": 2048, "error": 2048, "ui": 1568, "chart": 1568, "compare": 1568,
    "document": 2048, "math_stem": 1568, "detail": 2048, "video": 1568, "unknown": 1568,
}

TASK_DEFAULT_FOCUS = {
    "general": "请描述这张图片的内容",
    "ocr": "请逐字提取图中所有文字",
    "error": "请定位图中的报错/异常信息",
    "ui": "请分析界面布局与 UI 问题",
    "chart": "请解读图表数据",
    "compare": "请对比这些图片并指出异同",
    "document": "请提取文档的结构化内容",
    "math_stem": "请解答图中的题目并给出步骤",
    "detail": "请观察并描述图中的细节",
    "video": "请描述视频帧/截图的内容",
    "unknown": "请描述这张图片的内容",
}

_EXT_MIME = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp"}


class ProviderError(Exception):
    def __init__(self, message, transient=False, kind="other"):
        super().__init__(message)
        self.transient = transient
        self.kind = kind  # model_error / transient / other


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
            sys.stderr.write(
                "[提示] 未找到 %s，已按 config.example.json 自动生成空白配置。\n"
                "       请先填写各供应商 api_key（双击 配置工具.bat 或用 scripts/set_key.py），再重新执行。\n" % p)
        else:
            raise SystemExit("找不到配置文件: %s\n请先复制 config.example.json 为 config.json 并填入 api_key。" % p)
    with p.open("r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if "providers" not in cfg:
        raise SystemExit("配置文件缺少 providers 数组: %s" % p)
    return cfg
def resolve_max_size(cli_value, cfg_value, task):
    if cli_value is not None:
        return int(cli_value)
    if isinstance(cfg_value, bool):
        return TASK_MAX_SIZE.get(task, 1568)
    if isinstance(cfg_value, int):
        return int(cfg_value)
    s = str(cfg_value).strip().lower()
    if s in ("", "auto", "none", "null"):
        return TASK_MAX_SIZE.get(task, 1568)
    try:
        return int(s)
    except ValueError:
        return TASK_MAX_SIZE.get(task, 1568)


def build_system_prompt(task, n_images, focus, brief):
    task = task or "general"
    parts = ["你是一个专业的图像分析助手，请始终使用中文回答。"]
    if n_images > 1:
        parts.append("本次提供了 %d 张图片，请严格按图1、图2……的顺序分别处理；若为前后对比或关联图，请明确指出各图关系与差异。" % n_images)
    if task == "ocr":
        parts.append("请专注逐字提取图中所有可见文字（数字、报错码、按钮、标注等），无法辨认的用[?]标注，无文字写“无”；不要泛泛描述画面其他细节。")
    elif task == "error":
        parts.append("请只提取与报错/异常相关的内容：报错码、关键堆栈/日志、错误信息，并给出可能原因与修复方向；忽略与错误无关的 UI 细节。")
    elif task == "ui":
        parts.append("请描述界面布局、主要元素、交互状态，并指出可能的 UI 问题（错位、遮挡、样式异常等）；与问题相关的文字需提取。")
    elif task == "chart":
        parts.append("请解读图表：图表类型、坐标轴与单位、图例、关键数值、趋势或结论；不要泛泛描述颜色与外观。")
    elif task == "compare":
        parts.append("请逐图描述，并输出对比清单（相同点 / 不同点 / 变化量）。")
    elif task == "document":
        parts.append("请按阅读顺序提取文档结构化内容：标题、段落、表格（用行列描述）、关键数据与结论；保持原有逻辑结构。")
    elif task == "math_stem":
        parts.append("请识别图中的题目/公式/图表，给出解题步骤与最终答案；数学符号、数值与单位必须准确提取。")
    elif task == "detail":
        parts.append("请放大观察并描述图中细节：小字、纹理、细小差异、精确数值；不要遗漏关键细节。")
    elif task == "video":
        parts.append("这是视频帧/截图，请描述画面内容与关键信息（人物/动作/场景/字幕/时间点）。")
    else:
        parts.append("请严格按以下三段输出：")
        parts.append("【画面描述】客观描述图像的完整内容、主要元素、布局、颜色与整体状态。")
        parts.append("【文字内容】逐字提取图中所有可见文字（含数字、报错码、按钮、标注等），无法辨认的用[?]标注；若无文字则写“无”。")
        parts.append("【分析】结合用户关注点，给出具体、可执行的判断/结论/建议。")
    if focus:
        parts.append("请紧扣关注点：%s。结论优先，与关注点无关的细节可略过；与关注点相关的文字需完整提取。" % focus)
    if brief:
        parts.append("请输出紧凑证据，控制在约150字以内：先给结论，再列必要 OCR/数值，不要逐条罗列无关内容。")
    parts.append("不要编造图中不存在的内容。")
    return "\n".join(parts)


def build_user_text(focus, task, n_images, brief, labels):
    task = task or "general"
    base = focus or TASK_DEFAULT_FOCUS.get(task, TASK_DEFAULT_FOCUS["general"])
    if n_images > 1:
        if labels:
            label_part = "、".join(str(x) for x in labels)
        else:
            label_part = "、".join("图%d" % (i + 1) for i in range(n_images))
        return "以下是 %d 张图片（标记为 %s），可能为前后对比或关联图。%s" % (n_images, label_part, base)
    return base


def load_image_bytes(src, cfg, allow_local_url):
    s = str(src)
    image_max_bytes = int(cfg.get("image_max_bytes", 8000000))
    image_max_pixels = int(cfg.get("image_max_pixels", 40000000))
    timeout = int(cfg.get("download_timeout", 20))
    if s.startswith("http://") or s.startswith("https://"):
        data, ctype = safe_download(s, allow_private=allow_local_url, max_bytes=image_max_bytes, timeout=timeout)
        mime_hint = ctype.split(";")[0].strip() if ctype else ""
    else:
        p = Path(s)
        if not p.exists():
            raise ProviderError("图片文件不存在: %s" % s)
        data = p.read_bytes()
        mime_hint = mimetypes.guess_type(s)[0] or ""
    v = verify_image(data, max_pixels=image_max_pixels)
    if not v.get("ok"):
        raise ProviderError("图片安全校验未通过: %s" % v.get("reason"))
    if v.get("warning"):
        sys.stderr.write("[warn] %s\n" % v["warning"])
    return data, mime_hint


def _auto_trim(im, tolerance=12):
    if not HAS_PIL or ImageChops is None:
        return im
    try:
        w, h = im.size
        corner = im.getpixel((0, 0))
        bg = Image.new(im.mode, im.size, corner)
        diff = ImageChops.difference(im, bg)
        if diff.mode not in ("L", "1"):
            diff = diff.convert("L")
        mask = diff.point(lambda p: 255 if p > tolerance else 0)
        bbox = mask.getbbox()
        if bbox:
            l, t, r, b = bbox
            if l > 0 or t > 0 or r < w or b < h:
                nw, nh = r - l, b - t
                if nw >= 200 and nh >= 200:
                    im = im.crop((l, t, r, b))
    except Exception:
        pass
    return im


def bytes_to_block(data, mime_hint, max_size, quality, auto_trim):
    fmt = sniff_image(data)
    mime = mime_hint if (mime_hint and mime_hint.startswith("image/")) else _EXT_MIME.get(fmt, "image/png")
    if HAS_PIL:
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
            if auto_trim:
                im = _auto_trim(im)
            w, h = im.size
            if max(w, h) > max_size:
                scale = max_size / float(max(w, h))
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), _RESAMPLE)
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            mime = "image/jpeg"
        except Exception as e:
            sys.stderr.write("[warn] 图片预处理失败，改用原图: %s\n" % e)
    b64 = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}}


def prepare_image_block(src, input_mode, max_size, quality, auto_trim, allow_local_url, cfg):
    s = str(src)
    is_remote = s.startswith("http://") or s.startswith("https://")
    if is_remote and input_mode == "url":
        validate_url(s, allow_private=allow_local_url)
        return {"type": "image_url", "image_url": {"url": s}}
    if input_mode == "url" and not is_remote:
        raise ProviderError("该供应商 input_mode=url，仅支持公开 URL，无法处理本地文件")
    data, mime_hint = load_image_bytes(s, cfg, allow_local_url)
    return bytes_to_block(data, mime_hint, max_size, quality, auto_trim)


def _model_list(provider):
    m = provider.get("model")
    if isinstance(m, list):
        return [str(x) for x in m if x]
    return [str(m)] if m else []


def _model_display(provider):
    ml = _model_list(provider)
    return ml[0] if ml else ""


def _has_key(provider):
    return bool(provider.get("api_key") or os.environ.get("VISION_API_KEY"))


def _find_provider(providers, name):
    for p in providers:
        if p.get("name") == name:
            return p
    return None


def _classify_http(code, detail):
    d = (detail or "").lower()
    if code == 404 or "not found" in d or "不存在" in d or "does not exist" in d or "model not" in d:
        return "model_error"
    if code == 429 or code >= 500:
        return "transient"
    return "other"


def _post_chat(provider, payload, cfg):
    base_url = (provider.get("base_url") or "").rstrip("/")
    api_key = _decrypt_key(provider.get("api_key") or os.environ.get("VISION_API_KEY") or "")
    if not base_url:
        raise ProviderError("缺少 base_url")
    if not api_key:
        raise ProviderError("未配置 api_key（可在 config.json 填写，或用环境变量 VISION_API_KEY）")
    url = base_url + "/chat/completions"
    req = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + api_key)
    timeout = int(provider.get("timeout", cfg.get("timeout", 60)))
    t0 = time.time()
    try:
        resp = urlopen(req, timeout=timeout)
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        kind = _classify_http(e.code, detail)
        raise ProviderError("HTTP %s: %s" % (e.code, detail or e.reason), transient=(kind == "transient"), kind=kind)
    except URLError as e:
        raise ProviderError("网络错误: %s" % e.reason, transient=True, kind="transient")
    body = resp.read().decode("utf-8", "replace")
    elapsed = time.time() - t0
    try:
        data = json.loads(body)
    except Exception:
        raise ProviderError("响应不是合法 JSON: %s" % body[:300])
    if "choices" not in data or not data["choices"]:
        raise ProviderError("响应缺少 choices: %s" % json.dumps(data, ensure_ascii=False)[:300])
    msg = data["choices"][0].get("message", {})
    content_out = msg.get("content")
    text = ""
    if isinstance(content_out, str):
        text = content_out
    elif isinstance(content_out, list):
        for block in content_out:
            if isinstance(block, dict) and block.get("type") == "text":
                text += block.get("text", "")
    if not text:
        raise ProviderError("模型未返回文字内容: %s" % json.dumps(msg, ensure_ascii=False)[:300])
    return text, elapsed


def _build_visual_payload(model, image_blocks, cfg, focus, task, brief, labels):
    n = len(image_blocks)
    system = build_system_prompt(task, n, focus, brief)
    user_text = build_user_text(focus, task, n, brief, labels)
    content = image_blocks + [{"type": "text", "text": user_text}]
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": float(cfg.get("temperature", 0.2)),
        "max_tokens": int(cfg.get("max_tokens", 2048)),
    }


def call_provider(provider, image_blocks, cfg, focus, task, brief, labels):
    models = _model_list(provider)
    last = None
    for idx, model in enumerate(models):
        try:
            payload = _build_visual_payload(model, image_blocks, cfg, focus, task, brief, labels)
            return _post_chat(provider, payload, cfg)
        except ProviderError as e:
            last = e
            if e.kind == "model_error" and idx < len(models) - 1:
                sys.stderr.write("[候选] %s 模型 %s 不可用，换下一个候选\n" % (provider.get("name"), model))
                continue
            break
    raise last


def build_summary_prompt(task, focus, entries):
    lines = ["请综合以下 %d 个模型对同一批图片的分析结果，融合成一个准确、完整、去重的中文结论。" % len(entries)]
    lines.append("任务类型：%s" % task)
    if focus:
        lines.append("用户关注点：%s" % focus)
    lines.append("")
    for i, e in enumerate(entries, 1):
        lines.append("--- 模型%d（%s / %s）---" % (i, e["provider"], e["model"]))
        lines.append(e["text"])
        lines.append("")
    lines.append("请输出综合结论：先给最终结论，再给关键证据（OCR/数值/差异）；若结果有分歧，指出分歧并给出更可信的一方。")
    return "\n".join(lines)


def call_summarizer(provider, task, focus, entries, cfg):
    models = _model_list(provider)
    if not models:
        raise ProviderError("汇总器缺少 model")
    prompt = build_summary_prompt(task, focus, entries)
    payload = {
        "model": models[0],
        "messages": [
            {"role": "system", "content": "你是视觉分析结果的汇总器，请用中文融合多个模型的结果，去重并给出准确结论。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(cfg.get("temperature", 0.2)),
        "max_tokens": int(cfg.get("max_tokens", 2048)),
    }
    return _post_chat(provider, payload, cfg)[0]


def build_task_check_prompt(focus, task, text):
    lines = ["你是视觉分析任务类型的校验器。请判断当前任务类型是否准确匹配用户需求和图片内容。"]
    lines.append("用户需求：%s" % (focus or "无"))
    lines.append("当前任务类型：%s" % task)
    lines.append("视觉模型识别结果：")
    lines.append((text or "")[:1500])
    lines.append("")
    lines.append("可用任务类型：general / ocr / document / math_stem / chart / detail / ui / error / video / unknown")
    lines.append("规则：若图片明显属于专门类型（报错、文档、图表、数学题、界面截图、纯文字），应推荐专门任务类型，不要用 general 兜底。")
    lines.append("请只输出下面两种之一：")
    lines.append("1) 当前任务类型已是最合适：TASK_OK")
    lines.append("2) 需要更换：TASK_CHANGE:<最适合的任务类型>")
    return "\n".join(lines)


def _check_task(providers, cfg, focus, task, text):
    """用汇总器（默认 agnes）校验 task，返回正确 task 或 None（无需重跑）。"""
    summarizer_name = str(cfg.get("summarizer", "agnes"))
    p = _find_provider(providers, summarizer_name)
    if not p or not p.get("enabled", True) or not _has_key(p):
        return None
    prompt = build_task_check_prompt(focus, task, text)
    payload = {
        "model": _model_list(p)[0],
        "messages": [
            {"role": "system", "content": "你是视觉分析任务类型校验器，只输出 TASK_OK 或 TASK_CHANGE:xxx。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 32,
    }
    try:
        out = _post_chat(p, payload, cfg)[0].strip()
    except Exception as e:
        sys.stderr.write("[task校验] agnes 不可用，跳过: %s\n" % e)
        return None
    if out.startswith("TASK_CHANGE:"):
        new = out.split(":", 1)[1].strip().lower()
        if new in TASK_MAX_SIZE and new != task:
            return new
    return None


def _quota_state_file(cfg):
    q = cfg.get("quota") or {}
    return q.get("state_file") or str(SCRIPT_DIR.parent / ".quota_state.json")


def _routing_for(cfg, task):
    routing = cfg.get("routing") or {}
    default_chain = routing.get("default_chain")
    if not default_chain:
        default_chain = [p.get("name") for p in cfg.get("providers", []) if p.get("enabled", True)]
    overrides = routing.get("overrides") or {}
    chain = overrides.get(task) or default_chain
    multi_tasks = routing.get("multi_tasks") or []
    return {"chain": chain, "multi": task in multi_tasks}


def route_order(providers, chain_names):
    by_name = {p.get("name"): p for p in providers}
    ordered = []
    seen = set()
    for n in chain_names or []:
        p = by_name.get(n)
        if p and p.get("name") not in seen:
            ordered.append(p)
            seen.add(p.get("name"))
    for p in providers:
        if p.get("enabled", True) and p.get("name") not in seen:
            ordered.append(p)
            seen.add(p.get("name"))
    return ordered


def _retry_backoff(cfg, attempt):
    return float(cfg.get("retry_delay", 2)) * (attempt + 1)


def _attempt_single(chain, make_blocks, cfg, focus, task, brief, labels, quota):
    retries = int(cfg.get("retries", 1))
    errors = []
    for p in chain:
        if not p.get("enabled", True):
            continue
        name = p.get("name", "?")
        group = p.get("group", "")
        ok, reason = quota.check(name, group)
        if not ok:
            msg = "[%s] %s，跳过" % (name, reason)
            errors.append(msg)
            sys.stderr.write("[额度] %s\n" % msg)
            continue
        if not _has_key(p):
            msg = "[%s] 未配置 api_key" % name
            errors.append(msg)
            sys.stderr.write("[失败] %s\n" % msg)
            continue
        quota.throttle(group)
        try:
            blocks = make_blocks(p)
        except ProviderError as e:
            msg = "[%s] %s" % (name, e)
            errors.append(msg)
            sys.stderr.write("[失败] %s\n" % msg)
            continue
        except Exception as e:
            msg = "[%s] 预处理异常: %s" % (name, e)
            errors.append(msg)
            sys.stderr.write("[失败] %s\n" % msg)
            continue
        last = None
        for attempt in range(retries + 1):
            quota.consume(name, group)
            try:
                text, elapsed = call_provider(p, blocks, cfg, focus, task, brief, labels)
                return {"text": text, "name": name, "model": _model_display(p), "elapsed": elapsed, "mode": "single", "task": task}
            except ProviderError as e:
                last = e
                if e.transient and attempt < retries:
                    delay = _retry_backoff(cfg, attempt)
                    sys.stderr.write("[重试] %s 第 %d/%d 次失败（%s），%.1fs 后重试...\n" % (name, attempt + 1, retries + 1, e, delay))
                    time.sleep(delay)
                    continue
                break
            except Exception as e:
                last = ProviderError("未知错误: %s" % e)
                break
        msg = "[%s] %s" % (name, last)
        errors.append(msg)
        sys.stderr.write("[失败] %s\n" % msg)
    raise ProviderError("所有供应商均失败：\n" + "\n".join(errors))


def _attempt_multi(chain, make_blocks, cfg, focus, task, brief, labels, quota):
    groups = {}
    for p in chain:
        g = p.get("group", "other")
        groups.setdefault(g, []).append(p)

    retries = int(cfg.get("retries", 1))

    def run_group(gname):
        gpros = groups.get(gname, [])
        errs = []
        for p in gpros:
            if not p.get("enabled", True):
                continue
            name = p.get("name", "?")
            group = p.get("group", "")
            ok, reason = quota.check(name, group)
            if not ok:
                errs.append("[%s] %s" % (name, reason))
                continue
            if not _has_key(p):
                errs.append("[%s] 未配置 api_key" % name)
                continue
            quota.throttle(group)
            try:
                blocks = make_blocks(p)
            except Exception as e:
                errs.append("[%s] 预处理: %s" % (name, e))
                continue
            last = None
            for attempt in range(retries + 1):
                quota.consume(name, group)
                try:
                    text, elapsed = call_provider(p, blocks, cfg, focus, task, brief, labels)
                    return {"group": gname, "name": name, "model": _model_display(p), "text": text, "elapsed": elapsed}
                except ProviderError as e:
                    last = e
                    if e.transient and attempt < retries:
                        time.sleep(_retry_backoff(cfg, attempt))
                        continue
                    break
                except Exception as e:
                    last = ProviderError("未知错误: %s" % e)
                    break
            errs.append("[%s] %s" % (name, last))
        return {"group": gname, "error": "; ".join(errs) if errs else "无可用模型"}

    group_names = list(groups.keys())
    with ThreadPoolExecutor(max_workers=min(len(group_names), 3)) as ex:
        group_results = list(ex.map(run_group, group_names))

    ok = [r for r in group_results if "text" in r]
    if not ok:
        err_text = "; ".join(r.get("error", "") for r in group_results)
        raise ProviderError("多模型并发全部失败：%s" % err_text)

    entries = [{"provider": r["name"], "model": r["model"], "text": r["text"]} for r in ok]
    elapsed = sum(r.get("elapsed", 0.0) for r in ok)
    model_joined = "+".join(r["model"] for r in ok)

    summarizer_name = str(cfg.get("summarizer", "agnes"))
    summarizer = _find_provider(chain, summarizer_name)
    if summarizer and summarizer.get("enabled", True) and _has_key(summarizer):
        try:
            summary = call_summarizer(summarizer, task, focus, entries, cfg)
            return {
                "text": summary, "name": "multi", "model": model_joined,
                "elapsed": elapsed, "mode": "multi", "results": entries, "summarizer": summarizer_name, "task": task,
            }
        except Exception as e:
            sys.stderr.write("[汇总失败] %s，返回多份原始结果由主模型兜底\n" % e)

    parts = []
    for r in ok:
        parts.append("=== %s (%s) ===\n%s" % (r["provider"], r["model"], r["text"]))
    return {
        "text": "\n\n".join(parts), "name": "multi-fallback", "model": model_joined,
        "elapsed": elapsed, "mode": "multi-fallback", "results": entries, "task": task,
    }


def _attempt(providers, make_blocks, cfg, focus, task, brief, labels, quota, mode="auto"):
    r = _routing_for(cfg, task)
    chain = route_order(providers, r["chain"])
    do_multi = r["multi"]
    if mode == "multi":
        do_multi = True
    elif mode == "single":
        do_multi = False
    if do_multi:
        return _attempt_multi(chain, make_blocks, cfg, focus, task, brief, labels, quota)
    return _attempt_single(chain, make_blocks, cfg, focus, task, brief, labels, quota)


def _run_with_task_check(providers, make_blocks, cfg, focus, task, brief, labels, quota, mode):
    result = _attempt(providers, make_blocks, cfg, focus, task, brief, labels, quota, mode)
    if not cfg.get("task_check", True):
        result["task_corrected"] = None
        return result
    if result.get("mode") not in ("single",):
        result["task_corrected"] = None
        return result
    new_task = _check_task(providers, cfg, focus, task, result["text"])
    if new_task:
        sys.stderr.write("[task校验] %s -> %s，重新识别一次\n" % (task, new_task))
        try:
            result2 = _attempt(providers, make_blocks, cfg, focus, new_task, brief, labels, quota, mode=mode)
            result2["task_corrected"] = new_task
            return result2
        except ProviderError as e:
            sys.stderr.write("[task重跑失败] %s，使用首次结果\n" % e)
    result["task_corrected"] = None
    return result


def run_providers(providers, images, cfg, focus, task, brief, max_size, auto_trim, allow_local_url, labels=None, mode="auto"):
    quality = int(cfg.get("jpeg_quality", 88))
    quota = Quota(cfg.get("quota", {}), _quota_state_file(cfg))

    def make_blocks(p):
        return [prepare_image_block(s, p.get("input_mode", "base64"), max_size, quality, auto_trim, allow_local_url, cfg) for s in images]

    return _run_with_task_check(providers, make_blocks, cfg, focus, task, brief, labels, quota, mode)


def run_blocks(providers, blocks, cfg, focus, task, brief, labels=None, mode="auto"):
    quota = Quota(cfg.get("quota", {}), _quota_state_file(cfg))
    r = _run_with_task_check(providers, lambda p: blocks, cfg, focus, task, brief, labels, quota, mode)
    return r["text"], r["name"], r["model"], r["elapsed"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="视觉分析：图片 -> 视觉模型 -> 结构化文字（半自动路由）")
    ap.add_argument("images", nargs="+", help="图片文件路径或 http(s) URL，可多个")
    ap.add_argument("-f", "--focus", help="关注点/问题，如：只提取报错信息")
    ap.add_argument("--task", choices=sorted(TASK_MAX_SIZE.keys()), default="general", help="任务类型（11 类）")
    ap.add_argument("--provider", help="强制使用某个供应商（name）")
    ap.add_argument("--config", default=None, help="配置文件路径，默认取脚本旁 config.json")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ap.add_argument("--max-size", type=int, default=None, help="图片长边像素上限（覆盖任务默认值）")
    ap.add_argument("--auto-trim", action="store_true", help="裁剪纯色/空白边缘后再压缩")
    ap.add_argument("--brief", action="store_true", help="输出紧凑证据，节省 token")
    ap.add_argument("--no-downscale", action="store_true", help="不做压缩，直接用原图")
    ap.add_argument("--multi", action="store_true", help="强制多模型并发 + 汇总")
    ap.add_argument("--serial", action="store_true", help="强制单模型串行降级")
    ap.add_argument("--no-task-check", action="store_true", help="关闭 agnes task 校验")
    args = ap.parse_args(argv)

    cfg_path = args.config or os.environ.get("VISION_CONFIG") or str(DEFAULT_CONFIG)
    cfg = load_config(cfg_path)
    if args.no_task_check:
        cfg["task_check"] = False
    providers = cfg.get("providers", [])
    task = args.task
    if task == "compare" and len(args.images) < 2:
        sys.stderr.write("[提示] compare 需要至少两张图，已降级为 general\n")
        task = "general"
    max_size = 999999 if args.no_downscale else resolve_max_size(args.max_size, cfg.get("max_size", "auto"), task)
    auto_trim = args.auto_trim or bool(cfg.get("auto_trim", False))
    allow_local_url = bool(cfg.get("allow_local_url", False))
    if args.provider:
        providers = [p for p in providers if p.get("name") == args.provider]
        if not providers:
            raise SystemExit("找不到供应商: %s" % args.provider)
    mode = "multi" if args.multi else ("single" if args.serial else "auto")
    labels = None
    if len(args.images) > 1:
        labels = ["图%d(%s)" % (i + 1, os.path.basename(str(s))[:24] or str(s)[:24]) for i, s in enumerate(args.images, 1)]
    try:
        result = run_providers(providers, args.images, cfg, args.focus, task, args.brief, max_size, auto_trim, allow_local_url, labels, mode=mode)
    except ProviderError as e:
        sys.stderr.write(str(e) + "\n")
        return 1
    if args.json:
        out = {
            "provider": result["name"], "model": result["model"], "images": args.images,
            "task": task, "text": result["text"], "mode": result.get("mode"),
        }
        if result.get("task_corrected"):
            out["task_corrected"] = result["task_corrected"]
        if result.get("results"):
            out["results"] = result["results"]
        if result.get("summarizer"):
            out["summarizer"] = result["summarizer"]
        sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(result["text"].rstrip() + "\n")
    sys.stderr.write("[ok] %s (%s)，耗时 %.1fs\n" % (result["name"], result["model"], result["elapsed"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
