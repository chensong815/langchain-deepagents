# langchain_deepagent

一个面向本地协作与可视化调试的 Deep Agent 全栈工作台。

仓库分为两个子项目：

- `backend/`：Python Agent 后端，负责模型调用、工具执行、技能注入、上下文管理、会话持久化和 API 服务
- `frontend/`：Next.js 前端，提供对话、记忆、技能、Prompt 四个工作台页面

项目目标不是只做一个聊天框，而是提供一套可编辑、可追踪、可恢复的 agent 运行环境：

- 支持 Web UI 和 CLI 两种使用方式
- 支持技能卡片 `SKILL.md` 注入
- 支持长期记忆文件与会话级运行时上下文
- 支持流式 SSE 事件、工具调用可视化、调试追踪
- 支持对话摘要压缩、历史会话检索、会话恢复

## 适合什么场景

- 想快速搭一个可运行的 LangChain / deepagents agent 工作台
- 想把 prompt、memory、skills 做成可编辑资产，而不是写死在代码里
- 想观察一轮对话中模型、工具、技能选择、上下文注入分别发生了什么
- 想保留每个 session 的完整历史，同时控制实际注入模型的上下文长度

## 技术栈

- Backend: Python, FastAPI, LangChain, deepagents, langchain-openai
- Frontend: Next.js App Router, React, TypeScript, Monaco Editor
- Storage: 本地文件持久化，无外部数据库依赖

## 核心能力

- 会话管理：创建、更新、删除、恢复、截断、重试
- Agent 运行：支持流式输出、工具调用、技能路由、调试事件
- 上下文管理：摘要压缩 + working memory + 检索召回 + 最近轮次
- 记忆管理：长期记忆文件浏览、编辑、AI 优化建议
- 技能管理：技能卡片列表、上传、创建、编辑、删除
- Prompt 管理：读取和编辑后端 prompt 文件
- 沙盒执行：按 session 隔离的工作目录与 Python 执行环境

## 当前实现说明

- `GET /api/sessions` 返回会话摘要列表，只包含列表页所需字段；完整 `messages` 和 `raw_messages` 通过 `GET /api/sessions/{session_id}` 按需加载
- `SessionStore` 对同一 `session_id` 的写操作增加了进程内锁，并对 session state、session context、session markdown 使用原子写，降低并发写丢失风险
- 历史检索层对 session markdown 和长期记忆 markdown 做了基于文件签名的缓存，避免每轮都全量重读和重分块
- 前端工作台改为 `summary list + active detail` 模型，并抽出 `chat-shell`、`skills-panel`、`debug-panel` 三个职责壳层组件

## 仓库结构

```text
.
├── README.md
├── backend/
│   ├── README.md
│   ├── app/
│   │   ├── agent.py
│   │   ├── server.py
│   │   ├── session_store.py
│   │   ├── context_retrieval.py
│   │   ├── session_context.py
│   │   ├── session_memory.py
│   │   ├── tools.py
│   │   ├── prompts.py
│   │   ├── skill_catalog.py
│   │   └── ...
│   ├── data/
│   │   ├── sessions/          # Web 会话 JSON 状态
│   │   ├── session_context/   # 每个会话的运行时上下文摘要
│   │   └── session_logs/      # raw/debug 事件日志
│   ├── memory/                # 长期记忆文件
│   ├── prompts/               # Prompt 模板
│   ├── sessions/              # 每个会话的完整 markdown 历史
│   ├── skills/                # 技能卡片
│   ├── requirements.txt
│   └── main.py
└── frontend/
    ├── README.md
    ├── app/
    ├── components/
    │   ├── workspace.tsx
    │   └── workspace/        # chat-shell、skills-panel、debug-panel
    ├── lib/
    └── package.json
```

## 上下文管理是怎么工作的

项目中的上下文不是简单地把全部聊天历史直接塞给模型，而是拆成四层：

1. `data/sessions/{session_id}.json`
   保存 Web 会话主状态：消息列表、summary、working_memory、tool 开关、技能开关等

2. `data/session_context/{session_id}.md`
   保存每轮真正用于注入 agent 的运行时上下文，包含：
   - `Summary`
   - `Working Memory`
   - `Retrieved Context`
   - `Recent Turns`

3. `sessions/session_{session_id}.md`
   保存完整会话历史，主要用于恢复历史 session 和做跨会话检索

4. `data/session_logs/{session_id}.ndjson`
   保存原始事件日志和调试日志，如 `tool_start`、`tool_end`、`debug_model_input`

### 单轮请求流程

