import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { DeployDialog } from '@/features/deployments/DeployDialog'
import { changeLanguage } from '@/i18n'

/**
 * "Outdated on two" used to leave finding the two to the reader: the dialog
 * knew every probe and nothing about versions. Now it marks who is behind,
 * ticks them when opened for exactly that, and warns when a chosen probe's
 * management helper would refuse the rollout anyway.
 */

function probe(id: string, name: string, helperOutdated = false) {
  return {
    id,
    nats_username: name,
    display_name: null,
    host: `${name}.example`,
    probe_name: name,
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
    helper_version: helperOutdated ? 1 : 3,
    helper_outdated: helperOutdated,
  }
}

const SENSORS = [
  {
    name: 'internet-speed',
    version: '2',
    description: 'Speed',
    needs_interface: false,
    requires_privileged_helper: false,
    iperf_kind: null,
    has_parameter_schema: false,
    supports_profiles: false,
    installed_on: 2,
    outdated_on: 1,
  },
]

const DETAIL = {
  ...SENSORS[0],
  files: [],
  parameter_schema: null,
  readme: null,
  profile_template: null,
  installations: [
    { probe: 'mpp-current', version: '2', current: true },
    { probe: 'mpp-behind', version: '1', current: false },
  ],
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
        permissions: ['probe.read', 'sensor.read', 'deployment.create'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () =>
    HttpResponse.json([
      probe('P1', 'mpp-current'),
      probe('P2', 'mpp-behind'),
      probe('P3', 'mpp-fresh', true),
    ]),
  ),
  http.get('/api/v1/sensors', () => HttpResponse.json(SENSORS)),
  http.get('/api/v1/sensors/internet-speed', () => HttpResponse.json(DETAIL)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(preselect?: 'outdated') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <DeployDialog
          sensorName="internet-speed"
          preselect={preselect}
          onClose={() => {}}
        />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('deploying to the probes that are behind', () => {
  it('marks who is behind, with both versions', async () => {
    await changeLanguage('en')
    wrap()

    expect(await screen.findByText('v1 → v2')).toBeInTheDocument()
  })

  it('ticks exactly the outdated probes when opened for them', async () => {
    await changeLanguage('en')
    wrap('outdated')

    await waitFor(() => {
      expect(screen.getByLabelText(/mpp-behind/)).toBeChecked()
    })
    expect(screen.getByLabelText(/mpp-current/)).not.toBeChecked()
    expect(screen.getByLabelText(/mpp-fresh/)).not.toBeChecked()
  })

  it('warns when a chosen probe cannot take the rollout yet', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    expect(
      screen.queryByText(/management helper is outdated/),
    ).not.toBeInTheDocument()

    await user.click(await screen.findByLabelText(/mpp-fresh/))

    expect(
      await screen.findByText(/management helper is outdated/),
    ).toHaveTextContent('mpp-fresh')
  })
})
