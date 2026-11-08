import { QueryClient } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { SensorLogPage, SensorLogRecordPage } from '@/features/sensorLogs/SensorLogPages'
import { changeLanguage } from '@/i18n'
import { AppLayout } from '@/layouts/AppLayout'

/**
 * What the probes report over their return channel, read back.
 *
 * An operator looking into a red wlan-auth sensor wants the failed runs of the
 * last day first, the raw output one click away, and new runs appearing while
 * the network is being reconfigured - without reloading the page.
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

function record(overrides: Record<string, unknown> = {}) {
  return {
    id: '0b6f2a52-0000-4000-8000-000000000001',
    probe: 'mpp-berlin-01',
    sensor: 'wlan-auth',
    job: 'a1b2c3d4e5f6',
    time: '2026-11-08T09:00:00Z',
    result: 'error',
    code: 'dhcp_timeout',
    summary: 'no DHCP lease after 30 s',
    ssid: 'corp-wifi',
    ...overrides,
  }
}

let listRequests: URLSearchParams[] = []
let listItems: ReturnType<typeof record>[] = []

const server = setupServer(
  http.get('/api/v1/auth/state', () =>
    HttpResponse.json({
      authenticated: true,
      setup_required: false,
      dev_auth: false,
      principal: {
        user_id: 'U1',
        username: 'viewer',
        display_name: 'viewer',
        roles: ['viewer'],
        permissions: ['sensor_log.read', 'probe.read', 'sensor.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json([])),
  http.get('/api/v1/sensors', () => HttpResponse.json([])),
  http.get('/api/v1/sensor-logs', ({ request }) => {
    listRequests.push(new URL(request.url).searchParams)
    return HttpResponse.json({
      items: listItems,
      total: listItems.length,
      limit: 100,
      offset: 0,
    })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
beforeEach(async () => {
  await changeLanguage('en')
  FakeEventSource.instances = []
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource
})
afterEach(() => {
  server.resetHandlers()
  listRequests = []
  listItems = []
})
afterAll(() => server.close())

function wrap(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/sensor-logs" element={<SensorLogPage />} />
          <Route path="/sensor-logs/:recordId" element={<SensorLogRecordPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

function lastRequest(): URLSearchParams {
  const last = listRequests.at(-1)
  if (!last) throw new Error('the list was never requested')
  return last
}

describe('the sensor log page', () => {
  it('lists the runs of the last 24 hours across probes', async () => {
    listItems = [record()]
    wrap('/sensor-logs')

    const row = (await screen.findByText('no DHCP lease after 30 s')).closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('mpp-berlin-01')).toBeInTheDocument()
    expect(within(row as HTMLElement).getByText('corp-wifi')).toBeInTheDocument()

    const since = Date.parse(lastRequest().get('since') ?? '')
    const hours = (Date.now() - since) / 3_600_000
    expect(hours).toBeGreaterThan(23.9)
    expect(hours).toBeLessThan(24.1)
  })

  it('links the probe of a run to its page', async () => {
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([{ id: 'P1', nats_username: 'mpp-berlin-01' }]),
      ),
    )
    listItems = [record()]
    wrap('/sensor-logs')

    expect(await screen.findByRole('link', { name: 'mpp-berlin-01' })).toHaveAttribute(
      'href',
      '/probes/P1',
    )
  })

  it('names the probe the way the fleet list does, in the column and the filter', async () => {
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([
          {
            id: 'P1',
            nats_username: 'mpp-berlin-01',
            display_name: 'Berlin office',
            probe_name: 'berlin-01',
          },
        ]),
      ),
    )
    listItems = [record()]
    const user = userEvent.setup()
    wrap('/sensor-logs')

    expect(await screen.findByRole('link', { name: 'Berlin office' })).toHaveAttribute(
      'href',
      '/probes/P1',
    )
    // Chosen by name, filtered by the account the records carry.
    const filter = screen.getByRole('combobox', { name: 'Probe' })
    await screen.findByRole('option', { name: 'Berlin office' })
    await user.selectOptions(filter, 'Berlin office')
    await waitFor(() => expect(lastRequest().get('probe')).toBe('mpp-berlin-01'))
  })

  it('shows the runs as cards on a phone, without sideways scrolling', async () => {
    const original = window.matchMedia
    window.matchMedia = ((query: string) => ({
      matches: query.includes('max-width'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
    try {
      listItems = [record()]
      wrap('/sensor-logs')

      const summary = await screen.findByText('no DHCP lease after 30 s')
      expect(screen.queryByRole('table')).toBeNull()
      // One control for the run and none nested in it: the card opens the
      // run's own page, and the probe is a separate thing to reach.
      const card = summary.closest('a')
      expect(card).toHaveAttribute('href', '/sensor-logs/0b6f2a52-0000-4000-8000-000000000001')
      expect(within(card as HTMLElement).queryByText('mpp-berlin-01')).toBeNull()
      expect(screen.getByText('mpp-berlin-01')).toBeInTheDocument()
      expect(screen.getByText('corp-wifi')).toBeInTheDocument()
    } finally {
      window.matchMedia = original
    }
  })

  it('asks the server again for every filter that changes', async () => {
    const user = userEvent.setup()
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([{ id: 'P1', nats_username: 'mpp-berlin-01' }]),
      ),
      http.get('/api/v1/sensors', () => HttpResponse.json([{ name: 'wlan-auth' }])),
    )
    wrap('/sensor-logs')
    await screen.findByText('No runs in this period')

    await user.selectOptions(screen.getByLabelText('Result'), 'error')
    await screen.findByText('No runs match these filters')
    expect(lastRequest().get('result')).toBe('error')

    await user.selectOptions(screen.getByLabelText('Probe'), 'mpp-berlin-01')
    await user.selectOptions(screen.getByLabelText('Sensor'), 'wlan-auth')
    await user.type(screen.getByLabelText('Network (SSID)'), 'corp-wifi{Enter}')
    await user.selectOptions(screen.getByLabelText('Period'), '1h')
    await user.type(screen.getByLabelText('Search output'), 'EAP-TLS')

    await waitFor(() => expect(lastRequest().get('q')).toBe('EAP-TLS'))
    const last = lastRequest()
    expect(last.get('probe')).toBe('mpp-berlin-01')
    expect(last.get('sensor')).toBe('wlan-auth')
    expect(last.get('ssid')).toBe('corp-wifi')
    expect(last.get('result')).toBe('error')
    const hours = (Date.now() - Date.parse(last.get('since') ?? '')) / 3_600_000
    expect(hours).toBeLessThan(1.1)
  })

  it('takes its filters from the address', async () => {
    wrap('/sensor-logs?result=gap&ssid=guest&range=7d')
    await screen.findByText('No runs match these filters')

    expect(lastRequest().get('result')).toBe('gap')
    expect(lastRequest().get('ssid')).toBe('guest')
    expect(screen.getByLabelText('Period')).toHaveValue('7d')
  })

  it('opens a run onto its raw output', async () => {
    const user = userEvent.setup()
    listItems = [record()]
    server.use(
      http.get('/api/v1/sensor-logs/:id', () =>
        HttpResponse.json({
          ...record(),
          body: {
            ssid: 'corp-wifi',
            bssid: '02:00:00:00:00:01',
            timings: { assoc_ms: 41, auth_ms: 230 },
            supplicant: ['wlan0: CTRL-EVENT-EAP-STARTED', 'wlan0: CTRL-EVENT-EAP-FAILURE'],
          },
        }),
      ),
    )
    wrap('/sensor-logs')

    await screen.findByText('no DHCP lease after 30 s')
    expect(screen.queryByText(/CTRL-EVENT-EAP-FAILURE/)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Show details' }))

    expect(await screen.findByText(/CTRL-EVENT-EAP-FAILURE/)).toBeInTheDocument()
    expect(screen.getByText('02:00:00:00:00:01')).toBeInTheDocument()
    expect(screen.getByText('230 ms')).toBeInTheDocument()
  })

  it('adds a new run from the event stream without reloading', async () => {
    listItems = [record()]
    wrap('/sensor-logs?result=error')
    await screen.findByText('no DHCP lease after 30 s')
    const requests = listRequests.length

    const source = FakeEventSource.instances.at(-1)
    expect(source?.url).toBe('/api/v1/sensor-logs/events')
    act(() => {
      source?.emit(
        'sensor_log.record',
        record({
          id: 'new-error',
          time: new Date().toISOString(),
          summary: 'EAP failure from the RADIUS server',
        }),
      )
      // Filtered out: the page shows failures only.
      source?.emit(
        'sensor_log.record',
        record({
          id: 'new-ok',
          time: new Date().toISOString(),
          result: 'ok',
          summary: 'signed in after 1.2 s',
        }),
      )
    })

    expect(await screen.findByText('EAP failure from the RADIUS server')).toBeInTheDocument()
    expect(screen.queryByText('signed in after 1.2 s')).toBeNull()
    expect(listRequests.length).toBe(requests)
  })
})

describe('the sensor tab of a probe', () => {
  function probeDetail() {
    return {
      summary: {
        id: 'P1',
        nats_username: 'mpp-berlin-01',
        display_name: null,
        host: '198.51.100.29',
        probe_name: 'berlin-01',
        status: 'healthy',
        service: 'active',
        package_version: '3.10.0-1',
        ca_state: 'ok',
        nats_connection: 'connected',
        sensor_count: 1,
        deviation_count: 0,
        observed_at: null,
        stale: false,
        running_job_id: null,
        error_code: null,
        helper_version: 10,
        helper_outdated: false,
        prtg_registered: true,
        managed_over_tunnel: false,
      },
      inventory: {
        ssh_host: '198.51.100.29',
        ssh_port: 22,
        probe_id: '11111111-2222-3333-4444-555555555555',
        probe_name: 'berlin-01',
        access_key_present: true,
        pending_transaction: null,
        assigned_sensors: [],
        known_iperf_endpoints: [],
      },
      observed: null,
      sensors: [],
      deviations: [],
      notes: null,
      labels: {},
      prtg_registered_at: null,
      prtg_registered_by: null,
      management_route: null,
      nats_route: null,
    }
  }

  function wrapProbe() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <AppProviders client={client}>
        <MemoryRouter initialEntries={['/probes/P1?tab=sensors']}>
          <Routes>
            <Route path="/probes/:probeId" element={<ProbeDetailPage />} />
          </Routes>
        </MemoryRouter>
      </AppProviders>,
    )
  }

  it('lists the runs of each sensor and network on this probe', async () => {
    server.use(
      http.get('/api/v1/probes/P1', () => HttpResponse.json(probeDetail())),
      http.get('/api/v1/probes/P1/wireless-interfaces', () => HttpResponse.json([])),
    )
    listItems = [
      record({ id: 'a', ssid: 'corp-wifi', summary: 'corp failed' }),
      record({ id: 'b', ssid: 'guest', result: 'ok', summary: 'guest signed in' }),
      record({ id: 'c', ssid: 'corp-wifi', result: 'ok', summary: 'corp signed in' }),
    ]
    wrapProbe()

    const corp = (await screen.findByRole('heading', { name: 'wlan-auth · corp-wifi' }))
      .closest('section') as HTMLElement
    const guest = screen
      .getByRole('heading', { name: 'wlan-auth · guest' })
      .closest('section') as HTMLElement
    expect(within(corp).getByText('corp failed')).toBeInTheDocument()
    expect(within(corp).getByText('corp signed in')).toBeInTheDocument()
    expect(within(corp).queryByText('guest signed in')).toBeNull()
    expect(within(guest).getByText('guest signed in')).toBeInTheDocument()

    expect(lastRequest().get('probe')).toBe('mpp-berlin-01')
    expect(FakeEventSource.instances.at(-1)?.url).toBe(
      '/api/v1/sensor-logs/events?probe=mpp-berlin-01',
    )
  })
})

describe('the navigation', () => {
  it('offers the sensor log to whoever may read it', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <AppProviders client={client}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/" element={<p>content</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AppProviders>,
    )

    const links = await screen.findAllByRole('link', { name: 'Sensor log' })
    expect(links[0]).toHaveAttribute('href', '/sensor-logs')
  })
})

describe('a single run', () => {
  it('opens from its link', async () => {
    server.use(
      http.get('/api/v1/sensor-logs/:id', ({ params }) =>
        params.id === 'R42'
          ? HttpResponse.json({
              ...record({ id: 'R42', summary: 'gateway did not answer' }),
              body: { dhcp: 'DHCPDISCOVER on wlan0\nNo DHCPOFFERS received.' },
            })
          : new HttpResponse(null, { status: 404 }),
      ),
    )
    wrap('/sensor-logs/R42')

    expect(await screen.findByText('gateway did not answer')).toBeInTheDocument()
    expect(screen.getByText(/No DHCPOFFERS received/)).toBeInTheDocument()
    expect(screen.getByText('R42')).toBeInTheDocument()
  })

  // wlan-auth names its fields for the helper, not for the reader; the run
  // page puts them in words, in both languages.
  function wlanAuthRun() {
    server.use(
      http.get('/api/v1/sensor-logs/:id', () =>
        HttpResponse.json({
          ...record({ id: 'R7' }),
          body: {
            ssid: 'corp-wifi',
            auth: 'peap',
            client_mac: '00:00:5e:00:53:10',
            bssid: '00:00:5e:00:53:c2',
            profile: 'office',
            frequency_mhz: 5180,
            timings: { assoc_ms: 41, total_ms: 2100 },
            supplicant: ['+0.500s wlan0: CTRL-EVENT-EAP-FAILURE'],
            events: ['+0.000s interface wlan0 locked'],
          },
        }),
      ),
    )
    wrap('/sensor-logs/R7')
  }

  it('labels the fields of a wlan-auth run', async () => {
    wlanAuthRun()

    expect(await screen.findByText('Client address')).toBeInTheDocument()
    expect(screen.getByText('Access point (BSSID)')).toBeInTheDocument()
    expect(screen.getByText('Authentication')).toBeInTheDocument()
    expect(screen.getByText('Stage timings')).toBeInTheDocument()
    expect(screen.getByText('Association')).toBeInTheDocument()
    expect(screen.getByText('wpa_supplicant output')).toBeInTheDocument()
    expect(screen.getByText('Helper events')).toBeInTheDocument()
    expect(screen.getByText('Credential profile')).toBeInTheDocument()
    expect(screen.queryByText('client_mac')).toBeNull()
    // Numbers through Intl, like everywhere else in the interface, and the
    // unit with the value: a stage past a second is read in seconds.
    expect(screen.getByText('5,180 MHz')).toBeInTheDocument()
    expect(screen.getByText('41 ms')).toBeInTheDocument()
    expect(screen.getByText('2.1 s')).toBeInTheDocument()
  })

  it('labels them in German too', async () => {
    await changeLanguage('de')
    wlanAuthRun()

    expect(await screen.findByText('Client-Adresse')).toBeInTheDocument()
    expect(screen.getByText('Ausgabe von wpa_supplicant')).toBeInTheDocument()
    expect(screen.getByText('Zugangsdaten-Profil')).toBeInTheDocument()
    expect(screen.getByText('5.180 MHz')).toBeInTheDocument()
    expect(screen.getByText('2,1 s')).toBeInTheDocument()
  })

  it('names the network in its header when only the body carries it', async () => {
    server.use(
      http.get('/api/v1/sensor-logs/:id', () =>
        HttpResponse.json({
          ...record({ id: 'R9', ssid: null }),
          body: { ssid: 'corp-wifi' },
        }),
      ),
    )
    wrap('/sensor-logs/R9')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'wlan-auth · corp-wifi' }),
    ).toBeInTheDocument()
  })

  it('puts the result code, the stage and the probe in words, and drops empty output', async () => {
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([
          {
            id: 'P1',
            nats_username: 'mpp-berlin-01',
            display_name: 'Berlin office',
            probe_name: 'berlin-01',
          },
        ]),
      ),
      http.get('/api/v1/sensor-logs/:id', () =>
        HttpResponse.json({
          ...record({ id: 'R10', code: 'dhcp-timeout' }),
          body: {
            ssid: 'corp-wifi',
            stage: 'dhcp',
            timings: {},
            supplicant: ['+0.500s wlan0: CTRL-EVENT-CONNECTED'],
            dhcp: [],
          },
        }),
      ),
    )
    wrap('/sensor-logs/R10')

    expect(await screen.findByText('No DHCP answer')).toBeInTheDocument()
    // The raw code stays beside the words, for searching and the sensor docs.
    expect(screen.getByText('dhcp-timeout')).toBeInTheDocument()
    expect(screen.getByText('Through DHCP')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Berlin office' })).toBeInTheDocument()
    expect(screen.getByText('wpa_supplicant output')).toBeInTheDocument()
    expect(screen.queryByText('DHCP client output')).not.toBeInTheDocument()
    expect(screen.queryByText('Stage timings')).not.toBeInTheDocument()
  })

  it('keeps the field names of a sensor it has no labels for', async () => {
    server.use(
      http.get('/api/v1/sensor-logs/:id', () =>
        HttpResponse.json({
          ...record({ id: 'R8', sensor: 'aruba-uplink' }),
          body: { client_mac: '00:00:5e:00:53:10' },
        }),
      ),
    )
    wrap('/sensor-logs/R8')

    expect(await screen.findByText('client_mac')).toBeInTheDocument()
  })
})
