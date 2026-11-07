# CockpitAgentOS 座舱智能体模拟器

在 Mac 上模拟一套 Agentic 座舱语音助手，包括车辆状态、车控协议、模拟 App、安全闸门和双工语音，并附带一套评测框架。
后端用 FastAPI，前端用 React + Vite。

## 功能

**一句话的处理流程：**

```
语音 / 文字 → 端侧安全闸门 → 端侧快速通道 → 网关（正常 / 弱网 / 断网）→ 云端规划器 → 执行器 → 车辆状态
```

- **对话控车**：用自然语言控制空调、车窗、座椅、灯光、遮阳帘等，一句话可以带多个意图。规划器调用工具执行，再根据状态码组织回复。执行失败（如车速过高、条件不满足）时会重新规划。
- **安全闸门**：命中驾驶类、违法有害或未上线能力时，在端侧直接拒绝，不调用工具。模型自己拼出来的驾驶类调用也会被拦下。
- **快速通道**：“打开主驾车窗”这类高频单意图指令用规则整句匹配，直接执行，不经过大模型。
- **弱网 / 断网**：顶栏可以切换网络状态。断网时只有快速通道可用；弱网时调用模型前会加延迟。
- **二次确认**：紧急呼叫、卸载应用、美团下单等操作会先挂起，等用户说“是 / 否”或在屏幕上点确认。
- **模拟 App**：导航（SVG 地图、路线、到达提醒）、音乐（曲库、切歌、收藏）、美团外卖（选店、下单、订单状态）、电话，以及搜索、车辆百科、智能家居等子代理。所有数据都是写死的模拟数据。触屏操作和语音操作走同一条执行路径。
- **模拟时钟与定时任务**：可以快进时间，导航里程、播放进度和外卖订单会跟着推进。“到了提醒我”“十分钟后关空调”这类任务到点后自动触发。
- **乘员与音区**：可以设置每个座位有没有人、是谁，以及说话人在哪个座位。“把我这边窗户打开”会作用到说话人那一侧。
- **双工语音**：豆包 S2S 负责听、说、判停和打断，车控仍然交给规划器。回复以规划器为准，豆包 S2S 只负责念出来，不会一问两答。
- **回复来源标记**：每条回复上方标明是谁回答的，例如“云端大模型 · deepseek-flash”“端侧快速通道”“端侧安全闸门”“豆包 S2S · 闲聊”“🔊 豆包 S2S 播报”。
- **链路追踪**：每轮都显示各环节（闸门、快速通道、网关、大模型、执行器）的耗时和结果，本次改动的车况字段会高亮。
- **手机页**：`/phone` 模拟手机 App，可以远程开空调、锁车、关窗、推送目的地到车机。
- **体验脚本**：载入预设场景后，可以逐句或自动播放对话，用来演示。
- **调试面板**：由协议自动生成，可以不经过大脑，直接调用任意指令。
- **评测**：1575 条用例分 6 个集合，按断言自动判分，生成 HTML 报告。可以对比不同模型或提示词逐条的通过情况。

## 模块

### 后端 `backend/`

| 模块 | 作用 |
|---|---|
| `app/` | FastAPI 服务：WebSocket（对话、车况推送、语音音频）、HTTP 接口（协议、App 数据、体验脚本、手机页） |
| `brain/` | 大脑：`PlannerAgent`（DeepSeek 规划器，流式输出，最多 4 回合重规划）、确认流程、会话记忆、提示词组装 |
| `llm/` | 大模型客户端（OpenAI 兼容接口）和测试用的脚本化假模型 |
| `edge/` | 端侧环节：安全闸门 `gate.py`、快速通道 `fast_path.py`、网络网关 `gateway.py` |
| `protocol/` | 车控协议：从 `config/` 的 YAML 生成工具 schema、路由表和状态码 |
| `vehicle/` | 车辆模拟：不可变的车辆状态、每条指令的 handler（带物理约束）、前置条件规则、执行器（并行 / 串行、超时、回滚） |
| `apps/` | 模拟 App 的 handler：导航、音乐、美团、电话及其他轻量页面 |
| `agents/` | 子代理：任务管理、美团点餐、车辆百科、搜索、智能家居（都是规则实现，不调大模型） |
| `voice/` | 双工语音：火山 S2S 客户端、二进制帧编解码、语音桥 `VoiceBridge`（判断该播谁的回复、处理打断） |
| `trace/` | 链路记录，以及从链路推断回复来源 |
| `clock.py` | 模拟时钟：推进时间，带动导航、播放和订单，触发到期任务 |

### 前端 `frontend/src/`

