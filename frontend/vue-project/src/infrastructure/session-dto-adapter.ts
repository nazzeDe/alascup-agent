import type { ChatSession, JsonValue, ToolCallInfo, ToolError } from '@/domain/models'

type ToolResultDto = {
  execution_status: string
  output?: JsonValue
  error?: ToolError
  execution_time_ms?: number
}

type ToolCallDto = Omit<ToolCallInfo, 'output'> & {
  result?: JsonValue | ToolResultDto
}

export type SessionDto = Omit<ChatSession, 'executed_tool_list'> & {
  executed_tool_list: ToolCallDto[]
}

export function toChatSession(dto: SessionDto): ChatSession {
  return {
    ...dto,
    executed_tool_list: dto.executed_tool_list.map(toToolCallInfo),
  }
}

function toToolCallInfo(dto: ToolCallDto): ToolCallInfo {
  const { result, ...toolCall } = dto
  if (result === undefined) return toolCall
  if (!isToolResultDto(result)) return { ...toolCall, output: result }

  return {
    ...toolCall,
    ...(Object.prototype.hasOwnProperty.call(result, 'output') ? { output: result.output } : {}),
    ...(!toolCall.error && result.error ? { error: result.error } : {}),
    ...(toolCall.execution_time_ms === undefined && result.execution_time_ms !== undefined
      ? { execution_time_ms: result.execution_time_ms }
      : {}),
  }
}

function isToolResultDto(result: JsonValue | ToolResultDto): result is ToolResultDto {
  return typeof result === 'object'
    && result !== null
    && !Array.isArray(result)
    && 'execution_status' in result
}
