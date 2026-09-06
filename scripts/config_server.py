#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shijuefenxi 图形化配置工具：本地 HTML 服务器 + 配置编辑。

用法:
  python config_server.py [--open] [--port N] [--config 路径]

默认只监听 127.0.0.1（仅本机可访问）；--open 启动后自动打开浏览器。
"""
import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

if sys.version_info < (3, 8):
    raise SystemExit("需要 Python 3.8 或更高版本")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from dpapi import encrypt_text, decrypt_text
except Exception:
    encrypt_text = None
    decrypt_text = None

DEFAULT_CONFIG = SCRIPT_DIR.parent / "config.json"

ALL_TASKS = ["general", "ocr", "error", "ui", "chart", "compare", "document", "math_stem", "detail", "video", "unknown"]

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>shijuefenxi 配置工具</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; margin: 0; background: #f5f6f8; color: #1a1b1c; }
  header { background: #1f6feb; color: #fff; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; }
  header h1 { font-size: 18px; margin: 0; }
  header button { background: #fff; color: #1f6feb; border: none; padding: 6px 14px; border-radius: 5px; cursor: pointer; font-weight: 600; }
  #msg { padding: 8px 20px; font-size: 14px; color: #b45309; min-height: 18px; }
  .cols { display: flex; gap: 16px; padding: 0 20px 16px; flex-wrap: wrap; }
  .panel { background: #fff; border: 1px solid #e2e4e8; border-radius: 8px; padding: 14px; flex: 1 1 380px; }
  .panel h2 { font-size: 15px; margin: 0 0 10px; }
  .panel h3 { font-size: 13px; margin: 14px 0 6px; color: #444; }
  ul, ol { list-style: none; padding: 0; margin: 0; }
  #provider-list li, #chain-list li { padding: 7px 9px; border: 1px solid #e2e4e8; border-radius: 5px; margin-bottom: 5px; cursor: pointer; font-size: 13px; }
  #provider-list li:hover, #chain-list li:hover { background: #f0f5ff; }
  .sel { background: #dbe9ff !important; border-color: #1f6feb !important; }
  label { font-size: 13px; }
  .field { margin-bottom: 10px; }
  .field span { display: block; font-size: 12px; color: #666; margin-bottom: 3px; }
  .field input[type=text], .field input[type=password], .field input[type=number] { width: 100%; padding: 6px 8px; border: 1px solid #cbd0d8; border-radius: 5px; font-size: 13px; }
  .field input[type=checkbox] { width: auto; }
  button.mini { padding: 4px 10px; margin-right: 5px; border: 1px solid #cbd0d8; background: #fff; border-radius: 5px; cursor: pointer; }
  #multi-tasks label { margin-right: 12px; display: inline-block; }
  .chain-bar { margin-bottom: 6px; }
  select { padding: 5px; border: 1px solid #cbd0d8; border-radius: 5px; }
</style>
</head>
<body>
<header>
  <h1>shijuefenxi 视觉配置</h1>
  <div>
    <button onclick="save()">保存</button>
    <button onclick="shutdown()">退出</button>
  </div>
</header>
<div id="msg"></div>
<div class="cols">
  <div class="panel">
    <h2>模型供应商 providers <button class="mini" onclick="showAddForm()">+ 添加</button></h2>
    <ul id="provider-list"></ul>
    <div id="add-form" style="display:none; margin-top:10px; border:1px dashed #1f6feb; border-radius:6px; padding:10px;">
      <h3>添加供应商</h3>
      <div class="field"><span>name（唯一标识）</span><input type="text" id="a-name" placeholder="modelscope-30b"></div>
      <div class="field"><span>group</span><input type="text" id="a-group" placeholder="modelscope / glm / agnes"></div>
      <div class="field"><span>base_url</span><input type="text" id="a-base" placeholder="https://..."></div>
      <div class="field"><span>model（多个候选逗号分隔）</span><input type="text" id="a-model" placeholder="Qwen/Qwen3-VL-30B-A3B-Instruct"></div>
      <div class="field"><span>api_key</span><input type="password" id="a-key"></div>
      <div class="field"><span>timeout（秒）</span><input type="text" id="a-timeout" value="60"></div>
      <div class="field"><label><input type="checkbox" id="a-enabled" checked> 启用</label> <label><input type="checkbox" id="a-chain"> 加入 default_chain</label></div>
      <button class="mini" onclick="addProvider()">确认添加</button>
      <button class="mini" onclick="document.getElementById('add-form').style.display='none'">取消</button>
    </div>
    <div id="provider-form" style="display:none; margin-top:12px; border-top:1px solid #e2e4e8; padding-top:10px;">
      <h3 id="f-title"></h3>
      <div class="field"><span>api_key（留空表示保持不变；输入明文，保存时自动加密）</span><input type="password" id="f-key" placeholder=""></div>
      <div class="field"><span>base_url</span><input type="text" id="f-base"></div>
      <div class="field"><span>model（多个候选用英文逗号分隔）</span><input type="text" id="f-model"></div>
      <div class="field"><label><input type="checkbox" id="f-enabled"> 启用</label></div>
      <button class="mini" onclick="applyProvider()">应用到该供应商</button>
    </div>
  </div>
  <div class="panel">
    <h2>路由 routing</h2>
    <h3>质量顺序（选中一项后上移/下移）</h3>
    <div class="chain-bar">
      <select id="chain-select">
        <option value="default_chain">默认链 default_chain</option>
        <option value="math_stem">math_stem（特化）</option>
        <option value="chart">chart（特化）</option>
      </select>
      <button class="mini" onclick="moveChain(-1)">上移</button>
      <button class="mini" onclick="moveChain(1)">下移</button>
    </div>
    <ol id="chain-list"></ol>
    <h3>多模型任务 multi_tasks</h3>
    <div id="multi-tasks"></div>
  </div>

  <div class="panel">
    <h2>缓存 cache</h2>
    <div class="field"><label><input type="checkbox" id="cache-enabled" onchange="cfg.cache.enabled=this.checked"> 启用答案缓存</label></div>
    <div class="field"><span>有效期秒 ttl_seconds</span><input type="number" id="cache-ttl" min="1" value="3600" onchange="cfg.cache.ttl_seconds=parseInt(this.value)||3600"></div>
    <div class="field"><span>最大条目 max_entries</span><input type="number" id="cache-max" min="1" value="200" onchange="cfg.cache.max_entries=parseInt(this.value)||200"></div>
    <div class="field"><span>缓存目录（留空=config 同目录）dir</span><input type="text" id="cache-dir" placeholder="" oninput="cfg.cache.dir=this.value"></div>
    <p style="font-size:12px;color:#666">缓存开关即时生效，保存后写入 config.json。</p>
  </div>
</div>
<script>
let cfg = null;
let selProvider = null;
let selChain = null;
let curChainKey = 'default_chain';

function msg(s) { document.getElementById('msg').textContent = s; }

function ensureCache() {
  if (cfg.cache) return false;
  cfg.cache = { enabled: true, ttl_seconds: 3600, max_entries: 200, dir: '' };
  return true;
}

function renderCache() {
  const c = cfg.cache || {};
  document.getElementById('cache-enabled').checked = !!c.enabled;
  document.getElementById('cache-ttl').value = c.ttl_seconds || 3600;
  document.getElementById('cache-max').value = c.max_entries || 200;
  document.getElementById('cache-dir').value = c.dir || '';
}

async function loadConfig() {
  const r = await fetch('/api/config');
  const d = await r.json();
  if (!d.ok) { msg('加载失败: ' + d.error); return; }
  cfg = d.config;
  const cacheCreated = ensureCache();
  renderProviders();
  renderRouting();
  renderCache();
  if (cacheCreated) msg('已补默认缓存配置，保存时随配置写入');
}

function showAddForm() { document.getElementById('add-form').style.display = 'block'; }

function addProvider() {
  const name = document.getElementById('a-name').value.trim();
  if (!name) { msg('请输入 name'); return; }
  if (cfg.providers.some(p => p.name === name)) { msg('name 已存在: ' + name); return; }
  const group = document.getElementById('a-group').value.trim() || 'other';
  const base_url = document.getElementById('a-base').value.trim();
  const m = document.getElementById('a-model').value.split(',').map(s => s.trim()).filter(Boolean);
  const key = document.getElementById('a-key').value.trim();
  const timeout = parseInt(document.getElementById('a-timeout').value) || 60;
  const enabled = document.getElementById('a-enabled').checked;
  const joinChain = document.getElementById('a-chain').checked;
  const p = {
    name: name, group: group, base_url: base_url,
    model: m.length === 1 ? m[0] : m,
    api_key: key || '', input_mode: 'base64', timeout: timeout, enabled: enabled
  };
  cfg.providers.push(p);
  if (joinChain && !cfg.routing.default_chain.includes(name)) cfg.routing.default_chain.push(name);
  ['a-name','a-group','a-base','a-model','a-key','a-timeout'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('a-enabled').checked = true;
  document.getElementById('a-chain').checked = false;
  document.getElementById('add-form').style.display = 'none';
  renderProviders();
  renderRouting();
  msg('已添加 ' + name + '，点「保存」写入 config.json');
}

function renderProviders() {
  const sorted = cfg.providers.map((p, i) => ({ p: p, i: i })).sort((a, b) => {
    const ga = (a.p.group || '').localeCompare(b.p.group || '');
    if (ga !== 0) return ga;
    const ma = (Array.isArray(a.p.model) ? a.p.model[0] : (a.p.model || '')).localeCompare(
               Array.isArray(b.p.model) ? b.p.model[0] : (b.p.model || ''));
    return ma;
  });
  const ul = document.getElementById('provider-list');
  ul.innerHTML = '';
  sorted.forEach(({ p, i }) => {
    const li = document.createElement('li');
    const m = Array.isArray(p.model) ? p.model.join(', ') : (p.model || '');
    const span = document.createElement('span');
    span.textContent = p.name + '  [' + p.group + ']  ' + m + '  enabled=' + p.enabled + '  key=' + p.api_key;
    const btnT = document.createElement('button');
    btnT.className = 'mini';
    btnT.textContent = '测试';
    btnT.onclick = (e) => { e.stopPropagation(); testProvider(i); };
    const btnD = document.createElement('button');
    btnD.className = 'mini';
    btnD.textContent = '删除';
    btnD.onclick = (e) => { e.stopPropagation(); deleteProvider(i); };
    li.appendChild(span);
    li.appendChild(btnT);
    li.appendChild(btnD);
    li.onclick = () => selectProvider(i);
    if (i === selProvider) li.className = 'sel';
    ul.appendChild(li);
  });
}

function deleteProvider(i) {
  const p = cfg.providers[i];
  const a = Math.floor(Math.random() * 9) + 1;
  const b = Math.floor(Math.random() * (10 - a)) + 1;
  let ans, q;
  if (Math.random() < 0.5) {
    const big = Math.max(a, b), small = Math.min(a, b);
    ans = big - small; q = big + ' - ' + small;
  } else {
    ans = a + b; q = a + ' + ' + b;
  }
  const input = prompt('确认删除「' + p.name + '」？请计算：' + q + ' = ?（答对才删除）');
  if (input === null) return;
  if (parseInt(input, 10) !== ans) { msg('答错，未删除'); return; }
  cfg.providers.splice(i, 1);
  const chains = [cfg.routing.default_chain].concat(Object.values(cfg.routing.overrides || {}));
  chains.forEach(ch => { if (Array.isArray(ch)) { for (let k = ch.length - 1; k >= 0; k--) { if (ch[k] === p.name) ch.splice(k, 1); } } });
  if (selProvider === i) { selProvider = null; document.getElementById('provider-form').style.display = 'none'; }
  renderProviders();
  renderRouting();
  msg('已删除 ' + p.name + '，点「保存」写入 config.json');
}

async function testProvider(i) {
  const p = cfg.providers[i];
  msg('正在测试 ' + p.name + ' ...');
  try {
    const r = await fetch('/api/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: p.name }) });
    const d = await r.json();
    if (!d.ok) { msg('测试失败: ' + d.error); return; }
    const res = d.result;
    msg(res.ok ? ('测试通过 ' + p.name + '（' + res.elapsed + 's）：' + (res.reply || '')) : ('测试失败 ' + p.name + '：' + (res.error || '')));
  } catch (e) {
    msg('测试异常: ' + e);
  }
}

function selectProvider(i) {
  selProvider = i;
  const p = cfg.providers[i];
  document.getElementById('f-title').textContent = '编辑 ' + p.name + '（group=' + p.group + '）';
  document.getElementById('f-key').value = '';
  document.getElementById('f-key').placeholder = p.api_key === 'dpapi' ? '已加密，留空保持不变' : (p.api_key === 'plain' ? '当前是明文，输入新值或留空' : '未设置，输入 key');
  document.getElementById('f-base').value = p.base_url || '';
  document.getElementById('f-model').value = Array.isArray(p.model) ? p.model.join(', ') : (p.model || '');
  document.getElementById('f-enabled').checked = !!p.enabled;
  document.getElementById('provider-form').style.display = 'block';
  renderProviders();
}

function applyProvider() {
  if (selProvider === null) return;
  const p = cfg.providers[selProvider];
  const key = document.getElementById('f-key').value.trim();
  if (key) p.api_key = key;
  p.base_url = document.getElementById('f-base').value.trim();
  const m = document.getElementById('f-model').value.split(',').map(s => s.trim()).filter(Boolean);
  p.model = m.length === 1 ? m[0] : m;
  p.enabled = document.getElementById('f-enabled').checked;
  renderProviders();
  msg('已应用，点“保存”写入 config.json');
}

function getCurChain() {
  if (curChainKey === 'default_chain') return cfg.routing.default_chain;
  return (cfg.routing.overrides || {})[curChainKey];
}

function renderChain() {
  const chain = getCurChain();
  const ol = document.getElementById('chain-list');
  ol.innerHTML = '';
  if (!chain) { ol.innerHTML = '<li>（无此链）</li>'; return; }
  chain.forEach((name, i) => {
    const li = document.createElement('li');
    li.textContent = (i + 1) + '. ' + name;
    li.onclick = () => { selChain = i; renderChain(); };
    if (i === selChain) li.className = 'sel';
    ol.appendChild(li);
  });
}

function renderRouting() {
  renderChain();
  const mt = document.getElementById('multi-tasks');
  mt.innerHTML = '';
  ALL_TASKS.forEach(t => {
    const lab = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = cfg.routing.multi_tasks.includes(t);
    cb.onchange = () => {
      if (cb.checked && !cfg.routing.multi_tasks.includes(t)) cfg.routing.multi_tasks.push(t);
      if (!cb.checked) cfg.routing.multi_tasks = cfg.routing.multi_tasks.filter(x => x !== t);
    };
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(' ' + t));
    mt.appendChild(lab);
  });
}

document.getElementById('chain-select').onchange = (e) => {
  curChainKey = e.target.value;
  selChain = null;
  renderChain();
};

function moveChain(d) {
  const chain = getCurChain();
  if (!chain || selChain === null || selChain < 0 || selChain >= chain.length) return;
  const j = selChain + d;
  if (j < 0 || j >= chain.length) return;
  const tmp = chain[selChain];
  chain[selChain] = chain[j];
  chain[j] = tmp;
  selChain = j;
  renderChain();
}

async function save() {
  const r = await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config: cfg }) });
  const d = await r.json();
  msg(d.ok ? '已保存到 config.json' : ('保存失败: ' + d.error));
  if (d.ok) await loadConfig();
}

async function shutdown() {
  await fetch('/api/shutdown');
  msg('已退出，可关闭此页面');
}

loadConfig();
</script>
</body>
</html>
'''


def mask_key(v):
    if not v:
        return "empty"
    if str(v).startswith("dpapi:"):
        return "dpapi"
    return "plain"


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
            sys.stderr.write("[提示] 未找到 config.json，已按 config.example.json 自动生成空白配置，请填写 api_key 后点保存。\n")
        else:
            raise FileNotFoundError("找不到配置文件: %s" % p)
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)
def public_config(cfg):
    out = json.loads(json.dumps(cfg, ensure_ascii=False))
    for p in out.get("providers", []):
        p["api_key"] = mask_key(p.get("api_key", ""))
    return out


def save_config(cfg, path):
    old_keys = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            old = json.load(f)
        for p in old.get("providers", []):
            old_keys[p.get("name")] = p.get("api_key", "")
    except Exception:
        old = None
    for p in cfg.get("providers", []):
        k = p.get("api_key", "")
        if k in ("dpapi", "empty", "plain", ""):
            # 脱敏标记/空：保留旧 key，不覆盖
            p["api_key"] = old_keys.get(p.get("name"), "")
        elif not str(k).startswith("dpapi:"):
            # 明文 key：加密
            if encrypt_text:
                try:
                    p["api_key"] = encrypt_text(k)
                except Exception:
                    pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


BUILTIN_TEST_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAT0lEQVR42u3PsQkAAAzDsPz/dHpCp2wCzwalybTxvgEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA+Dq47PDiz8p6GQAAAABJRU5ErkJggg=="


def _test_image_data_uri():
    """生成一张真实测试图片的 data URI。

    优先用 PIL 现场绘制 96x64 左红右蓝；PIL 不可用时退回内置 64x64 PNG。
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:
        pass
    else:
        try:
            import io as _io
            import base64 as _b64
            img = Image.new("RGB", (96, 64), "white")
            d = ImageDraw.Draw(img)
            d.rectangle([0, 0, 47, 63], fill=(255, 0, 0))
            d.rectangle([48, 0, 95, 63], fill=(0, 0, 255))
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            return "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            pass
    return "data:image/png;base64," + BUILTIN_TEST_PNG_B64


