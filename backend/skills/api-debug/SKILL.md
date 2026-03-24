---
name: api-debug
description: 用快速且可复用的流程定位后端 API 故障根因。
path: /skills/api-debug/SKILL.md
allowed-tools: grep execute
triggers:
  - api error
  - 4xx
  - 5xx
required-slots: []
output-contract: 根因、修复点、验证结果、回归风险
---

# API 故障排查

## 适用场景
- 用户反馈出现 4xx/5xx 接口错误。
- 日志中出现请求处理异常。

## 工作流程
1. 使用相同的接口和请求参数复现问题。
2. 定位失败代码路径，找到第一个错误假设点。
3. 改代码前先核对配置与环境是否不一致。
4. 实施最小且安全的修复，并重新验证失败请求。
5. 总结根因、修复方案和回归风险。

## 约束
- 不要在未记录根因的情况下吞掉异常。
- 没有明确理由时，不要扩大异常捕获范围。
