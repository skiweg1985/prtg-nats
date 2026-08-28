import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { DeployDialog } from '@/features/deployments/DeployDialog'
import { changeLanguage } from '@/i18n'

/**
 * A rollout that installs a script with nothing to measure against.
 *
 * The first rollout onto a probe seeds it with every registered endpoint, so
 * the warning only counts probes that already have the sensor - otherwise it
 * would fire on every first rollout and stop being read.
 */

let endpointsAsked = 0

const PROBES = [
  { id: 'P1', nats_username: 'holding', display_name: 'Holding', host: 'a.example' },
  { id: 'P2', nats_username: 'installed', display_name: 'Installed', host: 'b.example' },
  { id: 'P3', nats_username: 'fresh', display_name: 'Fresh', host: 'c.example' },
]

function sensor(name: string, kind: string | null) {
  return {
    name,
    version: '1.0.0',
    description: name,
    needs_interface: false,
    requires_privileged_helper: false,
    iperf_kind: kind,
    has_parameter_schema: false,
    supports_profiles: true,
    installed_on: 2,
    outdated_on: 0,
  }
}

const SENSORS = [sensor('iperf-throughput', 'iperf3'), sensor('uptime', null)]

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
        probe: 'holding',
        endpoints_held: 1,
        uses_default_alias: true,
        parameter_line: '',
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
        permissions: ['iperf.read', 'sensor.read', 'deployment.create'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json(PROBES)),
  http.get('/api/v1/sensors', () => HttpResponse.json(SENSORS)),
  // "holding" and "installed" have the script; "fresh" does not.
  http.get('/api/v1/sensors/:name', ({ params }) =>
    HttpResponse.json({
      ...sensor(String(params.name), params.name === 'iperf-throughput' ? 'iperf3' : null),
      files: [],
      parameter_schema: null,
      readme: null,
      profile_template: null,
      probes: ['holding', 'installed'],
    }),
  ),
  http.get('/api/v1/iperf-endpoints', () => {
    endpointsAsked += 1
    return HttpResponse.json(ENDPOINTS)
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  endpointsAsked = 0
})
afterAll(() => server.close())

function wrap(sensorName: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <DeployDialog sensorName={sensorName} onClose={() => {}} />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('deploying a sensor that measures against an endpoint', () => {
  it('names the probes that will install it with nothing to measure against', async () => {
    await changeLanguage('en')
    wrap('iperf-throughput')

    await userEvent.click(await screen.findByLabelText(/Installed/))
    expect(
      await screen.findByText(/already have the sensor but hold no endpoint/),
    ).toHaveTextContent('installed')
  })

  it('stays quiet for a probe the rollout will seed itself', async () => {
    await changeLanguage('en')
    wrap('iperf-throughput')

    await userEvent.click(await screen.findByLabelText(/Fresh/))
    expect(
      screen.queryByText(/already have the sensor but hold no endpoint/),
    ).not.toBeInTheDocument()
  })

  it('says so when no endpoint is registered at all', async () => {
    await changeLanguage('en')
    server.use(http.get('/api/v1/iperf-endpoints', () => HttpResponse.json([])))
    wrap('iperf-throughput')

    expect(
      await screen.findByText(/No endpoint is registered for this sensor/),
    ).toBeInTheDocument()
  })

  it('does not ask for endpoints for a sensor that needs none', async () => {
    await changeLanguage('en')
    wrap('uptime')

    await screen.findByLabelText(/Fresh/)
    expect(endpointsAsked).toBe(0)
  })
})
