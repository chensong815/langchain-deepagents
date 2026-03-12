# langchain_deepagent

基于 `deepagents.create_deep_agent` 的终端智能体项目，支持本地 Skill 加载、LLM 语义路由、流式输出、会话落盘和会话级临时 Python sandbox。

## 核心能力

- 终端多轮对话，模型回复按 token 流式打印。
- 启动时自动加载 Skill 元数据，并支持 `/skills` 查看可用技能。
- 内置语义路由：根据用户问题在候选技能中选一个高置信技能，再把路由提示注入给 Agent。
- 内置工具：
  - `get_weather`（示例天气工具）
  - `search_knowledge_base`（示例知识库检索）
  - `ensure_python_packages`（在当前会话 sandbox 的 `.venv` 中安装临时依赖）
  - `run_python_code`（在当前会话 sandbox 中执行临时 Python 代码）
  - `query_field_lineage_step`（字段血缘单步查询）
  - `query_field_lineage_until_stop`（字段血缘自动迭代下钻）
- 每次 CLI 会话创建独立文件：`memory/session_<session_id>.md`，按 Turn 记录 user/assistant 内容和时间戳。
- 当前 CLI 会话文件会作为 memory source 注入 Agent，并在每轮调用前重新加载，形成“落盘记忆 -> 下一轮可读”的闭环。
- 每次 CLI 会话创建独立 sandbox 目录和 `.venv`；默认退出时自动清理。

## 目录结构

```text
.
├── app/
│   ├── agent.py            # Agent 构建、同步/异步/流式调用
│   ├── cli.py              # 终端循环与命令处理
│   ├── config.py           # .env 配置加载
│   ├── intent_router.py    # Skill 意图路由
│   ├── reloading_memory.py # 每轮重载 memory 文件的 middleware
│   ├── sandbox.py          # 会话级临时 sandbox / .venv
│   ├── session_memory.py   # 会话落盘
│   ├── skill_catalog.py    # 扫描 SKILL.md frontmatter
│   └── tools.py            # 工具定义（含字段血缘）
├── skills/base/
│   ├── api-debug/SKILL.md
│   ├── db-field-lineage/SKILL.md
│   ├── python-sandbox-exec/SKILL.md
│   └── research-plan/SKILL.md
├── memory/
│   ├── AGENTS.md
│   └── session_*.md
├── main.py
├── requirements.txt
└── pyproject.toml
```

## 环境要求

- Python `>=3.11`
- 已安装依赖：
  - `langchain==1.2.10`
  - `deepagents==0.4.4`
  - `langchain-openai>=1.0.1`
  - `python-dotenv>=1.1.1`
  - `PyYAML>=6.0.2`

## 快速开始

1. 创建并激活虚拟环境（可选，但推荐）：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 配置 `.env`（至少要有 `DEEPSEEK_API_KEY`）。

4. 启动：

```bash
python3 main.py
```

也可以在启动时直接恢复历史会话：

```bash
python3 main.py --resume latest
python3 main.py --resume <session_id>
python3 main.py --pick-session
python3 main.py --sessions
```

## .env 配置项

### 模型与会话

- `DEEPSEEK_API_KEY`：必填，DeepSeek/OpenAI 兼容 API Key。
- `DEEPSEEK_BASE_URL`：默认 `https://api.deepseek.com`。
- `MODEL_NAME`：默认 `deepseek-chat`。
- `MODEL_TEMPERATURE`：默认 `0.3`。
- `DEFAULT_THREAD_ID`：默认 `default`。
- `SYSTEM_PROMPT`：系统提示词。

### Skill 与 Memory

- `SKILL_SOURCES`：技能目录（逗号分隔），默认 `/skills/base`。
- `MEMORY_SOURCES`：记忆文件路径（逗号分隔），默认 `/memory/AGENTS.md`。
  - CLI 启动后还会自动追加当前会话文件 `memory/session_<session_id>.md` 作为动态 memory source。

### Sandbox

- `SANDBOX_ROOT_REL_PATH`：会话沙盒根目录，相对项目根目录，默认 `.sandbox`。
- `SANDBOX_COMMAND_TIMEOUT_SECONDS`：临时代码执行超时，默认 `60`。
- `SANDBOX_INSTALL_TIMEOUT_SECONDS`：依赖安装超时，默认 `180`。
- `SANDBOX_OUTPUT_CHAR_LIMIT`：命令输出截断上限，默认 `12000`。
- `SANDBOX_CLEANUP_ON_EXIT`：退出时是否删除会话 sandbox，默认 `true`。

### 意图路由

- `INTENT_ROUTER_ENABLED`：默认 `true`。
- `INTENT_ROUTER_THRESHOLD`：默认 `0.72`（越高越保守）。
- `INTENT_ROUTER_MODEL`：默认空，空时复用 `MODEL_NAME`。

### 字段血缘工具

- `FIELD_LINEAGE_ENDPOINT`：字段血缘 API，默认 `http://123.207.206.62:39000/api/graph/field-lineage-analysis`。
- `FIELD_LINEAGE_TIMEOUT_SECONDS`：HTTP 超时秒数，默认 `20.0`。

## CLI 命令

- 直接输入文本：与 Agent 对话。
- `/skills`：查看加载到的技能名称和描述。
- `/session`：查看当前会话信息。
- `/sessions`：查看最近历史会话。
- `/resume <session_id|latest>`：恢复指定历史会话继续对话。
- `/exit` / `exit` / `quit` / `/quit`：结束会话。

## 会话恢复说明

- 启动参数 `--resume` 可直接恢复指定会话，`--pick-session` 可在进入 CLI 前交互选择会话。
- `--sessions` 仅打印最近历史会话并退出。
- 会话恢复基于 `memory/session_<session_id>.md` 的历史内容和原始 `thread_id`。
- 恢复后，新轮次会继续追加到同一个 session 文件中。
- 当前恢复的是“对话记忆”，不会恢复旧 sandbox 工作目录中的临时文件或 `.venv`。

## 技能编写要求

- Skill 必须是目录形式，且目录下存在 `SKILL.md`。
- `SKILL.md` 需要 YAML frontmatter，至少包含：
  - `name`
  - `description`
- 系统会按 `SKILL_SOURCES` 扫描；同名技能后加载项覆盖先加载项。
