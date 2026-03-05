---
name: db-field-lineage
description: 根据数据库表名、字段名和 insert_rank 调用知识图谱字段血缘接口，按返回的 business_entities 持续追踪并输出 logic_summary。用于用户要求“查字段血缘”“按表名+字段名追踪上游/下游关系”“循环查询直到无相关血缘”为止的场景。停止条件为 count=0 且 business_entities 为空。
path: /skills/base/db-field-lineage/SKILL.md
---

# DB Field Lineage

## 工作流

1. 优先解析两个输入：`name`（表名）和 `target_col`（字段名）。
2. 若本轮仅提供了其中一个参数（例如“换成字段 xxx”），优先复用同一会话中最近一次字段血缘查询已确认的另一个参数，不要重复追问。
3. 仅当当前轮次与会话上下文都无法确定 `name` 或 `target_col` 时，才向用户追问缺失参数。
4. 参数齐全后，优先调用工具 `query_field_lineage_until_stop(name, target_col, insert_rank)`。
5. 若用户未提供 `insert_rank`，默认传 `1`。
6. 从工具返回结果中读取 `messages` 列表（每一项是 `logic_summary`），按顺序返回给 agent。
7. 若 `stopped=true`，说明已命中停止条件 `count=0`（且无 business_entities），停止继续查询。
8. 若 `stopped=false`，说明达到 `max_rounds` 仍未完成追踪，向 agent 明确说明“已达到最大查询轮次”。

## 结果返回规范

- 只返回 `logic_summary` 相关信息，不改写字段含义。
- 保持时间顺序：先返回首轮查询结果中的 `logic_summary`，后续轮次依次追加。
- 若接口报错，原样返回错误信息并停止。

## 回退策略

当 `query_field_lineage_until_stop` 不可用时，改用 `query_field_lineage_step` 循环调用：
1. 调用 `query_field_lineage_step(name, target_col, insert_rank)`（默认 `insert_rank=1`）。
2. 读取返回中的 `logic_summaries` 并追加到输出。
3. 若 `count>0` 且 `business_entities` 非空，则将每个实体的 `name` 和 `insert_rank` 作为下一轮查询参数，`target_col` 保持不变继续调用；否则停止。
