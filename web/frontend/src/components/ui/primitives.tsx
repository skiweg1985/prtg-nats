import clsx from 'clsx'
import { Link } from 'react-router-dom'
import { useEffect, useRef } from 'react'
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

/**
 * The small set of primitives everything else is built from.
 *
 * No component here defines a colour, radius or duration of its own - they all
 * resolve to the token layer, which is what keeps twelve screens looking like
 * one product.
 */

type Tone = 'ok' | 'warn' | 'danger' | 'neutral' | 'accent'

const TONE_CLASSES: Record<Tone, string> = {
  ok: 'bg-ok-soft text-ok border-ok/25',
  warn: 'bg-warn-soft text-warn border-warn/25',
  danger: 'bg-danger-soft text-danger border-danger/25',
  neutral: 'bg-neutral-soft text-ink-2 border-rule',
  accent: 'bg-accent-soft text-accent border-accent/25',
}

export function Badge({
  tone = 'neutral',
  children,
  className,
  quiet,
}: {
  tone?: Tone
  children: ReactNode
  className?: string
  /** Sentence-case body text instead of the shouting label style. For chips
      that carry words a human wrote - step names, connection names - which
      ALL-CAPS turns into noise. */
  quiet?: boolean
}) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-inset border px-2 py-0.5',
        quiet
          ? 'text-xs whitespace-nowrap'
          : 'font-mono text-[0.6875rem] tracking-[0.04em] whitespace-nowrap uppercase',
        TONE_CLASSES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

