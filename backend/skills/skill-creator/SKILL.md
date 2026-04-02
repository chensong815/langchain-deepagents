---
name: skill-creator
description: 在当前项目中新增、修改或完善 skill 时使用；适用于创建 `backend/skills/<slug>/SKILL.md`、补齐 skill frontmatter、整理触发词与必填槽位、更新可选的 `agents/openai.yaml`，并让 skill 内容与项目的 skill catalog / 路由规则保持一致。
path: /skills/skill-creator/SKILL.md
allowed-tools: []
triggers:
  - 创建 skill
  - 新增 skill
  - 增加 skill
  - 修改 skill
  - 更新 skill
  - 完善 skill
  - skill creator
  - skill-creator
required-slots:
  - skill_name
output-contract: 已创建或更新的 skill 路径、关键 frontmatter 字段、主体说明摘要，以及仍需补充的信息（若有）
---

# Skill Creator

## 目标

在当前仓库内创建或更新一个可被 `backend/app/skill_catalog.py` 正确解析的 skill。

优先生成简洁、可维护、贴合本项目风格的 skill，不要把通用教程、README 或过程文档一并塞进 skill 目录。

## 项目约束

- skill 目录放在 `backend/skills/<slug>/`。
- skill 名称使用小写短横线形式；如果用户给的是 `skill_creator`、中文标题或带空格名称，先规范化为 hyphen-case，例如 `skill_creator -> skill-creator`。
- 必需文件是 `backend/skills/<slug>/SKILL.md`。
- 可选文件是 `backend/skills/<slug>/agents/openai.yaml`，用于 UI 展示；如果目录里已经有该文件，更新时保持与 skill 内容一致。
- 本项目现有 skill frontmatter 通常使用逻辑路径 `path: /skills/<slug>/SKILL.md`；实际文件仍创建在 `backend/skills/<slug>/SKILL.md`。
- 不要创建 README、CHANGELOG、安装说明等无关文件。

## Frontmatter 规则

至少提供下面两个字段：

- `name`
- `description`

本项目建议补齐以下字段，以提升路由质量和调试可读性：

- `path`
- `allowed-tools`
- `triggers`
- `required-slots`
- `output-contract`

最小推荐模板：

```yaml
---
name: <slug>
description: <说明 skill 做什么，以及什么场景下应触发>
path: /skills/<slug>/SKILL.md
allowed-tools: []
triggers:
  - <触发词 1>
required-slots:
  - <必填参数名>
output-contract: <输出应包含什么>
---
```

规则：

1. `description` 必须同时写清“做什么”和“什么时候用”，因为路由器会直接读取它。
2. `triggers` 只放高信号词，不要把过于泛化的词写进去，避免误路由。
3. `required-slots` 只保留真正缺了就无法执行的参数。
4. `allowed-tools` 只有在你明确想限制工具时才填写；否则保持空列表。
5. `output-contract` 写最终回答的结构要求，而不是实现步骤。

## 创建流程

1. 先确认是“新建 skill”还是“更新已有 skill”。
2. 查看相邻 skill 的写法，尤其是 `backend/skills/*/SKILL.md` 的 frontmatter 风格。
3. 规范化 skill 名称，并创建或定位到 `backend/skills/<slug>/`。
4. 写 `SKILL.md`：
   - frontmatter 先写清楚
   - 正文只写另一个 Codex 真正需要的流程、约束和输出要求
5. 若这个 skill 需要在 UI 中更易读，补一个 `agents/openai.yaml`，至少包含：
   - `display_name`
   - `short_description`
   - `default_prompt`
6. 若是更新已有 skill，不要覆盖用户已有的有效内容；在原有基础上增量修改。

## 正文写法

优先使用下面这类结构：

- `## 适用场景`
- `## 工作流程`
- `## 输出要求`

只有当 skill 明确需要脚本、参考文档或模板资源时，再增加 `scripts/`、`references/`、`assets/`。没有实际需要时不要预留空目录。

正文应强调：

- 这个 skill 处理什么任务
- 执行顺序和决策规则
- 需要读取哪些项目文件或目录
- 哪些参数必须追问，哪些参数可以从上下文复用
- 最终输出必须包含哪些结论

避免：

- 解释 AI 常识
- 冗长背景介绍
- 与当前仓库无关的通用技能开发教程

## 校验方式

优先按本项目的解析规则校验，而不是只依赖通用 skill 校验脚本。

至少确认以下几点：

1. `backend/app/skill_catalog.py` 能成功解析该 skill。
2. `name` 与目录名一致。
3. `description`、`triggers`、`required-slots`、`output-contract` 与正文内容一致。
4. 若添加了 `agents/openai.yaml`，其中的 `display_name`、`short_description`、`default_prompt` 与 skill 本身一致。

如果通用校验脚本与本项目扩展 frontmatter 冲突，以本项目加载结果为准，并在结果里明确说明差异。

## 输出要求

完成后，最终回答至少说明：

- 新增或修改了哪个 skill
- 实际文件路径
- 关键 frontmatter 字段
- 是否已经通过项目内解析校验
- 若仍缺少业务规则、触发词或工具约束，需要用户补什么
