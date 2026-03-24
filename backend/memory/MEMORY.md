# MEMORY

长期稳定记忆只放这里，不要把完整会话 transcript 写回本文件。

## 约束
- `backend/memory/` 只保留长期、稳定、可复用的事实与协作约束。
- 会话级历史保存在 `backend/sessions/`，仅用于审计、恢复和回放。
- 运行时会话上下文由 `data/session_context/` 下的生成文件提供，内容应以摘要、结构化 working_memory、检索片段为主。

## 当前项目
- 技能来源目录默认是 `/skills`。
- 长期记忆默认来源包括 `/memory/AGENTS.md`、`/memory/MEMORY.md`、`/memory/SOUL.md`、`/memory/USER.md`。
- `skills_enabled` 应同时约束路由层与运行时注入层。
- skill 编辑后应在同一会话的下一轮调用中立即生效。
- `working_memory` 至少维护：`active_skill`、`recent_tools`、`current_goal`、`confirmed_slots`、`pending_slots`、`artifacts`、`open_loops`。
- 会话上下文需要支持按需检索历史 session 与长期记忆，而不是只依赖最近轮次。
- 会话超过阈值后应自动滚动压缩旧消息到 `summary`，并记录 `summary_message_count`。