1. 前端调用 `/api/sessions/{id}/messages/stream`
2. 后端先写入用户消息
3. 如有必要，对较早轮次自动做摘要压缩
4. 更新 `working_memory`
5. 从当前会话旧轮次、其他历史会话、长期记忆文件中召回相关片段
6. 生成最新的 `session_context/{id}.md`
7. 将 `长期记忆文件 + 当前 session_context.md` 作为 memory source 注入 agent
8. agent 流式执行，输出 token/tool/debug 事件
9. 写回 assistant 消息、完整 session markdown、tool 日志和新的 working memory
10. 下一轮再读取最新的 session context，形成闭环

这套设计的好处是：

- 完整历史能保留
- 真正注入模型的上下文可控
- 历史信息可以做轻量召回
- 运行态信息和长期记忆分层清晰

## 快速开始

### 1. 环境准备

建议环境：

- Python 3.11+
- Node.js 20+
- npm 10+

后端依赖见 `backend/requirements.txt`，核心包括：

- `langchain==1.2.10`
- `deepagents==0.4.4`
- `langchain-openai>=1.0.1`
- `fastapi>=0.115.0`
- `uvicorn>=0.34.0`

前端基于：

- `next@16`
- `react@19`
- `@monaco-editor/react`

### 2. 配置环境变量

环境变量支持放在：

- 仓库根目录 `.env`
- `backend/.env`

最少需要配置：

```bash
DEEPSEEK_API_KEY=your_api_key
```

常用配置示例：

```bash
DEEPSEEK_API_KEY=your_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-chat
MODEL_TEMPERATURE=0.3
SYSTEM_PROMPT=You are an engineering copilot. Be concise, factual, and action-oriented.

API_HOST=127.0.0.1
API_PORT=8000
CORS_ORIGINS=http://localhost:39002,http://127.0.0.1:39002

SKILL_SOURCES=/skills
MEMORY_SOURCES=/memory/AGENTS.md,/memory/MEMORY.md,/memory/SOUL.md,/memory/USER.md

SESSION_MEMORY_DIR_REL_PATH=sessions
SESSION_CONTEXT_DIR_REL_PATH=data/session_context
SESSION_LOG_DIR_REL_PATH=data/session_logs
SESSION_STATE_DIR_REL_PATH=data/sessions
SANDBOX_ROOT_REL_PATH=.sandbox
SANDBOX_PYTHON_BIN=/usr/local/bin/python3
```

前端如需指定后端地址：

```bash
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

服务器示例配置：

```bash
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://<SERVER_IP>:39002,http://localhost:39002,http://127.0.0.1:39002
NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
```

### 3. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py serve
```

默认监听地址来自环境变量 `API_HOST` 和 `API_PORT`。

也可以显式指定：

```bash
python3 main.py serve --host 127.0.0.1 --port 8000
```

服务器部署时，可直接这样启动：

```bash
cd backend
source .venv/bin/activate
export CORS_ORIGINS=http://<SERVER_IP>:39002,http://localhost:39002,http://127.0.0.1:39002
export SANDBOX_PYTHON_BIN=/usr/local/bin/python3
python3 main.py serve --host 0.0.0.0 --port 8000
```

如果希望关闭终端后仍在后台运行，可使用：

```bash
cd backend
source .venv/bin/activate
export CORS_ORIGINS=http://<SERVER_IP>:39002,http://localhost:39002,http://127.0.0.1:39002
export SANDBOX_PYTHON_BIN=/usr/local/bin/python3
nohup python3 main.py serve --host 0.0.0.0 --port 8000 > backend.8000.log 2>&1 & echo $! > backend.8000.pid
```

如果后端本身运行在项目 `.venv` 中，但工具执行需要使用容器基础 Python 里已预装的依赖，必须设置 `SANDBOX_PYTHON_BIN` 指向容器内的目标解释器。

停止后台进程：

```bash
kill "$(cat backend.8000.pid)"
rm -f backend.8000.pid
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

服务器部署前端时，建议使用生产模式：

```bash
cd frontend
export NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
npm install
npm run build
npm run start -- --hostname 0.0.0.0 --port 39002
```

如果希望关闭终端后仍在后台运行，可使用：

```bash
cd frontend
export NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
npm install
npm run build
nohup npm run start -- --hostname 0.0.0.0 --port 39002 > frontend.39002.log 2>&1 & echo $! > frontend.39002.pid
```

如果服务器上使用开发模式：

```bash
cd frontend
export NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
nohup npm run dev -- --hostname 0.0.0.0 --port 39002 > frontend.39002.log 2>&1 & echo $! > frontend.39002.pid
```

停止后台进程：

```bash
kill "$(cat frontend.39002.pid)"
rm -f frontend.39002.pid
```

默认访问：

- Frontend: `http://127.0.0.1:3000`
- Backend API: `http://127.0.0.1:8000`

