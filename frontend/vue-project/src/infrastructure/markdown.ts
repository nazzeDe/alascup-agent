import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Configure DOMPurify hook for anchor href validation (same logic as
// existing MessageItem.vue). Allow https:, http:, /, mailto:, and # schemes.
// Strip hrefs that don't match any allowed scheme.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node instanceof HTMLAnchorElement) {
    const href = node.getAttribute('href')
    if (href && !/^(https?:|\/|mailto:|#)/.test(href)) {
      node.removeAttribute('href')
    }
  }
})

let hljsInitialized = false

async function ensureHighlightJs(): Promise<void> {
  if (hljsInitialized) return
  const hljs = (await import('highlight.js')).default
  const { default: bash } = await import('highlight.js/lib/languages/bash')
  const { default: json } = await import('highlight.js/lib/languages/json')
  const { default: python } = await import('highlight.js/lib/languages/python')
  hljs.registerLanguage('bash', bash)
  hljs.registerLanguage('json', json)
  hljs.registerLanguage('python', python)
  hljsInitialized = true
}

export function renderMarkdown(content: string): string {
  // Kick off highlight.js registration (non-blocking); first render may
  // not have highlighting but subsequent ones will.
  ensureHighlightJs()

  const raw = marked.parse(content, { async: false }) as string
  return DOMPurify.sanitize(raw)
}
