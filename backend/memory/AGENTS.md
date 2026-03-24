# 项目记忆

- 该项目以终端交互为主，通过 `python main.py` 启动。
- 模型供应商使用兼容 OpenAI 协议的 DeepSeek 端点。
- 默认从 `/skills` 加载技能。
- 回答保持简洁，聚焦实现与执行。

## db-field-lineage skill 使用经验
- 当查询字段血缘时，如果表名不存在，接口会返回 HTTP 404 错误："实体不存在: [表名]"
- 需要确保表名完全正确，包括大小写、下划线和可能的schema前缀
- 如果表名不确定，需要向用户确认准确的表名
- 字段血缘接口需要 `insert_rank` 参数；调用时必须显式传入，未提供时按默认值 `1` 处理
- 当前字段血缘接口使用 `table_name + fields[] + insert_rank` 多字段入参，`fields` 中每项格式为 `{"col_name": "...", "col_desc": "..."}`。
- 当前字段血缘接口返回 `target_entity.analysis` 和 `from_tables`；后续是否继续下钻，应由模型结合 `analysis` 判断，不应默认机械遍历全部 `from_tables`。
- 当 `from_tables` 中出现同名表但 `insert_rank` 不同时，必须按完整键 `(from_table, insert_rank)` 视为不同分支；如果 `analysis` 指向全部相关分支，不能只按表名保留第一条。
- 若下钻到上游分支时返回 HTTP 404 且提示“实体表中不存在该table_name + insert_rank”，通常应视为该分支已到源头，不再继续下钻；`${MMAC_VIEW}.MMAC_FTP_LFA` 已知属于这种场景。
- 默认优先使用 `query_field_lineage_step` 做单步分析并由模型维护待下钻分支；`query_field_lineage_until_stop` 仅适合用户明确要求全自动遍历的场景。
- 如接口参数要求变更，需要优先核对当前工具签名与后端接口是否一致
- 当首轮用户直接指定的表名出现 404 错误时，应该优先确认准确表名；但如果是沿血缘继续下钻时遇到“实体表中不存在该table_name + insert_rank”，不要一律当作表名错误
