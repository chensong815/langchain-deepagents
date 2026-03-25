# frontend

Next.js App Router 前端，提供一个面向 agent 调试与资产编辑的 IDE 风格工作台。

## 页面

- `/`：对话页
- `/memory`：长期记忆文件页面
- `/skills`：技能卡片与技能文件页面
- `/prompts`：Prompt 文件页面

## 主要能力

- 会话列表与会话切换
- 会话列表走 summary 接口，完整会话详情按需加载
- 流式聊天展示
- tool / debug 事件时间线
- 上下文 token、输入输出 token 展示
- 记忆文件编辑与 AI 优化建议
- 技能卡片查看、创建、上传、编辑、删除
- Prompt 文件查看与编辑
- Monaco Editor 集成

## 当前结构

- `workspace.tsx` 负责工作台级状态编排、路由联动和 API 请求协调
- `components/workspace/chat-shell.tsx` 承担对话主区壳层
- `components/workspace/skills-panel.tsx` 承担技能页壳层
- `components/workspace/debug-panel.tsx` 承担调试面板壳层
- 前端当前采用 `session summaries + active session detail` 的状态模型，避免会话列表长期持有完整消息体

## 运行

```bash
cd frontend
npm install
npm run dev
```

生产构建：

```bash
npm run build
npm run start
```

服务器部署并对外提供服务时，建议使用生产模式并显式指定端口：

```bash
export NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
npm run build
npm run start -- --hostname 0.0.0.0 --port 39002
```

如果希望关闭终端后仍在后台运行，可使用：

```bash
export NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
npm run build
nohup npm run start -- --hostname 0.0.0.0 --port 39002 > frontend.39002.log 2>&1 & echo $! > frontend.39002.pid
```

服务器如果使用开发模式：

```bash
export NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
nohup npm run dev -- --hostname 0.0.0.0 --port 39002 > frontend.39002.log 2>&1 & echo $! > frontend.39002.pid
```

停止后台进程：

```bash
kill "$(cat frontend.39002.pid)"
rm -f frontend.39002.pid
```

## 环境变量

- `NEXT_PUBLIC_API_BASE_URL`
  后端 API 地址，默认 `http://127.0.0.1:8000`

示例：

```bash
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

服务器示例：

```bash
NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
```

## 与后端联调

本项目默认前后端分开启动：

1. 在 `backend/` 中启动 API 服务
2. 在 `frontend/` 中运行 `npm run dev`
3. 浏览器访问 `http://127.0.0.1:3000`

前端会调用后端这些能力：

- 会话摘要列表、会话详情、创建、删除、更新
- SSE 流式聊天
- 记忆文件读写与优化
- 技能文件读写、上传、删除
- Prompt 文件读写

## 目录说明

```text
frontend/
├── app/               # Next.js 路由页面
├── components/        # Workspace、编辑器、JSON 树等组件
│   └── workspace/     # chat-shell、skills-panel、debug-panel
├── lib/
│   ├── api.ts         # API 调用封装
│   └── types.ts       # 前端类型定义（含 SessionSummary / SessionRecord）
├── public/
└── package.json
```

## 会话加载约定

- `GET /api/sessions` 只用于列表展示，返回 summary 字段
- `GET /api/sessions/{session_id}` 才会返回完整 `messages` 和 `raw_messages`
- 工作台会在选中会话时再拉取详情，并同步更新左侧 summary 列表

## 权限与缓存说明

如果本机全局 `~/.npm` 缓存存在权限问题，项目已通过仓库内本地缓存目录规避常见安装错误。优先直接在 `frontend/` 目录执行：

```bash
npm install
```

## 相关文档

- [../README.md](../README.md)
- [../backend/README.md](../backend/README.md)
