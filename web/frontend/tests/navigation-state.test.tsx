import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { useState } from 'react'
import {
  Link,
  MemoryRouter,
  useLocation,
  useNavigate,
  type Location,
} from 'react-router-dom'
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  onTestFinished,
  vi,
} from 'vitest'

import { resetNavigationMemory } from '@/app/navigation'
import { AppProviders } from '@/app/providers'
import { AppRoutes } from '@/app/router'
import { resolveCrumbs } from '@/components/ui/Breadcrumb'
import { changeLanguage } from '@/i18n'
import { oneOf, pageNumber, patchParams } from '@/utils/urlState'

/**
 * The state of a page and the way between pages, in the whole application.
 *
 * A reload or a link sent to a colleague used to lose the table search on
 * half the pages; a detail page's way back pointed at one fixed parent; the
 * sidebar opened every page bare; and the sensor log named a probe and a
 * sensor without leading to either.
 */

const PRINCIPAL = {
  authenticated: true,
  setup_required: false,
  dev_auth: false,
  principal: {
    user_id: 'U1',
    username: 'admin',
    display_name: 'admin',
    roles: ['administrator'],
    permissions: ['probe.read', 'sensor.read', 'sensor_log.read', 'job.read'],
    locale: 'en',
    is_development: false,
    must_change_password: false,
  },
}

function probeSummary(overrides: Record<string, unknown> = {}) {
  return {
    id: 'P1',
    nats_username: 'mpp-berlin-01',
    display_name: 'Berlin office',
    host: '192.0.2.10',
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
    helper_version: 3,
    helper_outdated: false,
    prtg_registered: true,
    managed_over_tunnel: false,
    ...overrides,
  }
}

const PROBES = [
  probeSummary(),
  probeSummary({
    id: 'P2',
    nats_username: 'mpp-hamburg-02',
    display_name: null,
    probe_name: 'hamburg-02',
    host: '192.0.2.20',
    deviation_count: 2,
  }),
]

const PROBE_DETAIL = {
  summary: PROBES[0],
  inventory: {
    ssh_host: '192.0.2.10',
    ssh_port: 22,
    probe_id: '11111111-2222-3333-4444-555555555555',
    probe_name: 'berlin-01',
    access_key_present: false,
    pending_transaction: null,
    assigned_sensors: [],
    known_iperf_endpoints: [],
  },
  observed: null,
  sensors: [],
  deviations: [],
  notes: null,
  labels: {},
}

const RUN = {
  id: 'R1',
  probe: 'mpp-berlin-01',
  sensor: 'wlan-auth',
  job: 'a1b2c3d4e5f6',
  time: '2026-11-08T12:04:00Z',
  result: 'error',
  code: 'dhcp_timeout',
  summary: 'no DHCP lease after 30 s',
  ssid: 'corp-wifi',
}

const SENSORS = [
  {
    name: 'wlan-auth',
    version: '6',
    description: 'WLAN auth',
    needs_interface: true,
    requires_privileged_helper: true,
    iperf_kind: null,
    has_parameter_schema: false,
    supports_profiles: false,
    installed_on: 1,
    outdated_on: 0,
  },
  {
    name: 'internet-speed',
    version: '2',
    description: 'Internet speed',
    needs_interface: false,
    requires_privileged_helper: false,
    iperf_kind: null,
    has_parameter_schema: false,
    supports_profiles: false,
    installed_on: 0,
    outdated_on: 0,
  },
]

const SENSOR_DETAIL = {
  ...SENSORS[0],
  files: [],
  parameter_schema: null,
  readme: null,
  profile_template: null,
  installations: [{ probe: 'mpp-berlin-01', version: '6', current: true }],
}

