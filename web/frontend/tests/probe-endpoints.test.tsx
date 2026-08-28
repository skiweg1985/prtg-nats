import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { changeLanguage } from '@/i18n'

/**
 * What a probe holds for its sensors, next to the sensors themselves.
 *
 * A held endpoint is a resource the probe carries, like a reserved interface -
 * and what a PRTG object on this probe needs depends on how many it holds. As
 * a list of bare names under diagnostics that answer was nowhere.
 */

const PROBE_DETAIL = {
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
    assigned_sensors: ['iperf-throughput'],
    // The second name is only in the sidecar on the probe: the registry has
    // no such endpoint any more.
    known_iperf_endpoints: ['berlin', 'gone'],
  },
  observed: null,
  sensors: [],
  deviations: [],
  notes: null,
  labels: {},
}

const ENDPOINTS = [
  {
    name: 'berlin',
    host: 'iperf.example.test',
    port: 5201,
    username: 'prtg-probe',
    kind: 'iperf3',
    updated_at: null,
    has_public_key: true,
    managed: true,
    holders: [
      {
        probe: 'mpp-berlin-01',
        endpoints_held: 2,
        uses_default_alias: false,
        parameter_line: '--profile berlin',
      },
    ],
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
        permissions: ['probe.read', 'iperf.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(PROBE_DETAIL)),
  http.get('/api/v1/iperf-endpoints', () => HttpResponse.json(ENDPOINTS)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/probes/P1']}>
        <Routes>
          <Route path="/probes/:probeId" element={<ProbeDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('the endpoints a probe holds', () => {
  it('lists them under the sensors, with the line PRTG needs', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: /Sensors/i }))

    const link = await screen.findByRole('link', { name: 'berlin' })
    expect(link).toHaveAttribute('href', '/infrastructure/iperf/berlin')
    expect(await screen.findByText('--profile berlin')).toBeInTheDocument()
  })

  it('marks a name the registry no longer knows', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: /Sensors/i }))

    expect(await screen.findByText('gone')).toBeInTheDocument()
    expect(screen.getByText('not registered')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'gone' })).not.toBeInTheDocument()
  })

  it('no longer repeats the bare names under diagnostics', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: /Diagnostics/i }))

    expect(screen.queryByText('berlin, gone')).not.toBeInTheDocument()
  })
})
