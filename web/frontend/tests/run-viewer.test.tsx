import { QueryClient } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import type { SensorLogRecord } from '@/api/types'
import { AppProviders } from '@/app/providers'
import { ProbeSensorPage } from '@/features/probes/ProbeSensorPage'
import { phasesOf } from '@/features/sensorLogs/phases'
import { changeLanguage } from '@/i18n'

/**
 * The run viewer on a sensor's page: a click into the timeline opens the run
 * in a dialog, with its stages and the runs before and after it. And hiding
 * a row of the timeline, with the configuration on the probe if asked.
 */

function record(
  id: string,
  result: 'ok' | 'error',
  overrides: Partial<SensorLogRecord> = {},
): SensorLogRecord {
  const failed = result === 'error'
  return {
    id,
    probe: 'mpp-berlin-01',
    sensor: 'wlan-auth',
    job: 'a1b2c3d4e5f6',
    time: '2026-11-08T00:20:00Z',
    result,
    code: failed ? 'auth-failed-eap' : 'ok',
    summary: failed ? 'EAP authentication failed' : 'OK',
    ssid: 'Example-Corp',
    body: {
      ssid: 'Example-Corp',
      auth: 'peap',
      stage: 'dhcp',
      bssid: '02:00:00:00:00:01',
      identity: 'probe@example.test',
      ...(failed ? {} : { ip_address: '192.0.2.40', gateway: '192.0.2.1' }),
      timings: failed
        ? { assoc_ms: 120, total_ms: 9000 }
        : { assoc_ms: 110, auth_ms: 800, dhcp_ms: 1500, gateway_ms: 4, total_ms: 2500 },
      supplicant: ['CTRL-EVENT-EAP-FAILURE EAP authentication failed'],
    },
    ...overrides,
  }
}

describe('stages of a run', () => {
  it('marks every stage of a passed run as passed, with its time', () => {
    expect(phasesOf(record('r', 'ok'))).toEqual([
      { key: 'assoc', state: 'ok', ms: 110 },
      { key: 'auth', state: 'ok', ms: 800 },
      { key: 'dhcp', state: 'ok', ms: 1500 },
      { key: 'gateway', state: 'ok', ms: 4 },
    ])
  })

  it('places a failure at the stage its code names and skips the rest', () => {
    expect(phasesOf(record('r', 'error'))).toEqual([
      { key: 'assoc', state: 'ok', ms: 120 },
      { key: 'auth', state: 'failed', ms: null },
      { key: 'dhcp', state: 'skipped', ms: null },
    ])
  })

  it('places a code without a stage at the first stage without a time', () => {
    const lost = record('r', 'error', { code: 'assoc-lost' })
    lost.body = { ...lost.body, timings: { assoc_ms: 100, auth_ms: 700 } }
    expect(phasesOf(lost).map((phase) => [phase.key, phase.state])).toEqual([
      ['assoc', 'ok'],
      ['auth', 'ok'],
      ['dhcp', 'failed'],
    ])
  })

  it('puts a run that never reached the radio into the preparation', () => {
    const busy = record('r', 'error', { code: 'iface-busy' })
    busy.body = { ...busy.body, timings: {} }
    expect(phasesOf(busy).map((phase) => [phase.key, phase.state])).toEqual([
      ['prepare', 'failed'],
      ['assoc', 'skipped'],
      ['auth', 'skipped'],
      ['dhcp', 'skipped'],
    ])
  })

  it('has no stages for a sensor it knows none for', () => {
    expect(phasesOf(record('r', 'ok', { sensor: 'aruba-uplink' }))).toEqual([])
  })
})

// --- On the sensor page -------------------------------------------------------

const RUNS: Record<string, SensorLogRecord> = {
  r1: record('r1', 'error', { time: '2026-11-08T00:05:00Z' }),
  r2: record('r2', 'ok', { time: '2026-11-08T00:20:00Z' }),
  r3: record('r3', 'error', { time: '2026-11-08T00:35:00Z' }),
}
const ORDER = ['r1', 'r2', 'r3']

const requests: { neighbours: string[]; hidden: unknown[]; removed: string[] } = {
  neighbours: [],
  hidden: [],
  removed: [],
}

function bucket(minute: number, state: 'ok' | 'failing' | 'none') {
  return {
    start: new Date(Date.UTC(2026, 10, 8, 0, minute)).toISOString(),
    ok: state === 'ok' ? 1 : 0,
    error: state === 'failing' ? 1 : 0,
    state,
    code: state === 'failing' ? 'auth-failed-eap' : null,
  }
}