| 模块 | 作用 |
|---|---|
| `cockpit/` | 座舱主界面，分三栏：<br>左：对话框（含语音条、来源标签）<br>中：仪表、空调条、车辆俯视图、乘员面板、中控屏<br>右：体验脚本、调试面板、链路追踪、任务面板 |
| `apps/` | 中控屏里的 App 页面：导航地图、音乐、美团等 |
| `phone/` | 手机页 `/phone` |
| `lib/` | WebSocket 连接（`useCockpitSocket`）、协议类型、麦克风采集与 PCM 播放 |

### 配置与数据

| 目录 | 内容 |
|---|---|
| `config/` | 车控协议（设备、指令、参数）、状态码规则、闸门、快速通道、别名、语音参数（`voice.yaml`） |
| `data/scenarios/` | 评测用例（oral / single / hard / multi / permission / protocol，JSONL） |
| `data/demos/` | 体验脚本（YAML） |
| `data/apps/` | 模拟 App 数据：商户菜单、曲库、地图点位、联系人、百科、搜索结果 |
| `eval/` | 评测框架：判分器、Oracle / Null 基线、报告与对比、执行检查、实验用提示词 |
| `scripts/` | `dev.sh` 一键启动 |
| `doc/` | 研究文档和测试脚本源文件（已加入 `.gitignore`，不进版本库） |

## 环境要求

- Python 3.12 或更高版本，依赖用 [uv](https://docs.astral.sh/uv/) 管理
- Node.js 20 或更高版本（目前用 24 开发）

首次安装依赖：

```bash
uv sync
```

```bash
npm --prefix frontend install
```

## 配置 key

两个 key 都只放在项目根目录的 `.env` 文件里。代码只从环境变量读取，不要把 key 写进代码或配置文件。`.env` 已加入 `.gitignore`。

先从模板复制一份：

```bash
cp .env.example .env
```

```bash
chmod 600 .env
```

然后编辑 `.env`：

```ini
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的 DeepSeek key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_MODEL_PRO=deepseek-v4-pro

VOLC_S2S_API_KEY=你的火山 S2S key
# VOLC_S2S_MODEL=   可选，覆盖 config/voice.yaml 里的模型版本
```

| 变量 | 用途 | 没配会怎样 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 云端规划器（理解指令、调用工具） | 后端退回 Echo 占位，对话框顶部提示没配大模型；快速通道和安全闸门照常工作 |
| `VOLC_S2S_API_KEY` | 火山引擎端到端实时语音（豆包 S2S），负责听、说和打断 | 语音按钮不可用，文字输入不受影响 |

### 怎么拿到 key

- **DeepSeek**：在 [DeepSeek 开放平台](https://platform.deepseek.com/) 的 API keys 页面创建。
- **火山 S2S**：在火山引擎控制台开通“端到端实时语音大模型”，创建 API Key。新版控制台用单个 key 鉴权，请求头是 `X-Api-Key`。

### 其他说明

- 系统环境变量里的同名变量优先于 `.env`。
- 改完 `.env` 要重启后端才生效。
- 如果 key 曾经出现在聊天记录、截图或日志里，请到控制台轮换。

## 启动

一条命令同时启动后端（:8000）和前端（:5173），按 Ctrl+C 一起退出：

```bash
scripts/dev.sh
```

然后打开 http://localhost:5173 。

- **页面布局**：左边是对话，中间是车况和中控屏，右边是调试面板和链路追踪。
- **来源标签**：每条回复上方会标出是谁回答的，例如“云端大模型”“端侧快速通道”“豆包 S2S · 闲聊”。
- **语音**：点对话框的麦克风按钮开始。需要浏览器给麦克风权限，Chrome 或 Safari 都可以。
- **手机页**：http://localhost:5173/phone 。

也可以分开启动后端和前端：

```bash
uv run uvicorn backend.app.main:app --port 8000 --reload --reload-dir backend
```

```bash
npm --prefix frontend run dev
```

后端只在 `backend/` 有改动时自动重载。改了 `config/` 或 `data/` 后要手动重启后端。

## 测试与评测

```bash
uv run pytest
```

```bash
uv run python -m eval.run_eval --agent deepseek --set single --show-fail
```

测试不联网，也不需要 key。评测要用 `DEEPSEEK_API_KEY`，报告写在 `eval/reports/`。常用参数：`--set` 选集合（oral / single / hard / multi / permission / protocol），`--only <用例 id>` 只跑一条，`--limit N` 每个集合抽 N 条。

## 测试场景从哪来

> 测试场景 `data/scenarios/*.jsonl` 是用 `doc/v7 测试场景脚本-v1.html` 生成的，文件已经在仓库里。
> 只有想重新生成时，才需要本地有 `doc/` 目录，然后运行 `uv run python -m eval.parse_html`。
