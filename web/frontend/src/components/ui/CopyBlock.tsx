import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from './primitives'

/** Clipboard write with the 2-second confirmation every copy control shares.
 *
 * Extracted because seven features had grown their own copies of this, most
 * of them without any feedback at all - a press that does nothing visible
 * reads as a press that did nothing.
 */
export function CopyButton({
  value,
  label,
  size = 'sm',
}: {
  value: string
  /** Defaults to the shared "Copy" label. */
  label?: string
  size?: 'sm' | 'md'
}) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  return (
    <Button
      size={size}
      variant="ghost"
      aria-live="polite"
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => setCopied(true))
      }}
    >
      {copied ? `✓ ${t('common.copied')}` : (label ?? t('common.copy'))}
    </Button>
  )
}

/** An inline chip for a file name, a parameter line, a machine word.
 *
 * One padding and one background for what used to be seven hand-built
 * `<code>` spans with three paddings, two of them with no background at all.
 */
export function InlineCode({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <code
      className={`bg-surface-2 rounded-inset text-ink px-1.5 py-0.5 font-mono text-xs break-all ${className ?? ''}`}
    >
      {children}
    </code>
  )
}

/** How many lines a block may show before it folds. */
const COLLAPSE_AFTER = 3

/** A command in a box: copy always, read on demand.
 *
 * Collapsed by default once the value exceeds a few lines, because nobody
 * reads a shell script in a dialog - they copy it. The first line stays
 * visible so the block is recognisable, the expander says how much is
 * hidden, and copying always takes the complete value no matter what is
 * currently shown.
 *
 * The button lives in a header row rather than floating over the text; the
 * old absolute-positioned button sat on top of the first line whenever that
 * line was long.
 */
export function CopyBlock({
  value,
  label,
  title,
}: {
  value: string
  label?: string
  /** Optional heading shown left of the copy button. */
  title?: string
}) {
  const { t } = useTranslation()
  const lines = useMemo(() => value.split('\n'), [value])
  const collapsible = lines.length > COLLAPSE_AFTER
  const [expanded, setExpanded] = useState(false)
  const shown = collapsible && !expanded ? lines.slice(0, 1).join('\n') : value

  return (
    <div className="border-rule-2 bg-surface-2 rounded-card border">
      <div className="border-rule flex items-center justify-between gap-2 border-b px-3 py-1">
        <span className="label-mono text-ink-3 truncate">{title ?? ''}</span>
        <CopyButton value={value} label={label} />
      </div>
      <pre className="text-ink overflow-x-auto p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
        {shown}
        {collapsible && !expanded && <span className="text-ink-3">{' …'}</span>}
      </pre>
      {collapsible && (
        <button
          type="button"
          className="text-ink-3 hover:text-ink border-rule block w-full border-t px-3 py-1 text-left text-xs"
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded
            ? t('common.collapse')
            : t('common.showLines', { count: lines.length })}
        </button>
      )}
    </div>
  )
}

/** Time left, as a string, or null once it has run out. */
export function useCountdown(deadline: string): string | null {
  const target = useMemo(() => new Date(deadline).getTime(), [deadline])
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const seconds = Math.floor((target - now) / 1000)
  if (seconds <= 0) return null
  const minutes = Math.floor(seconds / 60)
  return minutes > 0
    ? `${minutes} min ${String(seconds % 60).padStart(2, '0')} s`
    : `${seconds} s`
}
