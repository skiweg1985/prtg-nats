import { isValidElement, type ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import { Link } from 'react-router-dom'
import remarkGfm from 'remark-gfm'

interface MarkdownProps {
  children: string
  resolveHref?: (href: string) => string
}

function textContent(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textContent).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return textContent(node.props.children)
  }
  return ''
}

/** Match the predictable fragment names people expect from Markdown headings. */
function headingId(children: ReactNode) {
  return textContent(children)
    .trim()
    .toLocaleLowerCase('en')
    .replace(/[^\p{Letter}\p{Number}\s_-]/gu, '')
    .replace(/\s+/g, '-')
}

const components: Components = {
  h1: ({ node: _node, children, ...props }) => (
    <h1
      {...props}
      id={headingId(children)}
      className="text-ink mt-6 mb-3 text-base font-semibold"
    >
      {children}
    </h1>
  ),
  h2: ({ node: _node, children, ...props }) => (
    <h2
      {...props}
      id={headingId(children)}
      className="border-rule text-ink mt-6 mb-3 border-b pb-1.5 text-sm font-semibold"
    >
      {children}
    </h2>
  ),
  h3: ({ node: _node, children, ...props }) => (
    <h3
      {...props}
      id={headingId(children)}
      className="text-ink mt-5 mb-2 text-sm font-semibold"
    >
      {children}
    </h3>
  ),
  h4: ({ node: _node, children, ...props }) => (
    <h4
      {...props}
      id={headingId(children)}
      className="text-ink mt-4 mb-2 text-sm font-semibold"
    >
      {children}
    </h4>
  ),
  p: ({ node: _node, ...props }) => <p {...props} className="my-3" />,
  ul: ({ node: _node, ...props }) => (
    <ul {...props} className="my-3 list-disc space-y-1 pl-5" />
  ),
  ol: ({ node: _node, ...props }) => (
    <ol {...props} className="my-3 list-decimal space-y-1 pl-5" />
  ),
  blockquote: ({ node: _node, ...props }) => (
    <blockquote
      {...props}
      className="border-accent bg-accent-soft text-ink-2 my-3 border-l-2 px-3 py-2"
    />
  ),
  a: ({ node: _node, href, ...props }) => {
    const className =
      'text-accent underline decoration-transparent transition-colors hover:decoration-current'
    if (href?.startsWith('/')) {
      return <Link {...props} to={href} className={className} />
    }

    const external = Boolean(href && /^(?:https?:)?\/\//i.test(href))
    return (
      <a
        {...props}
        href={href}
        className={className}
        target={external ? '_blank' : undefined}
        rel={external ? 'noreferrer noopener' : undefined}
      />
    )
  },
  table: ({ node: _node, children, ...props }) => (
    <div className="border-rule rounded-inset my-3 overflow-x-auto border">
      <table {...props} className="w-full border-collapse text-left text-xs">
        {children}
      </table>
    </div>
  ),
  th: ({ node: _node, ...props }) => (
    <th
      {...props}
      className="bg-surface-2 border-rule text-ink border-b px-3 py-2 font-semibold whitespace-nowrap"
    />
  ),
  td: ({ node: _node, ...props }) => (
    <td {...props} className="border-rule border-b px-3 py-2 align-top" />
  ),
  pre: ({ node: _node, ...props }) => (
    <pre
      {...props}
      className="bg-surface-2 rounded-inset text-ink my-3 overflow-x-auto p-3 text-xs"
    />
  ),
  code: ({ node: _node, children, className, ...props }) => {
    const block = Boolean(className || String(children).endsWith('\n'))
    return (
      <code
        {...props}
        className={
          block
            ? `font-mono ${className ?? ''}`
            : 'bg-surface-2 rounded-inset text-ink px-1 py-0.5 font-mono text-xs'
        }
      >
        {children}
      </code>
    )
  },
  hr: ({ node: _node, ...props }) => (
    <hr {...props} className="border-rule my-5 border-t" />
  ),
  img: ({ node: _node, ...props }) => (
    <img {...props} className="rounded-inset my-3 max-w-full" />
  ),
}

/** Render repository-owned Markdown without enabling embedded HTML. */
export function Markdown({ children, resolveHref }: MarkdownProps) {
  return (
    <div className="text-ink-2 min-w-0 text-sm leading-6 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={components}
        urlTransform={(url, key) =>
          defaultUrlTransform(key === 'href' && resolveHref ? resolveHref(url) : url)
        }
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