def test_provider(p):
    import time as _time
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    base_url = (p.get("base_url") or "").rstrip("/")
    m = p.get("model")
    model = m[0] if isinstance(m, list) else (m or "")
    key = p.get("api_key", "")
    if str(key).startswith("dpapi:") and decrypt_text:
        try:
            key = decrypt_text(key)
        except Exception as e:
            return {"ok": False, "error": "key 解密失败: %s" % e}
    if not base_url or not model:
        return {"ok": False, "error": "缺少 base_url 或 model"}
    if not key:
        return {"ok": False, "error": "未配置 api_key"}
    content = [
        {"type": "image_url", "image_url": {"url": _test_image_data_uri()}},
        {"type": "text", "text": "图中主体颜色或内容是什么？用中文一句话回答"},
    ]
    payload = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 64}
    url = base_url + "/chat/completions"
    req = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    t0 = _time.time()
    try:
        resp = urlopen(req, timeout=int(p.get("timeout", 60)))
        body = resp.read().decode("utf-8", "replace")
        elapsed = _time.time() - t0
        data = json.loads(body)
        msg = data.get("choices", [{}])[0].get("message", {})
        c = msg.get("content")
        if isinstance(c, str):
            reply = c
        elif isinstance(c, list):
            reply = "".join(b.get("text", "") for b in c if isinstance(b, dict))
        else:
            reply = ""
        reply = (reply or "").strip()
        if not reply:
            return {"ok": False, "elapsed": round(elapsed, 1), "error": "HTTP 2xx 但返回内容为空（模型可能不支持图片）"}
        return {"ok": True, "elapsed": round(elapsed, 1), "reply": reply[:120]}
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return {"ok": False, "elapsed": round(_time.time() - t0, 1), "error": "HTTP %s: %s" % (e.code, detail or e.reason)}
    except URLError as e:
        return {"ok": False, "elapsed": round(_time.time() - t0, 1), "error": "URLError: %s" % e}
    except Exception as e:
        return {"ok": False, "elapsed": round(_time.time() - t0, 1), "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    config_path = str(DEFAULT_CONFIG)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._html()
        elif self.path == "/api/config":
            try:
                cfg = load_config(self.config_path)
                self._json({"ok": True, "config": public_config(cfg)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif self.path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/test":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                name = data.get("name")
                cfg = load_config(self.config_path)
                p = next((x for x in cfg.get("providers", []) if x.get("name") == name), None)
                if not p:
                    self._json({"ok": False, "error": "找不到供应商: %s" % name}, 404)
                else:
                    self._json({"ok": True, "result": test_provider(p)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif self.path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                cfg = data.get("config")
                if not cfg or "providers" not in cfg:
                    raise ValueError("config 缺少 providers")
                save_config(cfg, self.config_path)
                self._json({"ok": True, "config": public_config(load_config(self.config_path))})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def log_message(self, fmt, *args):
        pass


def find_port(start):
    port = start
    while True:
        try:
            srv = HTTPServer(("127.0.0.1", port), Handler)
            return srv, port
        except OSError:
            port += 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="shijuefenxi 图形化配置工具")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", default=None, help="配置文件路径，默认取脚本旁 config.json")
    args = ap.parse_args(argv)
    if args.config:
        Handler.config_path = args.config
    srv, port = find_port(args.port)
    url = "http://127.0.0.1:%d/" % port
    print("配置工具已启动: %s" % url, flush=True)
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    print("已退出", flush=True)


if __name__ == "__main__":
    main()
