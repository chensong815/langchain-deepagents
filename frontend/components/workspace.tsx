"use client";

import { Fragment, startTransition, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUp,
  Bot,
  BrainCircuit,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  Clipboard,
  FileCode2,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
  LoaderCircle,
  Maximize2,
  MessageSquareText,
  Moon,
  Pencil,
  Paperclip,
  Plus,
  Save,
  Scissors,
  Sparkles,
  Square,
  SunMedium,
  TriangleAlert,
  Trash2,
  Wrench,
  X,
} from "lucide-react";

import { CodeEditor } from "@/components/code-editor";
import { JsonTree } from "@/components/json-tree";
import { api, buildSandboxFileUrl } from "@/lib/api";
import { ChatShell } from "@/components/workspace/chat-shell";
import { DebugPanel } from "@/components/workspace/debug-panel";
import { SkillsPanel } from "@/components/workspace/skills-panel";
import type {
  ChatMessage,
  FileCard,
  OptionsPayload,
  RawMessage,
  SessionRecord,
  SessionSummary,
  SkillCard,
  SkillFileCard,
  StreamEvent,
  TurnState,
  WorkspacePage,
} from "@/lib/types";

const SKILL_CARDS_MIN_WIDTH = 320;
const SKILL_CARDS_MAX_WIDTH = 620;
const SKILL_FILES_MIN_WIDTH = 240;
const SKILL_FILES_MAX_WIDTH = 520;

function formatDate(value: string | number) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function toSessionSummary(session: SessionSummary | SessionRecord): SessionSummary {
  return {
    id: session.id,
    title: session.title,
    thread_id: session.thread_id,
    model_name: session.model_name,
    created_at: session.created_at,
    updated_at: session.updated_at,
    summary: session.summary,
    summary_message_count: session.summary_message_count,
    debug: session.debug,
    stats: session.stats,
    message_count: "message_count" in session ? session.message_count : session.messages.length,
  };
}

function replaceSessionSummary(sessions: SessionSummary[], nextSession: SessionSummary | SessionRecord) {
  const nextSummary = toSessionSummary(nextSession);
  const rest = sessions.filter((session) => session.id !== nextSummary.id);
  return [nextSummary, ...rest].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

function normalizeSkillPath(path: string) {
  return path.replace(/^\/?skills\//, "");
}

function skillRootFromPath(path: string) {
  return normalizeSkillPath(path).split("/")[0] ?? "";
}

function isSkillMainFile(path: string) {
  return path.endsWith("/SKILL.md") || path === "SKILL.md";
}

function isMarkdownLikeFile(path: string) {
  return /\.(md|mdx)$/i.test(path) || isSkillMainFile(path);
}

function skillFileLanguage(path: string) {
  const normalized = path.toLowerCase();
  if (/\.(md|mdx)$/.test(normalized) || normalized.endsWith("skill.md")) {
    return "markdown";
  }
  if (normalized.endsWith(".json")) {
    return "json";
  }
  if (normalized.endsWith(".yaml") || normalized.endsWith(".yml")) {
    return "yaml";
  }
  if (normalized.endsWith(".py")) {
    return "python";
  }
  if (normalized.endsWith(".ts") || normalized.endsWith(".tsx")) {
    return "typescript";
  }
  if (normalized.endsWith(".js") || normalized.endsWith(".jsx")) {
    return "javascript";
  }
  if (normalized.endsWith(".sh")) {
    return "shell";
  }
  return "plaintext";
}

type SkillTreeNode = {
  id: string;
  name: string;
  kind: "folder" | "file";
  path: string;
  relativePath: string;
  children: SkillTreeNode[];
};

function skillFileDisplayType(path: string) {
  const normalized = path.toLowerCase();
  if (isSkillMainFile(path)) {
    return "SKILL";
  }
  if (/\.(md|mdx)$/.test(normalized)) {
    return "MD";
  }
  if (normalized.endsWith(".json")) {
    return "JSON";
  }
  if (normalized.endsWith(".yaml") || normalized.endsWith(".yml")) {
    return "YAML";
  }
  if (normalized.endsWith(".py")) {
    return "PY";
  }
  if (normalized.endsWith(".ts") || normalized.endsWith(".tsx")) {
    return "TS";
  }
  if (normalized.endsWith(".js") || normalized.endsWith(".jsx")) {
    return "JS";
  }
  if (normalized.endsWith(".sh")) {
    return "SH";
  }
  const extension = normalized.split(".").pop();
  return extension ? extension.toUpperCase() : "FILE";
}

function buildSkillFileTree(files: SkillFileCard[]) {
  const rootNodes: SkillTreeNode[] = [];
  const folderIndex = new Map<string, SkillTreeNode>();

  for (const file of files) {
    const segments = file.relative_path.split("/").filter(Boolean);
    let currentLevel = rootNodes;
    let currentPath = "";

    segments.forEach((segment, index) => {
      const isLeaf = index === segments.length - 1;
      currentPath = currentPath ? `${currentPath}/${segment}` : segment;

      if (isLeaf) {
        currentLevel.push({
          id: `file:${file.path}`,
          name: segment,
          kind: "file",
          path: file.path,
          relativePath: file.relative_path,
          children: [],
        });
        return;
      }

      let folderNode = folderIndex.get(currentPath);
      if (!folderNode) {
        folderNode = {
          id: `folder:${currentPath}`,
          name: segment,
          kind: "folder",
          path: currentPath,
          relativePath: currentPath,
          children: [],
        };
        folderIndex.set(currentPath, folderNode);
        currentLevel.push(folderNode);
      }
      currentLevel = folderNode.children;
    });
  }

  const sortNodes = (nodes: SkillTreeNode[]) => {
    nodes.sort((left, right) => {
      if (left.kind !== right.kind) {
        return left.kind === "folder" ? -1 : 1;
      }
      if (left.kind === "file" && right.kind === "file") {
        if (isSkillMainFile(left.path) && !isSkillMainFile(right.path)) {
          return -1;
        }
        if (!isSkillMainFile(left.path) && isSkillMainFile(right.path)) {
          return 1;
        }
      }
      return left.name.localeCompare(right.name);
    });
    nodes.forEach((node) => {
      if (node.kind === "folder") {
        sortNodes(node.children);
      }
    });
  };

  sortNodes(rootNodes);
  return rootNodes;
}

function collectExpandedSkillFolders(nodes: SkillTreeNode[], bucket = new Set<string>()) {
  nodes.forEach((node) => {
    if (node.kind === "folder") {
      bucket.add(node.path);
      collectExpandedSkillFolders(node.children, bucket);
    }
  });
  return bucket;
}

function collectSelectedSkillFolderPaths(path: string) {
  const relativePath = normalizeSkillPath(path);
  const segments = relativePath.split("/").filter(Boolean);
  const folderSegments = segments.slice(1, -1);
  const selectedPaths: string[] = [];
  let currentPath = "";

  folderSegments.forEach((segment) => {
    currentPath = currentPath ? `${currentPath}/${segment}` : segment;
    selectedPaths.push(currentPath);
  });

  return selectedPaths;
}

function formatSkillSource(source: string) {
  if (source === "/skills") {
    return "Skill";
  }
  return source.replace(/^\/?skills\//, "");
}

function splitMarkdownFrontmatter(content: string) {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!match) {
    return { frontmatter: "", body: content.trim() };
  }
  return {
    frontmatter: match[1].trim(),
    body: content.slice(match[0].length).trim(),
  };
}

function readFrontmatterField(frontmatter: string, field: string) {
  const match = frontmatter.match(new RegExp(`^${field}:\\s*(.+)$`, "m"));
  return match?.[1]?.trim() ?? "";
}

function slugifySkillName(value: string) {
  const normalized = value.trim().toLowerCase().replace(/[^\w]+/g, "-").replace(/_/g, "-");
  const slug = normalized.replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");
  return slug || "custom-skill";
}

function buildSkillDraftContent(name: string, description: string, slug: string) {
  return [
    "---",
    `name: ${name}`,
    `description: ${description}`,
    `path: /skills/${slug}/SKILL.md`,
    "allowed-tools: []",
    "---",
    "",
    `# ${name}`,
    "",
    "## 适用场景",
    "- 描述这个技能最适合被调用的任务。",
    "",
    "## 工作流程",
    "1. 先确认用户目标与输入边界。",
    "2. 按步骤执行核心操作。",
    "3. 输出结果时给出关键结论与下一步建议。",
    "",
    "## 输出要求",
    "- 结果保持简洁、具体、可执行。",
    "",
  ].join("\n");
}

function getErrorMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function normalizeTurnState(turnState: TurnState | null | undefined): TurnState {
  return (
    turnState ?? {
      status: "idle",
      phase: null,
      turn_id: null,
      user_message_id: null,
      requested_text: "",
      selected_skill: null,
      active_tool: null,
      tool_count: 0,
      stop_requested: false,
      started_at: null,
      updated_at: null,
      completed_at: null,
    }
  );
}

function resolveTurnStatusLabel(turnState: TurnState | null | undefined) {
  const normalized = normalizeTurnState(turnState);
  switch (normalized.status) {
    case "streaming":
      return normalized.phase === "tool"
        ? "工具处理中"
        : normalized.phase === "routing"
          ? "正在理解请求"
          : "正在生成";
    case "cancelling":
      return "正在停止";
    case "completed":
      return "已完成";
    case "interrupted":
      return "已中断";
    case "error":
      return "失败";
    default:
      return "待命";
  }
}

function resolveTurnCaption(turnState: TurnState | null | undefined) {
  const normalized = normalizeTurnState(turnState);
  if (normalized.status === "cancelling") {
    return "已请求停止当前轮，后端会在下一次可中断节点结束本轮。";
  }
  if (normalized.phase === "tool" && normalized.active_tool) {
    return `当前工具：${normalized.active_tool}`;
  }
  if (normalized.phase === "routing" && normalized.selected_skill) {
    return `已命中技能：${normalized.selected_skill}`;
  }
  if (normalized.status === "interrupted") {
    return "保留当前草稿，可继续追问或重试这一轮。";
  }
  if (normalized.status === "completed") {
    return normalized.tool_count > 0 ? `本轮共调用 ${normalized.tool_count} 次工具。` : "本轮直接完成回答。";
  }
  return "多轮状态会持续挂在当前会话上，便于后续追问。";
}

const LOCAL_IMAGE_EXTENSION_PATTERN = /\.(?:png|jpe?g|gif|webp|svg)$/i;
const MARKDOWN_IMAGE_SOURCE_PATTERN = /!\[[^\]]*]\(([^)\n]+)\)/gi;
const GENERIC_IMAGE_PATH_PATTERN = /(?:^|[\s(`'"])([^\s`<>()]+?\.(?:png|jpe?g|gif|webp|svg))(?=$|[\s)`'",])/gi;
const SESSION_WORKSPACE_PREFIX = "/.sandbox/session_";
const EMBEDDED_IMAGE_VALUE_PATTERN =
  /((?:https?:\/\/|data:|blob:)[^\s<>()]+|\/[^\s<>()]+?\.(?:png|jpe?g|gif|webp|svg)|(?:\.{0,2}\/|backend\/\.sandbox\/|\.sandbox\/|workspace\/)[^\s<>()]+?\.(?:png|jpe?g|gif|webp|svg)|[A-Za-z0-9_.-]+\.(?:png|jpe?g|gif|webp|svg))/i;

function isExternalAssetUrl(value: string) {
  return /^(https?:|data:|blob:)/i.test(value);
}

function normalizeLocalAssetPath(value: string) {
  const normalized = value
    .trim()
    .replace(/^`+|`+$/g, "")
    .replace(/^<|>$/g, "")
    .replace(/^['"]+|['"]+$/g, "")
    .replace(/\\/g, "/");

  const embeddedMatch = normalized.match(EMBEDDED_IMAGE_VALUE_PATTERN);
  return embeddedMatch?.[1] ?? normalized;
}

function normalizeProjectSandboxPath(value: string) {
  if (value.startsWith("/.sandbox/")) {
    return value;
  }
  if (value.startsWith(".sandbox/")) {
    return `/${value}`;
  }
  if (value.startsWith("backend/.sandbox/")) {
    return `/${value.slice("backend/".length)}`;
  }
  return null;
}

function resolveSessionWorkspaceImagePath(value: string, sessionId?: string) {
  if (!sessionId) {
    return null;
  }

  let normalized = value.replace(/^\.\//, "");
  if (normalized.startsWith("workspace/")) {
    normalized = normalized.slice("workspace/".length);
  }
  if (normalized.startsWith("/workspace/")) {
    normalized = normalized.slice("/workspace/".length);
  }
  if (!normalized || normalized.startsWith("/") || normalized.startsWith("../")) {
    return null;
  }
  if (!LOCAL_IMAGE_EXTENSION_PATTERN.test(normalized)) {
    return null;
  }
  return `${SESSION_WORKSPACE_PREFIX}${sessionId}/workspace/${normalized}`;
}

function resolveRenderableImageUrl(value: string, sessionId?: string) {
  const normalized = normalizeLocalAssetPath(value);
  if (!normalized) {
    return null;
  }
  if (isExternalAssetUrl(normalized)) {
    return normalized;
  }

  if (normalized.startsWith("/")) {
    return LOCAL_IMAGE_EXTENSION_PATTERN.test(normalized) ? buildSandboxFileUrl(normalized) : null;
  }

  const sandboxPath = normalizeProjectSandboxPath(normalized);
  if (sandboxPath) {
    return LOCAL_IMAGE_EXTENSION_PATTERN.test(sandboxPath) ? buildSandboxFileUrl(sandboxPath) : null;
  }

  const sessionWorkspacePath = resolveSessionWorkspaceImagePath(normalized, sessionId);
  if (sessionWorkspacePath) {
    return buildSandboxFileUrl(sessionWorkspacePath);
  }

  return null;
}

function extractImagePathsFromContent(content: string, sessionId?: string) {
  const uniquePaths = new Set<string>();

  for (const match of content.matchAll(MARKDOWN_IMAGE_SOURCE_PATTERN)) {
    const normalized = normalizeLocalAssetPath(match[1] ?? "");
    if (normalized && resolveRenderableImageUrl(normalized, sessionId)) {
      uniquePaths.add(normalized);
    }
  }

  for (const match of content.matchAll(GENERIC_IMAGE_PATH_PATTERN)) {
    const normalized = normalizeLocalAssetPath(match[1] ?? "");
    if (!normalized) {
      continue;
    }
    if (resolveRenderableImageUrl(normalized, sessionId)) {
      uniquePaths.add(normalized);
    }
  }

  return [...uniquePaths];
}

const MANAGED_PROMPTS = [
  {
    path: "conversation_compress.md",
    title: "Conversation Compress Prompt",
    description: "会话压缩摘要使用的提示词模板",
  },
  {
    path: "memory_optimize.md",
    title: "Memory Optimize Prompt",
    description: "记忆 AI 优化使用的提示词模板",
  },
  {
    path: "skill_optimize.md",
    title: "Skill Optimize Prompt",
    description: "技能 AI 优化使用的提示词模板",
  },
] as const;

function MessageBubble({
  message,
  sessionId,
  onCopy,
  copied,
  onEdit,
  editing,
  editValue,
  onEditChange,
  onEditConfirm,
  onEditCancel,
}: {
  message: ChatMessage;
  sessionId?: string;
  onCopy: (value: string) => void;
  copied?: boolean;
  onEdit?: () => void;
  editing?: boolean;
  editValue?: string;
  onEditChange?: (value: string) => void;
  onEditConfirm?: () => void;
  onEditCancel?: () => void;
}) {
  const isUser = message.role === "user";
  const assistantState =
    !isUser && message.state && message.state !== "completed"
      ? message.state === "interrupted"
        ? "已中断"
        : "异常"
      : null;
  const previewImagePaths = useMemo(
    () => (isUser ? [] : extractImagePathsFromContent(message.content, sessionId)),
    [isUser, message.content, sessionId],
  );

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <article
        className={`w-full max-w-[min(100%,56rem)] rounded-[1.7rem] border px-4 py-3 shadow-[0_30px_60px_-42px_rgba(0,0,0,0.32)] backdrop-blur-xl ${
          isUser
            ? "border-[color:color-mix(in_srgb,var(--accent)_30%,var(--line))] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--accent-soft)_78%,white_22%),color-mix(in_srgb,var(--panel-strong)_94%,transparent))]"
            : "border-[var(--line)] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel)_97%,white_3%),color-mix(in_srgb,var(--panel)_88%,transparent))]"
        }`}
      >
        <div className="mb-2.5 flex items-center justify-between text-[11px] text-[var(--muted)]">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-full border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_82%,transparent)]">
              {isUser ? <MessageSquareText className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
            </span>
            <span className="rounded-full border border-[var(--line)] px-2 py-0.5 text-[9px] font-medium uppercase tracking-[0.16em]">
              {isUser ? "User" : "Assistant"}
            </span>
            {assistantState ? <span className="status-pill !px-2 !py-0.5 !text-[9px]">{assistantState}</span> : null}
            <span>{formatDate(message.created_at)}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <button className="icon-button" onClick={() => onCopy(message.content)} type="button">
              {copied ? <Check className="h-3.5 w-3.5" /> : <Clipboard className="h-3.5 w-3.5" />}
            </button>
            {isUser && onEdit ? (
              <button className="icon-button" onClick={onEdit} type="button">
                <Pencil className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        </div>
        {editing ? (
          <div className="space-y-3">
            <textarea
              className="min-h-28 w-full rounded-[1.2rem] border border-[var(--line)] bg-[var(--panel)] px-3.5 py-2.5 text-[13px] outline-none ring-0"
              value={editValue}
              onChange={(event) => onEditChange?.(event.target.value)}
            />
            <div className="flex gap-2">
              <button className="action-button" onClick={onEditConfirm} type="button">
                保存并重试
              </button>
              <button className="ghost-button" onClick={onEditCancel} type="button">
                取消
              </button>
            </div>
          </div>
        ) : isUser ? (
          <p className="whitespace-pre-wrap text-[14px] leading-7 text-[var(--foreground)]">{message.content}</p>
        ) : (
          <div className="space-y-4">
            <div className="markdown-body">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  img: ({ src, alt }) => {
                    const resolvedSrc = typeof src === "string" ? resolveRenderableImageUrl(src, sessionId) : null;
                    if (!resolvedSrc) {
                      return (
                        <span className="text-[var(--muted)]">
                          {alt || "图片"} 无法显示
                        </span>
                      );
                    }
                    return (
                      <>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          alt={alt || "assistant image"}
                          className="max-h-[28rem] w-auto max-w-full rounded-2xl border border-[var(--line)]"
                          src={resolvedSrc}
                        />
                      </>
                    );
                  },
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
            {previewImagePaths.length > 0 ? (
              <div className="space-y-3">
                {previewImagePaths.map((path) => {
                  const src = resolveRenderableImageUrl(path, sessionId);
                  if (!src) {
                    return null;
                  }
                  return (
                    <figure key={path} className="space-y-2">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        alt={path.split("/").pop() || "sandbox image"}
                        className="max-h-[28rem] w-auto max-w-full rounded-2xl border border-[var(--line)]"
                        src={src}
                      />
                      <figcaption className="break-all text-[11px] leading-5 text-[var(--muted)]">{path}</figcaption>
                    </figure>
                  );
                })}
              </div>
            ) : null}
          </div>
        )}
      </article>
    </div>
  );
}

type TimelineStep =
  | {
      id: string;
      kind: "phase";
      label: string;
      detail?: string;
      status: "running" | "done" | "error";
      phase: "routing" | "responding";
      createdAt?: string;
    }
  | {
      id: string;
      kind: "skill";
      label: string;
      detail?: string;
      confidence?: number;
      createdAt?: string;
    }
  | {
      id: string;
      kind: "tool";
      label: string;
      input?: unknown;
      output?: unknown;
      status: "running" | "done";
      toolCallId?: string;
      createdAt?: string;
    }
  | {
      id: string;
      kind: "model";
      label: string;
      context?: unknown;
      input?: unknown;
      output?: unknown;
      status: "running" | "done" | "error";
      runId?: string;
      createdAt?: string;
    }
  | {
      id: string;
      kind: "error";
      label: string;
      detail: string;
      createdAt?: string;
    };

type SkillDraftState = {
  mode: "create" | "upload";
  slug: string;
  name: string;
  description: string;
  filename?: string;
};

type TurnView = {
  id: string;
  userMessage: ChatMessage;
  assistantMessage: ChatMessage | null;
  thinkingSteps: TimelineStep[];
  thinkingState: TurnState;
  isStreaming: boolean;
  hasAssistantDraft: boolean;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function resolveToolStepLabel(tool: unknown, toolCallId?: string) {
  if (typeof tool === "string" && tool.trim()) {
    return tool.trim();
  }
  if (toolCallId) {
    return `调用 ${toolCallId}`;
  }
  return "未命名调用";
}

function resolveModelStepLabel(modelName: unknown, prefix = "Model") {
  if (typeof modelName === "string" && modelName.trim()) {
    return `${prefix} · ${modelName.trim()}`;
  }
  return prefix;
}

function resolvePhaseLabel(phase: "routing" | "responding") {
  return phase === "routing" ? "理解问题并规划路径" : "整合结果并撰写回答";
}

function resolvePhaseDetail(turnState: TurnState, phase: "routing" | "responding") {
  if (phase === "routing") {
    if (turnState.selected_skill) {
      return `已匹配 ${turnState.selected_skill}，正在决定是否需要调用工具。`;
    }
    return "先判断问题意图、可用技能和所需上下文。";
  }

  if (turnState.status === "interrupted") {
    return "本轮在生成阶段被中断，保留当前草稿。";
  }
  if (turnState.status === "error") {
    return "生成阶段出现异常，回答未完整结束。";
  }
  if (turnState.tool_count > 0) {
    return `已整理 ${turnState.tool_count} 次工具调用结果，正在输出最终回答。`;
  }
  return "无需额外工具，直接组织最终回答。";
}

function resolveToolDisplayName(label: string) {
  const normalized = label.trim();
  const aliases: Record<string, string> = {
    weather: "查询天气",
    knowledge_base: "检索知识库",
    python_packages: "安装依赖",
    python_code: "执行 Python",
    field_lineage_step: "字段血缘追踪",
    field_lineage_auto: "自动血缘分析",
    query_field_lineage_step: "字段血缘追踪",
    query_field_lineage_until_stop: "自动血缘分析",
  };
  return aliases[normalized] ?? normalized;
}

function summarizeText(value: string, maxLength = 120) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`;
}

function summarizePayload(value: unknown, maxLength = 140): string {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "string") {
    return summarizeText(value, maxLength);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "空列表";
    }
    if (value.every((item) => typeof item === "string")) {
      return summarizeText(value.join("，"), maxLength);
    }
    return `返回 ${value.length} 项结果`;
  }
  const record = asRecord(value);
  if (!record) {
    return summarizeText(String(value), maxLength);
  }

  const preferredKeys = ["query", "keyword", "keywords", "question", "path", "file_path", "table_name", "fields", "analysis"];
  for (const key of preferredKeys) {
    if (record[key] === undefined) {
      continue;
    }
    const summarized = summarizePayload(record[key], maxLength);
    if (summarized) {
      return `${key}: ${summarized}`;
    }
  }

  const entries = Object.entries(record).filter(([, candidate]) => candidate !== undefined && candidate !== null);
  if (entries.length === 0) {
    return "空对象";
  }

  const preview = entries
    .slice(0, 3)
    .map(([key, candidate]) => `${key}: ${summarizePayload(candidate, 48)}`)
    .join("，");
  return summarizeText(preview, maxLength);
}

