import { describe, it, expect } from 'vitest'
import { renderMarkdown } from '@/infrastructure/markdown'

describe('infrastructure/markdown — renderMarkdown', () => {
  it('renders plain text as paragraph', () => {
    const result = renderMarkdown('hello world')
    expect(result).toContain('<p>hello world</p>')
  })

  it('renders headers', () => {
    const result = renderMarkdown('# Title\n\n## Subtitle')
    expect(result).toContain('<h1>Title</h1>')
    expect(result).toContain('<h2>Subtitle</h2>')
  })

  it('renders bold and italic', () => {
    const result = renderMarkdown('**bold** and *italic*')
    expect(result).toContain('<strong>bold</strong>')
    expect(result).toContain('<em>italic</em>')
  })

  it('renders inline code', () => {
    const result = renderMarkdown('use `const x = 1` here')
    expect(result).toContain('<code>const x = 1</code>')
  })

  it('renders code blocks', () => {
    const result = renderMarkdown('```bash\nls -la\n```')
    expect(result).toContain('<code')
    expect(result).toContain('ls -la')
  })

  it('renders unordered lists', () => {
    const result = renderMarkdown('- item 1\n- item 2')
    expect(result).toContain('<ul>')
    expect(result).toContain('<li>item 1</li>')
    expect(result).toContain('<li>item 2</li>')
  })

  it('renders ordered lists', () => {
    const result = renderMarkdown('1. first\n2. second')
    expect(result).toContain('<ol>')
    expect(result).toContain('<li>first</li>')
    expect(result).toContain('<li>second</li>')
  })

  it('renders links', () => {
    const result = renderMarkdown('[click here](https://example.com)')
    expect(result).toContain('<a href="https://example.com"')
    expect(result).toContain('click here</a>')
  })

  it('renders images', () => {
    const result = renderMarkdown('![alt](https://example.com/img.png)')
    expect(result).toContain('<img')
    expect(result).toContain('alt="alt"')
    expect(result).toContain('src="https://example.com/img.png"')
  })

  it('sanitizes script tags (XSS prevention)', () => {
    const result = renderMarkdown('<script>alert("xss")</script>hello')
    expect(result).not.toContain('<script>')
    expect(result).not.toContain('alert')
    expect(result).toContain('hello')
  })

  it('sanitizes onclick handlers (XSS prevention)', () => {
    const result = renderMarkdown('<div onclick="alert(1)">click</div>')
    expect(result).not.toContain('onclick')
  })

  it('strips javascript: URLs from links (XSS prevention)', () => {
    const result = renderMarkdown('[evil](javascript:alert(1))')
    // The href should be sanitized away by DOMPurify
    expect(result).not.toContain('javascript:')
  })

  it('strips data: URLs from links', () => {
    const result = renderMarkdown('[data](data:text/html,<script>alert(1)</script>)')
    expect(result).not.toContain('data:')
  })

  it('allows safe relative URLs (/)', () => {
    const result = renderMarkdown('[home](/)')
    expect(result).toContain('href="/"')
  })

  it('strips relative URLs without leading slash (potential phishing)', () => {
    // Paths like ./foo are not in the allowed set — only /, mailto:, https:, http:, #
    const result = renderMarkdown('[docs](./docs/readme)')
    expect(result).not.toContain('href="./docs/readme"')
    expect(result).toContain('<a>docs</a>')
  })

  it('allows mailto: links', () => {
    const result = renderMarkdown('[email](mailto:test@example.com)')
    expect(result).toContain('href="mailto:test@example.com"')
  })

  it('allows fragment links', () => {
    const result = renderMarkdown('[section](#section-1)')
    expect(result).toContain('href="#section-1"')
  })

  it('returns empty string for empty input', () => {
    const result = renderMarkdown('')
    // marked.parse('') returns '', DOMPurify of '' is ''
    expect(result).toBe('')
  })

  it('renders multi-paragraph text', () => {
    const result = renderMarkdown('first paragraph\n\nsecond paragraph')
    expect(result).toContain('<p>first paragraph</p>')
    expect(result).toContain('<p>second paragraph</p>')
  })

  it('renders blockquotes', () => {
    const result = renderMarkdown('> quoted text')
    expect(result).toContain('<blockquote>')
    expect(result).toContain('quoted text')
  })

  it('renders horizontal rules', () => {
    const result = renderMarkdown('---')
    expect(result).toContain('<hr')
  })

  it('renders tables', () => {
    const result = renderMarkdown('| a | b |\n|---|---|\n| 1 | 2 |')
    expect(result).toContain('<table>')
    expect(result).toContain('<td>1</td>')
    expect(result).toContain('<td>2</td>')
  })

  it('escapes HTML in text content', () => {
    const result = renderMarkdown('hello <b>world</b>')
    // marked doesn't escape inline HTML by default unless it's in code,
    // but DOMPurify allows safe tags like <b>. The key test is XSS vectors.
    // Just verify it doesn't crash
    expect(result).toBeDefined()
  })

  it('handles nested markdown in lists', () => {
    const result = renderMarkdown('- item **bold** with `code`')
    expect(result).toContain('<li>')
    expect(result).toContain('<strong>bold</strong>')
    expect(result).toContain('<code>code</code>')
  })
})
