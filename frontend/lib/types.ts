export type WorkspacePage = "chat" | "memory" | "skills" | "prompts";

export type SessionStats = {
  input_tokens: number;
  output_tokens: number;
  context_tokens: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  state?: "completed" | "interrupted" | "error";
  created_at: string;
  updated_at: string;
};

export type TurnState = {
  status: "idle" | "streaming" | "cancelling" | "completed" | "interrupted" | "error";
  phase: "routing" | "tool" | "responding" | null;
  turn_id: string | null;
  user_message_id: string | null;
  requested_text: string;
  selected_skill: string | null;
  active_tool: string | null;
  tool_count: number;
  stop_requested: boolean;
  started_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
};

export type RawMessage = {
  id: string;
  kind: string;
  payload: unknown;
  created_at: string;
};

export type WorkingArtifact = {
  path: string;
  description: string;
};

export type WorkingMemory = {
  active_skill: string | null;
  recent_tools: string[];
  current_goal: string | null;
  confirmed_slots: Record<string, string>;
  pending_slots: string[];
  artifacts: WorkingArtifact[];
  open_loops: string[];
};

export type RetrievedContextItem = {
  kind: string;
  source: string;
  title: string;
  snippet: string;
  score: number;
};

export type SessionRecord = {
  id: string;
  title: string;
  thread_id: string;
  model_name: string;
  created_at: string;
  updated_at: string;
  summary: string;
  summary_message_count: number;
  messages: ChatMessage[];
  raw_messages: RawMessage[];
  working_memory: WorkingMemory;
  turn_state: TurnState;
  retrieved_context: RetrievedContextItem[];
  tool_switches: Record<string, boolean>;
  skills_enabled: string[];
  debug: boolean;
  system_prompt: string;
  stats: SessionStats;
};

export type SessionSummary = Pick<
  SessionRecord,
  | "id"
  | "title"
  | "thread_id"
  | "model_name"
  | "created_at"
  | "updated_at"
  | "summary"
  | "summary_message_count"
  | "debug"
  | "stats"
> & {
  message_count: number;
};

export type SkillCard = {
  name: string;
  description: string;
  path: string;
  source: string;
};

export type SkillMutationResult = {
  ok: true;
  path: string;
  content: string;
};

export type SkillFileCard = {
  path: string;
  relative_path: string;
  name: string;
  depth: number;
};

export type ToolOption = {
  id: string;
  label: string;
};

export type OptionsPayload = {
  models: string[];
  skills: SkillCard[];
  tool_switches: ToolOption[];
  default_model: string;
  system_prompt: string;
};

export type FileCard = {
  path: string;
  name: string;
  updated_at: number;
  size: number;
};

export type StreamEvent =
  | { type: "skill"; skill?: string; confidence?: number; reason?: string }
  | { type: "turn_state"; turn: TurnState }
  | { type: "token"; text: string }
  | { type: "tool_start"; tool?: string; tool_call_id?: string; input?: unknown }
  | { type: "tool_end"; tool?: string; tool_call_id?: string; output?: unknown }
  | { type: "debug"; kind: string; payload: unknown }
  | { type: "title"; title: string }
  | { type: "done"; session: SessionRecord; message_id: string }
  | { type: "error"; message: string };