function getVisibleThinkingSteps(steps: TimelineStep[]) {
  return steps.filter((step) => step.kind !== "phase" && step.kind !== "model");
}

function countToolSteps(steps: TimelineStep[]) {
  return steps.filter((step) => step.kind === "tool").length;
}

function getDistinctToolDisplayNames(steps: TimelineStep[], limit = Infinity) {
  const names: string[] = [];
  const seen = new Set<string>();

  for (const step of steps) {
    if (step.kind !== "tool") {
      continue;
    }
    const displayName = resolveToolDisplayName(step.label);
    if (seen.has(displayName)) {
      continue;
    }
    seen.add(displayName);
    names.push(displayName);
    if (names.length >= limit) {
      break;
    }
  }

  return names;
}

function getLatestSkillStep(steps: TimelineStep[]) {
  return [...steps].reverse().find((step) => step.kind === "skill");
}

function getLatestToolStep(steps: TimelineStep[]) {
  return [...steps].reverse().find((step) => step.kind === "tool");
}

function shouldRenderThinkingCard(turn: TurnView) {
  if (turn.isStreaming) {
    return true;
  }
  if (turn.thinkingState.status === "interrupted" || turn.thinkingState.status === "error") {
    return true;
  }
  return getVisibleThinkingSteps(turn.thinkingSteps).length > 0;
}

function findLatestPhaseStepIndex(steps: TimelineStep[], phase: "routing" | "responding") {
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    if (step.kind === "phase" && step.phase === phase) {
      return index;
    }
  }
  return -1;
}

function upsertPhaseStep(
  steps: TimelineStep[],
  phase: "routing" | "responding",
  status: "running" | "done" | "error",
  turnState: TurnState,
  createdAt?: string,
) {
  const nextSteps = [...steps];
  const step: TimelineStep = {
    id: `phase-${phase}-${createdAt ?? nextSteps.length}`,
    kind: "phase",
    phase,
    label: resolvePhaseLabel(phase),
    detail: resolvePhaseDetail(turnState, phase),
    status,
    createdAt,
  };

  const matchedIndex = findLatestPhaseStepIndex(nextSteps, phase);
  if (matchedIndex >= 0) {
    nextSteps[matchedIndex] = {
      ...nextSteps[matchedIndex],
      ...step,
      id: nextSteps[matchedIndex].id,
      createdAt: nextSteps[matchedIndex].createdAt ?? createdAt,
    };
    return nextSteps;
  }

  nextSteps.push(step);
  return nextSteps;
}

function mergeTurnStateIntoSteps(currentSteps: TimelineStep[], turnState: TurnState, createdAt?: string) {
  let nextSteps = [...currentSteps];

  if (turnState.phase === "routing" || turnState.phase === "tool" || turnState.phase === "responding") {
    nextSteps = upsertPhaseStep(nextSteps, "routing", turnState.phase === "routing" ? "running" : "done", turnState, createdAt);
  }

  if (turnState.phase === "responding") {
    nextSteps = upsertPhaseStep(nextSteps, "responding", "running", turnState, createdAt);
  }

  if (turnState.status === "completed") {
    nextSteps = upsertPhaseStep(nextSteps, "routing", "done", turnState, createdAt);
    nextSteps = upsertPhaseStep(nextSteps, "responding", "done", turnState, createdAt);
  }

  if (turnState.status === "interrupted" || turnState.status === "error") {
    nextSteps = upsertPhaseStep(nextSteps, "routing", "done", turnState, createdAt);
    nextSteps = upsertPhaseStep(nextSteps, "responding", turnState.status === "error" ? "error" : "done", turnState, createdAt);
  }

  return nextSteps;
}

function findMatchingToolStepIndex(
  steps: TimelineStep[],
  toolCallId?: string,
  toolLabel?: string,
) {
  let fallbackIndex = -1;

  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    if (step.kind !== "tool") {
      continue;
    }
    if (toolCallId && step.toolCallId === toolCallId) {
      return index;
    }
    if (step.status !== "running") {
      continue;
    }
    if (toolLabel && step.label === toolLabel) {
      return index;
    }
    if (fallbackIndex === -1) {
      fallbackIndex = index;
    }
  }

  return fallbackIndex;
}

function findMatchingModelStepIndex(
  steps: TimelineStep[],
  runId?: string,
  label?: string,
) {
  let fallbackIndex = -1;

  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    if (step.kind !== "model") {
      continue;
    }
    if (runId && step.runId === runId) {
      return index;
    }
    if (step.status !== "running") {
      continue;
    }
    if (label && step.label === label) {
      return index;
    }
    if (fallbackIndex === -1) {
      fallbackIndex = index;
    }
  }

  return fallbackIndex;
}

function hasDisplayableDebugValue(value: unknown) {
  if (value === undefined || value === null) {
    return false;
  }
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (typeof value === "object") {
    return Object.keys(value as Record<string, unknown>).length > 0;
  }
  return true;
}

function getModelStepInput(payload: Record<string, unknown> | null) {
  const candidates = [payload?.input_text, payload?.message_batches, payload?.messages, payload?.prompts];
  return candidates.find((candidate) => hasDisplayableDebugValue(candidate));
}

function getModelStepOutput(payload: Record<string, unknown> | null) {
  const candidates = [payload?.output_text, payload?.output_messages, payload?.generations, payload?.error];
  return candidates.find((candidate) => hasDisplayableDebugValue(candidate));
}

function buildModelStepContext(
  payload: Record<string, unknown> | null,
  phase: "router" | "input" | "output" | "error",
) {
  if (!payload) {
    return undefined;
  }

  const context: Record<string, unknown> = {};

  if (payload.model_name !== undefined) {
    context.model_name = payload.model_name;
  }
  if (payload.run_id !== undefined) {
    context.run_id = payload.run_id;
  }
  if (payload.parent_run_id !== undefined) {
    context.parent_run_id = payload.parent_run_id;
  }

  if (phase === "router") {
    if (payload.status !== undefined) {
      context.status = payload.status;
    }
    if (payload.threshold !== undefined) {
      context.threshold = payload.threshold;
    }
    if (payload.allowed_skill_names !== undefined) {
      context.allowed_skill_names = payload.allowed_skill_names;
    }
  }

  if (phase === "input") {
    if (payload.invocation_params !== undefined) {
      context.invocation_params = payload.invocation_params;
    }
    if (payload.tags !== undefined) {
      context.tags = payload.tags;
    }
    if (payload.metadata !== undefined) {
      context.metadata = payload.metadata;
    }
    if (payload.serialized !== undefined) {
      context.serialized = payload.serialized;
    }
  }

  if (phase === "output" || phase === "error") {
    if (payload.llm_output !== undefined) {
      context.llm_output = payload.llm_output;
    }
  }

  return Object.keys(context).length > 0 ? context : undefined;
}

function buildTimelineTurns(rawMessages: RawMessage[]): TimelineStep[][] {
  const turns: TimelineStep[][] = [];
  let currentTurn: TimelineStep[] | null = null;

  for (const rawMessage of rawMessages) {
    const payload = asRecord(rawMessage.payload);

    if (rawMessage.kind === "user") {
      currentTurn = [];
      turns.push(currentTurn);
      continue;
    }

    if (!currentTurn) {
      continue;
    }

    if (rawMessage.kind === "assistant") {
      continue;
    }

    if (rawMessage.kind === "turn_state") {
      currentTurn = mergeTurnStateIntoSteps(currentTurn, normalizeTurnState(payload as TurnState), rawMessage.created_at);
      turns[turns.length - 1] = currentTurn;
      continue;
    }

    if (rawMessage.kind === "skill") {
      currentTurn.push({
        id: rawMessage.id,
        kind: "skill",
        label: typeof payload?.skill === "string" && payload.skill ? payload.skill : "skill router",
        detail: typeof payload?.reason === "string" ? payload.reason : undefined,
        confidence: typeof payload?.confidence === "number" ? payload.confidence : undefined,
        createdAt: rawMessage.created_at,
      });
      continue;
    }

    if (rawMessage.kind === "tool_start") {
      const toolCallId = typeof payload?.tool_call_id === "string" ? payload.tool_call_id : undefined;
      const toolLabel = resolveToolStepLabel(payload?.tool, toolCallId);
      const matchedIndex = findMatchingToolStepIndex(currentTurn, toolCallId, toolLabel);

      if (matchedIndex >= 0) {
        const matchedStep = currentTurn[matchedIndex];
        if (matchedStep.kind === "tool") {
          matchedStep.label = toolLabel;
          matchedStep.input = payload?.input;
          matchedStep.toolCallId = matchedStep.toolCallId || toolCallId;
          matchedStep.status = "running";
        }
        continue;
      }

      currentTurn.push({
        id: rawMessage.id,
        kind: "tool",
        label: toolLabel,
        input: payload?.input,
        status: "running",
        toolCallId,
        createdAt: rawMessage.created_at,
      });
      continue;
    }

    if (rawMessage.kind === "tool_end") {
      const toolCallId = typeof payload?.tool_call_id === "string" ? payload.tool_call_id : undefined;
      const toolLabel = resolveToolStepLabel(payload?.tool, toolCallId);
      const matchedIndex = findMatchingToolStepIndex(currentTurn, toolCallId, toolLabel);

      if (matchedIndex >= 0) {
        const matchedStep = currentTurn[matchedIndex];
        if (matchedStep.kind === "tool") {
          matchedStep.status = "done";
          matchedStep.output = payload?.output;
          matchedStep.label = matchedStep.label || toolLabel;
          matchedStep.toolCallId = matchedStep.toolCallId || toolCallId;
        }
        continue;
      }

      currentTurn.push({
        id: rawMessage.id,
        kind: "tool",
        label: toolLabel,
        output: payload?.output,
        status: "done",
        toolCallId,
        createdAt: rawMessage.created_at,
      });
      continue;
    }

    if (rawMessage.kind === "debug_skill_router") {
      const hasModelIo =
        payload?.prompt !== undefined || payload?.response_text !== undefined || payload?.error !== undefined;
      if (!hasModelIo) {
        continue;
      }

      currentTurn.push({
        id: rawMessage.id,
        kind: "model",
        label: resolveModelStepLabel(payload?.model_name, "Intent Router"),
        context: buildModelStepContext(payload, "router"),
        input: payload?.prompt,
        output: payload?.response_text ?? payload?.error,
        status: payload?.status === "error" ? "error" : "done",
        createdAt: rawMessage.created_at,
      });
      continue;
    }

    if (rawMessage.kind === "debug_model_input") {
      const runId = typeof payload?.run_id === "string" ? payload.run_id : undefined;
      const label = resolveModelStepLabel(payload?.model_name);
      const matchedIndex = findMatchingModelStepIndex(currentTurn, runId, label);

      if (matchedIndex >= 0) {
        const matchedStep = currentTurn[matchedIndex];
        if (matchedStep.kind === "model") {
          matchedStep.label = label;
          matchedStep.context = buildModelStepContext(payload, "input") ?? matchedStep.context;
          matchedStep.input = getModelStepInput(payload);
          matchedStep.runId = matchedStep.runId || runId;
          matchedStep.status = "running";
        }
        continue;
      }

      currentTurn.push({
        id: rawMessage.id,
        kind: "model",
        label,
        context: buildModelStepContext(payload, "input"),
        input: getModelStepInput(payload),
        status: "running",
        runId,
        createdAt: rawMessage.created_at,
      });
      continue;
    }

    if (rawMessage.kind === "debug_model_output") {
      const runId = typeof payload?.run_id === "string" ? payload.run_id : undefined;
      const label = resolveModelStepLabel(payload?.model_name);
      const matchedIndex = findMatchingModelStepIndex(currentTurn, runId, label);

      if (matchedIndex >= 0) {
        const matchedStep = currentTurn[matchedIndex];
        if (matchedStep.kind === "model") {
          matchedStep.status = "done";
          matchedStep.context = {
            ...(asRecord(matchedStep.context) ?? {}),
            ...(asRecord(buildModelStepContext(payload, "output")) ?? {}),
          };
          matchedStep.output = getModelStepOutput(payload);
          matchedStep.label = matchedStep.label || label;
          matchedStep.runId = matchedStep.runId || runId;
        }
        continue;
      }

      currentTurn.push({
        id: rawMessage.id,
        kind: "model",
        label,
        context: buildModelStepContext(payload, "output"),
        output: getModelStepOutput(payload),
        status: "done",
        runId,
        createdAt: rawMessage.created_at,
      });
      continue;
    }

    if (rawMessage.kind === "debug_model_error") {
      const runId = typeof payload?.run_id === "string" ? payload.run_id : undefined;
      const label = resolveModelStepLabel(payload?.model_name);
      const matchedIndex = findMatchingModelStepIndex(currentTurn, runId, label);

      if (matchedIndex >= 0) {
        const matchedStep = currentTurn[matchedIndex];
        if (matchedStep.kind === "model") {
          matchedStep.status = "error";
          matchedStep.context = {
            ...(asRecord(matchedStep.context) ?? {}),
            ...(asRecord(buildModelStepContext(payload, "error")) ?? {}),
          };
          matchedStep.output = getModelStepOutput(payload);
          matchedStep.label = matchedStep.label || label;
          matchedStep.runId = matchedStep.runId || runId;
        }
        continue;
      }

      currentTurn.push({
        id: rawMessage.id,
        kind: "model",
        label,
        context: buildModelStepContext(payload, "error"),
        output: getModelStepOutput(payload),
        status: "error",
        runId,
        createdAt: rawMessage.created_at,
      });
      continue;
    }

    if (rawMessage.kind === "error") {
      currentTurn.push({
        id: rawMessage.id,
        kind: "error",
        label: "执行异常",
        detail: typeof payload?.message === "string" ? payload.message : "未知错误",
        createdAt: rawMessage.created_at,
      });
    }
  }

  return turns;
}

function getLatestTurnRawMessages(rawMessages: RawMessage[]) {
  if (rawMessages.length === 0) {
    return rawMessages;
  }

  let startIndex = 0;
  for (let index = rawMessages.length - 1; index >= 0; index -= 1) {
    if (rawMessages[index]?.kind !== "user") {
      continue;
    }
    startIndex = index;
    if (index > 0 && rawMessages[index - 1]?.kind === "system") {
      startIndex = index - 1;
    }
    break;
  }

  return rawMessages.slice(startIndex);
}

