import clsx from 'clsx'
import { Info } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type FocusEvent as ReactFocusEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'

/**
 * A small floating panel for the explanation a control does not need on
 * screen all the time.
 *
 * It replaces the hint paragraph that used to sit under 33 form fields and the
 * stacked tool details in the probe's sensor table. A `title` tooltip was not
 * an option: it never opens on focus or on a tap, so the keyboard and the
 * phone - both everyday here - would lose the text altogether.
 *
 * Opening: hover with a short delay (mouse only), keyboard focus, or a click
 * or tap, which also pins it open. Closing: Escape, a press outside, leaving
 * with the mouse while not pinned, or the focus moving on. On a narrow screen
 * it does not float at all; the caller renders `inline` in the flow below the
 * thing it explains, because a floating panel on a phone covers the very
 * field the person is filling in.
 */

const OPEN_DELAY_MS = 250
const CLOSE_DELAY_MS = 120
const GAP_PX = 6
const EDGE_PX = 8

// Below Tailwind's sm breakpoint: where form grids collapse to one column.
const NARROW_QUERY = '(max-width: 639px)'

function subscribeNarrow(onChange: () => void) {
  const query = window.matchMedia(NARROW_QUERY)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

function useIsNarrow() {
  return useSyncExternalStore(
    subscribeNarrow,
    () => window.matchMedia(NARROW_QUERY).matches,
    () => false,
  )
}

export interface PopoverTriggerProps {
  ref: RefObject<HTMLButtonElement | null>
  type: 'button'
  'aria-expanded': boolean
  'aria-controls': string | undefined
  onPointerEnter: (event: ReactPointerEvent) => void
  onPointerLeave: (event: ReactPointerEvent) => void
  onPointerDown: () => void
  onFocus: () => void
  onBlur: (event: ReactFocusEvent) => void
  onClick: (event: ReactMouseEvent) => void
}

export interface Popover {
  open: boolean
  /** Spread onto the button that opens the panel. */
  triggerProps: PopoverTriggerProps
  /** The floating panel, portalled to the body; null on a narrow screen. */
  floating: ReactNode
  /** The same content in the flow, for a narrow screen; null elsewhere. */
  inline: ReactNode
}

export function usePopover({
  content,
  className,
}: {
  content: ReactNode
  /** Extra classes for the panel, e.g. a wider max-width for a hover card. */
  className?: string
}): Popover {
  const [open, setOpen] = useState(false)
  // Opened by a click or tap rather than by passing over it: it then stays
  // until it is closed on purpose.
  const [pinned, setPinned] = useState(false)
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const timer = useRef<number | undefined>(undefined)
  // Set between a press and the focus it causes, so a click does not open on
  // focus and then close again on the click that follows.
  const pressing = useRef(false)
  const narrow = useIsNarrow()
  const id = useId()

  const clear = useCallback(() => {
    window.clearTimeout(timer.current)
    timer.current = undefined
  }, [])

  const hide = useCallback(() => {
    clear()
    setOpen(false)
    setPinned(false)
  }, [clear])

  useEffect(() => clear, [clear])

  // Below the trigger, start edges aligned; above it when the viewport ends
  // first. Measured before paint so the panel never flashes in the wrong spot.
  const measure = useCallback(() => {
    if (!trigger.current || !panel.current) return
    const anchor = trigger.current.getBoundingClientRect()
    const width = panel.current.offsetWidth
    const height = panel.current.offsetHeight
    const below = anchor.bottom + GAP_PX
    const top =
      below + height > window.innerHeight - EDGE_PX &&
      anchor.top - height - GAP_PX > EDGE_PX
        ? anchor.top - height - GAP_PX
        : below
    const left = Math.max(
      EDGE_PX,
      Math.min(anchor.left - EDGE_PX, window.innerWidth - width - EDGE_PX),
    )
    setPlace({ top, left })
  }, [])

  useLayoutEffect(() => {
    if (open && !narrow) measure()
    else setPlace(null)
  }, [open, narrow, measure])

  useEffect(() => {
    if (!open) return
    // Capture on the window: an open dialog listens for Escape on the
    // document, and the first Escape should close the panel, not the dialog.
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      if (panel.current?.contains(document.activeElement)) trigger.current?.focus()
      hide()
    }
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node
      if (trigger.current?.contains(target) || panel.current?.contains(target)) return
      hide()
    }
    window.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('scroll', measure, true)
    window.addEventListener('resize', measure)
    return () => {
      window.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('scroll', measure, true)
      window.removeEventListener('resize', measure)
    }
  }, [open, hide, measure])

  function leaveLater() {
    if (pinned) return
    clear()
    timer.current = window.setTimeout(() => setOpen(false), CLOSE_DELAY_MS)
  }

  function onPanelPointer(event: ReactPointerEvent, entering: boolean) {
    if (event.pointerType !== 'mouse') return
    if (entering) clear()
    else leaveLater()
  }

  function onBlurOut(event: ReactFocusEvent) {
    const next = event.relatedTarget as Node | null
    // A press on the panel focuses the panel itself (tabIndex -1), so reading
    // the text with the pointer does not count as leaving.
    if (next && (trigger.current?.contains(next) || panel.current?.contains(next))) return
    hide()
  }

  const triggerProps: PopoverTriggerProps = {
    ref: trigger,
    type: 'button',
    'aria-expanded': open,
    'aria-controls': open ? id : undefined,
    onPointerEnter: (event) => {
      // Hover is a mouse thing; a touch also fires pointerenter, and opening
      // on it would make the tap that follows close the panel again.
      if (event.pointerType !== 'mouse' || narrow) return
      clear()
      timer.current = window.setTimeout(() => setOpen(true), OPEN_DELAY_MS)
    },
    onPointerLeave: (event) => {
      if (event.pointerType === 'mouse') leaveLater()
    },
    onPointerDown: () => {
      pressing.current = true
    },
    onFocus: () => {
      if (!pressing.current) setOpen(true)
      pressing.current = false
    },
    onBlur: onBlurOut,
    onClick: (event) => {
      // A popover inside a clickable table row opens itself, not the row.
      event.stopPropagation()
      pressing.current = false
      clear()
      if (open && pinned) {
        hide()
        return
      }
      setOpen(true)
      setPinned(true)
    },
  }

  const floating =
    open && !narrow
      ? createPortal(
          <div
            ref={panel}
            id={id}
            tabIndex={-1}
            onPointerEnter={(event) => onPanelPointer(event, true)}
            onPointerLeave={(event) => onPanelPointer(event, false)}
            onBlur={onBlurOut}
            // Portalled, but React bubbles through the component tree: a
            // click on the card would otherwise reach a clickable row.
            onClick={(event) => event.stopPropagation()}
            style={place ?? { visibility: 'hidden', top: 0, left: 0 }}
            className={clsx(
              'border-rule bg-surface rounded-card text-ink-2 fixed z-(--z-popover) max-w-xs border px-3 py-2.5 text-sm shadow-md',
              'outline-none',
              className,
            )}
          >
            {content}
          </div>,
          document.body,
        )
      : null

  const inline =
    open && narrow ? (
      <div
        ref={panel}
        id={id}
        tabIndex={-1}
        onBlur={onBlurOut}
        onClick={(event) => event.stopPropagation()}
        className="bg-surface-2 rounded-control text-ink-2 px-3 py-2 text-sm outline-none"
      >
        {content}
      </div>
    ) : null

  return { open, triggerProps, floating, inline }
}

/**
 * The (i) beside a field label. A real button: it has a name, the Tab key
 * stops on it, and its hit area is larger than the glyph so a thumb finds it.
 */
export function InfoButton({
  label,
  popover,
}: {
  /** The accessible name - "Help for Network name". */
  label: string
  popover: Popover
}) {
  return (
    <>
      <button
        {...popover.triggerProps}
        aria-label={label}
        className={clsx(
          'text-ink-3 hover:text-ink rounded-inset relative inline-flex size-5 shrink-0 items-center justify-center',
          'transition-colors duration-(--dur-instant) ease-(--ease-out)',
          // 44px of target around a 14px glyph, without moving the label.
          "after:absolute after:-inset-3 after:content-['']",
          popover.open && 'text-ink',
        )}
      >
        <Info aria-hidden size={14} strokeWidth={1.75} />
      </button>
      {popover.floating}
    </>
  )
}
