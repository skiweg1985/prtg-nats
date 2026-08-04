import { QueryClient } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { LiveLog } from '@/components/ui/JobProgress'
import type { JobEvent } from '@/api/types'
// The connection states are translated, so the test asks the table what they
// say rather than repeating the prose and going stale with it. Event codes are
// not: an unknown code falls back to itself, which is why those are matched
// literally below.
import en from '@/i18n/locales/en.json'

/**
 * The live log has two sources and they overlap.
 *
 * The stored log is refetched on window focus and after every job mutation,
 * while the event stream keeps running. Replacing the list with the refetched
 * answer drops whatever arrived after the server built it - and the stream
 * never sends those lines again, because it has moved past their sequence.
 *
 * The second half is what happens when the stream itself goes: a proxy
 * timeout, a laptop waking up, a blip. The job carries on, so the log has to
 * pick the stream up again from the line it holds rather than sit there saying
 * it is disconnected.
 */

class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  readonly listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>()
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, handler: (event: MessageEvent<string>) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), handler])
  }

  close(): void {
    this.closed = true
  }

  emit(type: string, payload: unknown): void {
    for (const handler of this.listeners.get(type) ?? []) {
      handler(new MessageEvent(type, { data: JSON.stringify(payload) }))
    }
  }
}

/** The delays the component walks through, plus one attempt past the last. */
const RECONNECT_DELAYS_MS = [1_000, 2_000, 5_000, 10_000, 30_000]

function latest(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1)
  if (!source) throw new Error('no stream was opened')
  return source
}

/** Drop the current stream and let the backoff run out. */
function drop(): void {
  act(() => {
    latest().onerror?.()
  })
  act(() => {
    vi.advanceTimersByTime(RECONNECT_DELAYS_MS.at(-1)!)
  })
}

function event(sequence: number, code: string): JobEvent {
  return {
    id: `e${sequence}`,
    sequence,
    ts: '2026-08-04T10:00:00Z',
    level: 'info',
    step: null,
    target: null,
    code,
    params: {},
    raw: null,
  } as JobEvent
}

function wrap(children: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </AppProviders>,
  )
}

