import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { JobDetailPage } from '@/features/jobs/JobPages'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { ProbeListPage } from '@/features/probes/ProbeListPage'
import { changeLanguage } from '@/i18n'

/**
 * The way back, at the three places it used to break.
 *
 * Every action ends in a job, the job page led only to the job list, and the
 * detail page forgot both its tab and the list it came from. Somebody who
 * arrived from "probes with deviations: 3" had to find their way back by
 * hand each time.
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
    permissions: ['probe.read', 'probe.update', 'job.read'],
    locale: 'en',
    is_development: false,
    must_change_password: false,
  },
}

function probeSummary(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  }
}

const PROBES = [
  probeSummary(),
  probeSummary({
    id: 'P2',
    nats_username: 'mpp-hamburg-02',
    probe_name: 'hamburg-02',
    host: '192.0.2.20',
    deviation_count: 2,
  }),
]

const PROBE_DETAIL = {
  summary: probeSummary(),
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

const JOB = {
  id: 'J1',
  type: 'probe.validate',
  status: 'successful',
  target_type: 'probe',
  target_id: 'P1',
  target_label: 'mpp-berlin-01',
  progress: 100,
  current_step: null,
  requested_by_name: 'admin',
  trigger: 'manual',
  created_at: '2026-08-27T10:00:00Z',
  started_at: '2026-08-27T10:00:00Z',
  finished_at: '2026-08-27T10:01:00Z',
  duration_seconds: 60,
  blocked_reason_key: null,
  blocked_by_job_id: null,
  error_code: null,
  steps: [],
  payload: {},
  result: null,
  error_params: null,
  error_details: null,
  retry_of_job_id: null,
}

const server = setupServer(
  http.get('/api/v1/auth/state', () => HttpResponse.json(PRINCIPAL)),
  http.get('/api/v1/probes', () => HttpResponse.json(PROBES)),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(PROBE_DETAIL)),
  http.get('/api/v1/jobs/J1', () => HttpResponse.json(JOB)),
  http.get('/api/v1/jobs/J1/log', () => HttpResponse.json([])),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

/** Prints the current address, so a test can assert on where a click led. */
function Address() {
  const location = useLocation()
  return <p data-testid="address">{`${location.pathname}${location.search}`}</p>
}

function wrap(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Address />
        <Routes>
          <Route path="/probes" element={<ProbeListPage />} />
          <Route path="/probes/:probeId" element={<ProbeDetailPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

function address() {
  return screen.getByTestId('address').textContent
}

describe('the way back from a job', () => {
  it('leads to the probe the job ran on', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap('/jobs/J1')

    await user.click(await screen.findByRole('link', { name: 'mpp-berlin-01' }))
    expect(address()).toBe('/probes/P1')
  })

  it('leaves a target that is not a probe as plain text', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/jobs/J1', () =>
        HttpResponse.json({
          ...JOB,
          target_type: 'system',
          target_id: null,
          target_label: 'the stack',
        }),
      ),
    )
    wrap('/jobs/J1')

    expect(await screen.findByText('the stack')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'the stack' })).not.toBeInTheDocument()
  })
})

describe('the tab of the probe detail page', () => {
  it('stands in the address, so a reload and a link both land on it', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap('/probes/P1')

    await user.click(await screen.findByRole('button', { name: 'Configuration' }))
    expect(address()).toBe('/probes/P1?tab=deviations')
  })

  it('opens where the address says, not on the overview', async () => {
    await changeLanguage('en')
    wrap('/probes/P1?tab=diagnostics')

    expect(await screen.findByText('hostname')).toBeInTheDocument()
  })

  it('falls back to the overview for a tab that does not exist', async () => {
    await changeLanguage('en')
    wrap('/probes/P1?tab=nonsense')

    expect(await screen.findByText('NATS account')).toBeInTheDocument()
  })
})

describe('the way back to the list', () => {
  it('carries the filter and the search term the row was opened from', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap('/probes/P1?filter=deviations&q=hamburg&tab=sensors')

    await user.click(await screen.findByRole('link', { name: '← Probes' }))
    // The tab belongs to the detail page and stays behind; the rest is what
    // the list was showing.
    expect(address()).toBe('/probes?filter=deviations&q=hamburg')
  })

  it('is the plain list when nothing was filtered', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap('/probes/P1')

    await user.click(await screen.findByRole('link', { name: '← Probes' }))
    expect(address()).toBe('/probes')
  })
})

describe('the search box of the fleet', () => {
  it('puts the term in the address and hands it to the row it opens', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap('/probes')

    await user.type(await screen.findByLabelText('Search'), 'hamburg')
    expect(address()).toBe('/probes?q=hamburg')

    // One row left, and following it keeps the term for the way back.
    await user.click(await screen.findByRole('link', { name: /mpp-hamburg-02/ }))
    expect(address()).toBe('/probes/P2?q=hamburg')
  })
})