function stream(ssid: string, lastJob: string | null) {
  return {
    sensor: 'wlan-auth',
    stream: ssid,
    ssid,
    ok: 1,
    error: 2,
    success_rate: 1 / 3,
    runs_per_hour: 4,
    last_failure: null,
    buckets: [bucket(0, 'failing'), bucket(15, 'ok'), bucket(30, 'failing'), bucket(45, 'none')],
    last_run: '2026-11-08T00:35:00Z',
    ended: false,
    last_job: lastJob,
  }
}

const server = setupServer(
  http.get('/api/v1/auth/state', () =>
    HttpResponse.json({
      authenticated: true,
      setup_required: false,
      dev_auth: false,
      principal: {
        user_id: 'U1',
        username: 'olga',
        display_name: 'Olga',
        roles: ['operator'],
        permissions: ['probe.read', 'sensor.read', 'sensor.configure', 'sensor_log.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes/P1', () =>
    HttpResponse.json({
      summary: {
        id: 'P1',
        nats_username: 'mpp-berlin-01',
        display_name: 'Berlin',
        host: '198.51.100.29',
        probe_name: 'berlin-01',
        status: 'healthy',
      },
      sensors: [
        {
          name: 'wlan-auth',
          status: 'current',
          desired_version: '19',
          installed_version: '19',
          tool_name: null,
          installed_tool_version: null,
          interfaces: ['wlan0'],
          helper_state: 'listening',
        },
      ],
    }),
  ),
  http.get('/api/v1/probes/P1/prtg-name', () => HttpResponse.json({ state: 'unlinked' })),
  http.get('/api/v1/sensor-logs/timeline', () =>
    HttpResponse.json({
      probe: 'mpp-berlin-01',
      sensor: 'wlan-auth',
      start: '2026-11-08T00:00:00Z',
      until: '2026-11-08T01:00:00Z',
      bucket_seconds: 900,
      retained_since: '2026-10-27T00:00:00Z',
      streams: [stream('Example-Corp', 'a1b2c3d4e5f6'), stream('Example-Guest', 'bbbbbbbbbbbb')],
    }),
  ),
  http.get('/api/v1/sensor-logs', () =>
    HttpResponse.json({ items: [], total: 0, limit: 500, offset: 0 }),
  ),
  http.get('/api/v1/sensor-logs/timeline/run', ({ request }) => {
    const since = new URL(request.url).searchParams.get('since')
    return HttpResponse.json(since === '2026-11-08T00:30:00.000Z' ? RUNS.r3 : RUNS.r1)
  }),
  http.get('/api/v1/sensor-logs/:id/neighbours', ({ params, request }) => {
    const id = String(params.id)
    const failuresOnly = new URL(request.url).searchParams.get('failures_only') === 'true'
    requests.neighbours.push(`${id}:${failuresOnly}`)
    const pool = ORDER.filter((key) => !failuresOnly || RUNS[key].result === 'error')
    const at = ORDER.indexOf(id)
    const older = pool.filter((key) => ORDER.indexOf(key) < at)
    const newer = pool.filter((key) => ORDER.indexOf(key) > at)
    return HttpResponse.json({
      record: RUNS[id],
      previous: older.length ? RUNS[older[older.length - 1]] : null,
      next: newer.length ? RUNS[newer[0]] : null,
    })
  }),
  http.get('/api/v1/sensor-logs/:id', ({ params }) => HttpResponse.json(RUNS[String(params.id)])),
  http.get('/api/v1/probes/P1/sensors/wlan-auth/jobs', () =>
    HttpResponse.json([
      {
        label: 'bbbbbbbbbbbb',
        interface: 'wlan0',
        ssid: 'Example-Guest',
        stage: 'dhcp',
        result: 'failed',
        code: 'auth-failed-psk',
        age: 30,
        asked_every: 60,
        asked_ago: 10,
        interval: 60,
        idle: false,
        expires_in: 3500,
        sensor_id: '2042',
      },
    ]),
  ),
  http.post('/api/v1/sensor-logs/hidden-streams', async ({ request }) => {
    requests.hidden.push(await request.json())
    return HttpResponse.json({
      probe: 'mpp-berlin-01',
      sensor: 'wlan-auth',
      stream: 'Example-Guest',
      hidden_at: '2026-11-08T01:00:00Z',
    })
  }),
  http.delete('/api/v1/probes/P1/sensors/wlan-auth/jobs/:label', ({ params }) => {
    requests.removed.push(String(params.label))
    return new HttpResponse(null, { status: 204 })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
beforeEach(async () => {
  await changeLanguage('en')
  requests.neighbours.length = 0
  requests.hidden.length = 0
  requests.removed.length = 0
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function Where() {
  const location = useLocation()
  return <p data-testid="where">{location.search}</p>
}

function show(entry = '/probes/P1/sensors/wlan-auth') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/probes/:probeId/sensors/:sensorName"
            element={
              <>
                <ProbeSensorPage />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

function search(): URLSearchParams {
  return new URLSearchParams(screen.getByTestId('where').textContent ?? '')
}

describe('run viewer', () => {
  it('opens the first failure of a slice from a click into the timeline', async () => {
    show()
    const bar = await screen.findByRole('group', { name: /Example-Corp/ })
    fireEvent.click(bar.children[2])
    const dialog = await screen.findByRole('dialog', { name: 'wlan-auth · Example-Corp' })
    await waitFor(() => expect(search().get('run')).toBe('r3'))
    expect(dialog).toHaveTextContent('EAP authentication failed')
  })

  it('shows the stages with the failed one open and the raw output closed', async () => {
    show('/probes/P1/sensors/wlan-auth?run=r1')
    const dialog = await screen.findByRole('dialog')
    const stages = await within(dialog).findByRole('list', { name: 'Stages of the run' })
    const [assoc, auth, dhcp] = within(stages).getAllByRole('listitem')
    expect(assoc).toHaveTextContent('Association')
    expect(assoc).toHaveTextContent('120 ms')
    expect(auth).toHaveTextContent('Authentication (EAP)')
    expect(within(auth).getByRole('button')).toHaveAttribute('aria-expanded', 'true')
    expect(auth).toHaveTextContent('probe@example.test')
    expect(dhcp).toHaveTextContent('Not reached')

    const raw = within(dialog).getByRole('button', { name: 'Raw output' })
    expect(raw).toHaveAttribute('aria-expanded', 'false')
    expect(dialog).not.toHaveTextContent('CTRL-EVENT-EAP-FAILURE')
    await userEvent.click(raw)
    expect(dialog).toHaveTextContent('CTRL-EVENT-EAP-FAILURE')
  })

  it('steps to the next run with the arrow key and says where the history ends', async () => {
    show('/probes/P1/sensors/wlan-auth?run=r2')
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Next')).toBeInTheDocument()
    await waitFor(() => expect(requests.neighbours).toContain('r2:false'))
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    await waitFor(() => expect(search().get('run')).toBe('r3'))
    expect(await within(dialog).findByText('No newer run')).toBeInTheDocument()
    // At the end the key does nothing.
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(search().get('run')).toBe('r3')

    await userEvent.click(within(dialog).getByRole('button', { name: 'Previous' }))
    await waitFor(() => expect(search().get('run')).toBe('r2'))
  })

  it('skips passed runs with failures only, and keeps that in the address', async () => {
    show('/probes/P1/sensors/wlan-auth?run=r1')
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('checkbox', { name: 'Failures only' }))
    await waitFor(() => expect(search().get('failures')).toBe('1'))
    await waitFor(() => expect(requests.neighbours).toContain('r1:true'))
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    await waitFor(() => expect(search().get('run')).toBe('r3'))
  })

  it('closes on Escape and leaves the address as it was', async () => {
    show('/probes/P1/sensors/wlan-auth?window=7d&run=r1&failures=1')
    await screen.findByRole('dialog')
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(search().toString()).toBe('window=7d')
  })
})

describe('hiding a row', () => {
  it('hides the row and removes its configuration from the probe, preselected', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Hide Example-Guest' }))
    const dialog = await screen.findByRole('dialog', { name: 'Hide Example-Guest?' })
    const removeToo = await within(dialog).findByRole('checkbox', {
      name: /Also remove the configuration from the probe/,
    })
    expect(removeToo).toBeChecked()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Hide' }))
    await waitFor(() => expect(requests.removed).toEqual(['bbbbbbbbbbbb']))
    expect(requests.hidden).toEqual([
      { probe: 'mpp-berlin-01', sensor: 'wlan-auth', stream: 'Example-Guest' },
    ])
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('leaves the probe alone when the box is cleared', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Hide Example-Guest' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(await within(dialog).findByRole('checkbox'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Hide' }))
    await waitFor(() => expect(requests.hidden).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(requests.removed).toEqual([])
  })

  it('offers no removal when the configuration is no longer on the probe', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Hide Example-Corp' }))
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument())
    expect(dialog).toHaveTextContent('The runs stay in the sensor log.')
  })
})
