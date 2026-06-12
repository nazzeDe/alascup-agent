export type MessageType =
  | "user"
  | "assistant"
  | "tool_call"
  | "tool_result"
  | "system";

export interface Message {
  message_id: string;
  chat_id: string;
  timestamp: string;
  type: MessageType;
  content: string;
  is_meta?: boolean;
}

export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";
export type ExecutionStatus =
  | "PENDING_APPROVAL"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED";

export interface ToolCallInfo {
  call_id: string;
  chat_id: string;
  tool_name: string;
  server?: string;
  is_read_only: boolean;
  is_rollbackable?: boolean;
  params?: Record<string, unknown>;
  request_id?: string;
  approval_status?: ApprovalStatus;
  execution_status: ExecutionStatus;
  execution_time_ms?: number;
  output?: Record<string, unknown>;
  error?: { code: number; message: string; data?: string };
  timestamp: string;
}

export interface ChatSession {
  chat_id: string;
  title?: string;
  messages: Message[];
  executed_tool_list: ToolCallInfo[];
  timestamp: string;
}

// SSE event payloads
export interface AssistantEvent {
  delta: string;
}

export interface AssistantDoneEvent {
  // empty object
}

export interface ReasoningEvent {
  delta: string;
}

export interface ThinkingDoneEvent {
  // empty object
}

export interface ToolCallEvent {
  call_id: string;
  tool_name: string;
  params: Record<string, unknown>;
  is_read_only: boolean;
  server?: string;
}

export interface ToolResultEvent {
  call_id: string;
  execution_status: "SUCCEEDED" | "FAILED";
  output?: Record<string, unknown>;
  error?: { code: number; message: string };
  execution_time_ms?: number;
}

export interface ToolApprovalRequiredEvent {
  chat_id: string;
  request_id: string;
  tool_name: string;
  params: Record<string, unknown>;
  reason: string;
}

export interface SessionInitEvent {
  chat_id: string;
}

export interface ErrorEvent {
  code: string;
  message: string;
}

export type SSEEventType =
  | "assistant"
  | "assistant_done"
  | "reasoning"
  | "thinking_done"
  | "tool_call"
  | "tool_result"
  | "tool_approval_required"
  | "session_init"
  | "error"
  | "done";

export interface SSECallbacks {
  on_assistant?: (data: {delta: string}) => void;
  on_assistant_done?: (data: {}) => void;
  on_reasoning?: (data: {delta: string}) => void;
  on_thinking_done?: (data: {}) => void;
  on_tool_call?: (data: ToolCallEvent) => void;
  on_tool_result?: (data: ToolResultEvent) => void;
  on_tool_approval_required?: (data: ToolApprovalRequiredEvent) => void;
  on_session_init?: (data: SessionInitEvent) => void;
  on_error?: (data: ErrorEvent) => void;
  on_done?: (data: {}) => void;
}
