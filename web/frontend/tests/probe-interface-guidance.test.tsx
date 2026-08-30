import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { changeLanguage } from '@/i18n'

/**
 * wlan-auth without a reserved interface is a green rollout, a healthy-looking
 * sensor row, and a red sensor in PRTG. The probe page is where the platform
 * can say it first - and where an inactive privileged helper stops looking
 * exactly like a healthy one.
 */

const SENSORS = [
  {
    name: 'wlan-auth',
    version: '6',
    description: 'WLAN auth',
    needs_interface: true,
    requires_privileged_helper: true,
    iperf_kind: null,
    has_parameter_schema: true,
    supports_profiles: true,
    installed_on: 1,
    outdated_on: 0,
  },
  {
    name: 'link-quality',
    version: '1',
    description: 'Link quality',
    needs_interface: false,
    requires_privileged_helper: true,
    iperf_kind: null,
    has_parameter_schema: true,
    supports_profiles: false,
    installed_on: 1,
    outdated_on: 0,
  },
]

function detail(sensorOverrides: Record<string, unknown> = {}) {
  return {
    summary: {
      id: 'P1',
      nats_username: 'mpp-berlin-01',
      display_name: null,
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
    },
    inventory: {
      ssh_host: '192.0.2.10',
      ssh_port: 22,
      probe_id: '11111111-2222-3333-4444-555555555555',
      probe_name: 'berlin-01',
      access_key_present: true,
      pending_transaction: null,
      assigned_sensors: ['wlan-auth'],
      known_iperf_endpoints: [],
    },
    observed: null,
    sensors: [
      {
        name: 'wlan-auth',
        status: 'current',
        installed_version: '6',
        desired_version: '6',
        installed_sha256: null,
        expected_sha256: null,
        installed_helper_sha256: null,
        expected_helper_sha256: null,
        tool_name: 'iperf3',
        installed_tool_version: '3.21',
        expected_tool_version: '3.21',
        tool_platform: 'linux-arm64-glibc',
        tool_source: 'managed',
        tool_path: '/opt/prtg-nats/tools/iperf3/current/iperf3',
        installed_tool_sha256:
          '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
        expected_tool_sha256:
          'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210',
        tool_compatible: true,
        interfaces: [],
        helper_state: 'inactive',
        ...sensorOverrides,
      },
    ],
    deviations: [],
    notes: null,
    labels: {},
  }
}

const INTERFACES = [
  {
    name: 'wlan0',
    reserved_by: null,
    carries_default_route: false,
    operstate: 'down',
    nm_state: 'disconnected',
    connection: null,
  },
  {
    name: 'wlan1',
    reserved_by: null,
    carries_default_route: true,
    operstate: 'up',
    nm_state: 'connected',
    connection: 'uplink',
  },
]

const server = setupServer(
  http.get('/api/v1/auth/state', () =>
    HttpResponse.json({
      authenticated: true,
      setup_required: false,
      dev_auth: false,
      principal: {
        user_id: 'U1',
        username: 'admin',
        display_name: 'admin',
        roles: ['administrator'],
        permissions: ['probe.read', 'sensor.read', 'sensor.configure'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(detail())),
  http.get('/api/v1/sensors', () => HttpResponse.json(SENSORS)),
  http.get('/api/v1/sensors/wlan-auth/profiles', () => HttpResponse.json([])),
  http.get('/api/v1/probes/P1/wireless-interfaces', () =>
    HttpResponse.json(INTERFACES),
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap() {
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

describe('the probe page and the wlan sensor', () => {
  it('warns that nothing measures until an interface is reserved', async () => {
    await changeLanguage('en')
    wrap()

    const banner = await screen.findByText(
      'Nothing measures here without a reservation',
    )
    expect(banner.parentElement).toHaveTextContent('wlan-auth')
  })

  it('marks the inactive privileged helper instead of showing a healthy row', async () => {
    await changeLanguage('en')
    wrap()

    expect(await screen.findByText('inactive')).toBeInTheDocument()
  })

  it('shows the managed tool source, path and compatibility', async () => {
    await changeLanguage('en')
    wrap()

    const tool = await screen.findByText('iperf3')
    const cell = tool.closest('td')

    expect(cell).toHaveTextContent('linux-arm64-glibc')
    expect(cell).toHaveTextContent('Managed')
    expect(cell).toHaveTextContent('Compatible')
    expect(cell).toHaveTextContent(
      '/opt/prtg-nats/tools/iperf3/current/iperf3',
    )
    expect(cell).toHaveTextContent('Installed 3.21 · expected 3.21')
    expect(cell).toHaveTextContent('01234567…89abcdef')
    expect(cell).toHaveTextContent('fedcba98…76543210')
  })

  it('shows a compatible system fallback and its exact path', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(
          detail({
            tool_source: 'system',
            tool_path: '/usr/bin/iperf3',
            tool_platform: 'linux-armhf-v6-glibc',
            installed_tool_version: '3.18',
            expected_tool_version: '3.18',
            expected_tool_sha256: null,
          }),
        ),
      ),
    )
    wrap()

    const tool = await screen.findByText('iperf3')
    const cell = tool.closest('td')

    expect(cell).toHaveTextContent('System')
    expect(cell).toHaveTextContent('Compatible')
    expect(cell).toHaveTextContent('linux-armhf-v6-glibc')
    expect(cell).toHaveTextContent('/usr/bin/iperf3')
    expect(cell).toHaveTextContent('Installed 3.18 · minimum 3.18')
    expect(cell).toHaveTextContent('SHA-256 01234567…89abcdef')
    expect(cell).not.toHaveTextContent('fedcba98…76543210')
  })

  it('keeps an incompatible fallback visibly drifted', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(
          detail({
            status: 'drifted',
            tool_source: 'system',
            tool_path: '/usr/bin/iperf3',
            tool_platform: 'linux-armhf-v6-glibc',
            tool_compatible: false,
          }),
        ),
      ),
    )
    wrap()

    const tool = await screen.findByText('iperf3')
    const row = tool.closest('tr')

    expect(row).toHaveTextContent('Modified')
    expect(row).toHaveTextContent('Incompatible')
  })

  it('refuses to reserve the interface that carries the default route', async () => {
    await changeLanguage('en')
    wrap()

    const buttons = await screen.findAllByRole('button', { name: 'Reserve' })
    // wlan0 is free; wlan1 carries the default route and the probe would
    // refuse it - the button says so instead of daring the click.
    expect(buttons[0]).toBeEnabled()
    expect(buttons[1]).toBeDisabled()
  })
})
