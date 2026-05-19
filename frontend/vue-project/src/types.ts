export type MessageType = 'user' | 'assistant' | 'tool_call' | 'tool_result' | 'system'

export interface Message {
  messageID: string
  chatID: string
  timestamp: string
  type: MessageType
  content: string
  isMeta?: boolean
}

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED'
export type ExecutionStatus = 'PENDING_APPROVAL' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface ToolCallInfo {
  messageID: string
  chatID: string
  tool_name: string
  server?: string
  isReadOnly: boolean
  isRollbackable?: boolean
  params?: Record<string, unknown>
  request_id?: string
  approval_status?: ApprovalStatus
  execution_status: ExecutionStatus
  output?: Record<string, unknown>
  error?: { code: number; message: string; data?: string }
  timestamp: string
}

export interface ChatSession {
  chatID: string
  title?: string
  messages: Message[]
  executed_tool_list: ToolCallInfo[]
  timestamp: string
}

// SSE event payloads
export interface AssistantEvent {
  chat_id: string
  message_id: string
  delta: string
}

export interface ToolCallEvent {
  chat_id: string
  message_id: string
  tool_name: string
  params: Record<string, unknown>
  isReadOnly: boolean
}

export interface ToolResultEvent {
  chat_id: string
  message_id: string
  tool_name: string
  execution_status: 'SUCCEEDED' | 'FAILED'
  output?: Record<string, unknown>
  error?: { code: number; message: string }
}

export interface ToolApprovalRequiredEvent {
  chat_id: string
  request_id: string
  tool_name: string
  params: Record<string, unknown>
  reason: string
}

export interface ErrorEvent {
  code: string
  message: string
}

export interface DoneEvent {
  chat_id: string
}

export type SSEEventType = 'assistant' | 'tool_call' | 'tool_result' | 'tool_approval_required' | 'error' | 'done'

export interface SSECallbacks {
  onAssistant?: (data: AssistantEvent) => void
  onToolCall?: (data: ToolCallEvent) => void
  onToolResult?: (data: ToolResultEvent) => void
  onToolApprovalRequired?: (data: ToolApprovalRequiredEvent) => void
  onError?: (data: ErrorEvent) => void
  onDone?: (data: DoneEvent) => void
}