function createTransientRawMessage(kind: string, payload: unknown): RawMessage {
  return {
    id: `${kind}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    kind,
    payload,
    created_at: new Date().toISOString(),
  };
}

function createStreamRawMessage(event: StreamEvent): RawMessage | null {
  if (event.type === "skill" || event.type === "tool_start" || event.type === "tool_end") {
    return createTransientRawMessage(event.type, event);
  }

  if (event.type === "turn_state") {
    return createTransientRawMessage("turn_state", event.turn);
  }

  if (event.type === "error") {
    return createTransientRawMessage("error", { message: event.message });
  }

  if (event.type === "debug") {
    return createTransientRawMessage(event.kind, event.payload);
  }

  return null;
}

function describeRawKind(kind: string) {
  switch (kind) {
    case "system":
      return { label: "System Prompt", tone: "text-sky-600 dark:text-sky-400" };
    case "user":
      return { label: "User Input", tone: "text-[var(--accent)]" };
    case "assistant":
      return { label: "Assistant Output", tone: "text-emerald-600 dark:text-emerald-400" };
    case "skill":
      return { label: "Skill Route", tone: "text-fuchsia-600 dark:text-fuchsia-400" };
    case "tool_start":
      return { label: "Tool Input", tone: "text-amber-600 dark:text-amber-400" };
    case "tool_end":
      return { label: "Tool Output", tone: "text-emerald-600 dark:text-emerald-400" };
    case "debug_context":
      return { label: "Session Context", tone: "text-sky-600 dark:text-sky-400" };
    case "turn_state":
      return { label: "Turn State", tone: "text-violet-600 dark:text-violet-400" };
    case "debug_skill_router":
      return { label: "Skill Router IO", tone: "text-fuchsia-600 dark:text-fuchsia-400" };
    case "debug_agent_input":
      return { label: "Agent Input", tone: "text-indigo-600 dark:text-indigo-400" };
    case "debug_model_input":
      return { label: "Model Input", tone: "text-indigo-600 dark:text-indigo-400" };
    case "debug_model_output":
      return { label: "Model Output", tone: "text-emerald-600 dark:text-emerald-400" };
    case "debug_model_error":
      return { label: "Model Error", tone: "text-amber-600 dark:text-amber-400" };
    case "debug_turn_result":
      return { label: "Turn Result", tone: "text-emerald-600 dark:text-emerald-400" };
    case "error":
      return { label: "Error", tone: "text-amber-600 dark:text-amber-400" };
    default:
      return { label: kind, tone: "text-[var(--muted)]" };
  }
}

function mergeStreamEventIntoSteps(currentSteps: TimelineStep[], event: StreamEvent): TimelineStep[] {
  if (event.type === "turn_state") {
    return mergeTurnStateIntoSteps(currentSteps, normalizeTurnState(event.turn));
  }

  if (event.type === "skill") {
    return [
      ...currentSteps,
      {
        id: `skill-${currentSteps.length}-${event.skill ?? "unknown"}`,
        kind: "skill",
        label: event.skill || "skill router",
        detail: event.reason,
        confidence: event.confidence,
      },
    ];
  }

  if (event.type === "tool_start") {
    const toolLabel = resolveToolStepLabel(event.tool, event.tool_call_id);
    const matchedIndex = findMatchingToolStepIndex(currentSteps, event.tool_call_id, toolLabel);

    if (matchedIndex >= 0) {
      const nextSteps = [...currentSteps];
      const matchedStep = nextSteps[matchedIndex];
      if (matchedStep.kind === "tool") {
        nextSteps[matchedIndex] = {
          ...matchedStep,
          label: toolLabel,
          input: event.input,
          status: "running",
          toolCallId: matchedStep.toolCallId || event.tool_call_id,
        };
      }
      return nextSteps;
    }

    return [
      ...currentSteps,
      {
        id: event.tool_call_id || `tool-start-${currentSteps.length}`,
        kind: "tool",
        label: toolLabel,
        input: event.input,
        status: "running",
        toolCallId: event.tool_call_id,
      },
    ];
  }

  if (event.type === "tool_end") {
    const nextSteps = [...currentSteps];
    const toolLabel = resolveToolStepLabel(event.tool, event.tool_call_id);
    const matchedIndex = findMatchingToolStepIndex(nextSteps, event.tool_call_id, toolLabel);

    if (matchedIndex >= 0) {
      const matchedStep = nextSteps[matchedIndex];
      if (matchedStep.kind === "tool") {
        nextSteps[matchedIndex] = {
          ...matchedStep,
          label: matchedStep.label || toolLabel,
          output: event.output,
          status: "done",
          toolCallId: matchedStep.toolCallId || event.tool_call_id,
        };
      }
      return nextSteps;
    }

    return [
      ...nextSteps,
      {
        id: event.tool_call_id || `tool-end-${nextSteps.length}`,
        kind: "tool",
        label: toolLabel,
        output: event.output,
        status: "done",
        toolCallId: event.tool_call_id,
      },
    ];
  }

  if (event.type === "error") {
    return [
      ...currentSteps,
      {
        id: `error-${currentSteps.length}`,
        kind: "error",
        label: "执行异常",
        detail: event.message,
      },
    ];
  }

  if (event.type === "debug") {
    const payload = asRecord(event.payload);

    if (event.kind === "debug_skill_router") {
      const hasModelIo =
        payload?.prompt !== undefined || payload?.response_text !== undefined || payload?.error !== undefined;
      if (!hasModelIo) {
        return currentSteps;
      }
      return [
        ...currentSteps,
        {
          id: `router-${currentSteps.length}`,
          kind: "model",
          label: resolveModelStepLabel(payload?.model_name, "Intent Router"),
          context: buildModelStepContext(payload, "router"),
          input: payload?.prompt,
          output: payload?.response_text ?? payload?.error,
          status: payload?.status === "error" ? "error" : "done",
        },
      ];
    }

    if (event.kind === "debug_model_input") {
      const runId = typeof payload?.run_id === "string" ? payload.run_id : undefined;
      const label = resolveModelStepLabel(payload?.model_name);
      const matchedIndex = findMatchingModelStepIndex(currentSteps, runId, label);

      if (matchedIndex >= 0) {
        const nextSteps = [...currentSteps];
        const matchedStep = nextSteps[matchedIndex];
        if (matchedStep.kind === "model") {
          nextSteps[matchedIndex] = {
            ...matchedStep,
            label,
            context: buildModelStepContext(payload, "input") ?? matchedStep.context,
            input: getModelStepInput(payload),
            status: "running",
            runId: matchedStep.runId || runId,
          };
        }
        return nextSteps;
      }

      return [
        ...currentSteps,
        {
          id: runId || `model-input-${currentSteps.length}`,
          kind: "model",
          label,
          context: buildModelStepContext(payload, "input"),
          input: getModelStepInput(payload),
          status: "running",
          runId,
        },
      ];
    }

    if (event.kind === "debug_model_output" || event.kind === "debug_model_error") {
      const runId = typeof payload?.run_id === "string" ? payload.run_id : undefined;
      const label = resolveModelStepLabel(payload?.model_name);
      const matchedIndex = findMatchingModelStepIndex(currentSteps, runId, label);
      const status: "error" | "done" = event.kind === "debug_model_error" ? "error" : "done";

      if (matchedIndex >= 0) {
        const nextSteps = [...currentSteps];
        const matchedStep = nextSteps[matchedIndex];
        if (matchedStep.kind === "model") {
          nextSteps[matchedIndex] = {
            ...matchedStep,
            label: matchedStep.label || label,
            context: {
              ...(asRecord(matchedStep.context) ?? {}),
              ...(asRecord(buildModelStepContext(payload, event.kind === "debug_model_error" ? "error" : "output")) ??
                {}),
            },
            output: getModelStepOutput(payload),
            status,
            runId: matchedStep.runId || runId,
          };
        }
        return nextSteps;
      }

      return [
        ...currentSteps,
        {
          id: runId || `model-output-${currentSteps.length}`,
          kind: "model",
          label,
          context: buildModelStepContext(payload, event.kind === "debug_model_error" ? "error" : "output"),
          output: getModelStepOutput(payload),
          status,
          runId,
        },
      ];
    }
  }

  return currentSteps;
}

function buildTurnViews({
  messages,
  historicalTimelineTurns,
  streamSteps,
  streamTurnState,
  activeTurnState,
  streaming,
  streamAssistant,
}: {
  messages: ChatMessage[];
  historicalTimelineTurns: TimelineStep[][];
  streamSteps: TimelineStep[];
  streamTurnState: TurnState | null;
  activeTurnState: TurnState;
  streaming: boolean;
  streamAssistant: string;
}) {
  const turns: TurnView[] = [];
  const orphans: ChatMessage[] = [];
  let userTurnIndex = -1;

  for (const message of messages) {
    if (message.role === "user") {
      userTurnIndex += 1;
      const isStreamingTurn = message.id === "pending-user";
      turns.push({
        id: `turn-${message.id}`,
        userMessage: message,
        assistantMessage: null,
        thinkingSteps: isStreamingTurn ? streamSteps : historicalTimelineTurns[userTurnIndex] ?? [],
        thinkingState: isStreamingTurn
          ? normalizeTurnState(streamTurnState ?? activeTurnState)
          : normalizeTurnState({ status: "completed" } as TurnState),
        isStreaming: isStreamingTurn && streaming,
        hasAssistantDraft: isStreamingTurn && Boolean(streamAssistant),
      });
      continue;
    }

    const lastTurn = turns[turns.length - 1];
    if (lastTurn && !lastTurn.assistantMessage) {
      lastTurn.assistantMessage = message;
      lastTurn.thinkingState = normalizeTurnState({
        status: message.state === "interrupted" ? "interrupted" : message.state === "error" ? "error" : "completed",
      } as TurnState);
    } else {
      orphans.push(message);
    }
  }

  return { turns, orphans };
}

function resolveThinkingTitle(turn: TurnView) {
  const toolCount = countToolSteps(turn.thinkingSteps);

  if (turn.isStreaming) {
    if (turn.thinkingState.status === "cancelling") {
      return "正在停止这轮执行";
    }
    if (turn.thinkingState.phase === "tool") {
      return toolCount > 0 ? `正在执行工具，已调用 ${toolCount} 次` : "正在执行工具";
    }
    if (turn.thinkingState.phase === "responding") {
      return turn.hasAssistantDraft ? "正在输出回答" : "正在整理结果";
    }
    return "正在分析请求";
  }

  if (turn.thinkingState.status === "interrupted") {
    return "执行已中断";
  }
  if (turn.thinkingState.status === "error") {
    return "执行过程中出现异常";
  }

  if (toolCount > 0) {
    return "已完成执行";
  }
  return "直接完成回答";
}

function resolveThinkingCaption(turn: TurnView) {
  const toolNames = getDistinctToolDisplayNames(turn.thinkingSteps, 3);
  const latestSkill = getLatestSkillStep(turn.thinkingSteps);
  const latestTool = getLatestToolStep(turn.thinkingSteps);
  const parts: string[] = [];

  if (latestSkill && latestSkill.kind === "skill") {
    parts.push(`路径 ${latestSkill.label}`);
  }
  if (toolNames.length > 0) {
    const hasMoreTools = countToolSteps(turn.thinkingSteps) > toolNames.length;
    parts.push(`工具 ${toolNames.join("、")}${hasMoreTools ? " 等" : ""}`);
  }
  if (latestTool && latestTool.kind === "tool" && latestTool.status === "running") {
    parts.push(`当前 ${resolveToolDisplayName(latestTool.label)}`);
  }
  if (!turn.isStreaming && toolNames.length === 0) {
    parts.push("未使用外部工具");
  }

  return parts.length > 0 ? parts.join(" · ") : "直接回答，无需额外工具。";
}

function resolveThinkingPreview(turn: TurnView) {
  const toolNames = getDistinctToolDisplayNames(turn.thinkingSteps, 3);
  const latestSkill = getLatestSkillStep(turn.thinkingSteps);

  if (turn.thinkingState.status === "cancelling") {
    return "已请求停止，系统会在安全的中断点结束这一轮。";
  }
  if (turn.thinkingState.status === "interrupted") {
    return "这一轮在执行中被停止，当前已生成内容会保留在会话里。";
  }
  if (turn.thinkingState.status === "error") {
    const errorStep = [...turn.thinkingSteps].reverse().find((step) => step.kind === "error");
    return errorStep?.kind === "error" ? errorStep.detail : "执行链路出现异常，回答没有完整结束。";
  }

  if (turn.isStreaming) {
    if (turn.thinkingState.phase === "tool") {
      const activeTool = turn.thinkingState.active_tool ? resolveToolDisplayName(turn.thinkingState.active_tool) : null;
      if (activeTool) {
        return `正在调用 ${activeTool}，拿到结果后会直接继续写回答。`;
      }
      return "正在查找必要信息，结果会直接并入最终回答。";
    }
    if (turn.thinkingState.phase === "responding") {
      return toolNames.length > 0
        ? `已整理 ${toolNames.join("、")} 的结果，正在生成最终回答。`
        : "不需要额外检索，正在直接生成回答。";
    }
    if (latestSkill && latestSkill.kind === "skill") {
      return `先按 ${latestSkill.label} 的路径判断需要哪些能力，再决定是否调用工具。`;
    }
    return "先理解你的问题，再决定是直接回答还是执行工具。";
  }

  if (toolNames.length > 0) {
    return `这轮实际执行了 ${toolNames.join("、")}，并把结果整理进最终回复。`;
  }
  if (latestSkill && latestSkill.kind === "skill") {
    return `这轮按 ${latestSkill.label} 的路径直接完成回答，没有额外工具调用。`;
  }
  return "这轮直接基于当前上下文完成回答。";
}

function ActivityPayload({ value, nested = false }: { value: unknown; nested?: boolean }) {
  if (value === undefined) {
    return null;
  }

  let rendered = "";
  if (typeof value === "string") {
    rendered = value;
  } else if (typeof value === "number" || typeof value === "boolean" || value === null) {
    rendered = String(value);
  } else {
    try {
      rendered = JSON.stringify(value, null, 2);
    } catch {
      rendered = String(value);
    }
  }

  return (
    <pre
      className={`overflow-x-auto whitespace-pre-wrap break-all text-xs leading-6 text-[var(--foreground)] ${
        nested ? "" : "rounded-2xl border border-[var(--line)] bg-[var(--background)] px-3 py-2"
      }`}
    >
      {rendered}
    </pre>
  );
}

function StepIOPayload({
  title,
  input,
  output,
}: {
  title?: string;
  input?: unknown;
  output?: unknown;
}) {
  return (
    <div className="space-y-3 rounded-2xl border border-[var(--line)] bg-[var(--background)] px-3 py-2">
      {title ? <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">{title}</p> : null}

      {input !== undefined ? (
        <div className="space-y-1">
          <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">输入</p>
          <ActivityPayload value={input} nested />
        </div>
      ) : null}

      {output !== undefined ? (
        <div className="space-y-1">
          <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">输出</p>
          <ActivityPayload value={output} nested />
        </div>
      ) : null}
    </div>
  );
}

function ThinkingStepCard({
  step,
  index,
}: {
  step: TimelineStep;
  index: number;
}) {
  const supportsDetails =
    step.kind === "tool" || step.kind === "model" || (step.kind === "skill" && Boolean(step.detail));
  const [open, setOpen] = useState(step.kind === "error");

  const icon =
    step.kind === "phase" ? (
      step.status === "running" ? (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Circle className="h-3.5 w-3.5 fill-current" />
      )
    ) : step.kind === "skill" ? (
      <BrainCircuit className="h-3.5 w-3.5" />
    ) : step.kind === "model" ? (
      step.status === "running" ? (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Bot className="h-3.5 w-3.5" />
      )
    ) : step.kind === "tool" ? (
      step.status === "running" ? (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Wrench className="h-3.5 w-3.5" />
      )
    ) : (
      <TriangleAlert className="h-3.5 w-3.5 text-amber-600" />
    );

  const summary =
    step.kind === "phase"
      ? step.detail
      : step.kind === "skill"
        ? step.detail
        : step.kind === "tool"
          ? summarizePayload(step.input) || summarizePayload(step.output) || `${resolveToolDisplayName(step.label)} 已完成`
          : step.kind === "model"
            ? summarizePayload(step.output) || summarizePayload(step.input) || "模型调用已完成"
            : step.detail;

  const badge =
    step.kind === "phase"
      ? step.status === "running"
        ? "进行中"
        : step.status === "error"
          ? "失败"
          : "完成"
      : step.kind === "tool"
        ? step.status === "running"
          ? "工具中"
          : "已返回"
        : step.kind === "model"
          ? step.status === "running"
            ? "调用中"
            : step.status === "error"
              ? "失败"
              : "已完成"
          : step.kind === "skill" && step.confidence !== undefined
            ? `置信度 ${step.confidence.toFixed(2)}`
            : null;
  const displayLabel = step.kind === "tool" ? resolveToolDisplayName(step.label) : step.label;

  if (!supportsDetails && step.kind !== "error") {
    return (
      <article className="rounded-[1.1rem] border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_86%,transparent)] px-3.5 py-3">
        <div className="flex gap-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel-deep)_72%,transparent)] text-[var(--accent)]">
            {icon}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">节点 {index + 1}</p>
                <p className="text-[13px] font-medium text-[var(--foreground)]">{displayLabel}</p>
              </div>
              {badge ? <span className="status-pill">{badge}</span> : null}
            </div>
            {summary ? <p className="mt-1.5 text-[13px] leading-6 text-[var(--muted)]">{summary}</p> : null}
          </div>
        </div>
      </article>
    );
  }

  return (
    <details
      className="rounded-[1.1rem] border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_86%,transparent)] px-3.5 py-3"
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="flex cursor-pointer list-none gap-3">
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel-deep)_72%,transparent)] text-[var(--accent)]">
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">节点 {index + 1}</p>
              <p className="text-[13px] font-medium text-[var(--foreground)]">{displayLabel}</p>
            </div>
            <div className="flex items-center gap-2">
              {badge ? <span className="status-pill">{badge}</span> : null}
              {open ? <ChevronDown className="h-4 w-4 text-[var(--muted)]" /> : <ChevronRight className="h-4 w-4 text-[var(--muted)]" />}
            </div>
          </div>
          {summary ? <p className="mt-1.5 pr-6 text-[13px] leading-6 text-[var(--muted)]">{summary}</p> : null}
        </div>
      </summary>

      <div className="mt-3 space-y-2 pl-11">
        {step.kind === "skill" && step.detail ? <p className="text-[13px] leading-6 text-[var(--muted)]">{step.detail}</p> : null}
        {step.kind === "error" ? <p className="text-[13px] leading-6 text-amber-700 dark:text-amber-400">{step.detail}</p> : null}
        {step.kind === "model" && step.context !== undefined ? <StepIOPayload title="上下文" output={step.context} /> : null}
        {(step.kind === "tool" || step.kind === "model") && (step.input !== undefined || step.output !== undefined) ? (
          <StepIOPayload input={step.input} output={step.output} />
        ) : null}
      </div>
    </details>
  );
}

function ThinkingCard({ turn }: { turn: TurnView }) {
  const visibleSteps = getVisibleThinkingSteps(turn.thinkingSteps);
  const hasSteps = visibleSteps.length > 0;
  const toolCount = countToolSteps(turn.thinkingSteps);
  const shouldDefaultOpen = turn.isStreaming || turn.thinkingState.status === "error" || turn.thinkingState.status === "interrupted";
  const [open, setOpen] = useState(shouldDefaultOpen);
  const effectiveOpen = turn.isStreaming || open;

  return (
    <details
      className="rounded-[1.4rem] border border-[color:color-mix(in_srgb,var(--accent)_12%,var(--line))] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel-strong)_97%,white_3%),color-mix(in_srgb,var(--panel)_92%,transparent))] shadow-[0_18px_36px_-30px_rgba(0,0,0,0.2)]"
      open={effectiveOpen}
      onToggle={(event) => {
        if (!turn.isStreaming) {
          setOpen((event.currentTarget as HTMLDetailsElement).open);
        }
      }}
    >
      <summary className="list-none cursor-pointer px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[14px] font-semibold text-[var(--foreground)]">
              <span className="flex h-7 w-7 items-center justify-center rounded-full border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--accent-soft)_70%,white_30%)] text-[var(--accent)]">
                {turn.isStreaming ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <BrainCircuit className="h-4 w-4" />}
              </span>
              <span>{resolveThinkingTitle(turn)}</span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="status-pill !px-2.5 !py-1 !text-[11px]">
                {turn.isStreaming ? "执行中" : toolCount > 0 ? `工具 ${toolCount}` : "直接回答"}
              </span>
              <span className="text-[12px] text-[var(--muted)]">{resolveThinkingCaption(turn)}</span>
            </div>
            <p className="mt-2 text-[13px] leading-6 text-[var(--foreground)]/80">{resolveThinkingPreview(turn)}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="status-pill">
              {turn.isStreaming ? "处理中" : effectiveOpen ? "收起细节" : "查看细节"}
            </span>
            {effectiveOpen ? <ChevronDown className="h-4 w-4 text-[var(--muted)]" /> : <ChevronRight className="h-4 w-4 text-[var(--muted)]" />}
          </div>
        </div>
      </summary>

      <div className="border-t border-[var(--line)] px-4 pb-4 pt-3">
        {hasSteps ? (
          <div className="space-y-2.5">
            {visibleSteps.map((step, index) => (
              <ThinkingStepCard key={step.id} step={step} index={index} />
            ))}
          </div>
        ) : (
          <div className="rounded-[1.2rem] border border-dashed border-[var(--line)] bg-[var(--panel)] px-3.5 py-3 text-[13px] text-[var(--muted)]">
            {turn.isStreaming ? "正在等待第一批可展示的执行节点..." : "这一轮没有需要额外回看的执行节点。"}
          </div>
        )}

        {turn.isStreaming ? (
          <p className="mt-3 text-[11px] leading-5 text-[var(--muted)]">
            {turn.hasAssistantDraft ? "回答已经开始输出；如果后面还有工具返回，执行摘要会继续更新。" : "这里只展示用户可感知的执行节点；更底层的模型与调试事件不会默认铺开。"}
          </p>
        ) : null}
      </div>
    </details>
  );
}

type DebugTraceEntry = {
  createdAt: string;
  payload: unknown;
};

type DebugModelRunTrace = {
  runId: string;
  parentRunId?: string;
  label: string;
  status: "running" | "done" | "error";
  createdAt: string;
  updatedAt: string;
  inputPayload?: unknown;
  outputPayload?: unknown;
};

type DebugToolTrace = {
  key: string;
  toolCallId?: string;
  label: string;
  status: "running" | "done";
  createdAt: string;
  updatedAt: string;
  input?: unknown;
  output?: unknown;
  sourceRunId?: string;
  sourceParentRunId?: string;
  sourceModelName?: string;
  sourceRunnableName?: string;
  startPayload?: unknown;
  endPayload?: unknown;
};

type DebugTraceView = {
  sessionContext: DebugTraceEntry | null;
  skillRouter: DebugTraceEntry | null;
  agentInput: DebugTraceEntry | null;
  turnResult: DebugTraceEntry | null;
  modelRuns: DebugModelRunTrace[];
  toolCalls: DebugToolTrace[];
  rawErrors: DebugTraceEntry[];
};

function shortenDebugId(value: string | null | undefined, size = 8) {
  if (!value) {
    return "unknown";
  }
  return value.length <= size ? value : value.slice(0, size);
}

function buildDebugTrace(rawMessages: RawMessage[]): DebugTraceView {
  const modelRunMap = new Map<string, DebugModelRunTrace>();
  const toolCalls: DebugToolTrace[] = [];
  let sessionContext: DebugTraceEntry | null = null;
  let skillRouter: DebugTraceEntry | null = null;
  let agentInput: DebugTraceEntry | null = null;
  let turnResult: DebugTraceEntry | null = null;
  const rawErrors: DebugTraceEntry[] = [];

  for (let index = 0; index < rawMessages.length; index += 1) {
    const item = rawMessages[index];
    const payload = asRecord(item.payload);

    if (item.kind === "debug_context") {
      sessionContext = { createdAt: item.created_at, payload: item.payload };
      continue;
    }

    if (item.kind === "debug_skill_router") {
      skillRouter = { createdAt: item.created_at, payload: item.payload };
      continue;
    }

    if (item.kind === "debug_agent_input") {
      agentInput = { createdAt: item.created_at, payload: item.payload };
      continue;
    }

    if (item.kind === "debug_turn_result") {
      turnResult = { createdAt: item.created_at, payload: item.payload };
      continue;
    }

    if (item.kind === "debug_model_input" || item.kind === "debug_model_output" || item.kind === "debug_model_error") {
      const runId = typeof payload?.run_id === "string" && payload.run_id ? payload.run_id : `run-${index}`;
      const existing = modelRunMap.get(runId);
      const label = resolveModelStepLabel(payload?.model_name);
      const next: DebugModelRunTrace = existing ?? {
        runId,
        parentRunId: typeof payload?.parent_run_id === "string" ? payload.parent_run_id : undefined,
        label,
        status: item.kind === "debug_model_input" ? "running" : item.kind === "debug_model_error" ? "error" : "done",
        createdAt: item.created_at,
        updatedAt: item.created_at,
      };

      next.label = label;
      next.parentRunId = next.parentRunId || (typeof payload?.parent_run_id === "string" ? payload.parent_run_id : undefined);
      next.updatedAt = item.created_at;

      if (item.kind === "debug_model_input") {
        next.inputPayload = item.payload;
        next.status = existing?.status === "done" || existing?.status === "error" ? existing.status : "running";
      } else {
        next.outputPayload = item.payload;
        next.status = item.kind === "debug_model_error" ? "error" : "done";
      }

      modelRunMap.set(runId, next);
      continue;
    }

    if (item.kind === "tool_start") {
      const toolCallId = typeof payload?.tool_call_id === "string" ? payload.tool_call_id : undefined;
      const label = resolveToolDisplayName(resolveToolStepLabel(payload?.tool, toolCallId));
      toolCalls.push({
        key: toolCallId || `${label}-${index}`,
        toolCallId,
        label,
        status: "running",
        createdAt: item.created_at,
        updatedAt: item.created_at,
        input: payload?.input,
        sourceRunId: typeof payload?.source_run_id === "string" ? payload.source_run_id : undefined,
        sourceParentRunId: typeof payload?.source_parent_run_id === "string" ? payload.source_parent_run_id : undefined,
        sourceModelName: typeof payload?.source_model_name === "string" ? payload.source_model_name : undefined,
        sourceRunnableName: typeof payload?.source_runnable_name === "string" ? payload.source_runnable_name : undefined,
        startPayload: item.payload,
      });
      continue;
    }

    if (item.kind === "tool_end") {
      const toolCallId = typeof payload?.tool_call_id === "string" ? payload.tool_call_id : undefined;
      const label = resolveToolDisplayName(resolveToolStepLabel(payload?.tool, toolCallId));
      let matched: DebugToolTrace | undefined;

      for (let cursor = toolCalls.length - 1; cursor >= 0; cursor -= 1) {
        const candidate = toolCalls[cursor];
        if (toolCallId && candidate.toolCallId === toolCallId) {
          matched = candidate;
          break;
        }
        if (!toolCallId && candidate.status === "running" && candidate.label === label) {
          matched = candidate;
          break;
        }
      }

      if (matched) {
        matched.status = "done";
        matched.output = payload?.output;
        matched.updatedAt = item.created_at;
        matched.sourceRunId =
          matched.sourceRunId || (typeof payload?.source_run_id === "string" ? payload.source_run_id : undefined);
        matched.sourceParentRunId =
          matched.sourceParentRunId ||
          (typeof payload?.source_parent_run_id === "string" ? payload.source_parent_run_id : undefined);
        matched.sourceModelName =
          matched.sourceModelName || (typeof payload?.source_model_name === "string" ? payload.source_model_name : undefined);
        matched.sourceRunnableName =
          matched.sourceRunnableName ||
          (typeof payload?.source_runnable_name === "string" ? payload.source_runnable_name : undefined);
        matched.endPayload = item.payload;
      } else {
        toolCalls.push({
          key: toolCallId || `${label}-${index}`,
          toolCallId,
          label,
          status: "done",
          createdAt: item.created_at,
          updatedAt: item.created_at,
          output: payload?.output,
          sourceRunId: typeof payload?.source_run_id === "string" ? payload.source_run_id : undefined,
          sourceParentRunId: typeof payload?.source_parent_run_id === "string" ? payload.source_parent_run_id : undefined,
          sourceModelName: typeof payload?.source_model_name === "string" ? payload.source_model_name : undefined,
          sourceRunnableName: typeof payload?.source_runnable_name === "string" ? payload.source_runnable_name : undefined,
          endPayload: item.payload,
        });
      }
      continue;
    }

    if (item.kind === "error") {
      rawErrors.push({ createdAt: item.created_at, payload: item.payload });
    }
  }

  return {
    sessionContext,
    skillRouter,
    agentInput,
    turnResult,
    modelRuns: [...modelRunMap.values()].sort((left, right) => left.createdAt.localeCompare(right.createdAt)),
    toolCalls,
    rawErrors,
  };
}

type DebugFlowModelStage = {
  run: DebugModelRunTrace;
  tools: DebugToolTrace[];
};

type DebugFlowLoop = {
  id: string;
  index: number;
  run: DebugModelRunTrace;
  tools: DebugToolTrace[];
  nextRun?: DebugModelRunTrace;
  inputSource: "initial" | "tool_results";
  isFinal: boolean;
};

function formatDurationMs(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) {
    if (value < 1000) {
      return `${value.toFixed(value >= 100 ? 0 : 1)} ms`;
    }
    return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)} s`;
  }
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  return null;
}

