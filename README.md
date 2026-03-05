# Deep Agent Skills 终端项目

这是一个基于 `langchain==1.2.10` 与 `deepagents.create_deep_agent` 的终端交互项目。

## 功能特性

- 通过终端进行多轮对话（保留会话上下文，模型回复流式输出）。
- 每次启动会话会写入独立文件 `memory/session_<session_id>.md`，并在该文件中按轮次追加历史，包含 `pid/thread_id/session` 元信息，便于区分不同进程会话。
- 使用 `create_deep_agent(...)` 创建深度智能体。
- 通过 `FilesystemBackend` 从本地目录（`/skills/base`）加载技能。
- 从 `/memory/AGENTS.md` 加载记忆规则。
- 内置 LLM 语义路由：先根据用户问题与已加载 skill 描述做意图识别，再给 agent 注入 skill 路由提示（可配置阈值）。
- 内置 `/skills` 命令可在终端查看已加载技能。

## 项目结构

```text
.
├── app/
│   ├── agent.py
│   ├── cli.py
│   ├── config.py
│   ├── skill_catalog.py
│   └── tools.py
├── skills/base/
│   ├── api-debug/SKILL.md
│   └── research-plan/SKILL.md
├── memory/AGENTS.md
├── main.py
├── pyproject.toml
└── requirements.txt
```

## 脚本作用说明

- `main.py`：项目启动入口，仅负责调用 `run_cli()` 启动终端交互。
- `app/__init__.py`：应用包说明文件，用于标识模块边界与职责。
- `app/config.py`：加载并校验环境变量，输出统一的 `Settings` 配置对象。
- `app/tools.py`：定义可被 agent 调用的工具函数（当前为示例工具）。
- `app/skill_catalog.py`：扫描技能目录并解析 `SKILL.md` 前置元数据，用于技能列表展示。
- `app/agent.py`：创建并缓存 deep agent，提供同步、异步、流式三种调用方式。
- `app/cli.py`：实现终端命令循环，处理 `/skills`、`/exit` 与流式回答打印。

## 快速开始

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 启动终端交互：

```bash
python main.py
```

## 终端命令

- 直接输入文本：向 agent 提问
- `/skills`：查看当前技能列表
- `/exit`：退出会话

## 说明

- `langchain` 固定为 `1.2.10`，因为 `deepagents==0.4.4` 依赖 `1.2.x` 版本线。
- 每个技能目录都需要包含带 YAML frontmatter 的 `SKILL.md` 文件。
- 可选路由配置：
  - `INTENT_ROUTER_ENABLED`（默认 `true`）
  - `INTENT_ROUTER_THRESHOLD`（默认 `0.72`，越高越保守）
  - `INTENT_ROUTER_MODEL`（默认复用 `MODEL_NAME`）