## CLI 用法

后端保留了终端模式：

```bash
cd backend
python3 main.py
```

常用参数：

```bash
python3 main.py --sessions
python3 main.py --resume latest
python3 main.py --resume <SESSION_ID>
python3 main.py --pick-session
```

适合快速验证模型、技能和 memory 注入逻辑，不依赖前端。

## 前端页面

当前前端提供 4 个页面：

- `/`：对话页，流式查看消息、工具、调试事件
- `/memory`：长期记忆文件浏览、编辑、优化
- `/skills`：技能卡片与技能文件管理
- `/prompts`：Prompt 文件管理

## 后端 API 概览

### 基础

- `GET /api/health`
- `GET /api/options`

### 会话

- `GET /api/sessions`：返回摘要列表，用于左侧会话列表和最近会话选择
- `POST /api/sessions`
- `GET /api/sessions/{session_id}`：返回完整会话详情，包含 `messages` 和 `raw_messages`
- `PATCH /api/sessions/{session_id}`
- `DELETE /api/sessions/{session_id}`

### 消息与对话流

- `POST /api/sessions/{session_id}/messages/stream`
- `PATCH /api/sessions/{session_id}/messages/{message_id}`
- `POST /api/sessions/{session_id}/messages/{message_id}/truncate`
- `POST /api/sessions/{session_id}/messages/{message_id}/retry-base`
- `POST /api/sessions/{session_id}/compress`

### 记忆

- `GET /api/memory/files`
- `GET /api/memory/file`
- `PUT /api/memory/file`
- `POST /api/memory/optimize`

### Prompt

- `GET /api/prompts/file`
- `PUT /api/prompts/file`

### 技能

- `GET /api/skills`
- `POST /api/skills`
- `POST /api/skills/upload`
- `GET /api/skills/file`
- `GET /api/skills/files`
- `PUT /api/skills/file`
- `DELETE /api/skills/file`

### 沙盒文件

- `GET /api/sandbox/file`

## 重要目录说明

### `backend/memory/`

长期记忆目录。建议只放稳定、可复用的事实或协作规则，例如：

- `AGENTS.md`
- `MEMORY.md`
- `SOUL.md`
- `USER.md`

不要把频繁变化的会话状态直接写进这里。

### `backend/skills/`

技能目录。每个技能通常至少包含一个 `SKILL.md`，可选包含额外资源文件。

### `backend/prompts/`

Prompt 模板目录。当前项目已包含：

- `conversation_compress.md`
- `memory_optimize.md`
- `skill_optimize.md`

### `backend/sessions/`

完整会话 markdown 归档，适合人工查看、恢复和跨会话检索。

### `backend/data/session_context/`

每个会话的一份运行时上下文文件。agent 每轮调用前都会重新加载这里的内容。

## 日常开发建议

- 修改 memory 或 skill 后，不需要重启整个服务；agent 每轮会重新加载 memory/skills
- 如果你在排查为什么模型答非所问，先看 `session_context` 是否写入了预期的 summary / retrieved_context
- 如果你在排查工具问题，先看 `data/session_logs/*.ndjson`
- 如果你在做 prompt 或 memory 调优，优先从前端的 `/memory`、`/prompts` 页面入手

## 常见问题

### 1. 前端连不上后端

检查：

- 后端是否运行在 `API_HOST:API_PORT`
- 前端 `NEXT_PUBLIC_API_BASE_URL` 是否配置正确
- 浏览器控制台和后端日志是否有 CORS 或网络错误

### 2. 启动时报 `Missing DEEPSEEK_API_KEY in environment`

说明没有在根目录 `.env` 或 `backend/.env` 中配置 `DEEPSEEK_API_KEY`。

### 3. 会话很多但模型仍然看不到早期内容

这是设计使然。模型每轮主要读取：

- summary
- working memory
- retrieved context
- recent turns

不是无上限读取全部原始历史。

### 4. `npm install` 有权限问题

项目前端已经通过本地 npm 缓存目录规避常见的全局缓存权限问题。优先直接在 `frontend/` 目录运行 `npm install`。

## 进一步阅读

- [backend/README.md](./backend/README.md)：后端结构、运行方式、API 与上下文细节
- [frontend/README.md](./frontend/README.md)：前端页面、开发与联调说明