function resolveToolModelRunId(tool: DebugToolTrace, modelRuns: DebugModelRunTrace[]) {
  if (tool.sourceRunId && modelRuns.some((run) => run.runId === tool.sourceRunId)) {
    return tool.sourceRunId;
  }

  let fallbackRunId: string | null = null;
  for (const run of modelRuns) {
    if (run.createdAt <= tool.createdAt) {
      fallbackRunId = run.runId;
      continue;
    }
    break;
  }
  return fallbackRunId;
}

function buildDebugFlowStages(trace: DebugTraceView) {
  const sortedModelRuns = [...trace.modelRuns].sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  const sortedToolCalls = [...trace.toolCalls].sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  const toolsByRunId = new Map<string, DebugToolTrace[]>();
  const unlinkedTools: DebugToolTrace[] = [];

  for (const tool of sortedToolCalls) {
    const runId = resolveToolModelRunId(tool, sortedModelRuns);
    if (!runId) {
      unlinkedTools.push(tool);
      continue;
    }
    const bucket = toolsByRunId.get(runId) ?? [];
    bucket.push(tool);
    toolsByRunId.set(runId, bucket);
  }

  const modelStages: DebugFlowModelStage[] = sortedModelRuns.map((run) => ({
    run,
    tools: toolsByRunId.get(run.runId) ?? [],
  }));

  return { modelStages, unlinkedTools };
}

function buildDebugFlowLoops(trace: DebugTraceView) {
  const { modelStages, unlinkedTools } = buildDebugFlowStages(trace);
  const loops: DebugFlowLoop[] = modelStages.map((stage, index) => {
    const previousStages = modelStages.slice(0, index);
    const previousToolCount = previousStages.reduce((count, item) => count + item.tools.length, 0);
    const nextRun = modelStages[index + 1]?.run;
    return {
      id: stage.run.runId,
      index: index + 1,
      run: stage.run,
      tools: stage.tools,
      nextRun,
      inputSource: previousToolCount > 0 ? "tool_results" : "initial",
      isFinal: stage.tools.length === 0 && index === modelStages.length - 1,
    };
  });

  return { loops, unlinkedTools };
}

function getModelRunInputText(run: DebugModelRunTrace) {
  const inputPayload = asRecord(run.inputPayload);
  return getModelStepInput(inputPayload);
}

function getModelRunOutputText(run: DebugModelRunTrace) {
  const outputPayload = asRecord(run.outputPayload);
  return getModelStepOutput(outputPayload);
}

function getModelRunNodeName(run: DebugModelRunTrace) {
  const inputPayload = asRecord(run.inputPayload);
  const outputPayload = asRecord(run.outputPayload);
  return (
    (typeof outputPayload?.langgraph_node === "string" && outputPayload.langgraph_node) ||
    (typeof inputPayload?.langgraph_node === "string" && inputPayload.langgraph_node) ||
    null
  );
}

function getModelRunDurationText(run: DebugModelRunTrace) {
  const outputPayload = asRecord(run.outputPayload);
  return formatDurationMs(outputPayload?.duration_ms);
}

function TracePreviewBlock({
  label,
  value,
  emptyLabel,
}: {
  label: string;
  value: unknown;
  emptyLabel: string;
}) {
  const summary = summarizePayload(value, 220);

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--background)_72%,white_28%)] px-3 py-2.5">
      <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--muted)]">{label}</p>
      <p className="mt-1 text-[12px] leading-6 text-[var(--foreground)]/90">{summary || emptyLabel}</p>
    </div>
  );
}

function summarizeToolLabels(tools: DebugToolTrace[], limit = 3) {
  const names: string[] = [];
  const seen = new Set<string>();

  for (const tool of tools) {
    if (seen.has(tool.label)) {
      continue;
    }
    seen.add(tool.label);
    names.push(tool.label);
    if (names.length >= limit) {
      break;
    }
  }

  return names;
}

function describeReasoningLoop(loop: DebugFlowLoop) {
  const inputText = getModelRunInputText(loop.run);
  const outputText = getModelRunOutputText(loop.run);
  const toolNames = summarizeToolLabels(loop.tools);

  if (loop.run.status === "error") {
    return {
      title: "模型调用失败",
      summary: summarizePayload(outputText) || "这一轮模型执行异常，流程在这里中断。",
    };
  }

  if (loop.tools.length > 0) {
    const hasMoreTools = loop.tools.length > toolNames.length;
    const toolText = toolNames.join("、");
    return {
      title: `决定调用 ${toolText}${hasMoreTools ? " 等工具" : ""}`,
      summary:
        summarizePayload(outputText) ||
        (loop.inputSource === "initial"
          ? "先理解用户请求，再决定用哪些工具补齐信息或执行动作。"
          : "消费上轮工具结果后，继续决定下一批需要调用的工具。"),
    };
  }

  if (loop.isFinal) {
    return {
      title: loop.inputSource === "initial" ? "直接生成最终回答" : "整理工具结果并生成最终回答",
      summary:
        summarizePayload(outputText) ||
        summarizePayload(inputText) ||
        "这一轮不再继续调工具，而是直接收束为最终回复。",
    };
  }

  return {
    title: "进行中间推理",
    summary:
      summarizePayload(outputText) ||
      summarizePayload(inputText) ||
      "这一轮完成中间判断，准备进入下一步。",
  };
}

function TraceFlowToolRow({
  tool,
  index,
}: {
  tool: DebugToolTrace;
  index: number;
}) {
  const endPayload = asRecord(tool.endPayload);
  const durationText = formatDurationMs(endPayload?.duration_ms);
  const triggerText =
    tool.sourceRunnableName || tool.sourceModelName || tool.sourceRunId
      ? `由 ${tool.sourceRunnableName || tool.sourceModelName || `run ${shortenDebugId(tool.sourceRunId)}`} 触发`
      : "由上一个模型决策触发";

  return (
    <article className="rounded-xl border border-[var(--line)] bg-[var(--background)] px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[12px] font-medium text-[var(--foreground)]">
            工具 {index + 1} · {tool.label}
          </p>
          <p className="mt-1 text-[11px] leading-5 text-[var(--muted)]">
            {triggerText}
            {tool.toolCallId ? ` · call ${shortenDebugId(tool.toolCallId)}` : ""}
            {durationText ? ` · ${durationText}` : ""}
          </p>
        </div>
        <span className="status-pill shrink-0">{tool.status === "running" ? "running" : "done"}</span>
      </div>
      <div className="mt-2 space-y-1 text-[12px] leading-5 text-[var(--muted)]">
        {tool.input !== undefined ? <p>输入：{summarizePayload(tool.input) || "已提供参数"}</p> : null}
        {tool.output !== undefined ? <p>输出：{summarizePayload(tool.output) || "已返回结果"}</p> : null}
      </div>
    </article>
  );
}

function ReasoningFlowArrow({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 pl-4">
      <div className="h-5 w-px bg-[var(--line)]" />
      <p className="text-[11px] uppercase tracking-[0.16em] text-[var(--muted)]">{label}</p>
    </div>
  );
}

function ReasoningFlowNode({
  actor,
  title,
  summary,
  badge,
  meta,
  children,
}: {
  actor: string;
  title: string;
  summary?: string | null;
  badge?: string | null;
  meta?: string | null;
  children?: React.ReactNode;
}) {
  return (
    <article className="rounded-xl border border-[var(--line)] bg-[var(--background)] px-3.5 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">{actor}</p>
          <p className="mt-1 text-[13px] font-medium text-[var(--foreground)]">{title}</p>
        </div>
        {badge ? <span className="status-pill shrink-0">{badge}</span> : null}
      </div>
      {summary ? <p className="mt-2 text-[12px] leading-6 text-[var(--foreground)]/85">{summary}</p> : null}
      {meta ? <p className="mt-1 text-[11px] leading-5 text-[var(--muted)]">{meta}</p> : null}
      {children ? <div className="mt-3 space-y-2">{children}</div> : null}
    </article>
  );
}

function ReasoningLoopCard({ loop }: { loop: DebugFlowLoop }) {
  const description = describeReasoningLoop(loop);
  const durationText = getModelRunDurationText(loop.run);
  const nodeName = getModelRunNodeName(loop.run);
  const inputText = getModelRunInputText(loop.run);
  const outputText = getModelRunOutputText(loop.run);
  const nextStepLabel = loop.nextRun ? `进入 Loop ${loop.index + 1}` : "结束本轮并产出最终回答";

  return (
    <article className="rounded-[1.25rem] border border-[color:color-mix(in_srgb,var(--accent)_12%,var(--line))] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel-strong)_95%,white_5%),color-mix(in_srgb,var(--panel)_90%,transparent))] px-4 py-3.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="status-pill">Loop {loop.index}</span>
            <span className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">
              {loop.inputSource === "initial" ? "从用户请求开始" : "基于工具结果继续"}
            </span>
          </div>
          <p className="mt-2 text-[15px] font-medium text-[var(--foreground)]">{description.title}</p>
          <p className="mt-2 text-[13px] leading-6 text-[var(--foreground)]/85">{description.summary}</p>
          <p className="mt-1 text-[11px] leading-5 text-[var(--muted)]">
            run {shortenDebugId(loop.run.runId)}
            {nodeName ? ` · node ${nodeName}` : ""}
            {durationText ? ` · ${durationText}` : ""}
            {typeof loop.run.label === "string" && loop.run.label ? ` · ${loop.run.label}` : ""}
          </p>
        </div>
        <span className="status-pill shrink-0">{loop.run.status}</span>
      </div>

      <div className="mt-4 space-y-2">
        <ReasoningFlowNode
          actor="Model"
          title={description.title}
          summary={description.summary}
          badge={loop.run.status}
          meta={
            [
              loop.run.runId ? `run ${shortenDebugId(loop.run.runId)}` : null,
              nodeName ? `node ${nodeName}` : null,
              durationText,
              typeof loop.run.label === "string" && loop.run.label ? loop.run.label : null,
            ]
              .filter(Boolean)
              .join(" · ")
          }
        >
          <div className="grid gap-2 md:grid-cols-2">
            <TracePreviewBlock label="模型输入" value={inputText} emptyLabel="已注入 messages / prompt" />
            <TracePreviewBlock
              label="模型输出"
              value={outputText}
              emptyLabel={loop.tools.length > 0 ? "产出工具调用决策" : "产出回答内容"}
            />
          </div>
        </ReasoningFlowNode>

        {loop.tools.length > 0 ? (
          <>
            <ReasoningFlowArrow label="触发工具执行" />
            <div className="space-y-2 pl-4">
              {loop.tools.map((tool, toolIndex) => (
                <TraceFlowToolRow key={tool.key} tool={tool} index={toolIndex} />
              ))}
            </div>
            <ReasoningFlowArrow label="工具结果回流模型" />
            <div className="pl-4">
              <ReasoningFlowNode
                actor="Flow"
                title={nextStepLabel}
                summary={
                  loop.nextRun
                    ? "这一轮工具返回后，系统会把结果重新送回模型，进入下一轮判断。"
                    : "工具结果已经足够，系统会直接整理并输出最终回答。"
                }
                badge={loop.nextRun ? `Loop ${loop.index + 1}` : "final"}
              />
            </div>
          </>
        ) : (
          <>
            <ReasoningFlowArrow label="不再调用工具" />
            <div className="pl-4">
              <ReasoningFlowNode
                actor="Final"
                title="本轮直接收束为回答"
                summary="模型认为当前上下文已经足够，不再继续执行工具，而是直接输出结果。"
                badge="final"
              />
            </div>
          </>
        )}
      </div>
    </article>
  );
}

function TraceFlowStageCard({
  index,
  actor,
  title,
  badge,
  summary,
  meta,
  children,
}: {
  index: number;
  actor: string;
  title: string;
  badge?: string | null;
  summary?: string | null;
  meta?: string | null;
  children?: React.ReactNode;
}) {
  return (
    <article className="relative rounded-[1.25rem] border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_92%,transparent)] px-4 py-3.5">
      {index > 1 ? <div className="absolute -top-3 left-7 h-3 w-px bg-[var(--line)]" /> : null}
      <div className="flex gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--line)] bg-[var(--background)] text-[12px] font-semibold text-[var(--accent)]">
          {index}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">{actor}</p>
              <p className="text-[14px] font-medium text-[var(--foreground)]">{title}</p>
            </div>
            {badge ? <span className="status-pill shrink-0">{badge}</span> : null}
          </div>
          {summary ? <p className="mt-2 text-[13px] leading-6 text-[var(--foreground)]/85">{summary}</p> : null}
          {meta ? <p className="mt-1 text-[11px] leading-5 text-[var(--muted)]">{meta}</p> : null}
          {children ? <div className="mt-3 space-y-2.5">{children}</div> : null}
        </div>
      </div>
    </article>
  );
}

function TraceFlowOverview({ trace }: { trace: DebugTraceView }) {
  const sessionPayload = asRecord(trace.sessionContext?.payload);
  const routerPayload = asRecord(trace.skillRouter?.payload);
  const agentPayload = asRecord(trace.agentInput?.payload);
  const resultPayload = asRecord(trace.turnResult?.payload);
  const { loops, unlinkedTools } = buildDebugFlowLoops(trace);
  let stageIndex = 1;

  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Execution Flow</p>
          <p className="text-sm text-[var(--foreground)]">按实际执行顺序重组 trace，先看清 agent、模型、工具怎么串起来。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="status-pill">{loops.length} reasoning loops</span>
          <span className="status-pill">{trace.toolCalls.length} tool calls</span>
          {unlinkedTools.length > 0 ? <span className="status-pill">{unlinkedTools.length} unlinked</span> : null}
        </div>
      </div>

      <div className="space-y-3">
        {trace.sessionContext ? (
          <TraceFlowStageCard
            index={stageIndex++}
            actor="Session"
            title="准备本轮上下文"
            badge={typeof sessionPayload?.model_name === "string" ? sessionPayload.model_name : "context"}
            summary={summarizePayload(sessionPayload?.request_message) || "读取当前请求、系统提示词、会话消息与工作记忆。"}
            meta={`tools ${(sessionPayload?.enabled_tool_ids as unknown[] | undefined)?.length ?? 0} · skills ${(sessionPayload?.skills_enabled as unknown[] | undefined)?.length ?? 0}`}
          />
        ) : null}

        {trace.skillRouter ? (
          <TraceFlowStageCard
            index={stageIndex++}
            actor="Router"
            title="选择技能路径"
            badge={
              typeof routerPayload?.selected_skill === "string" && routerPayload.selected_skill
                ? routerPayload.selected_skill
                : "no skill"
            }
            summary={
              (typeof routerPayload?.reason === "string" && routerPayload.reason) ||
              summarizePayload(routerPayload?.response_text) ||
              "根据用户请求和技能卡片决定后续路径。"
            }
            meta={
              typeof routerPayload?.model_name === "string" && routerPayload.model_name
                ? `router model · ${routerPayload.model_name}`
                : null
            }
          />
        ) : null}

        {trace.agentInput ? (
          <TraceFlowStageCard
            index={stageIndex++}
            actor="Agent"
            title="组装最终注入消息"
            badge={typeof agentPayload?.model_name === "string" ? agentPayload.model_name : "agent input"}
            summary={
              summarizePayload(agentPayload?.resolved_message) ||
              summarizePayload(agentPayload?.original_message) ||
              "合并用户请求、memory source、系统提示词与可用工具。"
            }
            meta={
              `memory ${(agentPayload?.memory_sources as unknown[] | undefined)?.length ?? 0} · enabled tools ${(agentPayload?.enabled_tool_ids as unknown[] | undefined)?.length ?? 0}`
            }
          />
        ) : null}

        {loops.length > 0 ? (
          <TraceFlowStageCard
            index={stageIndex++}
            actor="Loops"
            title="按推理轮次查看模型与工具流转"
            badge={`${loops.length} loops`}
            summary="每一轮都表示一次模型决策；如果这一轮触发了工具，工具返回后会进入下一轮继续推理。"
          >
            <div className="space-y-3">
              {loops.map((loop) => (
                <ReasoningLoopCard key={loop.id} loop={loop} />
              ))}
            </div>
          </TraceFlowStageCard>
        ) : null}

        {unlinkedTools.length > 0 ? (
          <TraceFlowStageCard
            index={stageIndex++}
            actor="Tool"
            title="未明确关联到模型决策的工具调用"
            badge={`${unlinkedTools.length} calls`}
            summary="这些工具调用缺少明确的 source run 标识，前端无法严格挂到某个 model run 下面。"
          >
            <div className="space-y-2">
              {unlinkedTools.map((tool, toolIndex) => (
                <TraceFlowToolRow key={tool.key} tool={tool} index={toolIndex} />
              ))}
            </div>
          </TraceFlowStageCard>
        ) : null}

        {trace.turnResult ? (
          <TraceFlowStageCard
            index={stageIndex++}
            actor="Final"
            title="生成最终回答并写回会话"
            badge="final"
            summary={summarizePayload(resultPayload?.assistant_text) || "完成本轮输出，更新会话快照。"}
            meta={trace.turnResult ? `finished at ${formatDate(trace.turnResult.createdAt)}` : null}
          />
        ) : null}
      </div>
    </section>
  );
}

