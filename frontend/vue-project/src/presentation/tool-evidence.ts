import type { ToolCallInfo } from '@/domain/models'

const MAX_RESULT_LINES = 20
const MAX_RESULT_BYTES = 16 * 1024

export interface ToolEvidencePresentation {
  parameters: string
  error?: string
  result?: {
    fullText: string
    previewText: string
    truncated: boolean
  }
}

export function presentToolEvidence(toolCall: ToolCallInfo): ToolEvidencePresentation {
  return {
    parameters: formatJson(toolCall.params ?? {}),
    ...(toolCall.error ? { error: formatJson(toolCall.error) } : {}),
    ...(toolCall.output !== undefined ? { result: presentResult(toolCall.output) } : {}),
  }
}

function presentResult(output: ToolCallInfo['output']): NonNullable<ToolEvidencePresentation['result']> {
  const fullText = typeof output === 'string' ? output : formatJson(output)
  const lines = fullText.split('\n')
  const lineLimited = lines.slice(0, MAX_RESULT_LINES).join('\n')
  const byteLimited = limitUtf8Bytes(lineLimited, MAX_RESULT_BYTES)

  return {
    fullText,
    previewText: byteLimited.text,
    truncated: lines.length > MAX_RESULT_LINES || byteLimited.truncated,
  }
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2) ?? ''
}

function limitUtf8Bytes(text: string, maxBytes: number): { text: string; truncated: boolean } {
  const bytes = new TextEncoder().encode(text)
  if (bytes.length <= maxBytes) return { text, truncated: false }

  const preview = new TextDecoder().decode(bytes.slice(0, maxBytes)).replace(/\uFFFD$/, '')
  return { text: preview, truncated: true }
}
