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

try:
    from analyze import _normalize_cfg, _quota_state_file, _make_quota  # noqa: E402
except Exception:
    _normalize_cfg = None
    _quota_state_file = None
    _make_quota = None

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
  header .hdr-btns button { background: #fff; color: #1f6feb; border: none; padding: 6px 16px; border-radius: 5px; cursor: pointer; font-weight: 600; margin-left: 8px; }
  header .hdr-btns button#btn-save { background: #ffd33d; color: #1f2937; }
  #msg { padding: 8px 20px; font-size: 14px; color: #b45309; min-height: 18px; }
  #wrap { max-width: 1380px; margin: 0 auto; padding: 0 16px 24px; }
  section.panel { background: #fff; border: 1px solid #e2e4e8; border-radius: 10px; padding: 14px 16px; margin-bottom: 16px; }
  section.panel h2 { font-size: 15px; margin: 0 0 10px; display: flex; justify-content: space-between; align-items: center; gap: 10px; }
  .toolbar { margin-bottom: 8px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .toolbar select, .toolbar input[type=text] { padding: 5px 8px; border: 1px solid #cbd0d8; border-radius: 5px; font-size: 13px; }
  .scroll { overflow-x: auto; }
  table.grid { border-collapse: collapse; width: 100%; min-width: 1160px; font-size: 13px; table-layout: fixed; }
  table.grid th { background: #f0f4fb; color: #333; font-weight: 600; text-align: left; padding: 7px 8px; border-bottom: 1px solid #dbe1ea; white-space: nowrap; }
  table.grid td { border-bottom: 1px solid #eef1f5; padding: 6px 8px; vertical-align: middle; }
  table.grid tr:hover td { background: #fafcff; }
  .c { text-align: center; }
  td input[type=text], td input[type=password], td input[type=number] { width: 100%; border: 1px solid transparent; background: transparent; padding: 4px 6px; border-radius: 4px; font-size: 13px; font-family: inherit; color: inherit; }
  td input:hover { border-color: #d0d7e2; background: #fff; }
  td input:focus { border-color: #1f6feb; background: #fff; outline: none; }
  td input[type=checkbox] { width: auto; transform: scale(1.15); cursor: pointer; }
  .pname { font-weight: 600; color: #1f2937; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; margin-bottom: 2px; white-space: nowrap; }
  .b-empty { background: #eceff3; color: #6b7280; }
  .b-dpapi { background: #e6f4ea; color: #188038; }
  .b-plain { background: #fef7e0; color: #b06000; }
  .b-new { background: #e8f0fe; color: #1a56db; }
  button.mini { padding: 4px 10px; margin-right: 5px; border: 1px solid #cbd0d8; background: #fff; border-radius: 5px; cursor: pointer; font-size: 13px; }
  button.mini:hover { background: #f0f5ff; }
  button.mini.danger:hover { background: #fdeaea; border-color: #e5a0a0; }
  button.mini.ok:hover { background: #e6f4ea; border-color: #8fcaa0; }
  .box { margin-top: 12px; border: 1px dashed #1f6feb; border-radius: 8px; padding: 12px; }
  .box h3 { font-size: 13px; margin: 0 0 8px; color: #1f6feb; }
  .frow { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; margin-bottom: 8px; }
  .frow label { display: flex; flex-direction: column; font-size: 12px; color: #555; gap: 3px; }
  .frow input[type=text], .frow input[type=password] { width: 180px; padding: 5px 8px; border: 1px solid #cbd0d8; border-radius: 5px; font-size: 13px; }
  .frow input.wide { width: 320px; }
  .frow label.ck { flex-direction: row; align-items: center; gap: 4px; padding-bottom: 6px; font-size: 13px; color: #1a1b1c; }
  .hint { font-size: 12px; color: #8a94a6; margin: 4px 0 0; }
  #chain-table-wrap .scroll { }
  #multi-tasks { display: grid; grid-template-columns: repeat(auto-fill, minmax(255px, 1fr)); gap: 6px 10px; }
  #multi-tasks .mt-card { display: flex; flex-wrap: wrap; align-items: baseline; margin: 0; padding: 5px 8px; border: 1px solid #e4e8ee; border-radius: 6px; background: #fafbfc; cursor: pointer; font-size: 13px; line-height: 1.5; }
  #multi-tasks .mt-card:hover { border-color: #7aa5f0; background: #f3f7fe; }
  #multi-tasks .mt-card input { width: auto; transform: scale(1.15); margin-right: 6px; vertical-align: -2px; cursor: pointer; }
  #multi-tasks .mt-card .mt-en { font-family: Consolas, Menlo, monospace; font-size: 12px; color: #4a6cf7; margin-left: 5px; }
  #multi-tasks .mt-card .mt-desc { width: 100%; margin: 1px 0 0 21px; color: #8a94a6; font-size: 12px; }
  #group-table th, #provider-table th { white-space: normal; line-height: 1.35; vertical-align: middle; }
  #group-table td:nth-child(3) { word-break: break-all; }
  .chainnum { color: #8a94a6; }
  .missing { color: #c62828; }
  .upd { color: #1a56db; font-weight: 600; }
  #chain-add-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 13px; }
</style>
</head><body>
<header>
  <h1>shijuefenxi 视觉配置</h1>
  <div class="hdr-btns">
    <button id="btn-save" onclick="save()">保存配置</button>
    <button onclick="shutdown()">退出</button>
  </div>
</header>
<div id="msg"></div>
<div id="wrap">

  <section class="panel">
    <h2><span>① 模型供应商 providers（表格内直接修改，改完点右上「保存配置」）</span>
        <button class="mini ok" onclick="toggleAddForm()">+ 添加供应商</button></h2>
    <div id="add-form" style="display:none" class="box">
      <h3>添加供应商（name 为唯一标识；model 多个候选用英文逗号分隔）</h3>
      <div class="frow">
        <label>name<input type="text" id="a-name" placeholder="modelscope-30b"></label>
        <label>group<input type="text" id="a-group" placeholder="modelscope / glm / agnes"></label>
        <label>base_url<input type="text" id="a-base" class="wide" placeholder="https://..."></label>
        <label>model<input type="text" id="a-model" class="wide" placeholder="Qwen/Qwen3-VL-30B-A3B-Instruct"></label>
        <label>api_key<input type="password" id="a-key" placeholder="明文，保存时自动加密"></label>
        <label>timeout(秒)<input type="text" id="a-timeout" value="60" style="width:70px"></label>
        <label class="ck"><input type="checkbox" id="a-enabled" checked> 启用</label>
        <label class="ck"><input type="checkbox" id="a-multi" checked title="是否参与多模型任务(multi_tasks)并发"> 参与多模型</label>
        <label class="ck"><input type="checkbox" id="a-chain"> 加入 default_chain</label>
        <button class="mini ok" onclick="addProvider()">确认添加</button>
        <button class="mini" onclick="toggleAddForm()">取消</button>
      </div>
    </div>
    <div class="scroll">
      <table class="grid" id="provider-table" style="min-width:1260px">
        <thead><tr>
          <th style="width:44px" class="c">启用</th>
          <th style="width:64px" class="c" title="是否参与多模型任务(multi_tasks)并发">多模型</th>
          <th style="width:110px">名称 name</th>
          <th style="width:84px">厂商 group</th>
          <th style="width:23%">模型 model</th>
          <th style="width:22%">接口 base_url</th>
          <th style="width:160px">API Key</th>
          <th style="width:64px">timeout</th>
          <th style="width:140px">操作</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <p class="hint">提示：key 一栏留空 = 不修改原 key；输入明文保存时自动 DPAPI 加密。「测试」会先保存当前改动再实测该模型。</p>
  </section>

  <section class="panel">
    <h2>② 路由 routing（质量链按顺序降级；显示名称 + 实际模型 id）</h2>
    <div class="toolbar">
      <select id="chain-select"></select>
      <span id="chain-title" style="font-size:12px;color:#666"></span>
    </div>
    <div class="scroll">
      <table class="grid" id="chain-table" style="min-width:640px">
        <thead><tr>
          <th style="width:50px">顺序</th>
          <th style="width:200px">名称 name</th>
          <th>模型 model id</th>
          <th style="width:180px">操作</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div id="chain-add-row">
      <span>加入当前链尾：</span>
      <select id="chain-add-select"></select>
      <button class="mini ok" onclick="addToChain()">加入</button>
    </div>
    <h3 style="margin:14px 0 6px;font-size:13px;color:#444">多模型任务 multi_tasks（勾选的任务会并发多模型分析 + agnes 汇总成一份）</h3>
    <p class="hint" style="margin:2px 0 8px">每项 = 一种分析意图：勾选后该任务会同时让多个模型分析再汇总，结果更全面但更耗额度。</p>
    <div id="multi-tasks"></div>
  </section>

  <section class="panel">
    <h2>③ 缓存 cache</h2>
    <div class="frow">
      <label class="ck"><input type="checkbox" id="cache-enabled" onchange="cfg.cache.enabled=this.checked; dirty()"> 启用答案缓存</label>
    </div>
    <div class="frow">
      <label>有效期秒 ttl_seconds<input type="number" id="cache-ttl" min="1" value="3600" style="width:120px" onchange="cfg.cache.ttl_seconds=parseInt(this.value)||3600; dirty()"></label>
      <label>最大条目 max_entries<input type="number" id="cache-max" min="1" value="200" style="width:120px" onchange="cfg.cache.max_entries=parseInt(this.value)||200; dirty()"></label>
      <label>缓存目录（留空=config 同目录）<input type="text" id="cache-dir" placeholder="" style="width:280px" oninput="cfg.cache.dir=this.value; dirty()"></label>
    </div>
    <p class="hint">缓存开关即时生效，保存后写入 config.json；命中缓存不消耗 API 额度。</p>
  </section>

  <section class="panel">
    <h2>④ 分组与额度</h2>
    <div class="frow">
      <label>组间并发上限 max_multi_groups（-1=不限）
        <input type="number" id="g-max-multi" min="-1" value="3" style="width:100px"
               onchange="cfg.routing=(cfg.routing||{}); cfg.routing.max_multi_groups=parseInt(this.value)||3; dirty()"></label>
      <label>日额度缓冲 reserve_ratio（0~0.9）
        <input type="number" id="g-reserve" min="0" max="0.9" step="0.05" value="0.2" style="width:76px"
               onchange="cfg.quota=(cfg.quota||{}); cfg.quota.reserve_ratio=Math.min(0.9,Math.max(0,parseFloat(this.value)||0)); dirty()"></label>
      <label>熔断默认秒
        <input type="number" id="g-cd-def" min="0" value="60" style="width:76px"
               onchange="cfg.quota=(cfg.quota||{}); cfg.quota.cooldown_default_sec=parseInt(this.value)||0; dirty()"></label>
      <label>熔断上限秒
        <input type="number" id="g-cd-max" min="0" value="300" style="width:76px"
               onchange="cfg.quota=(cfg.quota||{}); cfg.quota.cooldown_max_sec=parseInt(this.value)||0; dirty()"></label>
      <button class="mini ok" onclick="refreshStatus()">刷新状态</button>
      <span class="hint" style="padding-bottom:8px">行内修改后点右上「保存配置」生效；开关/限额/限速/冷却均可改。</span>
    </div>
    <div class="scroll">
      <table class="grid" id="group-table" style="min-width:1150px">
        <thead><tr>
          <th style="width:60px" class="c" title="组是否参与多模型任务(multi_tasks)并发">多模型</th>
          <th style="width:140px">分组 group</th>
          <th>成员供应商（model）</th>
          <th style="width:118px" title="全组/自然日调用上限，0=不限且不计">组日限额</th>
          <th style="width:118px" title="单模型/自然日调用上限，0=不限且不计">模型日限额</th>
          <th style="width:128px" title="月 token 免费额度，0=不限">月限 token</th>
          <th style="width:104px" title="组内调用最小间隔秒">间隔秒</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="frow" style="margin-top:8px">
      <span style="font-size:13px;color:#333">手动加冷却：</span>
      <select id="cd-provider" style="min-width:170px"></select>
      <label>秒数<input type="number" id="cd-seconds" value="60" min="1" style="width:76px"></label>
      <button class="mini ok" onclick="addCooldown()">添加</button>
      <span class="hint" style="padding-bottom:8px">用于临时停用某供应商；下方状态区每条冷却可单独「清除」。</span>
    </div>
    <div id="quota-status" style="margin-top:6px;font-size:12px;color:#555;line-height:1.7"></div>
    <p class="hint">
      说明：多模型=该组是否参与「多模型任务」并发；providers 表里每行还有单独「多模型」开关，两者都开才参与并发。<br>
      组日限额/模型日限额 = 本地自然日调用上限（0=不限且不计），熔断生效值 = 限值 ×（1 − reserve_ratio）。<br>
      月限 token：0=不限；调用成功后按响应 usage 累计、跨自然月清零。<br>
      <b>internai（书生浦语）</b>：官方「赠送额度先用、耗尽自动扣余额」，默认已设月限 90000000 token + 2s 限速，请按控制台剩余赠送额度下调。
    </p>
  </section>

</div>
<script>const ALL_TASKS = ["general","ocr","error","ui","chart","compare","document","math_stem","detail","video","unknown"];
const TASK_INFO = {
  general:   { zh: '通用',        desc: '综合看图：描述画面内容与要点（默认）' },
  ocr:       { zh: '文字提取',    desc: 'OCR：逐字提取图中所有文字，适合截图/文档照片' },
  error:     { zh: '报错定位',    desc: '从报错/异常截图里定位错误信息与原因' },
  ui:        { zh: '界面分析',    desc: '分析软件/网页/App 界面布局与 UI 问题' },
  chart:     { zh: '图表解读',    desc: '解读数据图表、流程图、报表（走 GLM 特化链）' },
  compare:   { zh: '多图对比',    desc: '对比多张图片，指出异同与变化' },
  document:  { zh: '文档结构化',  desc: '提取文档/扫描件/表格的结构化内容' },
  math_stem: { zh: '数理题',      desc: '解答图中数学/理科题目并给出步骤（走 GLM 特化链）' },
  detail:    { zh: '细节观察',    desc: '放大观察图片中的细节与微小差异' },
  video:     { zh: '视频帧',      desc: '描述视频抽帧/截图的画面内容' },
  unknown:   { zh: '未知兜底',    desc: '无法归类的兜底项，等同通用' }
};
function taskLabel(k){ const i = TASK_INFO[k]; return i ? (i.zh + ' ' + k) : k; }
function chainLabel(k){
  if (k === 'default_chain') return '默认链 default_chain';
  return taskLabel(k) + '（特化）';
}
let cfg = null;
let curChainKey = 'default_chain';
let dirtyFlag = false;

function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function msg(s){ document.getElementById('msg').textContent = s; }
function dirty(){ dirtyFlag = true; document.getElementById('btn-save').textContent = '保存配置 *'; }
function cleanDirty(){ dirtyFlag = false; document.getElementById('btn-save').textContent = '保存配置'; }

function keyStateOf(v){
  v = v || '';
  if (v === 'empty' || v === '') return {cls:'b-empty', txt:'未设置', ph:'输入 API Key'};
  if (v === 'dpapi' || v.indexOf('dpapi:') === 0) return {cls:'b-dpapi', txt:'已加密', ph:'留空不变 / 输入新 key'};
  if (v === 'plain') return {cls:'b-plain', txt:'明文', ph:'已是明文，可输入覆盖'};
  return {cls:'b-new', txt:'新输入待保存', ph:'输入 API Key'};
}
function setBadge(elm, st){ elm.className = 'badge ' + st.cls; elm.textContent = st.txt; elm.nextElementSibling.placeholder = st.ph; }
function parseModel(v){ const parts = String(v||'').split(',').map(function(s){ return s.trim(); }).filter(Boolean); return parts.length === 1 ? parts[0] : parts; }
function modelStr(m){ return Array.isArray(m) ? m.join(', ') : String(m||''); }
function providerByName(name){ return cfg.providers.find(function(p){ return p.name === name; }); }
function modelLabel(name){
  const p = providerByName(name);
  if (!p) return '<span class="missing">未找到供应商</span>';
  return esc(modelStr(p.model)) || '<span class="missing">（无 model）</span>';
}
function ensureCache(){
  if (cfg.cache) return;
  cfg.cache = { enabled: true, ttl_seconds: 3600, max_entries: 200, dir: '' };
}
function getCurChain(){
  if (curChainKey === 'default_chain') return cfg.routing.default_chain;
  return (cfg.routing.overrides || {})[curChainKey];
}

/* ---------- providers ---------- */
function providerRow(p){
  const tr = document.createElement('tr');
  tr.dataset.name = p.name;
  const ks = keyStateOf(p.api_key);
  const tdHtml =
    '<td class="c"><input type="checkbox" class="p-en" title="是否启用"' + (p.enabled ? ' checked' : '') + '></td>' +
    '<td class="c"><input type="checkbox" class="p-multi" title="是否参与多模型任务(multi_tasks)并发"' + (p.multi !== false ? ' checked' : '') + '></td>' +
    '<td><span class="pname">' + esc(p.name) + '</span></td>' +
    '<td><input type="text" class="p-group" value="' + esc(p.group || '') + '" placeholder="厂商"></td>' +
    '<td><input type="text" class="p-model" value="' + esc(modelStr(p.model)) + '" placeholder="多候选逗号分隔"></td>' +
    '<td><input type="text" class="p-base" value="' + esc(p.base_url || '') + '" placeholder="https://..."></td>' +
    '<td><div class="p-keycell" style="display:flex;flex-direction:column;gap:3px;align-items:stretch">' +
        '<span class="badge ' + ks.cls + '">' + ks.txt + '</span>' +
        '<input type="password" class="p-key" placeholder="' + ks.ph + '"></div></td>' +
    '<td><input type="number" class="p-timeout" value="' + esc(p.timeout == null ? 60 : p.timeout) + '" min="1"></td>' +
    '<td style="white-space:nowrap"><button class="mini ok b-test">测试</button><button class="mini danger b-del">删除</button></td>';
  tr.innerHTML = tdHtml;

  const en = tr.querySelector('.p-en');
  en.onchange = function(){ p.enabled = en.checked; dirty(); };

  const mu = tr.querySelector('.p-multi');
  mu.onchange = function(){ p.multi = mu.checked; dirty(); };

  tr.querySelector('.p-group').oninput = function(e){ p.group = e.target.value.trim(); dirty(); };
  tr.querySelector('.p-model').oninput = function(e){ p.model = parseModel(e.target.value); dirty(); };
  tr.querySelector('.p-base').oninput = function(e){ p.base_url = e.target.value.trim(); dirty(); };
  tr.querySelector('.p-timeout').oninput = function(e){ const n = parseInt(e.target.value, 10); p.timeout = (isNaN(n) || n < 1) ? 60 : n; dirty(); };

  const keyInput = tr.querySelector('.p-key');
  const badge = tr.querySelector('.badge');
  const origKey = p.api_key;
  keyInput.oninput = function(){
    const v = keyInput.value;
    if (v) { p.api_key = v; setBadge(badge, keyStateOf(v)); }
    else { p.api_key = origKey; setBadge(badge, keyStateOf(origKey)); }
    dirty();
  };

  tr.querySelector('.b-test').onclick = function(){ testProvider(p.name); };
  tr.querySelector('.b-del').onclick = function(){ deleteProvider(p.name); };
  return tr;
}

function renderProviders(){
  const tb = document.querySelector('#provider-table tbody');
  tb.innerHTML = '';
  const arr = cfg.providers.map(function(p){ return p; });
  arr.sort(function(a, b){
    const g = (a.group || '').localeCompare(b.group || '');
    if (g !== 0) return g;
    return modelStr(a.model).localeCompare(modelStr(b.model));
  });
  if (!arr.length){
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="9" style="color:#8a94a6;text-align:center;padding:16px">暂无供应商，点右上「+ 添加供应商」添加</td>';
    tb.appendChild(tr);
  } else {
    arr.forEach(function(p){ tb.appendChild(providerRow(p)); });
  }
}

function toggleAddForm(){
  const f = document.getElementById('add-form');
  f.style.display = (f.style.display === 'none') ? 'block' : 'none';
}

function addProvider(){
  const name = document.getElementById('a-name').value.trim();
  if (!name){ msg('请输入 name'); return; }
  if (cfg.providers.some(function(p){ return p.name === name; })){ msg('name 已存在: ' + name); return; }
  const group = document.getElementById('a-group').value.trim() || 'other';
  const base_url = document.getElementById('a-base').value.trim();
  const m = parseModel(document.getElementById('a-model').value);
  const key = document.getElementById('a-key').value.trim();
  const timeout = parseInt(document.getElementById('a-timeout').value, 10) || 60;
  const enabled = document.getElementById('a-enabled').checked;
  const multi = document.getElementById('a-multi').checked;
  const joinChain = document.getElementById('a-chain').checked;
  cfg.providers.push({
    name: name, group: group, base_url: base_url,
    model: m, api_key: key || '', input_mode: 'base64', timeout: timeout, enabled: enabled, multi: multi
  });
  if (joinChain && cfg.routing.default_chain.indexOf(name) < 0) cfg.routing.default_chain.push(name);
  ['a-name','a-group','a-base','a-model','a-key'].forEach(function(id){ document.getElementById(id).value = ''; });
  document.getElementById('a-timeout').value = '60';
  document.getElementById('a-enabled').checked = true;
  document.getElementById('a-multi').checked = true;
  document.getElementById('a-chain').checked = false;
  document.getElementById('add-form').style.display = 'none';
  renderAll();
  msg('已添加 ' + name + '，点「保存配置」写入 config.json');
  dirty();
}

async function testProvider(name){
  msg('正在保存改动并测试 ' + name + ' ...');
  const saved = await saveCore();
  if (!saved) return;
  try {
    const r = await fetch('/api/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name }) });
    const d = await r.json();
    if (!d.ok){ msg('测试失败: ' + d.error); return; }
    const res = d.result;
    msg(res.ok ? ('测试通过 ' + name + '（' + res.elapsed + 's）：' + (res.reply || '')) : ('测试失败 ' + name + '：' + (res.error || '')));
  } catch (e){ msg('测试异常: ' + e); }
}

async function deleteProvider(name){
  const p = providerByName(name);
  if (!p) return;
  const a = Math.floor(Math.random() * 9) + 1;
  const b = Math.floor(Math.random() * (10 - a)) + 1;
  let ans, q;
  if (Math.random() < 0.5){ const big = Math.max(a, b), small = Math.min(a, b); ans = big - small; q = big + ' - ' + small; }
  else { ans = a + b; q = a + ' + ' + b; }
  const input = prompt('确认删除「' + p.name + '」？请计算：' + q + ' = ?（答对才删除）');
  if (input === null) return;
  if (parseInt(input, 10) !== ans){ msg('答错，未删除'); return; }
  cfg.providers = cfg.providers.filter(function(x){ return x.name !== name; });
  [cfg.routing.default_chain].concat(Object.values(cfg.routing.overrides || {})).forEach(function(ch){
    if (Array.isArray(ch)){ for (let k = ch.length - 1; k >= 0; k--){ if (ch[k] === name) ch.splice(k, 1); } }
  });
  renderAll();
  msg('已删除 ' + name + '，点「保存配置」写入 config.json');
  dirty();
}

/* ---------- routing ---------- */
function renderChain(){
  const chain = getCurChain() || [];
  const tb = document.querySelector('#chain-table tbody');
  tb.innerHTML = '';
  const title = document.getElementById('chain-title');
  title.textContent = chainLabel(curChainKey) + '（' + chain.length + ' 个）';
  if (!chain.length){
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="4" style="color:#8a94a6;text-align:center;padding:12px">（链为空，从下方把供应商加入链尾）</td>';
    tb.appendChild(tr);
    return;
  }
  chain.forEach(function(name, i){
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td class="chainnum">' + (i + 1) + '</td>' +
      '<td><span class="pname">' + esc(name) + '</span></td>' +
      '<td>' + modelLabel(name) + '</td>' +
      '<td style="white-space:nowrap">' +
        '<button class="mini b-up"' + (i === 0 ? ' disabled' : '') + '>↑上移</button>' +
        '<button class="mini b-down"' + (i === chain.length - 1 ? ' disabled' : '') + '>↓下移</button>' +
        '<button class="mini danger b-rm">移除</button></td>';
    tr.querySelector('.b-up').onclick = function(){ moveInChain(name, -1); };
    tr.querySelector('.b-down').onclick = function(){ moveInChain(name, 1); };
    tr.querySelector('.b-rm').onclick = function(){ removeFromChain(name); };
    tb.appendChild(tr);
  });
}

function renderChainAddSelect(){
  const sel = document.getElementById('chain-add-select');
  sel.innerHTML = '';
  const chain = getCurChain() || [];
  const sorted = cfg.providers.slice().sort(function(a, b){
    const g = (a.group || '').localeCompare(b.group || '');
    if (g !== 0) return g;
    return modelStr(a.model).localeCompare(modelStr(b.model));
  });
  const avail = sorted.filter(function(p){ return chain.indexOf(p.name) < 0; });
  if (!avail.length){
    const o = document.createElement('option');
    o.value = ''; o.textContent = '（没有可加入的供应商）';
    sel.appendChild(o);
    return;
  }
  avail.forEach(function(p){
    const o = document.createElement('option');
    o.value = p.name;
    o.textContent = p.name + '  [' + (p.group || '') + '] ' + modelStr(p.model);
    sel.appendChild(o);
  });
}

function renderMulti(){
  const mt = document.getElementById('multi-tasks');
  mt.innerHTML = '';
  if (!cfg.routing.multi_tasks) cfg.routing.multi_tasks = [];
  ALL_TASKS.forEach(function(t){
    const info = TASK_INFO[t] || { zh: t, desc: '' };
    const card = document.createElement('label');
    card.className = 'mt-card';
    card.title = t + '：' + info.desc;
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = cfg.routing.multi_tasks.indexOf(t) >= 0;
    cb.onchange = function(){
      if (cb.checked && cfg.routing.multi_tasks.indexOf(t) < 0) cfg.routing.multi_tasks.push(t);
      if (!cb.checked) cfg.routing.multi_tasks = cfg.routing.multi_tasks.filter(function(x){ return x !== t; });
      dirty();
    };
    const zh = document.createElement('span');
    zh.textContent = info.zh;
    const en = document.createElement('span');
    en.className = 'mt-en';
    en.textContent = t;
    const desc = document.createElement('span');
    desc.className = 'mt-desc';
    desc.textContent = info.desc;
    card.appendChild(cb);
    card.appendChild(zh);
    card.appendChild(en);
    card.appendChild(desc);
    mt.appendChild(card);
  });
}

function renderChainSelect(){
  const sel = document.getElementById('chain-select');
  const ov = cfg.routing.overrides || {};
  const keys = ['default_chain'].concat(Object.keys(ov));
  if (keys.indexOf(curChainKey) < 0) curChainKey = 'default_chain';
  sel.innerHTML = '';
  keys.forEach(function(k){
    const o = document.createElement('option');
    o.value = k;
    o.textContent = chainLabel(k);
    sel.appendChild(o);
  });
  sel.value = curChainKey;
}
function renderRouting(){
  renderChainSelect();
  renderChain();
  renderChainAddSelect();
  renderMulti();
}

function moveInChain(name, d){
  const chain = getCurChain();
  const i = chain.indexOf(name);
  const j = i + d;
  if (i < 0 || j < 0 || j >= chain.length) return;
  const tmp = chain[i]; chain[i] = chain[j]; chain[j] = tmp;
  renderChain(); renderChainAddSelect();
  dirty();
}
function removeFromChain(name){
  const chain = getCurChain();
  const i = chain.indexOf(name);
  if (i < 0) return;
  chain.splice(i, 1);
  renderChain(); renderChainAddSelect();
  dirty();
}
function addToChain(){
  const sel = document.getElementById('chain-add-select');
  const name = sel.value;
  if (!name) return;
  const chain = getCurChain();
  if (chain.indexOf(name) < 0) chain.push(name);
  renderChain(); renderChainAddSelect();
  dirty();
}

/* ---------- cache ---------- */
function renderCache(){
  const c = cfg.cache || {};
  document.getElementById('cache-enabled').checked = !!c.enabled;
  document.getElementById('cache-ttl').value = c.ttl_seconds || 3600;
  document.getElementById('cache-max').value = c.max_entries || 200;
  document.getElementById('cache-dir').value = c.dir || '';
}

/* ---------- groups / quota ---------- */
const GROUP_ZH = { modelscope: '魔搭', glm: '智谱', agnes: 'Agnes', internai: '书生浦语' };
function groupZh(g){ return GROUP_ZH[g] || g; }
function ensureGroups(){
  if (!cfg.groups || typeof cfg.groups !== 'object') cfg.groups = {};
  const byg = {};
  cfg.providers.forEach(function(p){ const g = (p.group || 'other').trim() || 'other'; (byg[g] = byg[g] || []).push(p); });
  Object.keys(byg).forEach(function(g){
    let e = cfg.groups[g];
    if (!e || typeof e !== 'object'){ e = {}; cfg.groups[g] = e; }
    if (typeof e.multi !== 'boolean') e.multi = true;
    if (e.daily_group_limit === undefined || e.daily_group_limit === null) e.daily_group_limit = 0;
    if (e.daily_model_limit === undefined || e.daily_model_limit === null) e.daily_model_limit = 0;
    if (e.min_interval_sec === undefined || e.min_interval_sec === null) e.min_interval_sec = 0;
    if (e.monthly_token_limit === undefined || e.monthly_token_limit === null) e.monthly_token_limit = 0;
  });
}
function renderGroups(){
  ensureGroups();
  const r = cfg.routing || (cfg.routing = {});
  const q = cfg.quota || (cfg.quota = {});
  document.getElementById('g-max-multi').value = (r.max_multi_groups == null ? 3 : r.max_multi_groups);
  document.getElementById('g-reserve').value = (q.reserve_ratio == null ? 0.2 : q.reserve_ratio);
  document.getElementById('g-cd-def').value = (q.cooldown_default_sec == null ? 60 : q.cooldown_default_sec);
  document.getElementById('g-cd-max').value = (q.cooldown_max_sec == null ? 300 : q.cooldown_max_sec);
  const byg = {};
  cfg.providers.forEach(function(p){ const g = (p.group || 'other').trim() || 'other'; (byg[g] = byg[g] || []).push(p); });
  const tb = document.querySelector('#group-table tbody');
  tb.innerHTML = '';
  Object.keys(byg).sort().forEach(function(g){
    const e = cfg.groups[g];
    const tr = document.createElement('tr');
    const td0 = document.createElement('td'); td0.className = 'c';
    const cb0 = document.createElement('input'); cb0.type = 'checkbox';
    cb0.checked = e.multi !== false;
    cb0.title = '组是否参与多模型并发';
    cb0.onchange = function(){ e.multi = cb0.checked; dirty(); };
    td0.appendChild(cb0);
    const td1 = document.createElement('td');
    const sp = document.createElement('span'); sp.className = 'pname'; sp.textContent = groupZh(g) + ' ' + g;
    td1.appendChild(sp);
    const td2 = document.createElement('td');
    td2.style.fontSize = '12px';
    td2.textContent = byg[g].map(function(p){
      const flags = [];
      if (p.enabled === false) flags.push('关');
      if (p.multi === false) flags.push('多模型关');
      return p.name + ' [' + modelStr(p.model) + ']' + (flags.length ? '（' + flags.join('、') + '）' : '');
    }).join('；');
    const td3 = document.createElement('td');
    const in3 = document.createElement('input'); in3.type = 'number'; in3.min = '0'; in3.step = '100';
    in3.value = e.daily_group_limit || 0;
    in3.title = '全组/自然日调用上限，0=不限且不计';
    in3.onchange = function(){ e.daily_group_limit = Math.max(0, parseInt(this.value) || 0); dirty(); };
    td3.appendChild(in3);
    const td4 = document.createElement('td');
    const in4 = document.createElement('input'); in4.type = 'number'; in4.min = '0'; in4.step = '100';
    in4.value = e.daily_model_limit || 0;
    in4.title = '单模型/自然日调用上限，0=不限且不计';
    in4.onchange = function(){ e.daily_model_limit = Math.max(0, parseInt(this.value) || 0); dirty(); };
    td4.appendChild(in4);
    const td5 = document.createElement('td');
    const in5 = document.createElement('input'); in5.type = 'number'; in5.min = '0'; in5.step = '1000000';
    in5.value = e.monthly_token_limit || 0;
    in5.title = '月 token 免费额度，0=不限';
    in5.onchange = function(){ e.monthly_token_limit = Math.max(0, parseInt(this.value) || 0); dirty(); };
    td5.appendChild(in5);
    const td6 = document.createElement('td');
    const in6 = document.createElement('input'); in6.type = 'number'; in6.min = '0'; in6.step = '0.5';
    in6.value = e.min_interval_sec || 0;
    in6.title = '组内调用最小间隔秒';
    in6.onchange = function(){ e.min_interval_sec = Math.max(0, parseFloat(this.value) || 0); dirty(); };
    td6.appendChild(in6);
    tr.appendChild(td0); tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(td4); tr.appendChild(td5); tr.appendChild(td6);
    tb.appendChild(tr);
  });
}
async function refreshStatus(){
  const box = document.getElementById('quota-status');
  box.textContent = '额度/冷却状态加载中…';
  const sel = document.getElementById('cd-provider');
  if (sel && sel.options.length === 0 && cfg && cfg.providers){
    cfg.providers.forEach(function(p){
      const o = document.createElement('option');
      o.value = p.name; o.textContent = p.name + '（' + (p.group || '') + '）';
      sel.appendChild(o);
    });
  }
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    if (!d.ok){ box.textContent = '状态获取失败：' + (d.error || ''); return; }
    let h = '';
    h += '<b>冷却</b>：';
    const cd = d.cooldowns || {};
    const keys = Object.keys(cd);
    if (keys.length){
      h += keys.map(function(k){
        return esc(k) + ' 剩余 ' + cd[k] + 's <button class="mini" onclick="clearCooldown(\'' + esc(k) + '\')">清除</button>';
      }).join('　');
      h += ' <button class="mini danger" onclick="clearCooldownAll()">清除全部冷却</button>';
    } else {
      h += '无';
    }
    h += '<br>';
    (d.groups || []).forEach(function(g){
      h += '<b>' + esc(groupZh(g.name) + ' ' + g.name) + '</b>（' + g.members.map(function(m){ return m.name; }).join('、') + '）：';
      if (g.daily_group_limit > 0 || g.daily_model_limit > 0){
        h += '今日组池 ' + (g.group_used || 0) + '（余 ' + g.group_remaining + '，熔断线 ' + g.group_effective + '）';
        if (g.daily_model_limit > 0) h += '；单模型日限 ' + g.daily_model_limit;
      }
      if (g.monthly_token_limit > 0){
        h += (g.daily_group_limit > 0 || g.daily_model_limit > 0 ? '；' : '') + '本月已用 ' + (g.month_used || 0) + '/' + g.monthly_token_limit + ' token';
        if (g.month_remaining != null) h += '（余 ' + g.month_remaining + '）';
      }
      h += '<br>';
    });
    h += '<span style="color:#8a94a6">state 文件：' + esc(d.state_file) + '</span>';
    box.innerHTML = h;
  } catch (e){
    box.textContent = '状态获取异常：' + e;
  }
}
async function cooldownPost(name, seconds){
  try {
    await fetch('/api/cooldown', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name, seconds: seconds }) });
  } catch (e) {}
}
async function addCooldown(){
  const name = document.getElementById('cd-provider').value;
  if (!name){ msg('请选择供应商'); return; }
  const sec = Math.max(1, parseInt(document.getElementById('cd-seconds').value, 10) || 60);
  await cooldownPost(name, sec);
  refreshStatus();
  msg('已为 ' + name + ' 设置冷却 ' + sec + 's');
}
async function clearCooldown(name){
  await cooldownPost(name, 0);
  refreshStatus();
}
async function clearCooldownAll(){
  try { await fetch('/api/clear_cooldown', { method: 'POST' }); refreshStatus(); } catch (e) {}
}

/* ---------- load / save ---------- */
function renderAll(){
  renderProviders();
  renderRouting();
  renderCache();
  renderGroups();
}

async function loadConfig(){
  try {
    const r = await fetch('/api/config');
    const d = await r.json();
    if (!d.ok){ msg('加载失败: ' + (d.error || '')); return false; }
    cfg = d.config;
    ensureCache();
    ensureGroups();
    if (!cfg.routing.multi_tasks) cfg.routing.multi_tasks = [];
    renderAll();
    cleanDirty();
    refreshStatus();
    return true;
  } catch (e){ msg('加载异常: ' + e); return false; }
}

async function saveCore(){
  if (!cfg) return false;
  try {
    const r = await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config: cfg }) });
    const d = await r.json();
    if (!d.ok){ msg('保存失败: ' + (d.error || '')); return false; }
    await loadConfig();
    msg('已保存到 config.json');
    return true;
  } catch (e){ msg('保存异常: ' + e); return false; }
}
async function save(){ await saveCore(); }

async function shutdown(){
  try { await fetch('/api/shutdown'); } catch (e) {}
  msg('已退出，可关闭此页面');
}

document.getElementById('chain-select').onchange = function(e){
  curChainKey = e.target.value;
  renderChain();
  renderChainAddSelect();
};

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
        cfg = json.load(f)
    if _normalize_cfg is not None:
        try:
            cfg = _normalize_cfg(cfg)
        except Exception:
            pass
    return cfg
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


def _status_payload(cfg):
    """组装分组/额度/冷却状态（不含任何明文 key）。"""
    groups = {}
    for p in cfg.get("providers", []):
        groups.setdefault(str(p.get("group", "other")), []).append(p)
    qcfg = cfg.get("quota") or {}
    out = {
        "ok": True,
        "quota_enabled": bool(qcfg.get("enabled", True)),
        "state_file": _quota_state_file(cfg) if _quota_state_file else "",
        "cooldowns": {},
        "groups": [],
    }
    quota = None
    if _make_quota is not None:
        try:
            quota = _make_quota(cfg)
        except Exception:
            quota = None
    if quota is not None:
        try:
            out["cooldowns"] = {k: round(v, 1) for k, v in quota.cooldowns_snapshot().items()}
        except Exception:
            pass
    for gname in sorted(groups.keys()):
        pros = groups[gname]
        gc = (cfg.get("groups") or {}).get(gname) or {}
        members = []
        for p in pros:
            m = p.get("model")
            if isinstance(m, list):
                m = m[0] if m else ""
            members.append({
                "name": p.get("name"), "enabled": bool(p.get("enabled", True)),
                "multi": bool(p.get("multi", True)),
                "model": m or "", "has_key": bool(p.get("api_key")),
            })
        entry = {
            "name": gname,
            "members": members,
            "multi": bool(gc.get("multi", True)),
            "daily_group_limit": int(gc.get("daily_group_limit", 0) or 0),
            "daily_model_limit": int(gc.get("daily_model_limit", 0) or 0),
            "min_interval_sec": float(gc.get("min_interval_sec", 0.0) or 0.0),
            "monthly_token_limit": int(gc.get("monthly_token_limit", 0) or 0),
        }
        if quota is not None:
            try:
                if quota.daily_active(gname):
                    entry["group_used"] = quota.group_used(gname)
                    entry["group_remaining"] = quota.group_remaining(gname)
                    entry["group_effective"] = quota.group_effective(gname)
                if quota.monthly_limit(gname) > 0:
                    entry["month_used"] = sum(quota.month_usage(gname).values())
                    entry["month_remaining"] = quota.month_remaining(gname)
            except Exception:
                pass
        out["groups"].append(entry)
    return out


def _clear_cooldowns(path):
    """清空本地状态文件中的冷却记录；不影响额度计数。"""
    cfg = load_config(path)
    if _quota_state_file is None:
        return {"ok": False, "error": "quota 模块不可用"}
    state_file = _quota_state_file(cfg)
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    if not isinstance(s, dict):
        s = {}
    s["cooldowns"] = {}
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    return {"ok": True}


def _set_cooldown(path, name, seconds):
    """设置/清除单个 provider 冷却；seconds<=0 为清除。只改 cooldowns 键，不动额度计数。"""
    if _quota_state_file is None or _make_quota is None:
        return {"ok": False, "error": "quota 模块不可用"}
    try:
        quota = _make_quota(load_config(path))
    except Exception as e:
        return {"ok": False, "error": "quota 初始化失败: %s" % e}
    try:
        seconds = float(seconds)
    except Exception:
        seconds = 0.0
    try:
        if seconds <= 0:
            quota.clear_cooldown(name)
        else:
            quota.set_cooldown(name, seconds)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


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
        elif self.path == "/api/status":
            try:
                self._json(_status_payload(load_config(self.config_path)))
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
        elif self.path == "/api/cooldown":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                name = data.get("name")
                if not name:
                    self._json({"ok": False, "error": "缺少 name"}, 400)
                else:
                    self._json(_set_cooldown(self.config_path, name, data.get("seconds", 0)))
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif self.path == "/api/clear_cooldown":
            try:
                self._json(_clear_cooldowns(self.config_path))
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

