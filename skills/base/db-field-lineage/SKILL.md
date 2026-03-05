---
name: db-field-lineage
description: 在数据血缘分析场景下，基于目标表+字段调用字段血缘接口进行“单层挖掘+分析”，并按 logic_summary 决策是否继续下钻。逐层追踪后输出明细与最终总结。
path: /skills/base/db-field-lineage/SKILL.md
---

# DB Field Lineage

## 工作流

1. 优先解析两个输入：`name`（表名）和 `target_col`（字段名）。
2. 若本轮仅提供了其中一个参数（例如“换成字段 xxx”），优先复用同一会话中最近一次字段血缘查询已确认的另一个参数，不要重复追问。
3. 仅当当前轮次与会话上下文都无法确定 `name` 或 `target_col` 时，才向用户追问缺失参数。
4. 参数齐全后，优先调用工具 `query_field_lineage_until_stop(name, target_col, insert_rank)`。
5. 若用户未提供 `insert_rank`，默认传 `1`。
6. 工具会基于每个 `business_entity.logic_summary` 自动判断该分支是否继续下钻，判断明细在 `round_records[*].entity_decisions` 中。
7. 从工具返回结果中读取 `messages`（逐条 `logic_summary`）按顺序输出。
8. 若工具判定“无需继续下钻”或工具返回无上游链路（如 count=0/business_entities 为空），直接进入最终总结，不再继续调用工具。
9. 最后一段总结必须由 agent 生成，至少包含：涉及表、轮次、停止原因、关键血缘结论（常量赋值/同名直传/多分支插入等）。
10. 可结合工具返回的 `rounds`、`stopped`、`related_tables`、`visited_tables`、`logic_summary_count`、`round_records` 做总结。
11. 保留表scheme占位符${}，例如完整表名为 `${MMAC_DATA}.MMAC_MAC_SMR_CUB_GRP_SMP`，不允许去掉 `${} `,直接使用`${MMAC_DATA}.MMAC_MAC_SMR_CUB_GRP_SMP`作为表名。

## 结果返回规范

- 先返回逐轮 `logic_summary`，最后由 agent 追加一段自然语言总结。
- 保持时间顺序：首轮 `logic_summary` 在前，agent 总结在最后。
- 如 `messages` 为空，也要输出“未发现可继续追踪的上游链路”的总结。
- 若接口报错，原样返回错误信息并停止。

## 回退策略

当 `query_field_lineage_until_stop` 不可用时，改用 `query_field_lineage_step` 循环调用：
1. 调用 `query_field_lineage_step(name, target_col, insert_rank)`（默认 `insert_rank=1`）。
2. 读取返回中的 `logic_summaries` 并追加到输出。
3. 基于每个 entity 的 `logic_summary` 判断是否继续下钻，仅将需要下钻的 entity 的 `name` 和 `insert_rank` 作为下一轮参数，`target_col` 保持不变；若无可下钻分支则停止并总结。
