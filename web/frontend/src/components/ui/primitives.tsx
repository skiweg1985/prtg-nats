import clsx from 'clsx'
import { ArrowLeft, MoreHorizontal, Upload, type LucideIcon } from 'lucide-react'
import { Link, type LinkProps } from 'react-router-dom'
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  KeyboardEvent as ReactKeyboardEvent,
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

// Banners keep a hairline: they are surfaces that hold text.
const TONE_CLASSES: Record<Tone, string> = {
  ok: 'bg-ok-soft text-ok border-ok/25',
  warn: 'bg-warn-soft text-warn border-warn/25',
  danger: 'bg-danger-soft text-danger border-danger/25',
  neutral: 'bg-neutral-soft text-ink-2 border-rule',
  accent: 'bg-accent-soft text-accent border-accent/25',
}

// Badges have none: a border, capitals and padding are what a button looks
// like, and a state that looks clickable invites a click that does nothing.
const BADGE_TONES: Record<Tone, string> = {
  ok: 'bg-ok-soft text-ok',
  warn: 'bg-warn-soft text-warn',
  danger: 'bg-danger-soft text-danger',
  neutral: 'bg-neutral-soft text-ink-2',
  accent: 'bg-accent-soft text-accent',
}

/**
 * A state, never an action. Flat tinted text with no border and no hover, so
 * it cannot be mistaken for a control standing next to it.
 */