const server = setupServer(
  http.get('/api/v1/auth/state', () => HttpResponse.json(PRINCIPAL)),
  http.get('/api/v1/system/capabilities', () =>
    HttpResponse.json({ docker: true, runtime_state: 'ready' }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json(PROBES)),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(PROBE_DETAIL)),
  http.get('/api/v1/probes/P1/prtg-name', () => HttpResponse.json({ state: 'unlinked' })),
  http.get('/api/v1/probes/P1/sensors/:sensor/jobs', () => HttpResponse.json([])),
  http.get('/api/v1/prtg/probe-names', () => HttpResponse.json({ names: [] })),
  http.get('/api/v1/sensors', () => HttpResponse.json(SENSORS)),
  http.get('/api/v1/sensors/wlan-auth', () => HttpResponse.json(SENSOR_DETAIL)),
  http.get('/api/v1/sensors/wlan-auth/profiles', () => HttpResponse.json([])),
  http.get('/api/v1/sensor-logs/timeline', () =>
    HttpResponse.json({
      probe: 'mpp-berlin-01',
      sensor: 'wlan-auth',
      start: '2026-11-07T12:00:00Z',
      until: '2026-11-08T12:00:00Z',
      bucket_seconds: 900,
      retained_since: '2026-10-01T00:00:00Z',
      streams: [],
    }),
  ),
  http.get('/api/v1/sensor-logs/R1', () =>
    HttpResponse.json({ ...RUN, body: { client_mac: '00:00:5e:00:53:10' } }),
  ),
  http.get('/api/v1/sensor-logs', () =>
    HttpResponse.json({ items: [RUN], total: 1, limit: 100, offset: 0 }),
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
beforeEach(async () => {
  await changeLanguage('en')
  globalThis.EventSource = class {
    close() {}
    addEventListener() {}
  } as unknown as typeof EventSource
})
afterEach(() => {
  server.resetHandlers()
  vi.restoreAllMocks()
  try {
    window.sessionStorage.clear()
  } catch {
    // The test that blocks storage restores it afterwards.
  }
  resetNavigationMemory()
})
afterAll(() => server.close())

let lastLocation: Location | null = null

/**
 * The address, Back, and a link that can be followed from wherever the test
 * stands - the way a link on another page would be.
 */
function Harness({ jumpTo }: { jumpTo?: string }) {
  const location = useLocation()
  const navigate = useNavigate()
  lastLocation = location
  return (
    <>
      <p data-testid="address">{`${location.pathname}${location.search}`}</p>
      <button type="button" onClick={() => navigate(-1)}>
        test-back
      </button>
      {jumpTo && <Link to={jumpTo}>test-jump</Link>}
    </>
  )
}

type Entry = string | Partial<Location>

function App({ entries, index, jumpTo }: { entries: Entry[]; index?: number; jumpTo?: string }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  )
  return (
    <AppProviders client={client}>
      <MemoryRouter initialEntries={entries} initialIndex={index ?? entries.length - 1}>
        <Harness jumpTo={jumpTo} />
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>
  )
}

function open(entries: Entry[] | Entry, options: { jumpTo?: string } = {}) {
  return render(<App entries={Array.isArray(entries) ? entries : [entries]} {...options} />)
}

/**
 * A reload: the page starts over with the entry it was on, the module's
 * memory is gone, sessionStorage is not.
 */
function reload(view: ReturnType<typeof render>, options: { jumpTo?: string } = {}) {
  const location = lastLocation
  if (!location) throw new Error('nothing was rendered')
  view.unmount()
  resetNavigationMemory()
  return render(<App entries={[location]} {...options} />)
}

function address() {
  return screen.getByTestId('address').textContent
}

async function breadcrumb() {
  return within(await screen.findByRole('navigation', { name: 'Breadcrumb' }))
}

function sidebar() {
  const rail = screen
    .getAllByRole('navigation')
    .find((element) => element.getAttribute('aria-label') === null)
  if (!rail) throw new Error('no sidebar')
  return within(rail)
}

describe('the shared helper for page state', () => {
  it('writes changes and drops empty values instead of leaving `?q=`', () => {
    const next = patchParams(new URLSearchParams('q=old&tab=sensors'), {
      q: '',
      status: ['failed', 'running'],
      tab: null,
    })
    expect(next.toString()).toBe('status=failed&status=running')
  })

  it('falls back to the default for a stale or invalid value', () => {
    expect(oneOf('nonsense', ['overview', 'sensors'] as const, 'overview')).toBe('overview')
    expect(oneOf('sensors', ['overview', 'sensors'] as const, 'overview')).toBe('sensors')
    expect(pageNumber('-3')).toBe(1)
    expect(pageNumber('abc')).toBe(1)
    expect(pageNumber('4')).toBe(4)
  })
})

describe('a table search', () => {
  it('stands in the address, survives a reload and adds no history entry', async () => {
    const user = userEvent.setup()
    const view = open(['/nowhere', '/sensors'])

    await screen.findByRole('link', { name: 'internet-speed' })
    await user.type(screen.getByLabelText('Search'), 'wlan')
    expect(address()).toBe('/sensors?q=wlan')
    expect(screen.queryByRole('link', { name: 'internet-speed' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Sensor/ }))
    expect(address()).toBe('/sensors?q=wlan&sort=name')

    reload(view)
    expect(await screen.findByLabelText('Search')).toHaveValue('wlan')
    expect(screen.queryByRole('link', { name: 'internet-speed' })).not.toBeInTheDocument()
    view.unmount()
  })

  it('leaves the page with one step back, not one per keystroke', async () => {
    const user = userEvent.setup()
    open(['/nowhere', '/sensors'])

    await user.type(await screen.findByLabelText('Search'), 'wlan')
    await user.click(screen.getByRole('button', { name: 'test-back' }))
    expect(address()).toBe('/nowhere')
  })

  it('ignores a sort column that does not exist', async () => {
    open('/sensors?sort=-nonsense')
    expect(await screen.findByRole('link', { name: 'internet-speed' })).toBeInTheDocument()
  })
})

describe('an expanded section', () => {
  it('stays open across a reload', async () => {
    const user = userEvent.setup()
    const view = open('/sensor-logs')

    await screen.findByText('no DHCP lease after 30 s')
    await user.click(screen.getByRole('button', { name: 'Show details' }))
    expect(address()).toBe('/sensor-logs?run=R1')

    reload(view)
    expect(await screen.findByRole('button', { name: 'Hide details' })).toBeInTheDocument()
  })
})

describe('the breadcrumb', () => {
  it('leads back to the filtered sensor log a run was opened from', async () => {
    const user = userEvent.setup()
    open('/sensor-logs?result=error')

    // The row's time is the link to the run.
    const row = (await screen.findByText('no DHCP lease after 30 s')).closest('tr')!
    await user.click(within(row).getAllByRole('link')[0]!)
    expect(address()).toBe('/sensor-logs/R1')

    const back = await (await breadcrumb()).findByRole('link', { name: 'Sensor log' })
    expect(back).toHaveAttribute('href', '/sensor-logs?result=error')
    expect((await breadcrumb()).queryByRole('link', { name: 'Probes' })).not.toBeInTheDocument()
    // Probe and sensor stay reachable from the page whatever the origin.
    expect(screen.getAllByRole('link', { name: 'Berlin office' }).length).toBeGreaterThan(0)

    await user.click(back)
    expect(address()).toBe('/sensor-logs?result=error')
  })

  it('keeps the origin across a reload of the run', async () => {
    const user = userEvent.setup()
    const view = open('/sensor-logs?result=error')

    const row = (await screen.findByText('no DHCP lease after 30 s')).closest('tr')!
    await user.click(within(row).getAllByRole('link')[0]!)
    await (await breadcrumb()).findByRole('link', { name: 'Sensor log' })

    reload(view)
    expect(await (await breadcrumb()).findByRole('link', { name: 'Sensor log' })).toHaveAttribute(
      'href',
      '/sensor-logs?result=error',
    )
  })

  it('shows the hierarchy with the sensor page as it was left', async () => {
    const user = userEvent.setup()
    open('/probes/P1/sensors/wlan-auth?window=7d', { jumpTo: '/sensor-logs/R1' })

    await screen.findByRole('heading', { name: 'wlan-auth' })
    await user.click(screen.getByRole('link', { name: 'test-jump' }))

    const crumbs = await breadcrumb()
    expect(await crumbs.findByRole('link', { name: 'Probes' })).toHaveAttribute('href', '/probes')
    expect(crumbs.getByRole('link', { name: 'Berlin office' })).toHaveAttribute(
      'href',
      '/probes/P1?tab=sensors',
    )
    const sensor = crumbs.getByRole('link', { name: 'wlan-auth' })
    expect(sensor).toHaveAttribute('href', '/probes/P1/sensors/wlan-auth?window=7d')
    expect(crumbs.getByText(/^Run /)).toHaveAttribute('aria-current', 'page')

    // Stepping up does not make the run the sensor page's origin.
    await user.click(sensor)
    expect(address()).toBe('/probes/P1/sensors/wlan-auth?window=7d')
    expect(await (await breadcrumb()).findByRole('link', { name: 'Berlin office' })).toBeInTheDocument()
    expect((await breadcrumb()).queryByText(/^Run /)).not.toBeInTheDocument()
  })

  it('falls back to the hierarchy without a known origin', async () => {
    open('/sensor-logs/R1')

    const crumbs = await breadcrumb()
    expect(await crumbs.findByRole('link', { name: 'Probes' })).toBeInTheDocument()
    expect(crumbs.getByRole('link', { name: 'wlan-auth' })).toHaveAttribute(
      'href',
      '/probes/P1/sensors/wlan-auth',
    )
  })

  it('resolves the three cases without rendering anything', () => {
    const trail = [
      { to: '/probes', label: 'Probes' },
      { to: '/probes/P1?tab=sensors', label: 'Berlin office' },
    ]
    const label = () => 'Sensor log'
    expect(resolveCrumbs(trail, [], label).map((crumb) => crumb.to)).toEqual([
      '/probes',
      '/probes/P1?tab=sensors',
    ])
    expect(
      resolveCrumbs(trail, [{ pathname: '/sensor-logs', search: '?q=x', key: 'a' }], label),
    ).toEqual([{ to: '/sensor-logs?q=x', label: 'Sensor log', state: { crumb: 'a' } }])
    expect(
      resolveCrumbs(
        trail,
        [
          { pathname: '/probes/P1', search: '?tab=diagnostics', key: 'b' },
          { pathname: '/probes', search: '?status=failed', key: 'c' },
        ],
        label,
      ).map((crumb) => crumb.to),
    ).toEqual(['/probes?status=failed', '/probes/P1?tab=diagnostics'])
  })
})

describe('the sidebar', () => {
  it('restores the filters a page was last left with', async () => {
    const user = userEvent.setup()
    open('/probes?filter=deviations')

    await screen.findByRole('link', { name: /mpp-hamburg-02/ })
    await user.click(sidebar().getByRole('link', { name: 'Sensors' }))
    expect(address()).toBe('/sensors')

    const probes = sidebar().getByRole('link', { name: 'Probes' })
    expect(probes).toHaveAttribute('href', '/probes?filter=deviations')
    await user.click(probes)
    expect(address()).toBe('/probes?filter=deviations')
  })

  it('offers a reset while a filter is set, which clears the address and the memory', async () => {
    const user = userEvent.setup()
    open('/probes?filter=deviations&q=hamburg')

    await user.click(await screen.findByRole('button', { name: 'Clear filters' }))
    expect(address()).toBe('/probes')
    expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument()
    await waitFor(() =>
      expect(sidebar().getByRole('link', { name: 'Probes' })).toHaveAttribute('href', '/probes'),
    )
  })

  it('works without storage, as before', async () => {
    // Blocked storage throws on access, as a browser's SecurityError does.
    const original = Object.getOwnPropertyDescriptor(window, 'sessionStorage')!
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      get() {
        throw new Error('blocked')
      },
    })
    onTestFinished(() => {
      Object.defineProperty(window, 'sessionStorage', original)
    })
    const user = userEvent.setup()
    open('/probes?filter=deviations')

    await screen.findByRole('link', { name: /mpp-hamburg-02/ })
    await user.click(sidebar().getByRole('link', { name: 'Sensors' }))
    expect(address()).toBe('/sensors')
    expect((await screen.findAllByRole('link', { name: /wlan-auth/ })).length).toBeGreaterThan(0)
    // Within the tab the memory still works, in memory.
    expect(sidebar().getByRole('link', { name: 'Probes' })).toHaveAttribute(
      'href',
      '/probes?filter=deviations',
    )
  })
})

