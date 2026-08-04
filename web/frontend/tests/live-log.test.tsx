import { QueryClient } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { LiveLog } from '@/components/ui/JobProgress'
import type { JobEvent } from '@/api/types'

/**
 * The live log has two sources and they overlap.
 *
 * The stored log is refetched on window focus and after every job mutation,
 * while the event stream keeps running. Replacing the list with the refetched
 * answer drops whatever arrived after the server built it - and the stream
 * never sends those lines again, because it has moved past their sequence.
 */

class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  readonly listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>()
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
  })

  afterEach(() => {
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

    expect(screen.queryByText(/jobs.disconnected/)).toBeNull()
    expect(source?.closed).toBe(true)
  })
})
