import { QueryClient } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { changeLanguage } from '@/i18n'

/**
 * Switching one probe's overlay mode used to mean leaving the probe, finding
 * it again in the overlay area's list and selecting it there. The probe's own
 * page now starts the same job with the same confirmation - and the same
 * request, so the refusal that keeps a probe from being cut off by its own
 * switch applies here exactly as it does in the overlay area.
 */

let permissions = ['probe.read', 'overlay.read', 'overlay.manage']
let peers: unknown[] = []
let modeRequests: unknown[] = []
let attachRequests: unknown[] = []

const PEER = {
  nats_username: 'mpp-berlin-01',
  address: '10.83.1.0',
  public_key: 'A'.repeat(43) + '=',
  mode: 'auto',
  last_state: 'direct',
}

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
}

const accepted = (id: string) =>
  HttpResponse.json(
    { job_id: id, status: 'queued', events_url: `/api/v1/jobs/${id}/events` },
    { status: 202 },
  )

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
        permissions,
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(PROBE_DETAIL)),
  http.get('/api/v1/overlay', () =>
    HttpResponse.json({
      enabled: true,
      endpoint: 'nats.example.test:51820',
      endpoint_host: 'nats.example.test',
      port: 51820,
      subnet: '10.83.0.0/16',
      hub_address: '10.83.0.1',
      hub_public_key: 'D'.repeat(43) + '=',
      default_mode: 'auto',
      interface_up: true,
      peers,
    }),
  ),
  http.post('/api/v1/overlay/peers/mode', async ({ request }) => {
    modeRequests.push(await request.json())
    return accepted('J1')
  }),
  http.post('/api/v1/overlay/peers', async ({ request }) => {
    attachRequests.push(await request.json())
    return accepted('J2')
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  permissions = ['probe.read', 'overlay.read', 'overlay.manage']
  peers = []
  modeRequests = []
  attachRequests = []
})
afterAll(() => server.close())

function JobStub() {
  const { jobId } = useParams()
  return <p>job {jobId}</p>
}

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/probes/P1']}>
        <Routes>
          <Route path="/probes/:probeId" element={<ProbeDetailPage />} />
          <Route path="/jobs/:jobId" element={<JobStub />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe("the overlay mode on the probe's own page", () => {
  it('changes the mode through the same confirmation and lands on the job', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    peers = [PEER]
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Change mode' }))
    await user.click(screen.getByRole('radio', { name: /Off/ }))
    expect(screen.getByText(/does not answer there is refused/)).toBeInTheDocument()
    expect(modeRequests).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Apply' }))

    expect(await screen.findByText('job J1')).toBeInTheDocument()
    // The overlay area's request, word for word: no force flag, so the
    // backend still refuses a switch that would cut the only working route.
    expect(modeRequests).toEqual([{ probe_ids: ['P1'], mode: 'off' }])
  })

  it('puts a probe without a peer on the overlay', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Add to the overlay' }))
    await user.click(screen.getByRole('radio', { name: /^On/ }))
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Add to the overlay' }),
    )

    expect(await screen.findByText('job J2')).toBeInTheDocument()
    expect(attachRequests).toEqual([{ probe_ids: ['P1'], mode: 'on' }])
  })

  it('shows the mode but no switch to someone who may not change it', async () => {
    await changeLanguage('en')
    permissions = ['probe.read', 'overlay.read']
    peers = [PEER]
    wrap()

    expect(await screen.findByText('Auto')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Change mode' }),
    ).not.toBeInTheDocument()
  })

  it('offers nothing to add to someone who may not change it', async () => {
    await changeLanguage('en')
    permissions = ['probe.read', 'overlay.read']
    wrap()

    expect(await screen.findByText('Off')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add to the overlay' })).not.toBeInTheDocument()
  })
})
