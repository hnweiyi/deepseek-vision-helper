#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shijuefenxi 网页综合读取：抓取网页文字 + 提取图片交给视觉模型，输出综合信息。

用法:
  python read_web.py <网页URL...> [-f 关注点] [--task 类型] [--provider 名称]
                     [--json] [--config 路径] [--max-images N] [--no-images]
                     [--max-size N] [--auto-trim] [--brief] [--no-downscale]

流程:
  1. 安全下载网页 HTML（防 SSRF、限大小、限超时）。
  2. 抽取 title / description / 标题 / 段落 / 链接 / 图片 URL。
  3. 对每张图片安全下载 + 真实性/安全性校验（格式/尺寸/解压炸弹）。
  4. 视觉模型按图1/图2…分析图片，输出结构化文字。
  5. 汇总文字与视觉结果（最终综合判断由调用方/主模型完成）。
"""
import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

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

import analyze  # noqa: E402
from safe_net import safe_download, verify_image, SafeNetError  # noqa: E402

DEFAULT_CONFIG = SCRIPT_DIR.parent / "config.json"


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = []
        self.desc = ""
        self.charset = None
        self.headings = []
        self.paragraphs = []
        self.links = []
        self.images = []
        self._in_title = False
        self._in_h = False
        self._hlevel = 0
        self._htext = []
        self._in_p = False
        self._ptext = []
        self._a = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            if a.get("charset"):
                self.charset = a["charset"]
            name = (a.get("name") or "").lower()
            prop = (a.get("property") or "").lower()
            if name == "description" or prop == "og:description":
                self.desc = a.get("content", "")
        elif tag in ("h1", "h2", "h3"):
            self._in_h = True
            self._hlevel = int(tag[1])
            self._htext = []
        elif tag == "p":
            self._in_p = True
            self._ptext = []
        elif tag == "a":
            self._a = {"href": a.get("href", ""), "text": []}
        elif tag == "img":
            self.images.append({
                "src": a.get("src", ""),
                "alt": a.get("alt", ""),
                "width": a.get("width"),
                "height": a.get("height"),
            })

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3"):
            self._in_h = False
            t = "".join(self._htext).strip()
            if t:
                self.headings.append((self._hlevel, t))
        elif tag == "p":
            self._in_p = False
            t = " ".join(self._ptext).strip()
            if t:
                self.paragraphs.append(t)
        elif tag == "a":
            if self._a is not None:
                txt = "".join(self._a["text"]).strip()
                href = (self._a["href"] or "").strip()
                if href and not href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                    self.links.append((txt, href))
                self._a = None

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        if self._in_h:
            self._htext.append(data)
        if self._in_p:
            self._ptext.append(data)
        if self._a is not None:
            self._a["text"].append(data)


def _charset(headers, body_head):
    if headers:
        m = re.search(r"charset\s*=\s*[\"']?([\w-]+)", headers, re.I)
        if m:
            return m.group(1)
    if body_head:
        m = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?([\w-]+)", body_head[:4096], re.I)
        if m:
            return m.group(1).decode("ascii", "replace")
    return None


def _build_page(url, parser):
    images = []
    for im in parser.images:
        src = (im.get("src") or "").strip()
        if not src:
            continue
        src = urljoin(url, src)
        parts = urlsplit(src)
        if parts.scheme.lower() not in ("http", "https"):
            continue
        images.append({
            "url": src,
            "alt": (im.get("alt") or "").strip(),
            "width": im.get("width"),
            "height": im.get("height"),
        })
    seen = set()
    uniq = []
    for im in images:
        if im["url"] in seen:
            continue
        seen.add(im["url"])
        uniq.append(im)
    return {
        "url": url,
        "title": "".join(parser.title).strip(),
        "description": parser.desc or "",
        "headings": parser.headings,
        "paragraphs": parser.paragraphs,
        "links": parser.links,
        "images": uniq,
    }


def build_text_summary(page):
    lines = []
    if page.get("title"):
        lines.append("标题: %s" % page["title"])
    if page.get("description"):
        lines.append("摘要: %s" % page["description"][:500])
    if page.get("headings"):
        lines.append("小标题:")
        for lvl, t in page["headings"][:12]:
            lines.append("  %s%s" % ("  " * (lvl - 1), t[:200]))
    if page.get("paragraphs"):
        lines.append("正文摘要:")
        for t in page["paragraphs"][:8]:
            lines.append("  %s" % t[:400])
    if page.get("links"):
        lines.append("链接(%d):" % len(page["links"]))
        for txt, href in page["links"][:20]:
            lines.append("  - %s (%s)" % ((txt or "(无文字)")[:80], href[:120]))
    if not lines:
        lines.append("（未能提取到有效文字内容）")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="网页综合读取：网页文字 + 图片视觉分析")
    ap.add_argument("urls", nargs="+", help="网页 http(s) URL，可多个")
    ap.add_argument("-f", "--focus", help="关注点/问题")
    ap.add_argument("--task", choices=sorted(analyze.TASK_MAX_SIZE.keys()), default="general", help="图片任务类型")
    ap.add_argument("--provider", help="强制使用某个供应商（name）")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ap.add_argument("--max-images", type=int, default=None, help="最多分析几张图（默认取 config web.max_images）")
    ap.add_argument("--no-images", action="store_true", help="只读网页文字，不下载/分析图片")
    ap.add_argument("--max-size", type=int, default=None, help="图片长边像素上限")
    ap.add_argument("--auto-trim", action="store_true", help="裁剪图片纯色边缘")
    ap.add_argument("--brief", action="store_true", help="视觉输出紧凑证据")
    ap.add_argument("--no-downscale", action="store_true", help="图片不压缩")
    args = ap.parse_args(argv)

    cfg_path = args.config or os.environ.get("VISION_CONFIG") or str(DEFAULT_CONFIG)
    cfg = analyze.load_config(cfg_path)
    providers = cfg.get("providers", [])
    if args.provider:
        providers = [p for p in providers if p.get("name") == args.provider]
        if not providers:
            raise SystemExit("找不到供应商: %s" % args.provider)
    providers = [p for p in providers if p.get("input_mode", "base64") != "url"]

    web = cfg.get("web", {})
    page_max = int(web.get("max_page_bytes", 2000000))
    img_max = int(cfg.get("image_max_bytes", 8000000))
    img_pixels = int(cfg.get("image_max_pixels", 40000000))
    timeout = int(cfg.get("download_timeout", 20))
    allow_local = bool(cfg.get("allow_local_url", False))
    max_images = args.max_images if args.max_images is not None else int(web.get("max_images", 6))
    max_size = 999999 if args.no_downscale else analyze.resolve_max_size(args.max_size, cfg.get("max_size", "auto"), args.task)
    auto_trim = args.auto_trim or bool(cfg.get("auto_trim", False))
    quality = int(cfg.get("jpeg_quality", 88))

    results = []
    for url in args.urls:
        item = {"url": url}
        try:
            page_bytes, page_ctype = safe_download(url, allow_private=allow_local, max_bytes=page_max, timeout=timeout)
        except SafeNetError as e:
            item["error"] = str(e)
            results.append(item)
            sys.stderr.write("[网页失败] %s: %s\n" % (url, e))
            continue
        charset = _charset(page_ctype, page_bytes)
        html = page_bytes.decode(charset or "utf-8", "replace")
        parser = PageParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception as e:
            sys.stderr.write("[warn] HTML 解析不完整: %s\n" % e)
        page = _build_page(url, parser)
        item["title"] = page["title"]
        item["description"] = page["description"]
        item["headings"] = page["headings"]
        item["paragraphs"] = page["paragraphs"]
        item["links"] = page["links"]
        item["text_summary"] = build_text_summary(page)

        ok_images = []
        fail_images = []
        if not args.no_images:
            for im in page["images"][:max_images]:
                try:
                    data, ctype = safe_download(im["url"], allow_private=allow_local, max_bytes=img_max, timeout=timeout)
                except SafeNetError as e:
                    fail_images.append({"url": im["url"], "reason": str(e)})
                    continue
                v = verify_image(data, max_pixels=img_pixels)
                if not v.get("ok"):
                    fail_images.append({"url": im["url"], "reason": v.get("reason")})
                    continue
                ok_images.append({
                    "url": im["url"],
                    "alt": im.get("alt", ""),
                    "format": v.get("format"),
                    "width": v.get("width"),
                    "height": v.get("height"),
                    "data": data,
                    "mime": (ctype or "").split(";")[0].strip(),
                })
        item["images_ok"] = [{"url": i["url"], "alt": i["alt"], "format": i["format"], "width": i["width"], "height": i["height"]} for i in ok_images]
        item["images_failed"] = fail_images

        vision_text = None
        provider_info = {}
        if ok_images and providers:
            blocks = [analyze.bytes_to_block(i["data"], i["mime"], max_size, quality, auto_trim) for i in ok_images]
            labels = ["图%d(alt=%s)" % (idx + 1, (i["alt"][:30] or "无")) for idx, i in enumerate(ok_images, 1)]
            focus = args.focus or ("这些图片来自网页《%s》（%s），请按图序简要说明每张图的用途与关键内容（图表给出主题与关键数值，截图/照片概述内容）。" % (page["title"] or "无标题", url))
            try:
                text, name, model, elapsed = analyze.run_blocks(providers, blocks, cfg, focus, args.task, args.brief, labels)
                vision_text = text
                provider_info = {"provider": name, "model": model}
                sys.stderr.write("[ok] 视觉分析使用 %s (%s)，耗时 %.1fs\n" % (name, model, elapsed))
            except analyze.ProviderError as e:
                sys.stderr.write("[视觉失败] %s\n" % e)
        item["vision_text"] = vision_text
        item["provider"] = provider_info
        results.append(item)

    if args.json:
        out = []
        for r in results:
            r2 = dict(r)
            for k in list(r2):
                if isinstance(r2[k], dict) and "data" in r2[k]:
                    r2[k].pop("data", None)
            # 上面仅针对 images_ok 内联数据；此处统一再清理一次
            out.append(r2)
        sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
        return 0

    for r in results:
        print("=" * 22 + " 网页: %s " % r["url"] + "=" * 22)
        if r.get("error"):
            print("错误: %s" % r["error"])
            print()
            continue
        print(r["text_summary"])
        print()
        ok = r.get("images_ok") or []
        if ok:
            print("图片(%d 张):" % len(ok))
            for i, im in enumerate(ok, 1):
                extra = " (alt: %s)" % im["alt"] if im.get("alt") else ""
                print("  图%d: %s%s" % (i, im["url"], extra))
        failed = r.get("images_failed") or []
        if failed:
            print("跳过图片(%d 张):" % len(failed))
            for f in failed:
                print("  - %s -> %s" % (f["url"], f["reason"]))
        if r.get("vision_text"):
            print()
            print("=" * 22 + " 图片视觉分析 " + "=" * 22)
            print(r["vision_text"].rstrip())
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
