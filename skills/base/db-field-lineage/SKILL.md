---
name: db-field-lineage
description: 根据数据库表名与字段名调用知识图谱字段血缘接口，持续获取每一轮返回的 message。用于用户要求“查字段血缘”“按表名+字段名追踪上游/下游关系”“循环查询直到无相关血缘”为止的场景。停止条件为 message 等于“该阶段无目标字段相关血缘”。
---

# DB Field Lineage

## 工作流

1. 接收并确认两个输入：`name`（表名）和 `target_col`（字段名）。
2. 优先调用工具 `query_field_lineage_until_stop(name, target_col)`。
3. 从工具返回结果中读取 `messages` 列表，并按顺序返回给 agent。
4. 若 `stopped=true`，说明已命中停止条件 `message == "该阶段无目标字段相关血缘"`，停止继续查询。
5. 若 `stopped=false`，说明达到 `max_rounds` 仍未命中停止条件，向 agent 明确说明“已达到最大查询轮次”。

## 结果返回规范

- 只返回 `message` 相关信息，不改写字段含义。
- 保持时间顺序：第 1 轮 message 在前，后续轮次依次追加。
- 若接口报错，原样返回错误信息并停止。

## 回退策略

当 `query_field_lineage_until_stop` 不可用时，改用 `query_field_lineage_step` 循环调用：
1. 调用 `query_field_lineage_step(name, target_col)`。
2. 读取返回中的 `message` 并追加到输出。
3. 若 `should_continue=true` 则继续下一轮；否则停止。
