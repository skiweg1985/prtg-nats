import { QueryClient } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'

import type { SensorTimeline, SensorTimelineState } from '@/api/types'
import { AppProviders } from '@/app/providers'
import { SensorTimelineRows } from '@/features/sensorLogs/SensorTimeline'
import { foldFailures } from '@/features/probes/ProbeSensorPage'
import { changeLanguage } from '@/i18n'

/**
 * The timeline on a sensor's page: one bar per network on a shared axis,
 * with the figures that say at a glance whether the network is fine.
 */

function buckets(states: SensorTimelineState[]) {
  return states.map((state, index) => ({
    start: new Date(Date.UTC(2025, 2, 8, 0, index * 15)).toISOString(),
    ok: state === 'none' ? 0 : 4,
    error: state === 'failing' || state === 'outlier' ? 1 : 0,
    state,
    code:
      state === 'failing' || state === 'outlier' ? 'gateway-unreachable' : null,
  }))
}

const TIMELINE: SensorTimeline = {
  probe: 'mpp-berlin-01',
  sensor: 'wlan-auth',
  start: '2025-03-08T00:00:00Z',
  until: '2025-03-08T01:00:00Z',
  bucket_seconds: 900,
  retained_since: '2025-02-23T00:00:00Z',
  streams: [
    {
      sensor: 'wlan-auth',
      ssid: 'Example-Corp',
      ok: 98,
      error: 2,
      success_rate: 0.98,
      runs_per_hour: 52.4,
      last_failure: {
        id: 'r1',
        probe: 'mpp-berlin-01',
        sensor: 'wlan-auth',
        job: null,
        time: '2025-03-08T00:32:00Z',
        result: 'error',
        code: 'gateway-unreachable',
        summary: 'gateway no reply',
        ssid: 'Example-Corp',
      },
      buckets: buckets(['ok', 'outlier', 'failing', 'none']),
    },
    {
      sensor: 'wlan-auth',
      ssid: 'Example-Guest',
      ok: 40,
      error: 0,
      success_rate: 1,
      runs_per_hour: 50,
      last_failure: null,
      buckets: buckets(['ok', 'ok', 'ok', 'ok']),
    },
  ],
}

function Where() {
  const location = useLocation()
  return <p data-testid="where">{location.pathname + location.search}</p>
}

function show(timeline: SensorTimeline) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<SensorTimelineRows timeline={timeline} />} />
          <Route path="/sensor-logs" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('sensor timeline', () => {
  beforeEach(async () => {
    await changeLanguage('en')
  })

  it('draws one bar per network with its figures', () => {
    show(TIMELINE)
    const corp = screen.getByRole('group', {
      name: /Example-Corp: 98 OK, 2 failed/,
    })
    expect(corp.children).toHaveLength(4)
    expect(
      screen.getByRole('group', { name: /Example-Guest/ }),
    ).toBeInTheDocument()
    expect(screen.getByText('98%')).toBeInTheDocument()
    expect(screen.getByText('52 sign-ins/h')).toBeInTheDocument()
    expect(
      screen.getByText(/last failure .* gateway-unreachable/),
    ).toBeInTheDocument()
    expect(screen.getByText('no failure')).toBeInTheDocument()
  })

  it('marks a single failure quietly and a lasting one in red', () => {
    show(TIMELINE)
    const [ok, outlier, failing, none] = Array.from(
      screen.getByRole('group', { name: /Example-Corp/ }).children,
    )
    expect(ok).toHaveClass('bg-ok/70')
    expect(outlier).toHaveClass('bg-ok/70')
    expect(outlier.querySelector('.bg-danger')).not.toBeNull()
    expect(failing).toHaveClass('bg-danger')
    expect(none).toHaveClass('bg-surface-2')
  })

  it('counts runs rather than sign-ins for other sensors', () => {
    show({
      ...TIMELINE,
      sensor: 'aruba-uplink',
      streams: [{ ...TIMELINE.streams[1], sensor: 'aruba-uplink', ssid: null }],
    })
    expect(screen.getByText('50 runs/h')).toBeInTheDocument()
    expect(
      screen.getByRole('group', { name: /aruba-uplink/ }),
    ).toBeInTheDocument()
  })

  it('names the slice under the pointer and opens its runs on a click', () => {
    show(TIMELINE)
    const [, , failing] = Array.from(
      screen.getByRole('group', { name: /Example-Corp/ }).children,
    )
    fireEvent.mouseEnter(failing)
    const info = screen.getByRole('status')
    expect(info).toHaveTextContent('5 sign-ins · 1 failed')
    expect(info).toHaveTextContent('gateway-unreachable')
    fireEvent.click(failing)
    const where = new URL(screen.getByTestId('where').textContent ?? '', 'http://x')
    expect(where.pathname).toBe('/sensor-logs')
    expect(Object.fromEntries(where.searchParams)).toEqual({
      probe: 'mpp-berlin-01',
      sensor: 'wlan-auth',
      ssid: 'Example-Corp',
      since: '2025-03-08T00:30:00.000Z',
      until: '2025-03-08T00:45:00.000Z',
    })
  })

  it('moves along the bar with the arrow keys', () => {
    show(TIMELINE)
    const bar = screen.getByRole('group', { name: /Example-Corp/ })
    fireEvent.focus(bar)
    fireEvent.keyDown(bar, { key: 'Home' })
    expect(screen.getByRole('status')).toHaveTextContent('4 sign-ins')
    fireEvent.keyDown(bar, { key: 'End' })
    expect(screen.getByRole('status')).toHaveTextContent('No run')
  })

  it('tells slices the retention removed from slices nobody measured', () => {
    show({
      ...TIMELINE,
      retained_since: '2025-03-08T00:30:00Z',
      streams: [{ ...TIMELINE.streams[1], buckets: buckets(['none', 'none', 'ok', 'none']) }],
    })
    expect(screen.getByText('Outside retention')).toBeInTheDocument()
    expect(screen.getByText(/Runs before .* are no longer kept/)).toBeInTheDocument()
    const [first, , , last] = Array.from(
      screen.getByRole('group', { name: /Example-Guest/ }).children,
    )
    expect(first.className).toContain('repeating-linear-gradient')
    expect(last).toHaveClass('bg-surface-2')
  })
})

describe('failure list', () => {
  it('folds failures of one network with the same code into one line', () => {
    const run = (time: string, code: string, ssid = 'corp') => ({
      id: time,
      probe: 'p',
      sensor: 'wlan-auth',
      job: null,
      time,
      result: 'error' as const,
      code,
      summary: code,
      ssid,
    })
    const groups = foldFailures([
      run('2025-03-08T06:32:00Z', 'gateway'),
      run('2025-03-08T05:16:00Z', 'gateway'),
      run('2025-03-08T03:00:00Z', 'gateway'),
      run('2025-03-08T04:00:00Z', 'dhcp'),
      run('2025-03-08T02:00:00Z', 'gateway', 'guest'),
    ])
    expect(groups.map((group) => [group.ssid, group.code, group.count])).toEqual([
      ['corp', 'gateway', 3],
      ['corp', 'dhcp', 1],
      ['guest', 'gateway', 1],
    ])
    expect(groups[0].first).toBe('2025-03-08T03:00:00Z')
    expect(groups[0].last).toBe('2025-03-08T06:32:00Z')
  })
})