export function Badge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: Tone
  children: ReactNode
  className?: string
  /** Kept for callers written before badges went sentence case for good;
      every badge reads like this now. */
  quiet?: boolean
}) {
  return (
    <span
      className={clsx(
        'rounded-inset inline-flex h-5 items-center gap-1 px-1.5',
        'text-[0.6875rem] leading-none font-medium whitespace-nowrap',
        BADGE_TONES[tone],
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

/**
 * Four meanings, one geometry.
 *
 * primary   the one thing the screen is for, at most once per view
 * secondary a real action next to it - outlined, so it reads as a control
 * ghost     row and toolbar actions that should not compete with the data
 * danger    only where a destructive step is confirmed; elsewhere a
 *           destructive action is a menu item in danger text
 *
 * Height, padding and icon size are fixed per size, so a row of buttons
 * lines up whatever they say.
 */
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-accent-ink border-transparent hover:bg-accent/90 active:bg-accent/80',
  secondary:
    'bg-surface text-ink border-rule-2 hover:bg-surface-2 active:bg-surface-2/70',
  ghost:
    'bg-transparent text-ink-2 border-transparent hover:bg-surface-2 hover:text-ink active:bg-surface-2/70',
  danger: 'bg-danger text-accent-ink border-transparent hover:bg-danger/90 active:bg-danger/80',
}

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 gap-1.5 px-2 text-xs',
  md: 'h-8 gap-2 px-3 text-sm',
}

const ICON_SIZE: Record<ButtonSize, number> = { sm: 14, md: 16 }

function buttonClasses(variant: ButtonVariant, size: ButtonSize, className?: string) {
  return clsx(
    'rounded-control inline-flex shrink-0 items-center justify-center border font-medium whitespace-nowrap',
    'transition-colors duration-(--dur-instant) ease-(--ease-out)',
    'disabled:pointer-events-none disabled:opacity-45',
    BUTTON_SIZES[size],
    BUTTON_VARIANTS[variant],
    className,
  )
}

function ButtonIcon({ icon: Icon, size }: { icon?: LucideIcon; size: ButtonSize }) {
  if (!Icon) return null
  return <Icon aria-hidden size={ICON_SIZE[size]} strokeWidth={1.75} className="shrink-0" />
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
  /** A Lucide icon, drawn before the label at the size's icon size. */
  icon?: LucideIcon
}) {
  return (
    // No default type: buttons inside forms rely on the browser's submit.
    <button className={buttonClasses(variant, size, className)} {...rest}>
      <ButtonIcon icon={icon} size={size} />
      {children}
    </button>
  )
}

/**
 * A filter that is on or off. Not a button variant: a filter is a selection,
 * and drawing the selected one as a primary button put three blue "next
 * steps" in a row where there was none. Pressed reads as a raised inset -
 * surface, hairline, ink - and the signal colour stays for actions.
 */
export function FilterChip({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={clsx(
        'rounded-control inline-flex h-7 shrink-0 items-center border px-2.5 text-xs font-medium whitespace-nowrap',
        'transition-colors duration-(--dur-instant) ease-(--ease-out)',
        pressed
          ? 'border-rule-2 bg-surface-2 text-ink'
          : 'text-ink-2 hover:text-ink hover:bg-surface-2 border-transparent bg-transparent',
      )}
    >
      {children}
    </button>
  )
}

/** A navigation that sits among actions, dressed as one of them. */
export function ButtonLink({
  variant = 'ghost',
  size = 'sm',
  icon,
  className,
  children,
  ...rest
}: LinkProps & { variant?: ButtonVariant; size?: ButtonSize; icon?: LucideIcon }) {
  return (
    <Link className={buttonClasses(variant, size, className)} {...rest}>
      <ButtonIcon icon={icon} size={size} />
      {children}
    </Link>
  )
}

/** A square button that is only an icon. The label is required: it is the
    accessible name and the tooltip, the only words the button has. */
export function IconButton({
  icon,
  label,
  variant = 'ghost',
  size = 'sm',
  className,
  ...rest
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
  icon: LucideIcon
  label: string
  variant?: ButtonVariant
  size?: ButtonSize
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={buttonClasses(variant, size, clsx(size === 'sm' ? 'w-7 px-0' : 'w-8 px-0', className))}
      {...rest}
    >
      <ButtonIcon icon={icon} size={size} />
    </button>
  )
}

export interface MenuAction {
  key: string
  label: string
  icon?: LucideIcon
  onSelect: () => void
  /** Destructive: drawn in danger text below a divider. The confirmation is
      the action's own job - the menu only opens it. */
  tone?: 'danger'
  disabled?: boolean
}

/**
 * The overflow for actions that are needed rarely or that destroy something.
 *
 * A menu button with the keyboard behaviour people expect from one: arrows
 * move, Enter picks, Escape and a click elsewhere close and give the focus
 * back to the button. Destructive items come last, after a divider, so they
 * are never the item a slipping finger lands on first.
 */
export function ActionMenu({
  label,
  actions,
  size = 'sm',
  variant = 'ghost',
}: {
  label: string
  actions: MenuAction[]
  size?: ButtonSize
  variant?: ButtonVariant
}) {
  const [open, setOpen] = useState(false)
  // Fixed to the viewport rather than to the row: cards and tables clip what
  // overflows them, and a menu in the last row of a card would be cut off.
  const [place, setPlace] = useState<{ top: number; right: number } | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const menuId = useId()
  const ordered = [
    ...actions.filter((action) => action.tone !== 'danger'),
    ...actions.filter((action) => action.tone === 'danger'),
  ]
  const firstDanger = ordered.findIndex((action) => action.tone === 'danger')

  // Below the button, right edges aligned; above it when the viewport ends
  // first. Measured before paint so the menu never flashes in the wrong spot.
  useLayoutEffect(() => {
    if (!open || !trigger.current) return
    const button = trigger.current.getBoundingClientRect()
    const height = panel.current?.offsetHeight ?? 0
    const below = button.bottom + 4
    const top =
      below + height > window.innerHeight - 8 && button.top - height - 4 > 8
        ? button.top - height - 4
        : below
    setPlace({ top, right: window.innerWidth - button.right })
  }, [open])

  // Focus moves in once the menu is placed: a hidden element cannot take it.
  useEffect(() => {
    if (open && place) {
      panel.current?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')?.focus()
    }
  }, [open, place])

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    // A fixed menu would stay put while its button scrolls away.
    function onMove() {
      setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [open])

  if (ordered.length === 0) return null

  function close() {
    setOpen(false)
    trigger.current?.focus()
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      close()
      return
    }
    if (event.key === 'Tab') {
      setOpen(false)
      return
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const items = [
      ...(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? []),
    ]
    const at = items.indexOf(document.activeElement as HTMLElement)
    const step = event.key === 'ArrowDown' ? 1 : -1
    items[(at + step + items.length) % items.length]?.focus()
  }

  return (
    <div ref={root} className="relative inline-flex" onKeyDown={open ? onKeyDown : undefined}>
      <button
        ref={trigger}
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => {
          setPlace(null)
          setOpen((value) => !value)
        }}
        className={buttonClasses(variant, size, clsx(size === 'sm' ? 'w-7 px-0' : 'w-8 px-0'))}
      >
        <ButtonIcon icon={MoreHorizontal} size={size} />
      </button>
      {open && (
        <div
          ref={panel}
          id={menuId}
          role="menu"
          aria-label={label}
          style={place ?? { visibility: 'hidden' }}
          className="border-rule bg-surface rounded-card fixed z-(--z-popover) min-w-48 border py-1 shadow-md"
        >
          {ordered.map((action, index) => (
            <div key={action.key}>
              {index === firstDanger && index > 0 && (
                <div role="separator" className="border-rule my-1 border-t" />
              )}
              <button
                type="button"
                role="menuitem"
                disabled={action.disabled}
                onClick={() => {
                  setOpen(false)
                  action.onSelect()
                }}
                className={clsx(
                  'flex h-8 w-full items-center gap-2 px-3 text-left text-sm whitespace-nowrap',
                  'hover:bg-surface-2 focus-visible:bg-surface-2 disabled:pointer-events-none disabled:opacity-45',
                  action.tone === 'danger' ? 'text-danger' : 'text-ink',
                )}
              >
                <ButtonIcon icon={action.icon} size="md" />
                {action.label}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** The right-hand end of a row: a few actions, aligned, never wrapping into
    the value. */
export function RowActions({ children }: { children: ReactNode }) {
  return <div className="-mr-1 flex shrink-0 items-center gap-1">{children}</div>
}

/** The way back to the list a detail page belongs to. A drawn arrow, not a
    "←" glyph, and one shape for the six pages that had each typed their own. */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="text-ink-3 hover:text-ink inline-flex items-center gap-1 text-xs transition-colors duration-(--dur-instant)"
    >
      <ArrowLeft aria-hidden size={14} strokeWidth={1.75} />
      {children}
    </Link>
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
        {backTo && <BackLink to={backTo}>{backLabel}</BackLink>}
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
    <nav role="tablist" className="border-rule flex gap-1 overflow-x-auto border-b">
      {tabs.map((entry) => (
        <button
          key={entry}
          type="button"
          role="tab"
          aria-selected={entry === active}
          onClick={() => onSelect(entry)}
          className={
            entry === active
              ? 'border-accent text-ink -mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium'
              : 'text-ink-3 hover:text-ink -mb-px shrink-0 border-b-2 border-transparent px-3 py-2 text-sm'
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

/** A file to upload, dressed as a control rather than as a browser default.

    The native file input draws a small grey button and the words "no file
    chosen" beside it. In a form of bordered inputs that reads as a disabled
    row, and it was missed: the certificate fields of a variant looked like
    text that happened to be there. The input itself stays, hidden inside the
    label, so the picker opens on a click anywhere in the box and the keyboard
    still reaches it. */
export function FilePicker({
  label,
  hint,
  error,
  fileName,
  prompt,
  onSelect,
}: {
  label: string
  hint?: string
  error?: string
  /** The file chosen in this form, shown in place of the prompt. */
  fileName?: string
  /** What the empty box says - "Choose file", in the caller's wording. */
  prompt: string
  onSelect: (file: File) => void
}) {
  // Two labels for one control: the field name carries the accessible name,
  // the box is what the pointer aims at. The input itself comes before the
  // box so its focus ring can be drawn on the box through "peer".
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-ink text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        type="file"
        className="peer sr-only"
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) onSelect(file)
        }}
      />
      <label
        htmlFor={id}
        className={clsx(
          'rounded-control border-rule-2 bg-surface text-ink flex cursor-pointer items-center gap-2 border px-2.5 py-1.5 text-sm',
          'transition-colors duration-(--dur-instant) ease-(--ease-out)',
          'hover:bg-surface-2 peer-focus-visible:border-accent',
        )}
      >
        <Upload
          aria-hidden
          size={16}
          strokeWidth={1.75}
          className="text-ink-2 shrink-0"
        />
        <span className={clsx('truncate', !fileName && 'text-ink-2')}>
          {fileName ?? prompt}
        </span>
      </label>
      {error ? (
        <span className="text-danger text-xs">{error}</span>
      ) : hint ? (
        <span className="text-ink-3 text-xs">{hint}</span>
      ) : null}
    </div>
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
        'rounded-card flex flex-col items-start gap-3 border px-4 py-3 sm:flex-row sm:justify-between sm:gap-4',
        TONE_CLASSES[tone],
      )}
      role="status"
    >
      <div className="min-w-0">
        {title && <p className="text-sm font-semibold normal-case">{title}</p>}
        <div className="text-sm normal-case">{children}</div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
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

/**
 * A key-value row. The workhorse of every detail page.
 *
 * Label, value, and - optionally - the row's actions at the right edge, so an
 * action belongs visibly to its row instead of floating next to the value.
 * Value and actions share one flex line that wraps by the room the row
 * actually has, not by the viewport: the same row sits in a full-width card
 * and in half of a two-column grid, and only the card knows which. Every row
 * has the height of a row with buttons, which keeps a card even whether a
 * row has actions or not.
 */
export function DetailRow({
  label,
  children,
  actions,
}: {
  label: string
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    // On a phone the label goes above its value: beside it, a label column
    // leaves the value and its actions too little room to stay on one line.
    <div className="border-rule grid min-h-10 grid-cols-1 items-center gap-x-4 gap-y-0.5 border-b py-1.5 last:border-0 sm:grid-cols-[minmax(6.5rem,11rem)_minmax(0,1fr)]">
      <dt className="text-ink-3 text-xs sm:text-sm">{label}</dt>
      <dd className="text-ink min-w-0 text-sm">
        {actions ? (
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <div className="min-w-32 flex-1">{children}</div>
            {actions}
          </div>
        ) : (
          children
        )}
      </dd>
    </div>
  )
}
