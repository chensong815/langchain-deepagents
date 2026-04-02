import type {
  CsvPreviewPayload,
  FileCard,
  OptionsPayload,
  SessionRecord,
  SessionSummary,
  SkillCard,
  SkillFileCard,
  SkillMutationResult,
  StreamEvent,
} from "@/lib/types";

const DEFAULT_API_PORT = "8000";

function normalizeSandboxQueryPath(filePath: string): string {
  const normalized = filePath.trim().replace(/\\/g, "/");
  if (/^\/(?:\.sandbox|backend\/\.sandbox)(?:\/|$)/.test(normalized)) {
    return normalized.slice(1);
  }
  return normalized;
}

function resolveApiBaseUrl(): string {
  const configuredBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
  if (configuredBaseUrl) {
    return configuredBaseUrl;
  }

  if (typeof window !== "undefined") {
    const { protocol, hostname } = window.location;
    return `${protocol}//${hostname}:${DEFAULT_API_PORT}`;
  }

  return `http://127.0.0.1:${DEFAULT_API_PORT}`;
}

export function buildApiUrl(path: string): string {
  return `${resolveApiBaseUrl()}${path}`;
}

export function buildSandboxFileUrl(filePath: string): string {
  return buildApiUrl(`/api/sandbox/file?path=${encodeURIComponent(normalizeSandboxQueryPath(filePath))}`);
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const response = await fetch(buildApiUrl(path), {
    ...init,
    headers: isFormData
      ? (init?.headers ?? {})
      : {
          "Content-Type": "application/json",
          ...(init?.headers ?? {}),
        },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return (await response.json()) as T;
}

export const api = {
  async health(): Promise<{ status: string }> {
    return apiFetch("/api/health");
  },
  async getSandboxCsvPreview(path: string, limit = 50): Promise<CsvPreviewPayload> {
    return apiFetch(
      `/api/sandbox/csv-preview?path=${encodeURIComponent(normalizeSandboxQueryPath(path))}&limit=${limit}`,
    );
  },
  async options(): Promise<OptionsPayload> {
    return apiFetch("/api/options");
  },
  async listSessions(): Promise<SessionSummary[]> {
    return apiFetch("/api/sessions");
  },
  async createSession(model_name?: string): Promise<SessionRecord> {
    return apiFetch("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ model_name }),
    });
  },
  async getSession(sessionId: string): Promise<SessionRecord> {
    return apiFetch(`/api/sessions/${sessionId}`);
  },
  async updateSession(
    sessionId: string,
    payload: Partial<Pick<SessionRecord, "title" | "model_name" | "debug">> & {
      tool_switches?: Record<string, boolean>;
      skills_enabled?: string[];
    },
  ): Promise<SessionRecord> {
    return apiFetch(`/api/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  async deleteSession(sessionId: string): Promise<void> {
    await apiFetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
  },
  async cancelSessionTurn(sessionId: string): Promise<SessionRecord> {
    const result = await apiFetch<{ ok: true; session: SessionRecord }>(`/api/sessions/${sessionId}/cancel`, {
      method: "POST",
    });
    return result.session;
  },
  async updateMessage(sessionId: string, messageId: string, content: string): Promise<SessionRecord> {
    return apiFetch(`/api/sessions/${sessionId}/messages/${messageId}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    });
  },
  async truncateAfterMessage(sessionId: string, messageId: string): Promise<SessionRecord> {
    return apiFetch(`/api/sessions/${sessionId}/messages/${messageId}/truncate`, {
      method: "POST",
    });
  },
  async truncateFromMessage(sessionId: string, messageId: string): Promise<SessionRecord> {
    return apiFetch(`/api/sessions/${sessionId}/messages/${messageId}/retry-base`, {
      method: "POST",
    });
  },
  async compressSession(sessionId: string): Promise<{ summary: string; session: SessionRecord }> {
    return apiFetch(`/api/sessions/${sessionId}/compress`, { method: "POST" });
  },
  async listMemoryFiles(): Promise<FileCard[]> {
    return apiFetch("/api/memory/files");
  },
  async getMemoryFile(path: string): Promise<{ path: string; content: string }> {
    return apiFetch(`/api/memory/file?path=${encodeURIComponent(path)}`);
  },
  async saveMemoryFile(path: string, content: string): Promise<void> {
    await apiFetch("/api/memory/file", {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    });
  },
  async optimizeMemory(path: string | null, content: string): Promise<{ suggestion: string }> {
    return apiFetch("/api/memory/optimize", {
      method: "POST",
      body: JSON.stringify({ path, content }),
    });
  },
  async optimizeSkill(path: string | null, content: string): Promise<{ suggestion: string }> {
    return apiFetch("/api/skills/optimize", {
      method: "POST",
      body: JSON.stringify({ path, content }),
    });
  },
  async getPromptFile(path: string): Promise<{ path: string; content: string }> {
    return apiFetch(`/api/prompts/file?path=${encodeURIComponent(path)}`);
  },
  async savePromptFile(path: string, content: string): Promise<void> {
    await apiFetch("/api/prompts/file", {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    });
  },
  async listSkills(): Promise<SkillCard[]> {
    return apiFetch("/api/skills");
  },
  async createSkill(payload: {
    name: string;
    description: string;
    slug?: string;
    content?: string;
  }): Promise<SkillMutationResult> {
    return apiFetch("/api/skills", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  async uploadSkill(payload: {
    filename: string;
    content: string;
    name?: string;
    description?: string;
    slug?: string;
  }): Promise<SkillMutationResult> {
    return apiFetch("/api/skills/upload", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  async getSkillFile(path: string): Promise<{ path: string; content: string }> {
    return apiFetch(`/api/skills/file?path=${encodeURIComponent(path)}`);
  },
  async listSkillFiles(path: string): Promise<SkillFileCard[]> {
    return apiFetch(`/api/skills/files?path=${encodeURIComponent(path)}`);
  },
  async saveSkillFile(path: string, content: string): Promise<void> {
    await apiFetch("/api/skills/file", {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    });
  },
  async deleteSkillFile(path: string): Promise<void> {
    await apiFetch(`/api/skills/file?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    });
  },
  async streamMessage(
    sessionId: string,
    payload: {
      message: string;
      model_name: string;
      debug: boolean;
      tool_switches: Record<string, boolean>;
      skills_enabled: string[];
    },
    onEvent: (event: StreamEvent) => void,
    signal: AbortSignal,
  ): Promise<void> {
    const response = await fetch(buildApiUrl(`/api/sessions/${sessionId}/messages/stream`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });

    if (!response.ok || !response.body) {
      throw new Error(await response.text());
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const dataLine = frame
          .split("\n")
          .find((line) => line.startsWith("data: "));
        if (!dataLine) {
          continue;
        }
        onEvent(JSON.parse(dataLine.slice(6)) as StreamEvent);
      }
    }
  },
};