/** A filled dot. Carries the same meaning as the badge for a compact row. */
export function Dot({ tone = 'neutral' }: { tone?: Tone }) {
  const fill: Record<Tone, string> = {
    ok: 'bg-ok',
    warn: 'bg-warn',
    danger: 'bg-danger',
    neutral: 'bg-neutral',
    accent: 'bg-accent',
  }
  return (
    <span
      aria-hidden
      className={clsx('inline-block size-2 shrink-0 rounded-full', fill[tone])}
    />
  )
}

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-accent-ink hover:opacity-90 border-transparent',
  secondary: 'bg-surface text-ink border-rule-2 hover:bg-surface-2',
  ghost: 'bg-transparent text-ink-2 border-transparent hover:bg-surface-2',
  danger: 'bg-danger text-accent-ink border-transparent hover:opacity-90',
}

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
}) {
  return (
    <button
      className={clsx(
        'rounded-control inline-flex items-center justify-center gap-2 border font-medium',
        'transition-colors duration-100 ease-(--ease-out)',
        'disabled:cursor-not-allowed disabled:opacity-45',
        size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-sm',
        BUTTON_VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

/** Every page's first line: title, subtitle, actions - one shape.

    Four different header idioms had grown across the pages, and one page had
    no title at all. The nav label, the h1 and the browser's sense of "where
    am I" should be the same words. */
export function PageHeader({
  title,
  subtitle,
  action,
  backTo,
  backLabel,
}: {
  title: ReactNode
  subtitle?: ReactNode
  action?: ReactNode
  backTo?: string
  backLabel?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
      <div className="min-w-0">
        {backTo && (
          <Link to={backTo} className="text-ink-3 hover:text-ink text-xs">
            ← {backLabel}
          </Link>
        )}
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-lg">{title}</h1>
          {subtitle && <p className="text-ink-3 text-sm">{subtitle}</p>}
        </div>
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </header>
  )
}

/** The question every destructive action asks, asked the same way.

    Promoted from SystemPage, which had the cleanest of roughly nine
    hand-built copies. */
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel,
  tone = 'danger',
  pending,
  error,
  onConfirm,
  onClose,
}: {
  title: string
  body: ReactNode
  confirmLabel: string
  cancelLabel: string
  tone?: 'danger' | 'primary'
  pending?: boolean
  error?: ReactNode
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog title={title} onClose={onClose}>
      <div className="space-y-4">
        {typeof body === 'string' ? <Banner tone="warn">{body}</Banner> : body}
        {error}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {cancelLabel}
          </Button>
          <Button variant={tone} disabled={pending} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

/** A tab strip with the ARIA wiring the inline version never had. */
export function Tabs<T extends string>({
  tabs,
  active,
  onSelect,
  renderLabel,
}: {
  tabs: readonly T[]
  active: T
  onSelect: (tab: T) => void
  renderLabel: (tab: T) => ReactNode
}) {
  return (
    <nav role="tablist" className="border-rule flex gap-1 border-b">
      {tabs.map((entry) => (
        <button
          key={entry}
          type="button"
          role="tab"
          aria-selected={entry === active}
          onClick={() => onSelect(entry)}
          className={
            entry === active
              ? 'border-accent text-ink -mb-px border-b-2 px-3 py-2 text-sm font-medium'
              : 'text-ink-3 hover:text-ink -mb-px border-b-2 border-transparent px-3 py-2 text-sm'
          }
        >
          {renderLabel(entry)}
        </button>
      ))}
    </nav>
  )
}

/** One spinner. Two features had grown their own, in two sizes. */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={clsx(
        'border-accent inline-block size-4 animate-spin rounded-full border-2 border-t-transparent',
        className,
      )}
    />
  )
}

export function Card({
  title,
  action,
  children,
  className,
  dense,
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
  dense?: boolean
}) {
  return (
    <section className={clsx('surface-card overflow-hidden', className)}>
      {(title || action) && (
        <header className="border-rule flex items-center justify-between gap-3 border-b px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className={dense ? '' : 'p-4'}>{children}</div>
    </section>
  )
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: string
  error?: string
  children: ReactNode
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-ink text-sm font-medium">{label}</span>
      {children}
      {error ? (
        <span className="text-danger text-xs">{error}</span>
      ) : hint ? (
        <span className="text-ink-3 text-xs">{hint}</span>
      ) : null}
    </label>
  )
}

/** The native select in the Input's clothes.

    Eight features had hand-rolled this with three different paddings; the
    control is the same everywhere, so the styling should be too. */
export function Select({
  className,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        'rounded-control border-rule-2 bg-surface text-ink border px-2.5 py-1.5 text-sm',
        'disabled:opacity-50',
        className,
      )}
      {...rest}
    />
  )
}

export function Textarea({
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={clsx(
        'rounded-control border-rule-2 bg-surface text-ink border px-2.5 py-1.5 font-mono text-xs',
        'placeholder:text-ink-3 disabled:opacity-50',
        className,
      )}
      {...rest}
    />
  )
}

/** A labelled checkbox with an optional hint underneath the label.

    The raw pattern existed seven times with two different top margins on the
    box; the alignment is decided here once. */
export function CheckboxField({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: ReactNode
  hint?: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        <span className="text-ink">{label}</span>
        {hint && <span className="text-ink-3 block text-xs">{hint}</span>}
      </span>
    </label>
  )
}

export function Input({
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={clsx(
        'rounded-control border-rule-2 bg-surface text-ink border px-2.5 py-1.5 text-sm',
        'placeholder:text-ink-3 disabled:opacity-50',
        className,
      )}
      {...rest}
    />
  )
}

/** A machine value: id, checksum, version, path. Always monospaced. */
export function Mono({
  children,
  truncate,
  className,
}: {
  children: ReactNode
  truncate?: boolean
  className?: string
}) {
  return (
    <span
      className={clsx(
        'font-mono text-xs',
        truncate && 'block max-w-full truncate',
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Label({ children }: { children: ReactNode }) {
  return <span className="label-mono">{children}</span>
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <p className="text-ink text-sm font-medium">{title}</p>
      {hint && <p className="text-ink-3 max-w-md text-sm">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={clsx('bg-surface-2 rounded-control animate-pulse', className)}
      aria-hidden
    />
  )
}

export function Banner({
  tone = 'warn',
  title,
  children,
  action,
}: {
  tone?: Tone
  title?: string
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <div
      className={clsx(
        'rounded-card flex items-start justify-between gap-4 border px-4 py-3',
        TONE_CLASSES[tone],
      )}
      role="status"
    >
      <div className="min-w-0">
        {title && <p className="text-sm font-semibold normal-case">{title}</p>}
        <div className="text-sm normal-case">{children}</div>
      </div>
      {action}
    </div>
  )
}

/**
 * What every overlay in this interface is built from.
 *
 * There were eight hand-built copies of the markup below before this existed,
 * and all eight got the keyboard wrong in the same three ways: Escape did not
 * close them, Tab walked out of the dialog and on through the page behind it,
 * and closing left the focus wherever it had drifted to. None of that is
 * decoration - a dialog the keyboard can leave but not re-enter is one an
 * operator has to reach for the mouse to answer.
 *
 * Deliberately not a portal: the overlay is fixed to the viewport and carries
 * the dialog z-index, so where it sits in the tree changes nothing about where
 * it is drawn.
 */

// Everything the browser would stop at on the way through, which is what the
// trap has to work with.
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

export function Dialog({
  title,
  onClose,
  size = 'sm',
  children,
}: {
  title: string
  /** Escape, a click beside the panel, and nothing else - the content owns
      its own buttons and decides what closing means. */
  onClose: () => void
  size?: 'sm' | 'md' | 'lg'
  children: ReactNode
}) {
  const panel = useRef<HTMLDivElement>(null)
  // Read through a ref so the effect can run once. Callers pass a fresh arrow
  // function on every render; as a dependency it would tear the listener down
  // and put the focus back on each one.
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    const opener = document.activeElement
    const panelElement = panel.current
    // Into the dialog on open, or the first Tab would start at the top of the
    // page behind it.
    const first = panelElement?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? panelElement)?.focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        close.current()
        return
      }
      if (event.key !== 'Tab' || panelElement === null) return
      const stops = [...panelElement.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (stops.length === 0) {
        event.preventDefault()
        return
      }
      const last = stops[stops.length - 1]
      const leaving = event.shiftKey
        ? document.activeElement === stops[0]
        : document.activeElement === last
      // Also when the focus is outside already: a click on the overlay puts it
      // on the body, and Tab from there would walk the page behind.
      if (leaving || !panelElement.contains(document.activeElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : stops[0]).focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [])

  return (
    <div
      className="fixed inset-0 z-(--z-dialog) flex items-center justify-center bg-black/40 p-4"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={clsx(
          'max-h-full w-full overflow-auto',
          size === 'sm' && 'max-w-md',
          size === 'md' && 'max-w-lg',
          size === 'lg' && 'max-w-2xl',
        )}
      >
        <Card title={title}>{children}</Card>
      </div>
    </div>
  )
}

/** A key-value row. The workhorse of every detail page. */
export function DetailRow({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="border-rule grid grid-cols-[minmax(9rem,14rem)_1fr] gap-3 border-b py-2 last:border-0">
      <dt className="text-ink-3 text-sm">{label}</dt>
      <dd className="text-ink min-w-0 text-sm">{children}</dd>
    </div>
  )
}
