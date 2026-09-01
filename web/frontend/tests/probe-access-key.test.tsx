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
 * The last step of an enrollment happens in PRTG by hand: the operator adds the
 * probe's access key to the core's list. Before this the value existed only on
 * the NATS host, so anyone working in the interface had to fall back to a
 * shell. What the tests below pin down is the bargain that makes showing it
 * acceptable - the detail carries presence only, the value takes a deliberate
 * click, and the request that fetches it is the audited one.
 */

const ACCESS_KEY = 'Berlin-01-2f1c9d3b'

let revealRequests = 0
let permissions = ['probe.read', 'credential.read']

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
  http.get('/api/v1/probes/P1/access-key', () => {
    revealRequests += 1
    return HttpResponse.json({
      nats_username: 'mpp-berlin-01',
      access_key: ACCESS_KEY,
    })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  revealRequests = 0
  permissions = ['probe.read', 'credential.read']
})
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

describe('the access key on the probe detail', () => {
  it('stays hidden until somebody asks for it', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    // Loading the page must not fetch the secret, only report that there is one.
    expect(await screen.findByText('Hidden')).toBeInTheDocument()
    expect(screen.queryByText(ACCESS_KEY)).not.toBeInTheDocument()
    expect(revealRequests).toBe(0)

    await user.click(screen.getByRole('button', { name: 'Reveal' }))

    expect(await screen.findByText(ACCESS_KEY)).toBeInTheDocument()
    expect(revealRequests).toBe(1)
    // Both halves of what the operator needs to know: where the value goes,
    // and that looking was recorded.
    expect(screen.getByText(/PRTG access-key list/)).toBeInTheDocument()
    expect(screen.getByText(/recorded in the audit trail/)).toBeInTheDocument()
  })

  it('puts the key away again when the dialog is closed', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByText('Hidden')
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    await screen.findByText(ACCESS_KEY)

    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByText(ACCESS_KEY)).not.toBeInTheDocument()
  })

  it('clears a failed attempt once a later one has been dismissed', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    // An inventory that lost its key between the page load and the click,
    // in the envelope the server really sends.
    server.use(
      http.get('/api/v1/probes/P1/access-key', () =>
        HttpResponse.json(
          {
            error: {
              code: 'common.not_found',
              message_key: 'errors.common.not_found',
              params: { resource: 'access_key', id: 'mpp-berlin-01' },
              fields: [],
              details: null,
              correlation_id: 'C1',
              retryable: false,
            },
          },
          { status: 404 },
        ),
      ),
    )
    wrap()

    await screen.findByText('Hidden')
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    // Named, not "an unexpected error occurred" - the code has a translation.
    const failure = await screen.findByText(/access_key .* does not exist/i)

    server.resetHandlers()
    await user.click(screen.getByRole('button', { name: 'Reveal' }))
    await screen.findByText(ACCESS_KEY)
    await user.click(screen.getByRole('button', { name: 'Close' }))

    expect(failure).not.toBeInTheDocument()
  })

  it('offers no button to a caller who may not read credentials', async () => {
    await changeLanguage('en')
    permissions = ['probe.read']
    wrap()

    expect(await screen.findByText('Hidden')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reveal' })).not.toBeInTheDocument()
  })
})