function DebugTraceCard({
  title,
  subtitle,
  badge,
  defaultOpen = false,
  children,
}: {
  title: string;
  subtitle?: string;
  badge?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  return (
    <details
      className="rounded-2xl border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_92%,transparent)] p-3.5"
      open={defaultOpen}
    >
      <summary className="flex cursor-pointer list-none items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-[var(--foreground)]">{title}</p>
          {subtitle ? <p className="mt-1 text-[11px] leading-5 text-[var(--muted)]">{subtitle}</p> : null}
        </div>
        {badge ? <span className="status-pill shrink-0 !px-2 !py-0.5 !text-[10px]">{badge}</span> : null}
      </summary>
      <div className="mt-3 space-y-2.5">{children}</div>
    </details>
  );
}

function DebugTracePayload({
  title,
  value,
  defaultOpen = false,
}: {
  title: string;
  value: unknown;
  defaultOpen?: boolean;
}) {
  if (
    value === undefined ||
    value === null ||
    (typeof value === "string" && !value.trim()) ||
    (Array.isArray(value) && value.length === 0)
  ) {
    return null;
  }

  return (
    <details
      className="rounded-xl border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--background)_56%,white_44%)] px-3 py-2.5"
      open={defaultOpen}
    >
      <summary className="cursor-pointer list-none text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--muted)]">
        {title}
      </summary>
      <div className="mt-2">
        {typeof value === "string" ? (
          <ActivityPayload value={value} />
        ) : (
          <div className="rounded-xl border border-[var(--line)] bg-[var(--background)] p-2.5">
            <JsonTree value={value} />
          </div>
        )}
      </div>
    </details>
  );
}

function DebugTraceExplorer({ rawMessages }: { rawMessages: RawMessage[] }) {
  const currentTurnRawMessages = useMemo(() => getLatestTurnRawMessages(rawMessages), [rawMessages]);
  const trace = useMemo(() => buildDebugTrace(currentTurnRawMessages), [currentTurnRawMessages]);
  const sessionPayload = asRecord(trace.sessionContext?.payload);
  const routerPayload = asRecord(trace.skillRouter?.payload);
  const agentPayload = asRecord(trace.agentInput?.payload);
  const resultPayload = asRecord(trace.turnResult?.payload);

  return (
    <section className="space-y-3">
      <TraceFlowOverview trace={trace} />

      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Trace Explorer</p>
          <p className="text-sm text-[var(--foreground)]">聚焦当前这一轮，先看清模型输入/输出，再下钻每段 payload 细节。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="status-pill">{trace.modelRuns.length} model runs</span>
          <span className="status-pill">{trace.toolCalls.length} tool calls</span>
          <span className="status-pill">{currentTurnRawMessages.length} raw events</span>
          {currentTurnRawMessages.length !== rawMessages.length ? <span className="status-pill">current turn</span> : null}
        </div>
      </div>

      {trace.sessionContext ? (
        <DebugTraceCard
          title="Session Context"
          subtitle={`采样于 ${formatDate(trace.sessionContext.createdAt)}`}
          badge={typeof sessionPayload?.model_name === "string" ? sessionPayload.model_name : undefined}
          defaultOpen
        >
          <DebugTracePayload title="Request Message" value={sessionPayload?.request_message} defaultOpen />
          <DebugTracePayload title="System Prompt" value={sessionPayload?.system_prompt} />
          <DebugTracePayload title="Retrieved Context" value={sessionPayload?.retrieved_context} defaultOpen />
          <DebugTracePayload title="Working Memory" value={sessionPayload?.working_memory} />
          <DebugTracePayload title="Enabled Tools" value={sessionPayload?.enabled_tool_ids} />
          <DebugTracePayload title="Enabled Skills" value={sessionPayload?.skills_enabled} />
          <DebugTracePayload title="Memory Sources" value={sessionPayload?.memory_sources} />
          <DebugTracePayload title="Session Stats" value={sessionPayload?.stats} />
        </DebugTraceCard>
      ) : null}

      {trace.skillRouter ? (
        <DebugTraceCard
          title="Skill Router"
          subtitle={`路由完成于 ${formatDate(trace.skillRouter.createdAt)}`}
          badge={typeof routerPayload?.selected_skill === "string" && routerPayload.selected_skill ? routerPayload.selected_skill : "no skill"}
          defaultOpen
        >
          <DebugTracePayload title="Status" value={routerPayload?.status} defaultOpen />
          <DebugTracePayload title="Reason" value={routerPayload?.reason} defaultOpen />
          <DebugTracePayload title="User Message" value={routerPayload?.user_message} />
          <DebugTracePayload title="Router Prompt" value={routerPayload?.prompt} defaultOpen />
          <DebugTracePayload title="Router Response Text" value={routerPayload?.response_text} defaultOpen />
          <DebugTracePayload title="Parsed Response" value={routerPayload?.parsed_response} defaultOpen />
          <DebugTracePayload title="Available Skills" value={routerPayload?.available_skills ?? routerPayload?.enabled_skills} />
          <DebugTracePayload title="Selected Skill File" value={routerPayload?.selected_skill_file} />
        </DebugTraceCard>
      ) : null}

      {trace.agentInput ? (
        <DebugTraceCard
          title="Agent Input"
          subtitle={`注入 agent 于 ${formatDate(trace.agentInput.createdAt)}`}
          badge={typeof agentPayload?.model_name === "string" ? agentPayload.model_name : undefined}
        >
          <DebugTracePayload title="Original Message" value={agentPayload?.original_message} defaultOpen />
          <DebugTracePayload title="Resolved Message" value={agentPayload?.resolved_message} defaultOpen />
          <DebugTracePayload title="System Prompt" value={agentPayload?.system_prompt} />
          <DebugTracePayload title="Memory Sources" value={agentPayload?.memory_sources} />
          <DebugTracePayload title="Enabled Tool Ids" value={agentPayload?.enabled_tool_ids} />
          <DebugTracePayload title="Allowed Skills" value={agentPayload?.allowed_skill_names} />
          <DebugTracePayload title="Preferred Skill" value={agentPayload?.preferred_skill_name} />
        </DebugTraceCard>
      ) : null}

      {trace.modelRuns.length > 0 ? (
        <div className="space-y-3">
          <div>
            <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Model Runs</p>
            <p className="text-sm text-[var(--foreground)]">每次模型调用的输入、输出、上下文和元数据。</p>
          </div>
          {trace.modelRuns.map((run) => {
            const inputPayload = asRecord(run.inputPayload);
            const outputPayload = asRecord(run.outputPayload);
            const nodeName =
              (typeof outputPayload?.langgraph_node === "string" && outputPayload.langgraph_node) ||
              (typeof inputPayload?.langgraph_node === "string" && inputPayload.langgraph_node) ||
              undefined;
            const durationText =
              typeof outputPayload?.duration_ms === "number"
                ? `${outputPayload.duration_ms} ms`
                : typeof outputPayload?.duration_ms === "string"
                  ? outputPayload.duration_ms
                  : undefined;
            return (
              <DebugTraceCard
                key={run.runId}
                title={run.label}
                subtitle={`run ${shortenDebugId(run.runId)}${run.parentRunId ? ` · parent ${shortenDebugId(run.parentRunId)}` : ""}${nodeName ? ` · node ${nodeName}` : ""}${durationText ? ` · ${durationText}` : ""}`}
                badge={run.status === "running" ? "running" : run.status === "error" ? "error" : "done"}
              >
                <DebugTracePayload title="Started At" value={inputPayload?.started_at} />
                <DebugTracePayload title="Finished At" value={outputPayload?.finished_at} />
                <DebugTracePayload title="Duration" value={outputPayload?.duration_ms} />
                <DebugTracePayload title="LangGraph Node" value={outputPayload?.langgraph_node ?? inputPayload?.langgraph_node} />
                <DebugTracePayload title="Runnable Name" value={outputPayload?.runnable_name ?? inputPayload?.runnable_name} />
                <DebugTracePayload title="Input Text" value={inputPayload?.input_text} defaultOpen />
                <DebugTracePayload title="Message Batches" value={inputPayload?.message_batches} />
                <DebugTracePayload title="Raw Messages" value={inputPayload?.messages} />
                <DebugTracePayload title="Prompts" value={inputPayload?.prompts} />
                <DebugTracePayload title="Serialized Runnable" value={inputPayload?.serialized} />
                <DebugTracePayload title="Invocation Params" value={inputPayload?.invocation_params} />
                <DebugTracePayload title="Tags" value={inputPayload?.tags} />
                <DebugTracePayload title="Metadata" value={inputPayload?.metadata} />
                <DebugTracePayload title="Output Text" value={outputPayload?.output_text ?? outputPayload?.error} defaultOpen />
                <DebugTracePayload title="Token Usage" value={outputPayload?.token_usage} defaultOpen />
                <DebugTracePayload title="Output Messages" value={outputPayload?.output_messages} />
                <DebugTracePayload title="Generations" value={outputPayload?.generations} />
                <DebugTracePayload title="LLM Output" value={outputPayload?.llm_output} />
              </DebugTraceCard>
            );
          })}
        </div>
      ) : null}

      {trace.toolCalls.length > 0 ? (
        <div className="space-y-3">
          <div>
            <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Tool Calls</p>
            <p className="text-sm text-[var(--foreground)]">工具调用的输入、输出和调用标识。</p>
          </div>
          {trace.toolCalls.map((tool) => (
            (() => {
              const startPayload = asRecord(tool.startPayload);
              const endPayload = asRecord(tool.endPayload);
              const nodeName =
                (typeof endPayload?.langgraph_node === "string" && endPayload.langgraph_node) ||
                (typeof startPayload?.langgraph_node === "string" && startPayload.langgraph_node) ||
                undefined;
              const durationText =
                typeof endPayload?.duration_ms === "number"
                  ? `${endPayload.duration_ms} ms`
                  : typeof endPayload?.duration_ms === "string"
                    ? endPayload.duration_ms
                    : undefined;

              return (
                <DebugTraceCard
                  key={tool.key}
                  title={tool.label}
                  subtitle={`${tool.toolCallId ? `call ${shortenDebugId(tool.toolCallId)}` : `started ${formatDate(tool.createdAt)}`}${nodeName ? ` · node ${nodeName}` : ""}${durationText ? ` · ${durationText}` : ""}`}
                  badge={tool.status === "running" ? "running" : "done"}
                >
                  <DebugTracePayload title="Started At" value={startPayload?.started_at ?? endPayload?.started_at} />
                  <DebugTracePayload title="Finished At" value={endPayload?.finished_at} />
                  <DebugTracePayload title="Duration" value={endPayload?.duration_ms} />
                  <DebugTracePayload title="LangGraph Node" value={endPayload?.langgraph_node ?? startPayload?.langgraph_node} />
                  <DebugTracePayload title="Tool Input" value={tool.input} defaultOpen />
                  <DebugTracePayload title="Source Message" value={startPayload?.source_message ?? endPayload?.source_message} />
                  <DebugTracePayload title="Start Metadata" value={startPayload?.stream_metadata} />
                  <DebugTracePayload title="Tool Output" value={tool.output} defaultOpen={tool.status === "done"} />
                  <DebugTracePayload title="Parsed Output" value={endPayload?.output_json} defaultOpen={tool.status === "done"} />
                  <DebugTracePayload title="Artifacts" value={endPayload?.artifacts} />
                  <DebugTracePayload title="Tool Message" value={endPayload?.tool_message} />
                  <DebugTracePayload title="End Metadata" value={endPayload?.stream_metadata} />
                </DebugTraceCard>
              );
            })()
          ))}
        </div>
      ) : null}

      {trace.turnResult ? (
        <DebugTraceCard
          title="Turn Result"
          subtitle={`完成于 ${formatDate(trace.turnResult.createdAt)}`}
          badge="final"
        >
          <DebugTracePayload title="Assistant Text" value={resultPayload?.assistant_text} defaultOpen />
          <DebugTracePayload title="Final Session Snapshot" value={resultPayload?.session_snapshot} />
        </DebugTraceCard>
      ) : null}

      {trace.rawErrors.length > 0 ? (
        <div className="space-y-3">
          <div>
            <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Errors</p>
            <p className="text-sm text-[var(--foreground)]">未被聚合进模型运行的原始异常。</p>
          </div>
          {trace.rawErrors.map((entry, index) => (
            <DebugTraceCard
              key={`raw-error-${index}-${entry.createdAt}`}
              title="Execution Error"
              subtitle={formatDate(entry.createdAt)}
              badge="error"
            >
              <DebugTracePayload title="Payload" value={entry.payload} defaultOpen />
            </DebugTraceCard>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SessionInsightPanel({
  session,
  turnState,
  rawMessages,
  debugOpen,
}: {
  session: SessionRecord | null;
  turnState: TurnState;
  rawMessages: RawMessage[];
  debugOpen: boolean;
}) {
  const workingMemory = session?.working_memory;
  const retrievedContext = session?.retrieved_context ?? [];
  const currentTurnRawMessages = useMemo(() => getLatestTurnRawMessages(rawMessages), [rawMessages]);

  return (
    <aside className="panel sticky top-[5.25rem] hidden max-h-[calc(100vh-6rem)] w-[320px] shrink-0 self-start xl:flex xl:flex-col 2xl:w-[340px]">
      <div className="border-b border-[var(--line)] px-4 py-3">
        <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">Session Workspace</p>
        <p className="text-sm">把当前轮状态、会话记忆和上下文命中放在同一侧栏里。</p>
      </div>
      <div className="space-y-4 overflow-y-auto p-4">
        <section className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Current Turn</p>
              <p className="text-sm text-[var(--foreground)]">{resolveTurnStatusLabel(turnState)}</p>
            </div>
            <span className="status-pill">{turnState.tool_count} tools</span>
          </div>
          <p className="text-[13px] leading-6 text-[var(--muted)]">{resolveTurnCaption(turnState)}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {turnState.selected_skill ? <span className="status-pill">Skill · {turnState.selected_skill}</span> : null}
            {turnState.active_tool ? <span className="status-pill">Tool · {turnState.active_tool}</span> : null}
            {turnState.started_at ? <span className="status-pill">开始于 {formatDate(turnState.started_at)}</span> : null}
          </div>
        </section>

        <section className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-3">
          <div className="mb-2">
            <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Working Memory</p>
            <p className="text-sm text-[var(--foreground)]">当前目标、待办和产物</p>
          </div>
          <div className="space-y-3 text-[13px]">
            <div>
              <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">Current Goal</p>
              <p className="mt-1 leading-6 text-[var(--foreground)]">{workingMemory?.current_goal || "暂无"}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">Open Loops</p>
              <div className="mt-1 flex flex-wrap gap-2">
                {(workingMemory?.open_loops ?? []).length > 0 ? (
                  workingMemory?.open_loops.map((item) => <span key={item} className="status-pill !rounded-2xl">{item}</span>)
                ) : (
                  <span className="text-[var(--muted)]">暂无</span>
                )}
              </div>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">Recent Tools</p>
              <div className="mt-1 flex flex-wrap gap-2">
                {(workingMemory?.recent_tools ?? []).length > 0 ? (
                  workingMemory?.recent_tools.map((item) => <span key={item} className="status-pill">{item}</span>)
                ) : (
                  <span className="text-[var(--muted)]">暂无</span>
                )}
              </div>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--muted)]">Artifacts</p>
              <div className="mt-1 space-y-2">
                {(workingMemory?.artifacts ?? []).length > 0 ? (
                  workingMemory?.artifacts.map((artifact) => (
                    <article key={artifact.path} className="rounded-2xl border border-[var(--line)] bg-[var(--background)] px-3 py-2">
                      <p className="break-all text-[12px] font-medium text-[var(--foreground)]">{artifact.path}</p>
                      <p className="mt-1 text-[11px] leading-5 text-[var(--muted)]">{artifact.description || "artifact"}</p>
                    </article>
                  ))
                ) : (
                  <span className="text-[var(--muted)]">暂无</span>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-3">
          <div className="mb-2">
            <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Retrieved Context</p>
            <p className="text-sm text-[var(--foreground)]">每轮检索命中的上下文片段</p>
          </div>
          <div className="space-y-2.5">
            {retrievedContext.length > 0 ? (
              retrievedContext.map((item) => (
                <article key={`${item.source}-${item.title}`} className="rounded-2xl border border-[var(--line)] bg-[var(--background)] px-3 py-2">
                  <div className="flex items-center justify-between gap-3 text-[11px] text-[var(--muted)]">
                    <span>{item.kind}</span>
                    <span>{item.score.toFixed(2)}</span>
                  </div>
                  <p className="mt-1 text-[12px] font-medium text-[var(--foreground)]">{item.title}</p>
                  <p className="mt-1 text-[11px] leading-5 text-[var(--muted)]">{item.snippet}</p>
                </article>
              ))
            ) : (
              <p className="text-[13px] text-[var(--muted)]">暂无命中。</p>
            )}
          </div>
        </section>

        {debugOpen ? (
          <>
            <DebugTraceExplorer rawMessages={rawMessages} />
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Debug Feed</p>
                  <p className="text-sm text-[var(--foreground)]">当前这一轮的原始事件流，按实际顺序保留。</p>
                </div>
                <span className="status-pill">{currentTurnRawMessages.length} events</span>
              </div>
              {currentTurnRawMessages.map((item) => (
                <DebugEventCard key={item.id} item={item} />
              ))}
            </section>
          </>
        ) : null}
      </div>
    </aside>
  );
}

function DebugEventCard({ item }: { item: RawMessage }) {
  const meta = describeRawKind(item.kind);

  return (
    <article className="rounded-2xl border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_90%,transparent)] p-3.5">
      <div className="mb-2 flex items-center justify-between gap-3 text-xs">
        <div className="min-w-0">
          <p className={`font-medium ${meta.tone}`}>{meta.label}</p>
          <p className="truncate text-[var(--muted)]">{item.kind}</p>
        </div>
        <span className="shrink-0 text-[var(--muted)]">{formatDate(item.created_at)}</span>
      </div>
      <JsonTree value={item.payload} />
    </article>
  );
}

export function Workspace({ page }: { page: WorkspacePage }) {
  const pathname = usePathname();
  const { resolvedTheme, setTheme } = useTheme();
  const abortRef = useRef<AbortController | null>(null);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const shouldAutoScrollRef = useRef(false);
  const composerRef = useRef<HTMLDivElement | null>(null);
  const composerTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const skillUploadInputRef = useRef<HTMLInputElement | null>(null);
  const resizeStateRef = useRef<{
    panel: "skillCards" | "skillFiles";
    startX: number;
    startWidth: number;
  } | null>(null);

  const [options, setOptions] = useState<OptionsPayload | null>(null);
  const [sessionSummaries, setSessionSummaries] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>("");
  const [activeSession, setActiveSession] = useState<SessionRecord | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamAssistant, setStreamAssistant] = useState("");
  const [pendingUserContent, setPendingUserContent] = useState("");
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [memoryFiles, setMemoryFiles] = useState<FileCard[]>([]);
  const [selectedMemoryPath, setSelectedMemoryPath] = useState("");
  const [memoryContent, setMemoryContent] = useState("");
  const [memorySuggestion, setMemorySuggestion] = useState("");
  const [skills, setSkills] = useState<SkillCard[]>([]);
  const [selectedSkillPath, setSelectedSkillPath] = useState("");
  const [skillFiles, setSkillFiles] = useState<SkillFileCard[]>([]);
  const [selectedSkillFilePath, setSelectedSkillFilePath] = useState("");
  const [expandedSkillFolders, setExpandedSkillFolders] = useState<string[]>([]);
  const [skillContent, setSkillContent] = useState("");
  const [skillSuggestion, setSkillSuggestion] = useState("");
  const [skillDraft, setSkillDraft] = useState<SkillDraftState | null>(null);
  const [skillStudioOpen, setSkillStudioOpen] = useState(false);
  const [newSkillName, setNewSkillName] = useState("");
  const [newSkillDescription, setNewSkillDescription] = useState("");
  const [skillViewMode, setSkillViewMode] = useState<"edit" | "preview" | "split">("split");
  const [selectedPromptPath, setSelectedPromptPath] = useState("");
  const [promptContent, setPromptContent] = useState("");
  const [busyLabel, setBusyLabel] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [streamSteps, setStreamSteps] = useState<TimelineStep[]>([]);
  const [streamRawMessages, setStreamRawMessages] = useState<RawMessage[]>([]);
  const [streamTurnState, setStreamTurnState] = useState<TurnState | null>(null);
  const [summaryModalOpen, setSummaryModalOpen] = useState(false);
  const [skillSavePreviewOpen, setSkillSavePreviewOpen] = useState(false);
  const [composerPanel, setComposerPanel] = useState<"tools" | "skills" | "model" | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const [skillCardsWidth, setSkillCardsWidth] = useState(420);
  const [skillFilesWidth, setSkillFilesWidth] = useState(300);

  const activeTurnState = normalizeTurnState(streamTurnState ?? activeSession?.turn_state);
  const memoryEditorLabel = selectedMemoryPath || "No memory file selected";
  const promptEditorLabel = selectedPromptPath ? `prompts/${selectedPromptPath}` : "No prompt selected";
  const enabledToolCount = activeSession
    ? Object.values(activeSession.tool_switches).filter(Boolean).length
    : 0;
  const enabledSkillCount = activeSession?.skills_enabled.length ?? 0;
  const totalToolCount = options?.tool_switches.length ?? 0;
  const totalSkillCount = options?.skills.length ?? 0;
  const activeModelName = activeSession?.model_name ?? options?.default_model ?? "未设置";
  const selectedSkill = skills.find((skill) => normalizeSkillPath(skill.path) === selectedSkillPath) ?? null;
  const selectedSkillFile = skillFiles.find((file) => file.path === selectedSkillFilePath) ?? null;
  const selectedSkillRelativeFilePath =
    selectedSkillFile?.relative_path ?? normalizeSkillPath(selectedSkillFilePath).split("/").slice(1).join("/");
  const skillFileTree = useMemo(() => buildSkillFileTree(skillFiles), [skillFiles]);
  const skillFolderCount = useMemo(() => collectExpandedSkillFolders(skillFileTree).size, [skillFileTree]);
  const editingSkillMainFile = skillDraft ? true : isSkillMainFile(selectedSkillFilePath);
  const currentSkillFileIsMarkdown = skillDraft ? true : isMarkdownLikeFile(selectedSkillFilePath || selectedSkillPath);
  const skillPreview = useMemo(() => splitMarkdownFrontmatter(skillContent), [skillContent]);
  const resolvedDraftSkillName = readFrontmatterField(skillPreview.frontmatter, "name") || skillDraft?.name || "";
  const resolvedDraftSkillDescription = readFrontmatterField(skillPreview.frontmatter, "description") || skillDraft?.description || "";
  const hasChatDraft = chatInput.trim().length > 0;
  const historicalTimelineTurns = useMemo(
    () => (activeSession ? buildTimelineTurns(activeSession.raw_messages) : []),
    [activeSession],
  );
  const displayMessages = useMemo(() => {
    if (!activeSession) {
      return [];
    }
    const messages = [...activeSession.messages];
    if (pendingUserContent) {
      messages.push({
        id: "pending-user",
        role: "user",
        content: pendingUserContent,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
    }
    if (streamAssistant) {
      messages.push({
        id: "pending-assistant",
        role: "assistant",
        content: streamAssistant,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
    }
    return messages;
  }, [activeSession, pendingUserContent, streamAssistant]);
  const displayRawMessages = useMemo(
    () => [...(activeSession?.raw_messages ?? []), ...streamRawMessages],
    [activeSession, streamRawMessages],
  );
  const displayTurns = useMemo(
    () =>
      buildTurnViews({
        messages: displayMessages,
        historicalTimelineTurns,
        streamSteps,
        streamTurnState,
        activeTurnState,
        streaming,
        streamAssistant,
      }),
    [activeTurnState, displayMessages, historicalTimelineTurns, streamAssistant, streamSteps, streamTurnState, streaming],
  );

  function upsertSessionSummary(nextSession: SessionSummary | SessionRecord) {
    setSessionSummaries((current) => replaceSessionSummary(current, nextSession));
  }

  function syncActiveSession(nextSession: SessionRecord) {
    setActiveSession(nextSession);
    setActiveSessionId(nextSession.id);
    upsertSessionSummary(nextSession);
  }

  function scrollChatToBottom(behavior: ScrollBehavior = "smooth") {
    const container = chatScrollRef.current;
    if (!container) {
      return;
    }
    container.scrollTo({ top: container.scrollHeight, behavior });
  }

  function handleChatScroll() {
    const container = chatScrollRef.current;
    if (!container) {
      return;
    }
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    shouldAutoScrollRef.current = distanceToBottom < 120;
  }

  useEffect(() => {
    if (!shouldAutoScrollRef.current) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      scrollChatToBottom(streaming ? "auto" : "smooth");
    });
    return () => window.cancelAnimationFrame(frame);
  }, [displayMessages, displayTurns, streaming, debugOpen, streamTurnState]);

  useEffect(() => {
    const textarea = composerTextareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 240)}px`;
  }, [chatInput]);

  useEffect(() => {
    const handlePointerMove = (event: PointerEvent) => {
      const resizeState = resizeStateRef.current;
      if (!resizeState) {
        return;
      }

      const deltaX = event.clientX - resizeState.startX;
      if (resizeState.panel === "skillCards") {
        setSkillCardsWidth(clamp(resizeState.startWidth + deltaX, SKILL_CARDS_MIN_WIDTH, SKILL_CARDS_MAX_WIDTH));
        return;
      }

      setSkillFilesWidth(clamp(resizeState.startWidth + deltaX, SKILL_FILES_MIN_WIDTH, SKILL_FILES_MAX_WIDTH));
    };

    const stopResize = () => {
      if (!resizeStateRef.current) {
        return;
      }

      resizeStateRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
      stopResize();
    };
  }, []);

  useEffect(() => {
    if (!composerPanel) {
      return;
    }

    function handlePointerDown(event: MouseEvent) {
      if (!composerRef.current?.contains(event.target as Node)) {
        setComposerPanel(null);
      }
    }

    window.addEventListener("mousedown", handlePointerDown);
    return () => window.removeEventListener("mousedown", handlePointerDown);
  }, [composerPanel]);

  useEffect(() => {
    void (async () => {
      try {
        const loadedOptions = await api.options();
        setOptions(loadedOptions);

        if (page === "chat") {
          const loadedSummaries = await api.listSessions();
          setSessionSummaries(loadedSummaries);
          if (loadedSummaries.length === 0) {
            const created = await api.createSession(loadedOptions.default_model);
            setActiveSession(created);
            setActiveSessionId(created.id);
            setSessionSummaries([toSessionSummary(created)]);
          } else {
            const detail = await api.getSession(loadedSummaries[0].id);
            setActiveSession(detail);
            setActiveSessionId(detail.id);
            setSessionSummaries(replaceSessionSummary(loadedSummaries, detail));
          }
          return;
        }

        if (page === "memory") {
          const files = await api.listMemoryFiles();
          setMemoryFiles(files);
          if (files[0]) {
            setSelectedMemoryPath(files[0].path);
            const payload = await api.getMemoryFile(files[0].path);
            setMemoryContent(payload.content);
          } else {
            setSelectedMemoryPath("");
            setMemoryContent("");
            setMemorySuggestion("");
          }
          return;
        }

        if (page === "prompts") {
          const defaultPrompt = MANAGED_PROMPTS[0];
          setSelectedPromptPath(defaultPrompt.path);
          const payload = await api.getPromptFile(defaultPrompt.path);
          setPromptContent(payload.content);
          return;
        }

        const loadedSkills = await api.listSkills();
        setSkills(loadedSkills);
        if (loadedSkills[0]) {
          const relativePath = normalizeSkillPath(loadedSkills[0].path);
          setSelectedSkillPath(relativePath);
          setSelectedSkillFilePath(relativePath);
          const [files, payload] = await Promise.all([
            api.listSkillFiles(relativePath),
            api.getSkillFile(relativePath),
          ]);
          setSkillFiles(files);
          setSkillContent(payload.content);
        } else {
          setSelectedSkillPath("");
          setSelectedSkillFilePath("");
          setSkillFiles([]);
          setSkillContent("");
          setSkillSuggestion("");
        }
      } catch (error) {
        console.error(error);
      }
    })();
  }, [page]);

  useEffect(() => {
    if (!selectedMemoryPath) {
      return;
    }
    void (async () => {
      try {
        const payload = await api.getMemoryFile(selectedMemoryPath);
        setMemoryContent(payload.content);
        setMemorySuggestion("");
      } catch (error) {
        setMemoryContent(`加载失败\n\n${getErrorMessage(error)}`);
        setMemorySuggestion("");
      }
    })();
  }, [selectedMemoryPath]);

  useEffect(() => {
    if (!selectedSkillPath || skillDraft) {
      return;
    }
    void (async () => {
      try {
        const files = await api.listSkillFiles(selectedSkillPath);
        setSkillFiles(files);
        const fallbackFile =
          files.find((file) => file.path === selectedSkillFilePath)?.path ??
          normalizeSkillPath(
            files.find((file) => isSkillMainFile(file.path))?.path ?? files[0]?.path ?? selectedSkillPath,
          );
        if (!fallbackFile) {
          setSelectedSkillFilePath("");
          setSkillContent("");
          setSkillSuggestion("");
          return;
        }
        setSelectedSkillFilePath(fallbackFile);
      } catch {
        setSkillFiles([]);
      }
    })();
  }, [selectedSkillPath, selectedSkillFilePath, skillDraft]);

  useEffect(() => {
    if (!selectedSkillFilePath) {
      return;
    }
    void (async () => {
      try {
        const payload = await api.getSkillFile(selectedSkillFilePath);
        setSkillContent(payload.content);
        setSkillSuggestion("");
      } catch (error) {
        setSkillContent(`加载失败\n\n${getErrorMessage(error)}`);
        setSkillSuggestion("");
      }
    })();
  }, [selectedSkillFilePath]);

  useEffect(() => {
    if (!skillFileTree.length || skillDraft) {
      setExpandedSkillFolders([]);
      return;
    }

    const availableFolders = collectExpandedSkillFolders(skillFileTree);
    setExpandedSkillFolders((current) => {
      const nextFolders = new Set(current.filter((path) => availableFolders.has(path)));
      if (nextFolders.size === 0) {
        availableFolders.forEach((path) => nextFolders.add(path));
      }
      collectSelectedSkillFolderPaths(selectedSkillFilePath).forEach((path) => nextFolders.add(path));
      return Array.from(nextFolders);
    });
  }, [skillDraft, skillFileTree, selectedSkillFilePath]);

  useEffect(() => {
    if (!selectedPromptPath) {
      return;
    }
    void (async () => {
      try {
        const payload = await api.getPromptFile(selectedPromptPath);
        setPromptContent(payload.content);
      } catch (error) {
        setPromptContent(
          `加载失败\n\n${getErrorMessage(error)}\n\n如果你刚新增了 Prompt 管理入口，但后端仍返回 Not Found，通常是后端服务还没重启。`,
        );
      }
    })();
  }, [selectedPromptPath]);

  async function createSession() {
    if (!options) {
      return;
    }
    const created = await api.createSession(options.default_model);
    resetStreamingDraft();
    syncActiveSession(created);
  }

  async function selectSession(sessionId: string) {
    const session = await api.getSession(sessionId);
    startTransition(() => {
      resetStreamingDraft();
      syncActiveSession(session);
    });
  }

  async function renameSession(session: SessionSummary) {
    const title = window.prompt("输入新的会话标题", session.title);
    if (!title) {
      return;
    }
    const updated = await api.updateSession(session.id, { title });
    upsertSessionSummary(updated);
    if (activeSession?.id === updated.id) {
      setActiveSession(updated);
    }
  }

  async function removeSession(session: SessionSummary) {
    if (!window.confirm(`删除会话 “${session.title}”？`)) {
      return;
    }
    await api.deleteSession(session.id);
    const rest = sessionSummaries.filter((item) => item.id !== session.id);
    resetStreamingDraft();
    setSessionSummaries(rest);
    if (activeSession?.id === session.id) {
      setActiveSession(null);
      setActiveSessionId("");
      if (rest.length > 0) {
        const nextSession = await api.getSession(rest[0].id);
        syncActiveSession(nextSession);
      }
    }
    if (rest.length === 0) {
      await createSession();
    }
  }

  async function copyText(value: string) {
    await navigator.clipboard.writeText(value);
  }

  function startPanelResize(
    panel: "skillCards" | "skillFiles",
    event: ReactPointerEvent<HTMLButtonElement>,
    currentWidth: number,
  ) {
    resizeStateRef.current = {
      panel,
      startX: event.clientX,
      startWidth: currentWidth,
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  async function copyMessage(messageId: string, value: string) {
    await copyText(value);
    setCopiedMessageId(messageId);
    window.setTimeout(() => {
      setCopiedMessageId((current) => (current === messageId ? null : current));
    }, 1200);
  }

  function resetStreamingDraft() {
    setStreamAssistant("");
    setStreamSteps([]);
    setStreamRawMessages([]);
    setStreamTurnState(null);
    setStopRequested(false);
    setPendingUserContent("");
  }

  async function sendMessage(overrideText?: string) {
    if (!activeSession || streaming) {
      return;
    }

    const nextMessage = (overrideText ?? chatInput).trim();
    if (!nextMessage) {
      return;
    }

    const controller = new AbortController();
    const startedAt = new Date().toISOString();
    abortRef.current = controller;
    shouldAutoScrollRef.current = true;
    setStreaming(true);
    setStreamSteps([]);
    setStreamTurnState(null);
    setStopRequested(false);
    setStreamRawMessages(
      activeSession
        ? [
            {
              id: `system-${Date.now()}`,
              kind: "system",
              payload: { role: "system", content: activeSession.system_prompt },
              created_at: startedAt,
            },
            {
              id: `user-${Date.now()}`,
              kind: "user",
              payload: { role: "user", content: nextMessage },
              created_at: startedAt,
            },
          ]
        : [],
    );
    setPendingUserContent(nextMessage);
    setStreamAssistant("");
    setChatInput("");

    try {
      let finishedWithDone = false;

      await api.streamMessage(
        activeSession.id,
        {
          message: nextMessage,
          model_name: activeSession.model_name,
          debug: debugOpen,
          tool_switches: activeSession.tool_switches,
          skills_enabled: activeSession.skills_enabled,
        },
        (event) => {
          if (event.type === "turn_state") {
            setStreamTurnState(event.turn);
          }
          if (event.type === "token") {
            setStreamAssistant((current) => current + event.text);
            return;
          }
          if (event.type === "skill" || event.type === "tool_start" || event.type === "tool_end" || event.type === "error") {
            setStreamSteps((current) => mergeStreamEventIntoSteps(current, event));
          }
          const rawMessage = createStreamRawMessage(event);
          if (rawMessage) {
            setStreamRawMessages((current) => current.concat(rawMessage));
          }
          if (event.type === "title") {
            setSessionSummaries((current) =>
              current.map((session) =>
                session.id === activeSession.id ? { ...session, title: event.title } : session,
              ),
            );
            setActiveSession((current) => (current ? { ...current, title: event.title } : current));
            return;
          }
          if (event.type === "done") {
            finishedWithDone = true;
            syncActiveSession(event.session);
            resetStreamingDraft();
            shouldAutoScrollRef.current = true;
            return;
          }
          if (event.type === "error") {
            setStreamAssistant((current) => current || `[ERROR] ${event.message}`);
          }
        },
        controller.signal,
      );

      if (!finishedWithDone) {
        const refreshed = await api.getSession(activeSession.id);
        syncActiveSession(refreshed);
        resetStreamingDraft();
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      setStreamAssistant(`[ERROR] ${error instanceof Error ? error.message : "发送失败"}`);
      setStreamSteps((current) =>
        current.concat({
          id: `error-${current.length}`,
          kind: "error",
          label: "执行异常",
          detail: error instanceof Error ? error.message : "发送失败",
        }),
      );
      setStreamRawMessages((current) =>
        current.concat(
          createTransientRawMessage("error", {
            message: error instanceof Error ? error.message : "发送失败",
          }),
        ),
      );
    } finally {
      setStreaming(false);
      abortRef.current = null;
      setStopRequested(false);
    }
  }

  async function stopStreaming() {
    if (!activeSession || !streaming || stopRequested) {
      return;
    }
    setStopRequested(true);
    try {
      const updated = await api.cancelSessionTurn(activeSession.id);
      shouldAutoScrollRef.current = true;
      syncActiveSession(updated);
      setStreamTurnState(updated.turn_state);
    } catch (error) {
      setStopRequested(false);
      window.alert(getErrorMessage(error));
    }
  }

  async function saveMessageEditAndRetry(message: ChatMessage) {
    if (!activeSession) {
      return;
    }
    const truncated = await api.truncateFromMessage(activeSession.id, message.id);
    syncActiveSession(truncated);
    setEditingMessageId(null);
    await sendMessage(editValue);
  }

  async function toggleTool(toolId: string, enabled: boolean) {
    if (!activeSession) {
      return;
    }
    const updated = await api.updateSession(activeSession.id, {
      tool_switches: { ...activeSession.tool_switches, [toolId]: enabled },
    });
    syncActiveSession(updated);
  }

  async function toggleSkill(skillName: string, enabled: boolean) {
    if (!activeSession) {
      return;
    }
    const nextSkills = enabled
      ? [...new Set([...activeSession.skills_enabled, skillName])]
      : activeSession.skills_enabled.filter((item) => item !== skillName);
    const updated = await api.updateSession(activeSession.id, { skills_enabled: nextSkills });
    syncActiveSession(updated);
  }

  async function changeModel(modelName: string) {
    if (!activeSession) {
      return;
    }
    const updated = await api.updateSession(activeSession.id, { model_name: modelName });
    syncActiveSession(updated);
  }

  async function compressConversation() {
    if (!activeSession) {
      return;
    }
    setBusyLabel("压缩中...");
    try {
      const result = await api.compressSession(activeSession.id);
      syncActiveSession(result.session);
    } finally {
      setBusyLabel("");
    }
  }

  function toggleComposerPanel(panel: "tools" | "skills" | "model") {
    setComposerPanel((current) => (current === panel ? null : panel));
  }

  async function saveMemory() {
    if (!selectedMemoryPath) {
      return;
    }
    setBusyLabel("保存中...");
    try {
      await api.saveMemoryFile(selectedMemoryPath, memoryContent);
    } finally {
      setBusyLabel("");
    }
  }

  async function optimizeMemory() {
    setBusyLabel("优化中...");
    try {
      const result = await api.optimizeMemory(selectedMemoryPath || null, memoryContent);
      setMemorySuggestion(result.suggestion);
    } finally {
      setBusyLabel("");
    }
  }

  async function saveSkill() {
    if (!selectedSkillFilePath) {
      return;
    }
    setBusyLabel("保存中...");
    try {
      await api.saveSkillFile(selectedSkillFilePath, skillContent);
      await refreshSkillsPanel(selectedSkillPath, selectedSkillFilePath);
    } finally {
      setBusyLabel("");
    }
  }

  function confirmDiscardSkillDraft() {
    if (!skillDraft) {
      return true;
    }
    return window.confirm("当前有未保存的技能草稿，继续会丢失这些内容。是否继续？");
  }

  function selectSkillCard(relativePath: string) {
    if (skillDraft && !confirmDiscardSkillDraft()) {
      return;
    }
    setSkillDraft(null);
    setSelectedSkillPath(relativePath);
    setSelectedSkillFilePath(relativePath);
  }

  function selectSkillFile(relativePath: string) {
    setSelectedSkillFilePath(relativePath);
  }

  function toggleSkillFolder(path: string) {
    setExpandedSkillFolders((current) =>
      current.includes(path) ? current.filter((item) => item !== path) : [...current, path],
    );
  }

  async function optimizeSkill() {
    if (!selectedSkillFilePath || !editingSkillMainFile) {
      return;
    }
    setBusyLabel("优化中...");
    try {
      const result = await api.optimizeSkill(selectedSkillFilePath, skillContent);
      setSkillSuggestion(result.suggestion);
    } finally {
      setBusyLabel("");
    }
  }

  async function refreshSkillsPanel(nextSelectedPath?: string, nextSelectedFilePath?: string) {
    const loadedSkills = await api.listSkills();
    setSkills(loadedSkills);
    setOptions((current) => (current ? { ...current, skills: loadedSkills } : current));

    const preferredPath = nextSelectedPath || selectedSkillPath;
    const availablePath = preferredPath
      ? loadedSkills.find((skill) => normalizeSkillPath(skill.path) === preferredPath)
      : undefined;
    const fallbackPath = availablePath ? preferredPath : normalizeSkillPath(loadedSkills[0]?.path ?? "");
    if (!fallbackPath) {
      setSelectedSkillPath("");
      setSelectedSkillFilePath("");
      setSkillFiles([]);
      setSkillContent("");
      setSkillSuggestion("");
      return;
    }
    setSelectedSkillPath(fallbackPath);
    const files = await api.listSkillFiles(fallbackPath);
    setSkillFiles(files);
    const preferredFile = nextSelectedFilePath || selectedSkillFilePath;
    const fallbackFile =
      files.find((file) => file.path === preferredFile)?.path ??
      normalizeSkillPath(files.find((file) => isSkillMainFile(file.path))?.path ?? files[0]?.path ?? fallbackPath);
    setSelectedSkillFilePath(fallbackFile);
    const payload = await api.getSkillFile(fallbackFile);
    setSkillContent(payload.content);
    setSkillSuggestion("");
  }

  async function deleteSkillByPath(path: string, name: string) {
    if (!path) {
      return;
    }
    if (!window.confirm(`删除技能“${name}”？该技能目录会被一并删除。`)) {
      return;
    }

    setBusyLabel("删除技能中...");
    try {
      await api.deleteSkillFile(path);
      const currentRoot = skillRootFromPath(path);
      const nextRoot = selectedSkillPath === path ? undefined : selectedSkillPath;
      await refreshSkillsPanel(nextRoot);
      if (skillRootFromPath(selectedSkillFilePath) === currentRoot) {
        setSelectedSkillFilePath("");
      }
    } catch (error) {
      window.alert(getErrorMessage(error));
    } finally {
      setBusyLabel("");
    }
  }

  async function savePrompt() {
    if (!selectedPromptPath) {
      return;
    }
    setBusyLabel("保存中...");
    try {
      await api.savePromptFile(selectedPromptPath, promptContent);
    } finally {
      setBusyLabel("");
    }
  }

  async function createSkillDraft() {
    const name = newSkillName.trim();
    const description = newSkillDescription.trim();
    if (!name || !description) {
      window.alert("请先填写技能名称和描述");
      return;
    }
    if (skillDraft && !confirmDiscardSkillDraft()) {
      return;
    }
    const slug = slugifySkillName(name);
    setSkillDraft({ mode: "create", slug, name, description });
    setSkillStudioOpen(false);
    setSelectedSkillPath("");
    setSelectedSkillFilePath("");
    setSkillFiles([]);
    setSkillContent(buildSkillDraftContent(name, description, slug));
    setSkillSuggestion("");
    setNewSkillName("");
    setNewSkillDescription("");
    setSkillViewMode("split");
  }

  async function uploadLocalSkill(file: File) {
    if (skillDraft && !confirmDiscardSkillDraft()) {
      return;
    }
    setBusyLabel("读取技能中...");
    try {
      const content = await file.text();
      const parsed = splitMarkdownFrontmatter(content);
      const fallbackName = file.name.replace(/\.[^.]+$/, "") || "custom-skill";
      const name = readFrontmatterField(parsed.frontmatter, "name") || fallbackName;
      const description = readFrontmatterField(parsed.frontmatter, "description") || `${name} 自定义技能`;
      setSkillDraft({
        mode: "upload",
        slug: slugifySkillName(name),
        name,
        description,
        filename: file.name,
      });
      setSkillStudioOpen(false);
      setSelectedSkillPath("");
      setSelectedSkillFilePath("");
      setSkillFiles([]);
      setSkillContent(content);
      setSkillSuggestion("");
      setSkillViewMode("split");
    } catch (error) {
      window.alert(getErrorMessage(error));
    } finally {
      setBusyLabel("");
    }
  }

  function openSkillSavePreview() {
    if (!selectedSkillFilePath && !skillDraft) {
      return;
    }
    setSkillSavePreviewOpen(true);
  }

  async function confirmSkillSave() {
    if (skillDraft) {
      if (!resolvedDraftSkillName || !resolvedDraftSkillDescription) {
        window.alert("预览中缺少技能名称或描述，请先补全 frontmatter 后再保存。");
        return;
      }
      setBusyLabel("保存技能中...");
      try {
        const result =
          skillDraft.mode === "create"
            ? await api.createSkill({
                name: resolvedDraftSkillName,
                description: resolvedDraftSkillDescription,
                slug: skillDraft.slug,
                content: skillContent,
              })
            : await api.uploadSkill({
                filename: skillDraft.filename ?? `${skillDraft.slug}.md`,
                name: resolvedDraftSkillName,
                description: resolvedDraftSkillDescription,
                slug: skillDraft.slug,
                content: skillContent,
              });
        setSkillDraft(null);
        setSkillSavePreviewOpen(false);
        await refreshSkillsPanel(result.path, result.path);
      } catch (error) {
        window.alert(getErrorMessage(error));
      } finally {
        setBusyLabel("");
      }
      return;
    }

    await saveSkill();
    setSkillSavePreviewOpen(false);
  }

  function renderSkillTreeNode(treeNode: SkillTreeNode, depth = 0) {
    if (treeNode.kind === "folder") {
      const expanded = expandedSkillFolders.includes(treeNode.path);
      const containsSelectedFile =
        selectedSkillRelativeFilePath === treeNode.path || selectedSkillRelativeFilePath.startsWith(`${treeNode.path}/`);
      return (
        <div key={treeNode.id} className="space-y-1">
          <button
            className={`flex w-full items-center gap-1.5 rounded-xl border px-2 py-1.5 text-left text-xs transition ${
              containsSelectedFile
                ? "border-[color:color-mix(in_srgb,var(--accent)_24%,var(--line))] bg-[color:color-mix(in_srgb,var(--accent-soft)_40%,var(--panel))] text-[var(--foreground)]"
                : "border-transparent text-[var(--foreground)] hover:border-[var(--line)] hover:bg-[color:color-mix(in_srgb,var(--background)_82%,white_18%)]"
            }`}
            onClick={() => toggleSkillFolder(treeNode.path)}
            style={{ marginLeft: `${depth * 0.25}rem` }}
            type="button"
          >
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-[var(--line)] bg-[var(--background)] text-[var(--muted)]">
              {expanded ? <ChevronDown className="h-2.5 w-2.5" /> : <ChevronRight className="h-2.5 w-2.5" />}
            </span>
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-lg bg-[color:color-mix(in_srgb,var(--accent-soft)_52%,white_48%)] text-[var(--accent)]">
              {expanded ? <FolderOpen className="h-2.5 w-2.5" /> : <Folder className="h-2.5 w-2.5" />}
            </span>
            <span className="min-w-0 flex-1 truncate font-medium leading-5">{treeNode.name}</span>
            <span className="rounded-full border border-[var(--line)] px-1.5 py-0 text-[8px] uppercase tracking-[0.08em] text-[var(--muted)]">
              {treeNode.children.length}
            </span>
          </button>
          {expanded ? (
            <div
              className="ml-3 border-l border-[color:color-mix(in_srgb,var(--line)_78%,transparent)] pl-2"
              style={{ marginLeft: `${0.7 + depth * 0.25}rem` }}
            >
              <div className="space-y-0.5 py-0.5">{treeNode.children.map((child) => renderSkillTreeNode(child, depth + 1))}</div>
            </div>
          ) : null}
        </div>
      );
    }

    const selected = treeNode.path === selectedSkillFilePath;
    const fileType = skillFileDisplayType(treeNode.path);
    const fileLanguage = skillFileLanguage(treeNode.path);
    const isPrimary = isSkillMainFile(treeNode.path);

    return (
      <button
        key={treeNode.id}
        className={`flex w-full items-center gap-1.5 rounded-xl border px-2 py-1.5 text-left text-xs transition ${
          selected
            ? "border-[color:color-mix(in_srgb,var(--accent)_30%,var(--line))] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--accent-soft)_64%,white_36%),color-mix(in_srgb,var(--panel)_96%,transparent))] text-[var(--foreground)] shadow-[0_22px_42px_-34px_rgba(0,0,0,0.4)]"
            : "border-transparent text-[var(--foreground)] hover:border-[var(--line)] hover:bg-[color:color-mix(in_srgb,var(--background)_82%,white_18%)]"
        }`}
        onClick={() => selectSkillFile(treeNode.path)}
        style={{ marginLeft: `${depth * 0.25}rem` }}
        type="button"
      >
        <span
          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-lg ${
            isPrimary
              ? "bg-[color:color-mix(in_srgb,var(--accent-soft)_60%,white_40%)] text-[var(--accent)]"
              : "bg-[var(--background)] text-[var(--muted)]"
          }`}
        >
          {fileLanguage === "json" ? (
            <FileJson className="h-2.5 w-2.5" />
          ) : fileLanguage === "markdown" ? (
            <FileText className="h-2.5 w-2.5" />
          ) : (
            <FileCode2 className="h-2.5 w-2.5" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[11px] font-medium leading-4">{treeNode.name}</span>
          <span className="block truncate text-[9px] leading-4 text-[var(--muted)]">{treeNode.relativePath}</span>
        </span>
        <span
          className={`rounded-full border px-1.5 py-0 text-[8px] uppercase tracking-[0.08em] ${
            selected
              ? "border-[color:color-mix(in_srgb,var(--accent)_22%,var(--line))] bg-[var(--background)] text-[var(--accent)]"
              : "border-[var(--line)] text-[var(--muted)]"
          }`}
        >
          {fileType}
        </span>
      </button>
    );
  }

  const navItems = [
    { href: "/", label: "对话", icon: MessageSquareText },
    { href: "/memory", label: "记忆", icon: BrainCircuit },
    { href: "/skills", label: "技能", icon: Wrench },
    { href: "/prompts", label: "Prompts", icon: Sparkles },
  ];

  return (
    <div className="min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      <header className="fixed inset-x-0 top-0 z-40 h-[4.25rem] border-b border-[var(--line)] bg-[color:color-mix(in_srgb,var(--background)_68%,white_14%)] backdrop-blur-2xl">
        <div className="mx-auto flex h-full max-w-[1800px] items-center justify-end px-5">
          <div className="flex items-center gap-3 text-sm">
            <button
              className="icon-button"
              onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
              type="button"
            >
              {resolvedTheme === "dark" ? <SunMedium className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <a className="hidden text-[var(--muted)] transition hover:text-[var(--accent)] xl:block" href="https://seraph.ai" target="_blank" rel="noreferrer">
              SERAPH平台
            </a>
          </div>
        </div>
      </header>

      <div
        className={`mx-auto flex max-w-[1800px] items-start gap-4 px-4 pt-[5.4rem] ${
          page === "chat" ? "h-[100dvh] overflow-hidden pb-5" : "pb-5"
        }`}
      >
        <aside className="sticky top-[5.4rem] hidden h-[calc(100vh-6.2rem)] w-[248px] shrink-0 self-start flex-col gap-4 lg:flex xl:w-[260px]">
          <section className="panel p-4">
            <div className="mb-4">
              <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">Workspace</p>
            </div>
            <nav className="space-y-2">
              {navItems.map((item) => {
                const Icon = item.icon;
                const active = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    className={`nav-item ${active ? "nav-item-active" : ""}`}
                    href={item.href}
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </section>

          {page === "chat" ? (
            <section className="panel flex min-h-0 flex-1 flex-col">
              <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-4">
                <div className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">Sessions</div>
                <button className="icon-button" onClick={() => void createSession()} type="button">
                  <Plus className="h-4 w-4" />
                </button>
              </div>
              <div className="space-y-2 overflow-y-auto p-3">
                {sessionSummaries.map((session) => (
                  <div
                    key={session.id}
                    className={`session-card ${session.id === activeSessionId ? "session-card-active" : ""}`}
                    onClick={() => void selectSession(session.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        void selectSession(session.id);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 text-left">
                        <p className="truncate text-sm font-medium">{session.title}</p>
                        <p className="truncate text-xs text-[var(--muted)]">{session.model_name}</p>
                      </div>
                      <div className="flex gap-1">
                        <button
                          className="icon-button"
                          onClick={(event) => {
                            event.stopPropagation();
                            void renameSession(session);
                          }}
                          type="button"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          className="icon-button"
                          onClick={(event) => {
                            event.stopPropagation();
                            void removeSession(session);
                          }}
                          type="button"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-[11px] text-[var(--muted)]">
                      <span>输入 {session.stats.input_tokens}</span>
                      <span>输出 {session.stats.output_tokens}</span>
                      <span>上下文 {session.stats.context_tokens}</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </aside>

        {page === "chat" ? (
          <>
            <ChatShell
              bodyRef={chatScrollRef}
              composer={
                <div className="relative">
                    <div
                      className="relative overflow-visible rounded-[1.1rem] border border-[color:color-mix(in_srgb,var(--accent)_12%,var(--line))] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel-strong)_98%,white_2%),color-mix(in_srgb,var(--panel)_94%,transparent))] px-2.5 pb-2 pt-1.5 shadow-[0_14px_28px_-20px_rgba(34,24,15,0.18),0_0_0_1px_rgba(255,255,255,0.12)_inset] backdrop-blur-2xl dark:border-[color:color-mix(in_srgb,var(--accent)_16%,var(--line))] dark:bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel-strong)_94%,transparent),color-mix(in_srgb,var(--panel-deep)_92%,transparent))] dark:shadow-[0_16px_32px_-22px_rgba(0,0,0,0.4),0_0_0_1px_rgba(255,255,255,0.04)_inset]"
                      ref={composerRef}
                    >
                      <div className="rounded-[0.95rem] border border-[color:color-mix(in_srgb,var(--foreground)_6%,white_94%)] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--background)_24%,white_76%),color-mix(in_srgb,var(--panel)_96%,transparent))] px-3 py-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.55)] dark:border-[color:color-mix(in_srgb,var(--line)_86%,transparent)] dark:bg-[linear-gradient(180deg,color-mix(in_srgb,var(--background)_54%,transparent),color-mix(in_srgb,var(--panel)_92%,transparent))] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
                        <textarea
                          className="min-h-[1.4rem] w-full resize-none overflow-y-auto bg-transparent text-[13px] leading-6 text-[color:#43382f] outline-none placeholder:text-[14px] placeholder:font-medium placeholder:text-[rgba(107,102,117,0.42)] dark:text-[var(--foreground)] dark:placeholder:text-[color:color-mix(in_srgb,var(--muted)_62%,white_38%)]"
                          placeholder="继续追问、补充约束，或让它接着上一步往下做"
                          rows={1}
                          ref={composerTextareaRef}
                          value={chatInput}
                          onChange={(event) => setChatInput(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" && !event.shiftKey) {
                              event.preventDefault();
                              if (streaming) {
                                void stopStreaming();
                              } else {
                                void sendMessage();
                              }
                            }
                          }}
                        />
                      </div>

                      <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 border-t border-[rgba(34,24,15,0.06)] pt-1.5 dark:border-[var(--line)]">
                      <div className="relative flex flex-1 flex-wrap items-center gap-2">
                        {composerPanel ? (
                          <div className="absolute bottom-full left-0 z-20 mb-2.5 w-full max-w-[760px] overflow-hidden rounded-[1.35rem] border border-[color:color-mix(in_srgb,var(--accent)_12%,var(--line))] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel-strong)_98%,white_2%),color-mix(in_srgb,var(--panel)_96%,transparent))] p-3.5 shadow-[0_20px_44px_-30px_rgba(34,24,15,0.24)] backdrop-blur-xl dark:border-[color:color-mix(in_srgb,var(--accent)_14%,var(--line))] dark:bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel-strong)_96%,transparent),color-mix(in_srgb,var(--panel-deep)_94%,transparent))]">
                            {composerPanel === "model" ? (
                              <div className="space-y-2">
                                <div className="flex items-center justify-between gap-3">
                                  <p className="text-[11px] uppercase tracking-[0.2em] text-[var(--muted)]">模型引擎</p>
                                  <span className="text-xs text-[var(--muted)]">{activeModelName}</span>
                                </div>
                                <select
                                  className="input-chip w-full"
                                  value={activeModelName}
                                  onChange={(event) => void changeModel(event.target.value)}
                                >
                                  {options?.models.map((model) => (
                                    <option key={model} value={model}>
                                      {model}
                                    </option>
                                  ))}
                                </select>
                              </div>
                            ) : null}
                            {composerPanel === "tools" ? (
                              <div className="space-y-3">
                                <div className="flex items-center justify-between gap-3">
                                  <p className="text-[11px] uppercase tracking-[0.2em] text-[var(--muted)]">工具能力</p>
                                  <span className="text-xs text-[var(--muted)]">{enabledToolCount}/{totalToolCount}</span>
                                </div>
                                <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                                  {options?.tool_switches.map((tool) => (
                                    <label
                                      key={tool.id}
                                      className="flex items-center justify-between rounded-[1rem] border border-[rgba(34,24,15,0.08)] bg-[rgba(247,246,243,0.96)] px-3 py-2 text-[13px] dark:border-[var(--line)] dark:bg-[color:color-mix(in_srgb,var(--panel)_86%,transparent)]"
                                    >
                                      <span className="truncate pr-3">{tool.label}</span>
                                      <input
                                        checked={Boolean(activeSession?.tool_switches[tool.id])}
                                        onChange={(event) => void toggleTool(tool.id, event.target.checked)}
                                        type="checkbox"
                                      />
                                    </label>
                                  ))}
                                </div>
                              </div>
                            ) : null}
                            {composerPanel === "skills" ? (
                              <div className="space-y-3">
                                <div className="flex items-center justify-between gap-3">
                                  <p className="text-[11px] uppercase tracking-[0.2em] text-[var(--muted)]">技能助手</p>
                                  <span className="text-xs text-[var(--muted)]">{enabledSkillCount}/{totalSkillCount}</span>
                                </div>
                                <div className="grid max-h-[14rem] gap-2 overflow-y-auto md:grid-cols-2 xl:grid-cols-3">
                                  {options?.skills.map((skill) => (
                                    <label
                                      key={skill.name}
                                      className="flex items-center justify-between rounded-[1rem] border border-[rgba(34,24,15,0.08)] bg-[rgba(247,246,243,0.96)] px-3 py-2 text-[13px] dark:border-[var(--line)] dark:bg-[color:color-mix(in_srgb,var(--panel)_86%,transparent)]"
                                    >
                                      <span className="truncate pr-3">{skill.name}</span>
                                      <input
                                        checked={activeSession?.skills_enabled.includes(skill.name) ?? false}
                                        onChange={(event) => void toggleSkill(skill.name, event.target.checked)}
                                        type="checkbox"
                                      />
                                    </label>
                                  ))}
                                </div>
                              </div>
                            ) : null}
                          </div>
                        ) : null}
                        <button
                          className={`flex min-w-[2.6rem] items-center justify-between rounded-full border px-1.5 py-[0.32rem] text-[9px] leading-none transition ${
                            composerPanel === "tools"
                              ? "border-[color:color-mix(in_srgb,var(--accent)_26%,var(--line))] bg-[color:color-mix(in_srgb,var(--accent-soft)_56%,white_44%)] text-[color:#403832] shadow-[inset_0_1px_0_rgba(255,255,255,0.5)] dark:bg-[color:color-mix(in_srgb,var(--panel-deep)_92%,transparent)] dark:text-[var(--foreground)]"
                              : "border-[rgba(34,24,15,0.08)] bg-[color:color-mix(in_srgb,var(--panel)_94%,white_6%)] text-[color:#514840] dark:border-[var(--line)] dark:bg-[color:color-mix(in_srgb,var(--background)_82%,white_18%)] dark:text-[var(--foreground)]"
                          }`}
                          onClick={() => toggleComposerPanel("tools")}
                          type="button"
                        >
                          <span className="flex items-center gap-0.5">
                            <Wrench className="h-2.5 w-2.5" />
                            <span className="font-medium">工具能力</span>
                            <span className="text-[var(--muted)]">{enabledToolCount}/{totalToolCount}</span>
                          </span>
                          <ChevronDown className={`h-2 w-2 transition ${composerPanel === "tools" ? "rotate-180" : ""}`} />
                        </button>
                        <button
                          className={`flex min-w-[3.1rem] items-center justify-between rounded-full border px-1.5 py-[0.32rem] text-[9px] leading-none transition ${
                            composerPanel === "skills"
                              ? "border-[color:color-mix(in_srgb,var(--accent)_26%,var(--line))] bg-[color:color-mix(in_srgb,var(--accent-soft)_56%,white_44%)] text-[color:#403832] shadow-[inset_0_1px_0_rgba(255,255,255,0.5)] dark:bg-[color:color-mix(in_srgb,var(--panel-deep)_92%,transparent)] dark:text-[var(--foreground)]"
                              : "border-[rgba(34,24,15,0.08)] bg-[color:color-mix(in_srgb,var(--panel)_94%,white_6%)] text-[color:#514840] dark:border-[var(--line)] dark:bg-[color:color-mix(in_srgb,var(--background)_82%,white_18%)] dark:text-[var(--foreground)]"
                          }`}
                          onClick={() => toggleComposerPanel("skills")}
                          type="button"
                        >
                          <span className="flex items-center gap-0.5">
                            <Clipboard className="h-2.5 w-2.5" />
                            <span className="font-medium">技能助手</span>
                            <span className="text-[var(--muted)]">{enabledSkillCount}/{totalSkillCount}</span>
                          </span>
                          <ChevronDown className={`h-2 w-2 transition ${composerPanel === "skills" ? "rotate-180" : ""}`} />
                        </button>
                        <button
                          className={`flex min-w-[3.9rem] items-center justify-between rounded-full border px-1.5 py-[0.32rem] text-[9px] leading-none transition ${
                            composerPanel === "model"
                              ? "border-[color:color-mix(in_srgb,var(--accent)_26%,var(--line))] bg-[color:color-mix(in_srgb,var(--accent-soft)_56%,white_44%)] text-[color:#403832] shadow-[inset_0_1px_0_rgba(255,255,255,0.5)] dark:bg-[color:color-mix(in_srgb,var(--panel-deep)_92%,transparent)] dark:text-[var(--foreground)]"
                              : "border-[rgba(34,24,15,0.08)] bg-[color:color-mix(in_srgb,var(--panel)_94%,white_6%)] text-[color:#514840] dark:border-[var(--line)] dark:bg-[color:color-mix(in_srgb,var(--background)_82%,white_18%)] dark:text-[var(--foreground)]"
                          }`}
                          onClick={() => toggleComposerPanel("model")}
                          type="button"
                        >
                          <span className="flex min-w-0 items-center gap-0.5">
                            <Sparkles className="h-2.5 w-2.5" />
                            <span className="font-medium">模型引擎</span>
                            <span className="truncate text-[var(--muted)]">{activeModelName}</span>
                          </span>
                          <ChevronDown className={`h-2 w-2 shrink-0 transition ${composerPanel === "model" ? "rotate-180" : ""}`} />
                        </button>
                      </div>
                      <div className="flex items-center gap-2 text-[color:#59515b] dark:text-[var(--foreground)]">
                        <button
                          className="flex h-9 w-9 items-center justify-center rounded-full border border-transparent bg-transparent transition hover:bg-[rgba(232,233,237,0.72)] dark:hover:bg-[color:color-mix(in_srgb,var(--panel)_82%,transparent)]"
                          type="button"
                        >
                          <Maximize2 className="h-4 w-4" />
                        </button>
                        <button
                          className="flex h-9 w-9 items-center justify-center rounded-full border border-transparent bg-transparent transition hover:bg-[rgba(232,233,237,0.72)] dark:hover:bg-[color:color-mix(in_srgb,var(--panel)_82%,transparent)]"
                          type="button"
                        >
                          <Paperclip className="h-4 w-4" />
                        </button>
                        <button
                          className={`flex h-10 w-10 items-center justify-center rounded-full text-white transition ${
                            hasChatDraft || streaming
                              ? "bg-[linear-gradient(135deg,#8ea8ff,#5f83ff)] text-white shadow-[0_18px_34px_-16px_rgba(95,131,255,0.96)] ring-2 ring-[rgba(143,170,255,0.34)] hover:-translate-y-[1px] dark:bg-[linear-gradient(135deg,#8fa0ff,#6d84ff)] dark:ring-[rgba(143,170,255,0.28)]"
                              : "bg-[linear-gradient(135deg,#d9def3,#c7d0ef)] text-[rgba(255,255,255,0.82)] shadow-[0_10px_22px_-18px_rgba(125,138,255,0.42)] dark:bg-[linear-gradient(135deg,#626b94,#7b86af)]"
                          }`}
                          onClick={() => void (streaming ? stopStreaming() : sendMessage())}
                          type="button"
                        >
                          {streaming ? (
                            stopRequested ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />
                          ) : (
                            <ArrowUp className="h-4 w-4" />
                          )}
                        </button>
                      </div>
                      </div>
                    </div>
                  </div>
              }
              messages={
                <>
                  {displayTurns.turns.map((turn) => {
                    const assistantMessage = turn.assistantMessage;

                    return (
                      <Fragment key={turn.id}>
                        <MessageBubble
                          message={turn.userMessage}
                          sessionId={activeSession?.id}
                          copied={copiedMessageId === turn.userMessage.id}
                          onCopy={(value) => void copyMessage(turn.userMessage.id, value)}
                          onEdit={
                            !turn.userMessage.id.startsWith("pending-")
                              ? () => {
                                  setEditingMessageId(turn.userMessage.id);
                                  setEditValue(turn.userMessage.content);
                                }
                              : undefined
                          }
                          editing={editingMessageId === turn.userMessage.id}
                          editValue={editValue}
                          onEditChange={setEditValue}
                          onEditConfirm={() => void saveMessageEditAndRetry(turn.userMessage)}
                          onEditCancel={() => setEditingMessageId(null)}
                        />
                        {shouldRenderThinkingCard(turn) ? <ThinkingCard turn={turn} /> : null}
                        {assistantMessage ? (
                          <MessageBubble
                            message={assistantMessage}
                            sessionId={activeSession?.id}
                            copied={copiedMessageId === assistantMessage.id}
                            onCopy={(value) => void copyMessage(assistantMessage.id, value)}
                          />
                        ) : null}
                      </Fragment>
                    );
                  })}
                  {displayTurns.orphans.map((message) => (
                    <MessageBubble
                      key={message.id}
                      message={message}
                      sessionId={activeSession?.id}
                      copied={copiedMessageId === message.id}
                      onCopy={(value) => void copyMessage(message.id, value)}
                    />
                  ))}
                </>
              }
              onScroll={handleChatScroll}
              summaryModalTrigger={
                <div className="mt-1 flex flex-wrap gap-1.5">
                  <span className="inline-flex items-center rounded-full border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_90%,transparent)] px-2.5 py-1 text-[11px] text-[var(--foreground)]">
                    {activeSession?.model_name ?? options?.default_model ?? "未设置"}
                  </span>
                  <span className="inline-flex items-center rounded-full border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_90%,transparent)] px-2.5 py-1 text-[11px] text-[var(--foreground)]">
                    输入 {activeSession?.stats.input_tokens ?? 0}
                  </span>
                  <span className="inline-flex items-center rounded-full border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_90%,transparent)] px-2.5 py-1 text-[11px] text-[var(--foreground)]">
                    输出 {activeSession?.stats.output_tokens ?? 0}
                  </span>
                  <span className="inline-flex items-center rounded-full border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_90%,transparent)] px-2.5 py-1 text-[11px] text-[var(--foreground)]">
                    上下文 {activeSession?.stats.context_tokens ?? 0}
                  </span>
                </div>
              }
              title={activeSession?.title ?? "新会话"}
              toolbar={
                <>
                  {activeSession?.summary ? (
                    <button
                      className="ghost-button px-2.5 py-1 text-[11px]"
                      onClick={() => setSummaryModalOpen(true)}
                      type="button"
                    >
                      Summary
                    </button>
                  ) : null}
                  <button className="ghost-button gap-[0.2rem] px-[0.45rem] py-[0.15rem] text-[8px]" onClick={() => void compressConversation()} type="button">
                    <Scissors className="h-4 w-4" />
                    压缩摘要
                  </button>
                  <button
                    className="ghost-button px-2.5 py-1 text-[11px]"
                    onClick={() => setDebugOpen((current) => !current)}
                    type="button"
                  >
                    DEBUG
                  </button>
                </>
              }
            />

            <DebugPanel open={debugOpen}>
              <SessionInsightPanel
                session={activeSession}
                turnState={activeTurnState}
                rawMessages={displayRawMessages}
                debugOpen={debugOpen}
              />
            </DebugPanel>
          </>
        ) : (
          <>
            <main
              className={`panel flex h-[calc(100vh-5.5rem)] min-h-0 shrink-0 flex-col overflow-hidden ${
                page === "skills" ? "" : "w-[360px] xl:w-[420px]"
              }`}
              style={
                page === "skills"
                  ? {
                      width: `${skillCardsWidth}px`,
                      minWidth: `${SKILL_CARDS_MIN_WIDTH}px`,
                      maxWidth: `${SKILL_CARDS_MAX_WIDTH}px`,
                    }
                  : undefined
              }
            >
              <div className="border-b border-[var(--line)] px-4 py-3">
                <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">
                  {page === "memory" ? "Memory Files" : page === "skills" ? "Skill Cards" : "Prompt Files"}
                </p>
                <p className="text-sm">
                  {page === "memory" ? "记忆文件列表" : page === "skills" ? "技能卡片库" : "Prompt 管理"}
                </p>
              </div>
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
                {page === "memory"
                  ? (
                      <>
                        {memoryFiles.map((file) => (
                          <button
                            key={file.path}
                            className={`session-card ${file.path === selectedMemoryPath ? "session-card-active" : ""}`}
                            onClick={() => setSelectedMemoryPath(file.path)}
                            type="button"
                          >
                            <p className="truncate text-left text-sm font-medium">{file.name}</p>
                            <p className="mt-1 truncate text-left text-xs text-[var(--muted)]">{file.path}</p>
                            <div className="mt-3 flex items-center justify-between text-[11px] text-[var(--muted)]">
                              <span>{Math.round(file.size / 1024)} KB</span>
                              <span>{formatDate(file.updated_at * 1000)}</span>
                            </div>
                          </button>
                        ))}
                      </>
                    )
                  : page === "skills" ? (
                      <>
                        <div className="sticky top-0 z-10 rounded-[1.7rem] border border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_96%,white_4%)] p-3 backdrop-blur-xl">
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              className="action-button !min-h-0 !px-3 !py-1.5 text-xs"
                              disabled={Boolean(busyLabel)}
                              onClick={() => setSkillStudioOpen((current) => !current)}
                              type="button"
                            >
                              <Plus className="h-3.5 w-3.5" />
                              新建技能
                            </button>
                            <button
                              className="ghost-button !min-h-0 !px-3 !py-1.5 text-xs"
                              disabled={Boolean(busyLabel)}
                              onClick={() => skillUploadInputRef.current?.click()}
                              type="button"
                            >
                              <Paperclip className="h-3.5 w-3.5" />
                              本地上传
                            </button>
                          </div>
                          {skillStudioOpen ? (
                            <div className="mt-3 rounded-[1.35rem] border border-[var(--line)] bg-[var(--panel)] p-4">
                              <div className="flex items-start justify-between gap-3">
                                <div>
                                  <p className="text-[10px] uppercase tracking-[0.2em] text-[var(--muted)]">Skill Studio</p>
                                  <p className="mt-2 text-xs leading-6 text-[var(--muted)]">
                                    先填技能名称和描述，再生成一个可继续编辑的技能草稿。
                                  </p>
                                </div>
                                <button
                                  aria-label="收起 Skill Studio"
                                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--line)] bg-[var(--background)] text-[var(--muted)] transition hover:border-[color:color-mix(in_srgb,var(--accent)_18%,var(--line))] hover:text-[var(--accent)]"
                                  onClick={() => setSkillStudioOpen(false)}
                                  type="button"
                                >
                                  <ChevronDown className="h-3.5 w-3.5 rotate-180" />
                                </button>
                              </div>
                              <div className="mt-4 space-y-2">
                                <input
                                  className="w-full rounded-2xl border border-[var(--line)] bg-[var(--background)] px-4 py-3 text-xs outline-none"
                                  placeholder="技能名称，例如：sql-review"
                                  value={newSkillName}
                                  onChange={(event) => setNewSkillName(event.target.value)}
                                />
                                <textarea
                                  className="min-h-24 w-full rounded-2xl border border-[var(--line)] bg-[var(--background)] px-4 py-3 text-xs outline-none"
                                  placeholder="一句话描述这个技能解决什么问题"
                                  value={newSkillDescription}
                                  onChange={(event) => setNewSkillDescription(event.target.value)}
                                />
                              </div>
                              <div className="mt-4 flex justify-start">
                                <button
                                  className="action-button !min-h-0 !px-3.5 !py-2 text-[11px]"
                                  disabled={!newSkillName.trim() || !newSkillDescription.trim() || Boolean(busyLabel)}
                                  onClick={() => void createSkillDraft()}
                                  type="button"
                                >
                                  <Plus className="h-3.5 w-3.5" />
                                  创建草稿
                                </button>
                              </div>
                            </div>
                          ) : null}
                          <input
                            accept=".md,text/markdown"
                            className="hidden"
                            onChange={(event) => {
                              const file = event.target.files?.[0];
                              if (file) {
                                void uploadLocalSkill(file);
                              }
                              event.target.value = "";
                            }}
                            ref={skillUploadInputRef}
                            type="file"
                          />
                        </div>

                        {skillDraft ? (
                          <div className="session-card session-card-active">
                            <div className="flex items-start justify-between gap-3">
                              <p className="truncate text-left text-sm font-medium">{resolvedDraftSkillName || skillDraft.name}</p>
                              <span className="rounded-full border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-[0.14em] text-[var(--muted)]">
                                Draft
                              </span>
                            </div>
                            <p className="mt-2 text-left text-xs leading-6 text-[var(--muted)]">
                              {resolvedDraftSkillDescription || skillDraft.description}
                            </p>
                            <p className="mt-3 truncate text-left text-[11px] text-[var(--accent)]">
                              draft/{skillDraft.slug}/SKILL.md
                            </p>
                          </div>
                        ) : null}

                        {skills.map((skill) => {
                          const relativePath = normalizeSkillPath(skill.path);
                          const sourceLabel = formatSkillSource(skill.source);
                          return (
                            <div key={skill.path} className="relative">
                              <button
                                className={`session-card w-full pr-14 ${relativePath === selectedSkillPath ? "session-card-active" : ""}`}
                                onClick={() => selectSkillCard(relativePath)}
                                type="button"
                              >
                                <div className="flex items-start justify-between gap-3">
                                  <p className="truncate text-left text-sm font-medium">{skill.name}</p>
                                  <span className="rounded-full border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-[0.14em] text-[var(--muted)]">
                                    {sourceLabel}
                                  </span>
                                </div>
                                <p className="mt-2 text-left text-xs leading-6 text-[var(--muted)]">{skill.description}</p>
                                <p className="mt-3 truncate text-left text-[11px] text-[var(--accent)]">{skill.path}</p>
                              </button>
                              <button
                                aria-label={`删除技能 ${skill.name}`}
                                className="icon-button absolute bottom-3 right-3"
                                disabled={Boolean(busyLabel)}
                                onClick={() => void deleteSkillByPath(relativePath, skill.name)}
                                type="button"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          );
                        })}
                      </>
                    ) : (
                      <>
                        {MANAGED_PROMPTS.map((prompt) => (
                          <button
                            key={prompt.path}
                            className={`session-card ${prompt.path === selectedPromptPath ? "session-card-active" : ""}`}
                            onClick={() => setSelectedPromptPath(prompt.path)}
                            type="button"
                          >
                            <p className="truncate text-left text-sm font-medium">{prompt.title}</p>
                            <p className="mt-2 text-left text-xs leading-6 text-[var(--muted)]">{prompt.description}</p>
                            <p className="mt-3 truncate text-left text-[11px] text-[var(--accent)]">/prompts/{prompt.path}</p>
                          </button>
                        ))}
                      </>
                    )}
              </div>
            </main>

            {page === "skills" ? (
              <button
                aria-label="调整 Skill Cards 宽度"
                className="group my-8 flex w-3 shrink-0 cursor-col-resize items-center justify-center self-stretch"
                onPointerDown={(event) => startPanelResize("skillCards", event, skillCardsWidth)}
                type="button"
              >
                <span className="h-full w-px rounded-full bg-[var(--line)] transition group-hover:bg-[var(--accent)]" />
              </button>
            ) : null}

            <section className="panel flex h-[calc(100vh-5.5rem)] min-h-0 flex-1 flex-col overflow-hidden">
              <div
                className={`flex items-center border-b border-[var(--line)] px-5 py-4 ${
                  page === "skills" ? "justify-start" : "justify-between"
                }`}
              >
                <div>
                  {page === "skills" ? null : (
                    <>
                      <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">
                        {page === "prompts" ? "Prompt Editor" : "Editor"}
                      </p>
                      <p className="text-sm">
                        {page === "memory" ? memoryEditorLabel : promptEditorLabel}
                      </p>
                    </>
                  )}
                </div>
                <div className={`flex flex-wrap gap-2 ${page === "skills" ? "w-full justify-start" : ""}`}>
                  {page === "memory" ? (
                    <>
                      <button className="ghost-button" onClick={() => void optimizeMemory()} type="button">
                        <Sparkles className="h-4 w-4" />
                        AI 优化
                      </button>
                      <button className="action-button" onClick={() => void saveMemory()} type="button">
                        <Save className="h-4 w-4" />
                        保存
                      </button>
                    </>
                  ) : page === "skills" ? (
                    <>
                      <div className="inline-flex rounded-full border border-[var(--line)] bg-[var(--panel)] p-1">
                        {(["edit", "preview", "split"] as const).map((mode) => (
                          <button
                            key={mode}
                            className={`rounded-full px-3 py-1 text-xs transition ${
                              skillViewMode === mode
                                ? "bg-[var(--foreground)] text-[var(--background)]"
                                : "text-[var(--muted)]"
                            }`}
                            onClick={() => setSkillViewMode(mode)}
                            type="button"
                          >
                            {mode === "edit" ? "编辑" : mode === "preview" ? "预览" : "分栏"}
                          </button>
                        ))}
                      </div>
                      <button
                        className="ghost-button"
                        disabled={!selectedSkillFilePath || !editingSkillMainFile}
                        onClick={() => void optimizeSkill()}
                        type="button"
                      >
                        <Sparkles className="h-4 w-4" />
                        AI 优化
                      </button>
                      <button
                        className="action-button"
                        disabled={!selectedSkillFilePath && !skillDraft}
                        onClick={() => openSkillSavePreview()}
                        type="button"
                      >
                        <Save className="h-4 w-4" />
                        保存
                      </button>
                    </>
                  ) : (
                    <button className="action-button" onClick={() => void savePrompt()} type="button">
                      <Save className="h-4 w-4" />
                      保存 Prompt
                    </button>
                  )}
                </div>
              </div>
              <div className="flex min-h-0 flex-1 p-4">
                {page === "skills" ? (
                  <SkillsPanel
                    cardsPane={
                      !skillDraft ? (
                        <aside
                          className="flex shrink-0 flex-col overflow-hidden rounded-[1.9rem] border border-[color:color-mix(in_srgb,var(--accent)_12%,var(--line))] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--panel)_96%,white_4%),color-mix(in_srgb,var(--background)_96%,transparent))] shadow-[0_28px_72px_-48px_rgba(0,0,0,0.42)]"
                          style={{
                            width: `${skillFilesWidth}px`,
                            minWidth: `${SKILL_FILES_MIN_WIDTH}px`,
                            maxWidth: `${SKILL_FILES_MAX_WIDTH}px`,
                          }}
                        >
                          <div className="border-b border-[var(--line)] px-5 py-4">
                            <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">Skill Files</p>
                            <p className="mt-1 text-base font-semibold text-[var(--foreground)]">{selectedSkill?.name ?? "未选择技能"}</p>
                            <p className="mt-1 text-xs leading-6 text-[var(--muted)]">
                              目录树视图，支持展开目录并直接切换到具体文件。
                            </p>
                            <div className="mt-4 flex gap-2">
                              <span className="rounded-full border border-[var(--line)] bg-[var(--background)] px-3 py-1 text-[10px] uppercase tracking-[0.14em] text-[var(--muted)]">
                                {skillFiles.length} files
                              </span>
                              <span className="rounded-full border border-[var(--line)] bg-[var(--background)] px-3 py-1 text-[10px] uppercase tracking-[0.14em] text-[var(--muted)]">
                                {skillFolderCount} folders
                              </span>
                            </div>
                          </div>
                          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
                            {skillFileTree.length ? (
                              <div className="space-y-1">{skillFileTree.map((node) => renderSkillTreeNode(node))}</div>
                            ) : (
                              <div className="rounded-[1.5rem] border border-dashed border-[var(--line)] bg-[var(--background)] px-4 py-6 text-sm leading-7 text-[var(--muted)]">
                                当前 skill 目录下还没有可展示的文件。
                              </div>
                            )}
                          </div>
                        </aside>
                      ) : null
                    }
                    cardsResizeHandle={
                      !skillDraft ? (
                        <button
                          aria-label="调整 Skill Files 宽度"
                          className="group mx-2 flex w-3 shrink-0 cursor-col-resize items-center justify-center self-stretch"
                          onPointerDown={(event) => startPanelResize("skillFiles", event, skillFilesWidth)}
                          type="button"
                        >
                          <span className="h-full w-px rounded-full bg-[var(--line)] transition group-hover:bg-[var(--accent)]" />
                        </button>
                      ) : null
                    }
                    editorPane={
                      <>
                        {skillViewMode !== "preview" ? (
                          <div
                            className={`flex min-h-0 min-w-0 overflow-hidden rounded-[1.7rem] border border-[var(--line)] ${
                              skillViewMode === "split" ? "mr-4 flex-1" : "flex-1"
                            }`}
                          >
                            <div className="min-h-0 flex-1 overflow-hidden">
                              <CodeEditor
                                language={skillDraft ? "markdown" : skillFileLanguage(selectedSkillFilePath || selectedSkillPath)}
                                onChange={setSkillContent}
                                value={skillContent}
                              />
                            </div>
                          </div>
                        ) : null}
                        {skillViewMode !== "edit" ? (
                          <div
                            className={`flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[1.7rem] border border-[var(--line)] bg-[var(--panel)] ${
                              skillViewMode === "split" ? "mr-4 flex-1" : "flex-1"
                            }`}
                          >
                            <div className="min-h-0 flex-1 overflow-y-auto p-5">
                              {skillContent.trim() ? (
                                currentSkillFileIsMarkdown ? (
                                  <div className="space-y-4">
                                    {editingSkillMainFile ? (
                                      <div className="rounded-2xl border border-[var(--line)] bg-[var(--background)] p-4">
                                        <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">Skill Preview</p>
                                        <p className="mt-2 text-base font-semibold text-[var(--foreground)]">
                                          {readFrontmatterField(skillPreview.frontmatter, "name") || selectedSkill?.name || "未命名技能"}
                                        </p>
                                        <p className="mt-1 text-sm leading-7 text-[var(--muted)]">
                                          {readFrontmatterField(skillPreview.frontmatter, "description") || selectedSkill?.description || "暂无技能描述"}
                                        </p>
                                        {skillPreview.frontmatter ? (
                                          <pre className="mt-3 overflow-x-auto rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-3 text-xs leading-6 text-[var(--muted)]">
                                            {skillPreview.frontmatter}
                                          </pre>
                                        ) : null}
                                      </div>
                                    ) : null}
                                    <div className="rounded-2xl border border-[var(--line)] bg-[var(--background)] p-4">
                                      <div className="markdown-body">
                                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                          {editingSkillMainFile ? skillPreview.body || "_暂无正文内容_" : skillContent}
                                        </ReactMarkdown>
                                      </div>
                                    </div>
                                  </div>
                                ) : (
                                  <pre className="overflow-x-auto rounded-2xl border border-[var(--line)] bg-[var(--background)] p-4 text-sm leading-7 text-[var(--foreground)]">
                                    {skillContent}
                                  </pre>
                                )
                              ) : (
                                <p className="text-sm leading-7 text-[var(--muted)]">当前技能内容为空，保存后会显示 Markdown 预览。</p>
                              )}
                            </div>
                          </div>
                        ) : null}
                        {skillSuggestion ? (
                          <div className="ml-4 w-[340px] space-y-4">
                            <div className="h-full rounded-[1.7rem] border border-[var(--line)] bg-[var(--panel)] p-4">
                              <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">AI Suggestion</p>
                              <div className="mt-3 h-[calc(100%-2rem)] overflow-y-auto rounded-2xl border border-[var(--line)] bg-[var(--background)] p-4 text-sm leading-7">
                                <div className="markdown-body">
                                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{skillSuggestion}</ReactMarkdown>
                                </div>
                              </div>
                            </div>
                          </div>
                        ) : null}
                      </>
                    }
                  />
                ) : (
                  <>
                    <div className="min-w-0 flex-1 overflow-hidden rounded-[1.7rem] border border-[var(--line)]">
                      <CodeEditor
                        language="markdown"
                        value={page === "memory" ? memoryContent : promptContent}
                        onChange={page === "memory" ? setMemoryContent : setPromptContent}
                      />
                    </div>
                    {page === "memory" && memorySuggestion ? (
                      <div className="ml-4 w-[340px] space-y-4">
                        <div className="h-full rounded-[1.7rem] border border-[var(--line)] bg-[var(--panel)] p-4">
                          <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">AI Suggestion</p>
                          <div className="mt-3 h-[calc(100%-2rem)] overflow-y-auto rounded-2xl border border-[var(--line)] bg-[var(--background)] p-4 text-sm leading-7">
                            <div className="markdown-body">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{memorySuggestion}</ReactMarkdown>
                            </div>
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            </section>
          </>
        )}
      </div>

      {page === "chat" && activeSession?.summary && summaryModalOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-[rgba(12,10,8,0.14)] px-4 py-8"
          onClick={() => setSummaryModalOpen(false)}
        >
          <div
            className="panel mt-[4.5rem] max-h-[72vh] w-full max-w-2xl overflow-hidden border-[color:color-mix(in_srgb,var(--accent)_18%,var(--line))] shadow-[0_36px_80px_-46px_rgba(0,0,0,0.38)]"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-[var(--line)] px-6 py-4">
              <div>
                <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">Summary</p>
                <p className="mt-1 text-lg font-semibold text-[var(--foreground)]">
                  {activeSession.title}
                </p>
              </div>
              <button
                className="icon-button"
                onClick={() => setSummaryModalOpen(false)}
                type="button"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="overflow-y-auto px-6 py-5">
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeSession.summary}</ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {page === "skills" && skillSavePreviewOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-[rgba(12,10,8,0.18)] px-4 py-8"
          onClick={() => setSkillSavePreviewOpen(false)}
        >
          <div
            className="panel mt-[4.5rem] flex max-h-[78vh] w-full max-w-4xl flex-col overflow-hidden border-[color:color-mix(in_srgb,var(--accent)_18%,var(--line))] shadow-[0_36px_80px_-46px_rgba(0,0,0,0.38)]"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="shrink-0 border-b border-[var(--line)] px-6 py-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">Skill Preview</p>
                  <p className="mt-1 text-lg font-semibold text-[var(--foreground)]">
                    {editingSkillMainFile
                      ? resolvedDraftSkillName || selectedSkill?.name || "技能预览"
                      : selectedSkillFile?.relative_path || selectedSkillFilePath || "文件预览"}
                  </p>
                  <p className="mt-1 text-sm text-[var(--muted)]">
                    {editingSkillMainFile
                      ? resolvedDraftSkillDescription || selectedSkill?.description || "确认无误后再保存"
                      : "确认无误后再保存当前文件"}
                  </p>
                </div>
                <button
                  className="icon-button shrink-0"
                  onClick={() => setSkillSavePreviewOpen(false)}
                  type="button"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <p className="mt-3 text-xs leading-6 text-[var(--muted)]">
                这个弹窗只做最终校对。只有点击右下角“确认保存”后，技能才会真正写入本地目录。
              </p>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div className="space-y-4">
                {editingSkillMainFile && skillPreview.frontmatter ? (
                  <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
                    <p className="text-[11px] uppercase tracking-[0.24em] text-[var(--muted)]">Frontmatter</p>
                    <pre className="mt-3 max-h-[16rem] overflow-auto rounded-2xl border border-[var(--line)] bg-[var(--background)] p-4 text-xs leading-6 text-[var(--muted)]">
                      {skillPreview.frontmatter}
                    </pre>
                  </div>
                ) : null}
                <div className="rounded-2xl border border-[var(--line)] bg-[var(--background)] p-5">
                  {currentSkillFileIsMarkdown ? (
                    <div className="markdown-body">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {editingSkillMainFile ? skillPreview.body || "_暂无正文内容_" : skillContent || "_暂无正文内容_"}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <pre className="overflow-x-auto whitespace-pre-wrap text-sm leading-7 text-[var(--foreground)]">
                      {skillContent}
                    </pre>
                  )}
                </div>
              </div>
            </div>
            <div className="shrink-0 border-t border-[var(--line)] bg-[color:color-mix(in_srgb,var(--panel)_92%,transparent)] px-6 py-4 backdrop-blur-xl">
              <div>
                <div className="flex items-center justify-end gap-2">
                  <button
                    className="ghost-button"
                    onClick={() => setSkillSavePreviewOpen(false)}
                    type="button"
                  >
                    返回编辑
                  </button>
                  <button
                    className="action-button"
                    disabled={Boolean(busyLabel)}
                    onClick={() => void confirmSkillSave()}
                    type="button"
                  >
                    <Save className="h-4 w-4" />
                    确认保存
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