describe('LiveLog', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    FakeEventSource.instances = []
  })

  it('keeps a streamed line when the stored log is refetched behind it', () => {
    const stored = [event(1, 'jobs.first_line')]
    const { rerender } = wrap(
      <LiveLog jobId="job-1" initialEvents={stored} live />,
    )

    const source = FakeEventSource.instances.at(-1)
    expect(source).toBeDefined()
    act(() => source?.emit('job.event', event(2, 'jobs.streamed_line')))
    expect(screen.getByText(/jobs.streamed_line/)).toBeTruthy()

    // The refetch answers with what the database held when it was built -
    // the streamed line is not in it yet.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    rerender(
      <AppProviders client={client}>
        <MemoryRouter>
          <LiveLog jobId="job-1" initialEvents={[...stored]} live />
        </MemoryRouter>
      </AppProviders>,
    )

    expect(screen.getByText(/jobs.first_line/)).toBeTruthy()
    expect(screen.getByText(/jobs.streamed_line/)).toBeTruthy()
  })

  it('shows a line once, whichever source it arrives from', () => {
    const stored = [event(1, 'jobs.only_once')]
    wrap(<LiveLog jobId="job-1" initialEvents={stored} live />)

    const source = FakeEventSource.instances.at(-1)
    act(() => source?.emit('job.event', event(1, 'jobs.only_once')))

    expect(screen.getAllByText(/jobs.only_once/)).toHaveLength(1)
  })

  it('orders lines by sequence, not by arrival', () => {
    wrap(<LiveLog jobId="job-1" initialEvents={[event(3, 'jobs.third')]} live />)

    const source = FakeEventSource.instances.at(-1)
    act(() => source?.emit('job.event', event(1, 'jobs.first')))

    const rendered = screen.getAllByText(/jobs\.(first|third)/)
    expect(rendered.map((node) => node.textContent?.trim())).toEqual([
      'jobs.first',
      'jobs.third',
    ])
  })

  it('does not report a finished job as a dropped connection', () => {
    wrap(<LiveLog jobId="job-1" initialEvents={[]} live />)

    const source = FakeEventSource.instances.at(-1)
    act(() => source?.emit('end', {}))

    expect(screen.queryByText(en.jobs.disconnected)).toBeNull()
    expect(source?.closed).toBe(true)
  })

  it('picks the stream up again after a drop, from the line it holds', () => {
    wrap(<LiveLog jobId="job-1" initialEvents={[event(1, 'jobs.first_line')]} live />)

    const first = latest()
    act(() => first.emit('job.event', event(7, 'jobs.streamed_line')))

    act(() => first.onerror?.())
    expect(first.closed).toBe(true)
    expect(screen.getByText(en.jobs.reconnecting)).toBeTruthy()
    expect(screen.queryByText(en.jobs.disconnected)).toBeNull()

    act(() => vi.advanceTimersByTime(RECONNECT_DELAYS_MS[0]))

    expect(FakeEventSource.instances).toHaveLength(2)
    // Resumed from the streamed line, not from what the stored log held.
    expect(latest().url).toContain('after=7')

    act(() => latest().onopen?.())
    act(() => latest().emit('job.event', event(8, 'jobs.after_the_gap')))
    expect(screen.getByText(/jobs.first_line/)).toBeTruthy()
    expect(screen.getByText(/jobs.after_the_gap/)).toBeTruthy()
  })

  it('backs off further with every drop in a row', () => {
    wrap(<LiveLog jobId="job-1" initialEvents={[]} live />)

    for (const [index, delay] of RECONNECT_DELAYS_MS.entries()) {
      act(() => latest().onerror?.())
      // One tick short of the delay is still too early.
      act(() => vi.advanceTimersByTime(delay - 1))
      expect(FakeEventSource.instances).toHaveLength(index + 1)
      act(() => vi.advanceTimersByTime(1))
      expect(FakeEventSource.instances).toHaveLength(index + 2)
    }
  })

  it('starts the backoff over once a stream has held', () => {
    wrap(<LiveLog jobId="job-1" initialEvents={[]} live />)

    act(() => latest().onerror?.())
    act(() => vi.advanceTimersByTime(RECONNECT_DELAYS_MS[0]))

    // The second stream connects and stays up long enough to count.
    act(() => latest().onopen?.())
    act(() => vi.advanceTimersByTime(30_000))

    act(() => latest().onerror?.())
    act(() => vi.advanceTimersByTime(RECONNECT_DELAYS_MS[0]))
    expect(FakeEventSource.instances).toHaveLength(3)
  })

  it('gives up in the open, with a control to try again', () => {
    wrap(<LiveLog jobId="job-1" initialEvents={[]} live />)

    for (let attempt = 0; attempt <= RECONNECT_DELAYS_MS.length; attempt += 1) drop()

    const opened = FakeEventSource.instances.length
    expect(screen.getByText(en.jobs.disconnected)).toBeTruthy()
    expect(screen.queryByText(en.jobs.reconnecting)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: en.jobs.reconnect }))
    expect(FakeEventSource.instances).toHaveLength(opened + 1)
    expect(screen.queryByText(en.jobs.disconnected)).toBeNull()
  })

  it('tries again at once when the network comes back', () => {
    wrap(<LiveLog jobId="job-1" initialEvents={[]} live />)

    for (let attempt = 0; attempt <= RECONNECT_DELAYS_MS.length; attempt += 1) drop()
    const opened = FakeEventSource.instances.length

    act(() => {
      window.dispatchEvent(new Event('online'))
    })

    expect(FakeEventSource.instances).toHaveLength(opened + 1)
  })

  it('does not reconnect a job that has ended', () => {
    wrap(<LiveLog jobId="job-1" initialEvents={[]} live />)

    act(() => latest().emit('end', {}))
    act(() => vi.advanceTimersByTime(60_000))
    act(() => {
      window.dispatchEvent(new Event('online'))
    })

    expect(FakeEventSource.instances).toHaveLength(1)
  })
})
