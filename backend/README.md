# backend

Python 后端项目，负责：

- Agent 构建与模型调用
- Tool / Skill 注入
- 上下文整理与 memory 注入
- 会话持久化与恢复
- FastAPI 服务
- CLI 终端模式

## 主要能力

- `python3 main.py`：CLI 模式
- `python3 main.py serve`：启动 API 服务
- 按 session 持久化消息、运行时上下文、完整 markdown 历史和调试日志
- `GET /api/sessions` 返回摘要列表，完整详情通过 `GET /api/sessions/{session_id}` 按需获取
- 同一 `session_id` 的写操作带进程内锁，session state / session context / session markdown 采用原子写
- SSE 流式聊天事件：`token`、`tool_start`、`tool_end`、`skill`、`title`、`done`、`error`
- 对话自动压缩摘要
- 历史 session 和长期记忆检索带文件签名缓存，降低重复解析 markdown 的成本
- 长期记忆文件与技能文件热加载
- Skill 卡片管理、Prompt 管理、Memory 管理

## 目录说明

```text
backend/
├── app/
│   ├── agent.py              # agent 构建、流式事件封装
│   ├── server.py             # FastAPI 路由
│   ├── cli.py                # 终端 CLI
│   ├── session_store.py      # Web 会话主状态与运行时上下文写回
│   ├── session_context.py    # session_context.md 渲染
│   ├── session_memory.py     # sessions/*.md 完整历史文件
│   ├── context_retrieval.py  # 历史/长期记忆检索
│   ├── tools.py              # 工具函数
│   ├── prompts.py            # prompt 加载
│   ├── skill_catalog.py      # skill 枚举与校验
│   └── ...
├── data/
│   ├── sessions/             # Web session state
│   ├── session_context/      # 运行时上下文文件
│   └── session_logs/         # raw/debug 事件日志
├── memory/                   # 长期记忆
├── prompts/                  # Prompt 模板
├── sessions/                 # 完整会话 markdown 历史
├── skills/                   # 技能目录
├── requirements.txt
└── main.py
```

## 安装

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行方式

### CLI

```bash
python3 main.py
```

列出历史会话：

```bash
python3 main.py --sessions
```

恢复会话：

```bash
python3 main.py --resume latest
python3 main.py --resume <SESSION_ID>
python3 main.py --pick-session
```

### API

```bash
python3 main.py serve
```

指定监听地址：

```bash
python3 main.py serve --host 127.0.0.1 --port 8000
```

服务器部署，建议显式监听公网地址：

```bash
cd backend
source .venv/bin/activate
export CORS_ORIGINS=http://<SERVER_IP>:39002,http://localhost:39002,http://127.0.0.1:39002
python3 main.py serve --host 0.0.0.0 --port 8000
```

如果希望关闭终端后仍在后台运行，可使用：

```bash
cd backend
source .venv/bin/activate
export CORS_ORIGINS=http://<SERVER_IP>:39002,http://localhost:39002,http://127.0.0.1:39002
nohup python3 main.py serve --host 0.0.0.0 --port 8000 > backend.8000.log 2>&1 & echo $! > backend.8000.pid
```

停止后台进程：

```bash
kill "$(cat backend.8000.pid)"
rm -f backend.8000.pid
```

## 环境变量

环境变量支持放在：

- 仓库根目录 `.env`
- `backend/.env`

最少需要：

```bash
DEEPSEEK_API_KEY=your_api_key
```

常用配置：

```bash
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
```

服务器示例：

```bash
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://<SERVER_IP>:39002,http://localhost:39002,http://127.0.0.1:39002
```

## 会话与上下文文件

后端当前把一个 session 拆成多份文件：

- `data/sessions/{session_id}.json`
  Web 会话主状态

- `data/session_context/{session_id}.md`
  当前轮注入 agent 的运行时上下文

- `sessions/session_{session_id}.md`
  完整对话历史归档

- `data/session_logs/{session_id}.ndjson`
  tool/debug 原始事件日志

### 持久化语义

- 会话列表接口只读取摘要字段；完整消息和 raw event 只在详情接口加载
- 同一 `session_id` 的修改通过 `RLock` 串行化，避免流式生成、取消、改标题等并发操作互相覆盖
- `data/sessions/*.json`、`data/session_context/*.md`、`sessions/session_*.md` 使用临时文件替换的原子写策略
- 文件不存在时，API 会把 `FileNotFoundError` 映射为 `404`

## 上下文管理流程

单轮处理过程如下：

1. 写入 user message
2. 必要时自动压缩较早轮次，生成 summary
3. 更新 working memory
4. 从当前 session 旧轮次、其他 sessions 和长期 memory 中检索相关片段
5. 生成最新 `session_context.md`
6. 将 `memory_sources + session_context.md` 注入 agent
7. 流式运行 agent，并记录 tool/debug 事件
8. 写入 assistant message
9. 回写 working memory、artifacts、open loops
10. 刷新下一轮使用的 session context

`session_context.md` 目前主要包含：

- `Summary`
- `Working Memory`
- `Retrieved Context`
- `Recent Turns`

## Agent 运行机制

后端使用 `deepagents.create_deep_agent(...)` 构建 agent，并挂载两个关键 middleware：

- `ReloadingMemoryMiddleware`
  每轮调用前重新读取 memory sources，确保长期记忆和 session context 始终最新

- `ReloadingSkillsMiddleware`
  每轮重新加载技能卡片，便于边调边改

同时使用 `thread_id` 保持对话链路，使用 `InMemorySaver()` 作为运行期 checkpoint。

## API 概览

### 系统

- `GET /api/health`
- `GET /api/options`

### 会话

- `GET /api/sessions`
  返回摘要列表，不包含完整 `messages` / `raw_messages`
- `POST /api/sessions`
- `GET /api/sessions/{session_id}`
  返回完整会话详情，包含 `messages`、`raw_messages`、working memory 等
- `PATCH /api/sessions/{session_id}`
- `DELETE /api/sessions/{session_id}`

### 消息

- `POST /api/sessions/{session_id}/messages/stream`
- `PATCH /api/sessions/{session_id}/messages/{message_id}`
- `POST /api/sessions/{session_id}/messages/{message_id}/truncate`
- `POST /api/sessions/{session_id}/messages/{message_id}/retry-base`
- `POST /api/sessions/{session_id}/compress`

### 记忆 / Prompt / 技能

- `GET /api/memory/files`
- `GET /api/memory/file`
- `PUT /api/memory/file`
- `POST /api/memory/optimize`
- `GET /api/prompts/file`
- `PUT /api/prompts/file`
- `GET /api/skills`
- `POST /api/skills`
- `POST /api/skills/upload`
- `GET /api/skills/file`
- `GET /api/skills/files`
- `PUT /api/skills/file`
- `DELETE /api/skills/file`

## 开发建议

- 长期稳定信息写到 `memory/`
- 当前会话运行时状态交给 `session_store.py` 自动维护
- 需要调 agent 输入时，优先查看 `data/session_context/`
- 需要调工具事件时，优先查看 `data/session_logs/`
- 需要恢复或分析完整历史时，查看 `sessions/`
- 如果你在观察检索性能，优先看 `context_retrieval.py` 的文件签名缓存是否命中，而不是先怀疑模型侧

## 相关文档

- [../README.md](../README.md)
- [../frontend/README.md](../frontend/README.md)
