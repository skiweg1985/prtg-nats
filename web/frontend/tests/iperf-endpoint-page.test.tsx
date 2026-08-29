import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { IperfEndpointPage } from '@/features/infrastructure/IperfEndpointPage'
import { changeLanguage } from '@/i18n'

/**
 * The page that says what is still missing.
 *
 * Everything it shows is per probe, because that is what the answer depends
 * on: the same endpoint needs no parameter on a probe that holds it alone and
 * a named profile on one that holds two.
 */

const SENSORS = [
  {
    name: 'iperf-throughput',
    version: '1.0.0',
    description: 'Throughput',
    needs_interface: false,
    requires_privileged_helper: false,
    iperf_kind: 'iperf3',
    has_parameter_schema: true,
    supports_profiles: true,
    installed_on: 1,
    outdated_on: 0,
  },
]

const PROBES = [
  { id: 'P1', nats_username: 'mpp-berlin', display_name: 'Berlin', host: 'a.example' },
  { id: 'P2', nats_username: 'mpp-hamburg', display_name: 'Hamburg', host: 'b.example' },
]

function endpoint(holders: unknown[]) {
  return {
    name: 'berlin',
    host: 'iperf.example.test',
    port: 5201,
    username: 'prtg-probe',
    kind: 'iperf3',
    updated_at: '2026-08-01T10:00:00Z',
    has_public_key: true,
    managed: true,
    holders,
  }
}

const ALONE = {
  probe: 'mpp-berlin',
  endpoints_held: 1,
  uses_default_alias: true,
  parameter_line: '',
}
const SHARING = {
  probe: 'mpp-hamburg',
  endpoints_held: 2,
  uses_default_alias: false,
  parameter_line: '--profile berlin',
}

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
        permissions: [
          'iperf.read',
          'iperf.manage',
          'sensor.deploy',
          'sensor.read',
          'deployment.create',
        ],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json(PROBES)),
  http.get('/api/v1/sensors', () => HttpResponse.json(SENSORS)),
  // Installed on the probe that holds it alone, missing on the other one.
  http.get('/api/v1/sensors/iperf-throughput', () =>
    HttpResponse.json({
      ...SENSORS[0],
      files: [],
      parameter_schema: null,
      readme: null,
      profile_template: null,
      installations: [{ probe: 'mpp-berlin', version: '1.0.0', current: true }],
    }),
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(holders: unknown[]) {
  server.use(
    http.get('/api/v1/iperf-endpoints/berlin', () =>
      HttpResponse.json(endpoint(holders)),
    ),
    http.get('/api/v1/iperf-endpoints', () => HttpResponse.json([endpoint(holders)])),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/infrastructure/iperf/berlin']}>
        <Routes>
          <Route path="/infrastructure/iperf/:name" element={<IperfEndpointPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('IperfEndpointPage', () => {
  it('says nothing is to be pasted where the alias answers for it', async () => {
    await changeLanguage('en')
    wrap([ALONE])

    expect(await screen.findByText('no parameter needed')).toBeInTheDocument()
    expect(screen.queryByText('--profile berlin')).not.toBeInTheDocument()
  })

  it('shows the line to copy where the probe holds more than one', async () => {
    await changeLanguage('en')
    wrap([SHARING])

    expect(await screen.findByText('--profile berlin')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument()
  })

  it('offers the assignment when no probe holds it', async () => {
    await changeLanguage('en')
    wrap([])

    expect(await screen.findByText('No probe holds this endpoint yet')).toBeInTheDocument()
    expect(
      screen.getAllByRole('button', { name: 'Assign probes' }).length,
    ).toBeGreaterThan(0)
  })

  it('names the probe that has the credentials but not the sensor', async () => {
    await changeLanguage('en')
    wrap([ALONE, SHARING])

    const banner = await screen.findByText(
      'The credentials are there, but nothing reads them',
    )
    // The one holder without the sensor, not both - the banner is the list of
    // what is still open, not of who holds the endpoint.
    expect(banner.parentElement).toHaveTextContent('mpp-hamburg')
    expect(banner.parentElement).not.toHaveTextContent('mpp-berlin')
  })
})