describe('links between the sensor log, sensor pages and the catalog', () => {
  it('leads from a run to the sensor on its probe', async () => {
    open('/sensor-logs/R1')

    await screen.findByText('dhcp_timeout')
    const details = screen.getByText('Probe').closest('dl')!
    expect(within(details).getByRole('link', { name: 'Berlin office' })).toHaveAttribute(
      'href',
      '/probes/P1/sensors/wlan-auth',
    )
    expect(within(details).getByRole('link', { name: 'wlan-auth' })).toHaveAttribute(
      'href',
      '/probes/P1/sensors/wlan-auth',
    )
  })

  it('makes probe and sensor links in every row, and the row still opens the run', async () => {
    const user = userEvent.setup()
    open('/sensor-logs')

    const row = (await screen.findByText('no DHCP lease after 30 s')).closest('tr')!
    expect(within(row).getByRole('link', { name: 'Berlin office' })).toHaveAttribute(
      'href',
      '/probes/P1',
    )
    expect(within(row).getByRole('link', { name: 'wlan-auth' })).toHaveAttribute(
      'href',
      '/probes/P1/sensors/wlan-auth',
    )

    await user.click(within(row).getByText('no DHCP lease after 30 s'))
    expect(address()).toBe('/sensor-logs/R1')
  })

  it('lists the probes a catalog sensor runs on, each leading to its sensor page', async () => {
    open('/sensors/wlan-auth')

    const card = (await screen.findByText('Installed on these probes')).closest('section')!
    expect(await within(card).findByRole('link', { name: 'Berlin office' })).toHaveAttribute(
      'href',
      '/probes/P1/sensors/wlan-auth',
    )
  })
})
