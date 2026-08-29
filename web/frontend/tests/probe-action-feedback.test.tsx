import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { probeRefetchInterval } from '@/api/hooks'
import { AppProviders } from '@/app/providers'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { changeLanguage } from '@/i18n'

/**
 * Every button in the header says what came of it.
 *
 * Two of them used to throw the job id away: the button greyed out for a
 * second, came back, and the job ran somewhere. Three of them dropped their
 * failures silently, so a rejected CA install looked exactly like nothing
 * happening. Both are what these tests hold shut.
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
    sensor_count: 0,
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
    access_key_present: false,
    pending_transaction: null,
    assigned_sensors: [],
    known_iperf_endpoints: [],
  },
  observed: {
    observed_at: '2026-08-27T10:00:00Z',
    reachable: true,
    service: 'active',
    package_version: '3.10.0-1',
    hostname: 'berlin-probe-01',
    ca_sha256: 'aa',
    config_path: '/etc/paessler/mpprobe/config.yaml',
    probe_id: '11111111-2222-3333-4444-555555555555',
    probe_name: 'berlin-01',
    helper_version: 3,
    helper_sha256: 'bb',
    helper_outdated: false,
    error_code: null,
    error_details: null,
  },
  sensors: [],
  deviations: [],
  notes: null,
  labels: {},
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
        permissions: ['probe.read', 'probe.update'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(PROBE_DETAIL)),
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
          <Route path="/jobs/:jobId" element={<p>job page</p>} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('what the probe actions report back', () => {
  it('follows the job a check started', async () => {
    await changeLanguage('en')
    server.use(
      http.post('/api/v1/probes/actions/validate', () =>
        HttpResponse.json(
          { job_id: 'J7', status: 'queued', events_url: '/api/v1/jobs/J7/events' },
          { status: 202 },
        ),
      ),
    )
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Run check' }))
    expect(await screen.findByText('job page')).toBeInTheDocument()
  })

  it('follows the job a CA install started', async () => {
    await changeLanguage('en')
    server.use(
      http.post('/api/v1/probes/actions/install-ca', () =>
        HttpResponse.json(
          { job_id: 'J8', status: 'queued', events_url: '/api/v1/jobs/J8/events' },
          { status: 202 },
        ),
      ),
    )
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Install CA' }))
    expect(await screen.findByText('job page')).toBeInTheDocument()
  })

  it('shows the failure of an action that used to fail silently', async () => {
    await changeLanguage('en')
    server.use(
      http.post('/api/v1/probes/actions/install-ca', () =>
        HttpResponse.json(
          {
            error: {
              code: 'common.conflict',
              message_key: 'errors.common.conflict',
              params: {},
              fields: [],
              details: 'another job holds this probe',
              correlation_id: 'C1',
              retryable: false,
            },
          },
          { status: 409 },
        ),
      ),
    )
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Install CA' }))

    // The panel, naming the probe it is about - not a button that greys out
    // for a second and comes back.
    expect(await screen.findByText('What failed')).toBeInTheDocument()
    expect(screen.getByText('Affected target')).toBeInTheDocument()
  })
})

describe('how current the detail page keeps itself', () => {
  it('reloads while a job holds the probe, and not otherwise', () => {
    // Idle is the common case, and polling it would be a request every
    // fifteen seconds for a page whose content cannot change.
    expect(probeRefetchInterval(PROBE_DETAIL as never)).toBe(false)

    const busy = {
      ...PROBE_DETAIL,
      summary: { ...PROBE_DETAIL.summary, running_job_id: 'J1' },
    }
    expect(probeRefetchInterval(busy as never)).toBe(15_000)
  })

  it('has nothing to say about a probe it has not loaded yet', () => {
    expect(probeRefetchInterval(undefined)).toBe(false)
  })
})
