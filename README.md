# cnb2api

> CNB (`cnb.cool`) NPC 聊天接口的 OpenAI 兼容反向代理，Go 实现。

逆向自 `cnb.cool` 前端 `_app.js` 的 NPC 聊天接口，将其封装为标准 OpenAI 兼容 API，
免登录即可调用 `npc/CodeBuddy(deepseek-v4-flash)` 等 CNB NPC。

---

## ⭐ Fork 实战增补（wbxl0，2026-08-25）

> 本 fork 基于 [lwjlwjlwjlwj/cnb2api](https://github.com/lwjlwjlwjlwj/cnb2api)，**不影响上游**：
> 上游更新用 `git pull upstream main` 同步。以下是真实生产环境踩坑后的增量。

### 🏗 生产架构（端口对调版）

```
客户端(ZCode/Cline等) → ToolForge(:7863) → cnb2api(:17863) → CNB 上游
                        XYML注入实现tools     Go反代+CSRF池      npc接口
```

**为什么端口对调？** 公网入口固定给 ToolForge（工具调用是刚需），
cnb2api 退居内网 17863。客户端 key 零改动。

### 🚀 快速开始（裸跑版）

```bash
# 0. 打 ToolForge 补丁（见下方坑②）
cd docker/toolforge && git apply ../../patches/toolforge-anthropic-usage.patch && cd ../..

# 1. 配置
cp config.example.json config.json        # 改 api_key / listen=:17863
cp docker/config-local.example.yaml docker/config-local.yaml

# 2. 启动（顺序无所谓，但都要起）
nohup ./cnb2api-static -config config.json > cnb2api.log 2>&1 &   # :17863
cd docker/toolforge && TOOLFORGE_CONFIG=../config-local.yaml \
  nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 7863 &

# 3. 验证
curl http://127.0.0.1:7863/v1/chat/completions -H "Authorization: Bearer <你的key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"hi"}]}'
```

### 🕳 踩坑实录（全部亲测）

#### 坑① CNB 禁原生 tools —— 必须 ToolForge 前置
直连 cnb2api 传 `tools` 参数 → CNB 上游 403 `Agent calls are not allowed`。
**解法**：ToolForge 用 XYML 提示词注入模拟 tool_calls（`config-local.yaml` 里 `native_fc: false` 是关键，别改 true）。

#### 坑② Anthropic 流式 message_delta 缺 usage —— ZCode 直接拒收
ToolForge 原版生成的 `message_delta` 事件不带 `usage` 字段。Anthropic 规范里这是必填，
ZCode 等 zod 严格校验的客户端直接报：
```
Type validation failed: ... path ["usage"] expected object, received undefined
```
**解法**：应用 [`patches/toolforge-anthropic-usage.patch`](patches/toolforge-anthropic-usage.patch)（三处生成点补 `output_tokens` 估算值）。**改完必须重启 uvicorn！**

#### 坑③ 端口规划混乱
原默认两个服务都抢 7863/8080。本 fork 统一：**cnb2api=17863、ToolForge=7863（公网面）**。
`config.json` 的 `listen` 和 `config-local.yaml` 的 `base_url` 要配套改。

#### 坑④ 公网隧道别用临时的
trycloudflare.com 临时隧道每次重启变地址 + 有频率限制。上 **Cloudflare 命名隧道**（token 方式），
域名固定一劳永逸：
```bash
cloudflared tunnel --no-autoupdate run --token <CF后台拿token>
```

#### 坑⑤ 改完 ToolForge 忘重启
Python 进程不热加载。任何 ToolForge 代码/config 改动后必须杀掉 uvicorn 重启，否则"我明明改了怎么没生效"。

#### 坑⑥ 客户端 5 秒断连 —— cnb2api 层别做 XYML 转换
**现象**：ZCode / opencode 等客户端接入后，发消息约 5 秒断开 / 完全无响应。
**根因**：在 cnb2api 的 handler 里加了「XYML 提前转标准 tool_calls」的逻辑（xyProcess）。
ToolForge 是 prompt 模式（`native_fc: false`），它**只认上游返回的原始 XYML 文本**，自己解析。
cnb2api 一旦把 XYML 从 content 里吃掉并转成 `delta.tool_calls`，ToolForge 眼里上游就是"空内容"，
于是输出空流 `end_turn` → 客户端等 5 秒没等到内容就超时断开。与用哪个客户端无关。
**解法**：cnb2api 保持**纯透传**，XYML 的解析/转换统一由 ToolForge 完成，不要在 cnb2api 层碰工具协议。
**验证**：直连 cnb2api 发带工具注入指令的请求，应看到原始 `<|XYML|tool_calls>` 文本流（而不是标准 `tool_calls` JSON）。

#### 坑⑦ opencode 终端排版挤压 —— ToolForge 输出层做换行硬化
**现象**：opencode / ZCode 等终端 markdown 渲染把**单换行和硬换行都折叠成空格**，模型回复里带 emoji 状态、
多行日志、逐条要点时（尤其"带符号图标"的内容）全部挤成一行；普通叙述段落因为用了空行所以正常。
**根因**：终端渲染器 trim 行尾空格、再折叠单个 `\n`。模型默认爱用「行尾 2 空格 + `\n`」的硬换行或普通 `\n`
分隔视觉行，全被折叠；emoji/日志/代码这类内容几乎全是这种单换行，所以"带符号就挤"。
**解法**：ToolForge 输出层加换行硬化（改动在 `docker/toolforge` 子模块）：
- `app/engine/xyml.py`：新增 `harden_line_breaks` / `harden_line_breaks_stateful` —— 代码块外每个非空行后补空行
  （`\n\n` 任何渲染器都不折叠），代码块内换行**原样保留**；流式路径用 stateful 版本跨 chunk 维护代码围栏
  状态（``` 被拆成两个 SSE chunk 也不会破坏代码块）。
- `app/stream/openai_sse.py`、`app/stream/responses_sse.py`：`stream_prompt_fc` / `stream_native_passthrough`
  输出点接入 stateful 硬化。
- `app/engine/orchestrator.py`：`_extra_instructions` 注入 4 行精简排版约束（空行分段 + 代码进代码块 + 禁行尾空格）。
**验证**：opencode 里普通多行、emoji 行（📅⏳✅）、代码块三块均正常分行；服务端实测 `苹果\n香蕉` → `苹果\n\n香蕉`。
**注意**：deepseek-v4-flash 偶尔会把 emoji 状态行**主动合并成一行输出**（中间无换行符），服务端无法切分，
属模型输出质量问题，只能靠提示词引导，重发一次通常就好了。

### 📦 本 fork 增量文件清单

| 文件 | 说明 |
|---|---|
| `patches/toolforge-anthropic-usage.patch` | 坑②修复 |
| `patches/toolforge-opencode-formatting.patch` | 坑⑦修复（排版换行硬化 + XYML 泄漏兜底 + Anthropic usage 补全，5 文件，在 `docker/toolforge` 上 `git apply`）。修复已完整推送到 `wbxl0/toolforge`，子模块指针 = `eccb12f` |
| `docker/config-local.example.yaml` | ToolForge 实战配置模板 |
| `scripts/setup_toolforge.py` | 端口对调一键脚本（交互式填 key） |

---

## 功能特性（上游原文）

- 🔓 **免登录** — 自动从 CNB 首页获取 CSRF 凭证（`csrfkey` cookie + `csrftoken` header 配对），无需账号即可调用
- 🤖 **双模型支持** — `deepseek-v4-flash` + `deepseek-v4-pro`（均透传至 CNB 上游）
- 🔄 **弹性凭证池** — 并发获取多个独立会话凭证，round-robin 轮转，天然支持并发请求
- 🔧 **自动维护** — 凭证过期自动淘汰、补充、健康检查、连续失败自动失效
- 📡 **SSE 流式** — 流式透传上游 SSE；非流式自动聚合 `content` + `reasoning_content`
- 🎭 **多协议支持** — OpenAI Chat Completions + **Anthropic Messages**（`/v1/messages`、`/anthropic/v1/messages`）+ **OpenAI Responses**（`/v1/responses`，typed SSE events）
- 🔑 **可选鉴权** — 配置 `api_key` 后需 Bearer token 访问
- 🏗 **Go 单二进制** — 无外部依赖，`go build` 即得

> ⚠️ **原生工具调用受限** — CNB 上游禁止原生 `tools` 参数（403 `Agent calls are not allowed`）。
> 客户端声明的工具会被透传，但模型返回的 `tool_calls` 不经过解析/执行/桥接。
>
> ✅ **推荐方案：搭配 ToolForge 中间件**（见下方 [Docker 编排](#docker-编排toolforge--cnb2api)）— 通过 XYML 提示词注入实现完整工具调用支持。

## 快速开始

### 1. 构建 & 配置

```bash
git clone https://github.com/wbxl0/cnb2api.git
cd cnb2api
go build -o cnb2api ./cmd/server
cp config.example.json config.json
# 编辑 config.json，设置 api_key（可留空 = 不鉴权）
```

### 2. 启动服务

```bash
./cnb2api -config config.json
```

或直接用环境变量（无需配置文件）：

```bash
CNB2API_LISTEN=:7863 CNB2API_MODEL=deepseek-v4-flash ./cnb2api
```

### 3. 验证

```bash
# 健康检查
curl -s http://localhost:7863/healthz

# 模型列表
curl -s http://localhost:7863/v1/models -H "Authorization: Bearer your-api-key"

# 聊天（非流式）
curl -s http://localhost:7863/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"你好"}]}'

# 聊天（流式）
curl -N http://localhost:7863/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","stream":true,"messages":[{"role":"user","content":"数到3"}]}'

# 凭证池状态
curl -s http://localhost:7863/pool
```

## 配置说明

```json
{
  "listen": ":7863",
  "api_key": "your-api-key",
  "model": "deepseek-v4-flash",
  "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
  "pool_min": 2,
  "pool_max": 8,
  "ttl_minutes": 30
}
```

| 字段 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `listen` | `CNB2API_LISTEN` | `:7863` | 监听地址 |
| `api_key` | `CNB2API_API_KEY` | 空 | API 鉴权 key（空=不鉴权） |
| `model` | `CNB2API_MODEL` | `deepseek-v4-flash` | 默认模型 |
| `models` | — | `[flash, pro]` | 支持的模型列表 |
| `pool_min` | `CNB2API_POOL_MIN` | `2` | 凭证池最小凭证数 |
| `pool_max` | `CNB2API_POOL_MAX` | `8` | 凭证池最大凭证数（并发上限） |
| `ttl_minutes` | `CNB2API_TTL_MINUTES` | `30` | 凭证有效期（分钟） |

### 模型说明

| 模型 | 说明 |
|---|---|
| `deepseek-v4-flash` | 默认模型 |
| `deepseek-v4-pro` | 已支持，但**上游实际仍调用 flash**（CNB 上游仅暴露 flash 接口，pro 为前端映射），输出行为与 flash 有差异（如写诗风格） |

## API

### `POST /v1/chat/completions`

OpenAI 兼容。支持 `stream`（SSE）、`max_tokens`、`temperature`、`top_p`。

### `GET /v1/models`

返回配置的模型。

### `GET /pool`

查看凭证池状态（每个凭证的 csrfkey、token 前缀、有效期、错误计数）。

### `GET /healthz`

健康检查。

## 鉴权机制（逆向说明）

CNB 的 NPC 聊天接口 `POST /ai/chat/completions` 采用 CSRF 双因子校验：

1. `GET https://cnb.cool/` 首页：
   - 响应 `Set-Cookie: csrfkey=<32位hex>`（HTTPOnly）
   - HTML 内嵌 `<script id="cnb-csrftoken-script">window.csrftoken="<40位hex>"</script>`
2. 调用 chat 接口需同时携带：
   - `Cookie: csrfkey=<csrfkey>`
   - `Header: Csrftoken: <csrftoken>`

两者必须配对（同一会话签发的）。缺失其一或值不匹配 → `401 {"errcode":16,"errmsg":"CSRF 校验失败"}`。

本项目的 `internal/auth` 包每次用独立 cookie jar 建立新会话获取配对凭证，
多个凭证组成池供并发请求轮转使用。

## Docker 编排（ToolForge + cnb2api）

[**ToolForge**](https://github.com/wbxl0/toolforge) 是通用 LLM 工具调用中间件（原生 FC 透传 + XYML 提示词回退），
上游开源项目：<https://github.com/YuJunZhiXue/toolforge>；本项目使用其 fork（[wbxl0/toolforge](https://github.com/wbxl0/toolforge)，含坑⑦排版修复等增量）。

一键启动完整链路：**客户端 → ToolForge（XYML 工具调用中间件）→ cnb2api → CNB**。
ToolForge 作为前置，通过提示词注入（XYML）实现 CNB 不原生支持的工具调用。

```
┌────────┐   tools请求   ┌────────────┐   XYML注入   ┌───────────┐    ┌─────┐
│ 客户端  │ ───────────▶ │  ToolForge  │ ──────────▶ │  cnb2api  │ ──▶ │ CNB │
│        │ ◀─────────── │  (:18080)   │ ◀────────── │  (:7863)  │ ◀── │     │
└────────┘   tool_calls  └────────────┘   标准响应   └───────────┘    └─────┘
```

### 启动

```bash
# 拉取子模块（ToolForge 源码）
git submodule update --init --recursive

# 编辑配置：docker/config.yaml 中的 allowed_keys（客户端访问 key）
# 和 api_key（cnb2api 的鉴权 key，与 config.example.json 一致）

# 一键启动
 docker compose up -d --build
```

### 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| ToolForge | `18080` | OpenAI 兼容入口（带 tools 支持） |
| cnb2api | `7863` | 内部网关（仅容器网络内访问） |

客户端接入：`base_url: http://<host>:18080/v1`，`api_key: <docker/config.yaml 的 allowed_keys>`。

### 说明

- ToolForge 以 git submodule 引入（`docker/toolforge` → `wbxl0/toolforge`，fork 自上游 YuJunZhiXue/toolforge）
- 两服务共享 `cnb2api-net` 网络，ToolForge 通过容器名 `cnb2api:7863` 访问网关
- 支持非流式 + 流式工具调用（标准 OpenAI `tool_calls` 格式）
- 国内网络环境：Dockerfile 已使用清华 pip 镜像源，避免拉取超时

## 目录结构

```
cnb2api/
├── cmd/server/main.go            # 入口：配置、凭证池初始化、HTTP 服务
├── internal/auth/csrf.go         # CSRF 凭证获取 + 凭证池（核心）
├── internal/upstream/client.go   # 上游请求构造 + SSE 读取
├── internal/server/handler.go    # OpenAI 兼容 HTTP handler
├── internal/server/anthropic.go  # Anthropic Messages 适配
├── internal/server/responses.go  # OpenAI Responses 适配
├── config.example.json
└── go.mod
```

## 免责声明

本项目仅供学习和研究使用。请遵守 CNB 平台服务条款，自行承担使用风险。作者不对任何因使用本项目产生的直接或间接损失负责。

## License

MIT
