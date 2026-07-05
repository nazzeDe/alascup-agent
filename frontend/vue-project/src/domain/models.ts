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
  reasoning_content?: string;
  tool_calls?: Array<{
    id: string;
    type: string;
    function: { name: string; arguments: string };
  }>;
}

export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";
export type ExecutionStatus =
  | "PENDING_APPROVAL"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "REJECTED";

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
  error?: { code?: number; message: string; data?: string };
  timestamp: string;
}

export interface ChatSession {
  chat_id: string;
  title?: string;
  messages: Message[];
  executed_tool_list: ToolCallInfo[];
  timestamp: string;
}

export type AgentPhase =
  | "idle"
  | "thinking"
  | "calling_tool"
  | "waiting_for_tool"
  | "awaiting_approval"
  | "responding"
  | "done";

export interface ReasoningEntry {
  message_id: string;
  content: string;
  done: boolean;
  timestamp: string;
}

export interface ApprovalEvent {
  request_id: string;
  tool_name: string;
  params: Record<string, unknown>;
  reason: string;
  status: "pending" | "approved" | "rejected";
  message: string;
  timestamp?: string;
}

export interface ErrorInfo {
  code: string;
  message: string;
}
