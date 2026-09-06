---
name: shijuefenxi
description: 通用图像/视觉理解与网页综合读取。当需要“看懂”一张/多张图片，或读取一个网页的文字并识别其图片时使用——本模型无法直接接收图像 token，必须调用 scripts/analyze.py 或 scripts/read_web.py 把图片交给视觉模型转成结构化中文文字后再分析。适用：用户给出图片本地路径或 URL、网页 URL，需要描述画面、提取图中文字(OCR)、分析截图/报错图/手绘草稿/图表/UI/文档拍照/前后对比、网页内容+配图综合信息等。不适用：纯文本任务、已有现成文字无需看图、生成图片（走 imagegen）。
metadata:
  short-description: 通用图像理解 + 网页综合读取（半自动路由：任务→自动选模型组合）
---

# shijuefenxi（视觉分析）

把本地/远程图片交给视觉大模型，返回结构化中文描述；也可读取网页文字并识别其图片。**半自动路由引擎**：你只需判断任务类型，脚本自动完成“选模型 → 调模型 → 失败分类降级 → 额度熔断 → 多模型汇总”。

## 何时用
- 用户让你看某张图、分析截图、识别报错、理解手绘草稿、读图表/UI/文档照片、比较前后截图等。
- 用户给一个网页 URL，想让你“读网页 + 看里面的图”并给出综合信息。
- 只要需要从图像中获取信息，就先走本技能，**不要靠猜**。

## 核心流程（主模型只做一件事：判断 --task）
1. 判断用户需求类型，选择 `--task`（12 类）：
   - `ocr` 逐字提取文字；`document` 长文档结构化；`error` 报错定位；
   - `math_stem` 数学/科学题；`chart` 图表解读；`detail` 高精度细节；
   - `ui` 界面/截图转代码；`general` 通用描述；`compare` 多图对比；
   - `video` 视频帧/截图；`chat` 简单问答；`unknown` 兜底。
2. 拿到图片的**本地路径**或 http(s) URL。
3. 调用脚本（脚本自动按任务路由到最合适的模型组合）：
   ```bash
   python "<本技能目录>/scripts/analyze.py" "<图片路径或URL>" --task ocr -f "<关注点>"
   ```
4. 读取返回的结构化文字（关键任务已由 agnes 汇总成**一份**），据此回复用户。

## 自动路由与多模型汇总（核心）
- **厂商分组**：魔搭组（235b/8b）、智谱组（4.6v/4.1v-thinking/4v）、Agnes 组（2.5-flash）。
- **按任务选首选 + 升级链**：`ocr/document` 首选魔搭 8b；`detail/error` 首选魔搭 235b；`math_stem/chart/unknown` 首选 glm；`chat/ui/general` 首选 agnes。
- **失败按原因分类降级**：404/模型不存在→同组换模型；额度尽→跨组换厂商；429/超时→退避重试。
- **关键任务自动多模型**：`math_stem/detail/error/compare` 三厂商并发各出最强，结果交给 agnes 汇总成一份（agnes 不通则返回多份，由你兜底汇总）。
- **额度熔断**：魔搭本地按日计数（全局 2000 / 单模型 500），超限自动切 GLM/Agnes。

## 网页综合读取（文字 + 图片）
读取网页正文并识别页内图片，输出文字摘要 + 视觉分析，再由你综合成最终结论：
```bash
python "<本技能目录>/scripts/read_web.py" "<网页URL>" -f "<关注点>"
```
- `--max-images N`：最多分析几张图；`--no-images`：只读网页文字；`--json`：结构化输出。

## 参数（analyze.py）
- 位置参数：一个或多个图片路径/URL。
- `-f, --focus`：关注点。
- `--task`：任务类型（上述 12 类）。
- `--provider <name>`：强制用某个供应商（绕过路由）。
- `--multi`：强制多模型并发 + agnes 汇总；`--serial`：强制单模型串行降级。
- `--json`：输出 JSON（多模型时含 `results` 与 `summarizer`）。
- `--max-size N` / `--auto-trim` / `--brief` / `--no-downscale`：图片压缩与输出控制。
- `--config <path>`：指定配置文件。

## 配置、路由与额度
- 配置文件：`<本技能目录>/config.json`（`config.example.json` 是模板）。
- `providers`：6 个供应商，每项含 `group`、`model`（可候选数组）、`api_key`、`enabled`。
- `routing`：任务→路由矩阵（`chain` 首选/升级链 + `multi` 是否多模型）。
- `summarizer`：多模型汇总器，默认 `agnes`。
- `quota`：魔搭额度与限速（`global_daily` 2000、`per_model_daily` 500、`reserve_ratio` 0.2、`min_interval_sec` 5）。
- **魔搭换模型**：只需改 `providers` 里魔搭项的 `model` 候选数组（例如把 `Qwen/Qwen3-VL-8B-Instruct` 换成新模型名），脚本 404 自动降级到下一候选，零代码改动。

## 图形化配置工具（推荐）
双击 `<本技能目录>/配置工具.bat`，自动启动本地服务器（仅 127.0.0.1）并在浏览器打开配置界面，可编辑：
- providers：api_key / base_url / model 候选 / enabled
- routing：质量顺序（选中上移/下移）、multi_tasks 勾选
保存时自动 dpapi 加密 key 并写回 config.json。

## agent 文字打开配置界面
当用户说“打开配置 / 改 key / 改模型 / 改路由 / 改顺序 / 配置工具”时，后台启动图形化工具并自动开浏览器：
```powershell
Start-Process -WindowStyle Hidden -FilePath pythonw -ArgumentList '<本技能目录>/scripts/config_server.py','--open'
```

## 命令行兜底（无浏览器时）
```bash
python "<本技能目录>/scripts/set_key.py"
```

## key 加密（DPAPI，仅 Windows）
- key 支持明文或 `dpapi:` 加密串；加密后仅当前 Windows 用户能解。
- 一键加密 config 里所有明文 key：`python "<本技能目录>/scripts/encrypt_key.py" --encrypt-config`。
- `config.json` 与 `.quota_state.json` 已被 `.gitignore` 排除；可用环境变量 `VISION_API_KEY` 兜底单一 key。

## 安全说明（图片真实性/安全性甄别）
- 不限制 http；真正拦截的是 `file:`/`data:` 等非 http(s) 协议，以及本机/私网/元数据地址（防 SSRF）。
- 远程图片先做技术真实性校验：magic bytes 嗅探、PIL 结构校验、像素数上限、下载大小与超时限制、重定向逐跳复检。
- 脚本能校验“是不是合法图片”，不能判断图片内容真伪/AI 生成/侵权。

## 环境要求
- Python 3.8 或更高（脚本内有版本检查）。
- 无第三方依赖；PIL 可选（缺失时校验降级为 magic bytes、发送原图）。

## 注意事项
- 结果取决于所选视觉模型能力；OCR 对模糊小字/手写体可能不准，重要数字/报错码建议与原图核对。
- OCR 精度不足时：改 `--task ocr`，或调高 `--max-size`（如 2048）或 `--no-downscale` 重试。
- 输出里的【文字内容】是模型 OCR 结果，不是 100% 精确。
