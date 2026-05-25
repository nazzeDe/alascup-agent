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
  message_id: string;
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
  chat_id: string;
  message_id: string;
  delta: string;
}

export interface ReasoningEvent {
  chat_id?: string;
  message_id?: string;
  delta: string;
  done?: boolean;
}

export interface ToolCallEvent {
  chat_id: string;
  message_id: string;
  tool_name: string;
  params: Record<string, unknown>;
  is_read_only: boolean;
  server?: string;
}

export interface ToolResultEvent {
  chat_id: string;
  message_id: string;
  tool_name: string;
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

export interface ErrorEvent {
  code: string;
  message: string;
}

export interface DoneEvent {
  chat_id: string;
}

export type SSEEventType =
  | "assistant"
  | "reasoning"
  | "tool_call"
  | "tool_result"
  | "tool_approval_required"
  | "error"
  | "done";

export interface SSECallbacks {
  on_assistant?: (data: AssistantEvent) => void;
  on_reasoning?: (data: ReasoningEvent) => void;
  on_tool_call?: (data: ToolCallEvent) => void;
  on_tool_result?: (data: ToolResultEvent) => void;
  on_tool_approval_required?: (data: ToolApprovalRequiredEvent) => void;
  on_error?: (data: ErrorEvent) => void;
  on_done?: (data: DoneEvent) => void;
}
