---
name: db-field-lineage
description: 在数据血缘分析场景下，基于目标表+多字段调用字段血缘工具做单步分析；模型需结合 analysis 和 from_tables 自主决策下一轮要追踪的上游链路，直到没有可下钻的表。
path: /skills/db-field-lineage/SKILL.md
allowed-tools: query_field_lineage_step query_field_lineage_until_stop
triggers:
  - 字段血缘
  - lineage
required-slots:
  - table_name
  - fields
output-contract: 按轮次的分析记录和最终血缘总结
---

# DB Field Lineage

## 工作流

1. 优先解析三个输入：`table_name`、`fields`、可选 `insert_rank`（insert_rank如果没输入，则默认值为1）。
2. `fields` 中每项格式为 `{"col_name": "...", "col_desc": "..."}`；若用户只提供字段名，`col_desc` 可为空字符串。
3. 若当前轮只补充了部分参数，优先复用同一会话最近一次已确认的 `table_name`、`fields`、`insert_rank`。
4. 仅当当前轮与会话上下文都无法确定 `table_name` 或 `fields` 时，才追问用户。
5. 默认优先调用 `query_field_lineage_step(table_name, fields, insert_rank)`，不要默认调用 `query_field_lineage_until_stop`。
6. 每次工具返回后，读取四类关键信息：`analysis`、`from_tables`、`from_count`、`insert_rank`。
7. 将当前轮 `analysis` 先纳入输出，再基于该 `analysis` 判断是否要继续检查某些上游表。
8. 下一轮仍保持同一组 `fields`，只替换 `table_name` 与对应上游表的 `insert_rank`。
9. 模型自己维护待处理分支列表，并对 `(table_name, insert_rank)` 做去重，避免死循环；去重键必须同时包含表名和 `insert_rank`，不能只按表名去重。
10. 停止条件：所有待处理上游分支（`(table_name, insert_rank)`）都已完成判断，且没有新的可下钻表。
11. 保留表 schema 占位符`${}`，例如 `${MMAC_DATA}.MMAC_MAC_SMR_CUB_GRP_SMP`，不得去掉 `${}`。

## 分支决策

- 若 `analysis` 明确指出目标字段直接来源于某些上游表、或加工逻辑仍依赖上游表中的字段，则继续检查对应上游链路。
- 若 `analysis` 明确指出字段逻辑已在当前表闭合，例如常量赋值、纯当前表内计算、无相关上游，则该分支停止。
- 判断“对应上游链路”时，分支单位是完整的 `(from_table, insert_rank)`；即使 `from_table` 同名，只要 `insert_rank` 不同，也必须视为不同候选分支分别判断。
- 若 `from_tables` 中存在多个上游表，但 `analysis` 只说明其中部分表与目标字段相关，则只继续相关分支，不必机械遍历全部上游表。
- 若 `analysis` 表示“与 `from_tables` 中全部上游都相关”“来自所有上游共同加工”“需综合全部来源判断”等含义，则必须继续所有匹配的 `(from_table, insert_rank)` 分支，不能因为表名重复而提前停止。
- 若 `analysis` 无法明确排除某个上游表，而 `from_tables` 仍给出候选上游，为避免漏查，应保守地继续检查该上游一次。
- 若某个上游表明显只是维表补充、关联展示或与目标字段加工无关，可在总结中说明跳过原因，不继续下钻。

## 特殊场景

- 若 `from_tables` 为 `[{"from_table":"A","insert_rank":1},{"from_table":"A","insert_rank":2},{"from_table":"B","insert_rank":1}]`，且 `analysis` 指明目标字段与全部上游有关，则下一轮待处理分支必须包含 `("A", 1)`、`("A", 2)`、`("B", 1)` 三条，不能只保留 `("A", 1)`。
- 若 `analysis` 只指出表 `A` 相关，但未区分 `insert_rank`，而 `from_tables` 中存在多个 `("A", insert_rank)`，则默认这些 `A` 的候选分支都应继续一次；只有 `analysis` 明确排除某个 `insert_rank` 对应分支时，才可跳过。
- 输出总结时，实际追踪链路与被跳过分支都应按 `(table_name, insert_rank)` 展示，避免把同名不同 `insert_rank` 的分支合并。

## 输出要求

- 按时间顺序输出每一轮 `analysis`，不要丢失中间结论。
- 最后一段必须由模型生成总结，至少包含：起始表、目标字段、实际追踪过的链路、停止原因、关键加工结论、被跳过的分支及理由。
- 若首轮用户指定的起始表就报错，且无法判断为已知源头场景，原样返回错误并停止，不再继续调用工具。
- 若下钻某个上游分支时报“实体表中不存在该table_name + insert_rank”，则仅停止该分支，并在总结中注明“该分支到达源头视图/源表，接口无更上游实体定义”。
- 若首轮就无可下钻上游，也要明确说明“当前分析已闭合，无需继续追踪上游表”。

## 工具选择

- `query_field_lineage_step`：默认工具。适用于需要模型根据 `analysis` 自主判断下一步追踪策略的场景。
- `query_field_lineage_until_stop`：仅在用户明确要求“全自动穷举下钻”时使用；默认不要使用它代替模型决策。
