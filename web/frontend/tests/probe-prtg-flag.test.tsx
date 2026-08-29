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
 * The two manual PRTG steps have no observer, only the operator's own tick.
 * Until it is set, a probe is green here and invisible over there - the one
 * state the status colour cannot show.
 */

let patches: Record<string, unknown>[] = []

function detail(registered: boolean) {
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
      sensor_count: 0,
      deviation_count: 0,
      observed_at: null,
      stale: false,
      running_job_id: null,
      error_code: null,
      helper_version: 3,
      helper_outdated: false,
      prtg_registered: registered,
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
    prtg_registered_at: registered ? '2026-08-29T10:00:00Z' : null,
    prtg_registered_by: registered ? 'admin' : null,
  }
}

let registered = false

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
  http.get('/api/v1/probes/P1', () => HttpResponse.json(detail(registered))),
  http.patch('/api/v1/probes/P1', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    patches.push(body)
    registered = body.prtg_registered === true
    return HttpResponse.json(detail(registered))
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  patches = []
  registered = false
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

describe('the PRTG registration tick', () => {
  it('says the probe is missing from PRTG and ticks it off', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    expect(
      await screen.findByText('This probe is not registered in PRTG yet'),
    ).toBeInTheDocument()
    // The banner names both steps and the warning that matters.
    expect(screen.getByText(/Approve and auto-discover/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Mark as registered' }))
    expect(patches).toEqual([{ prtg_registered: true }])

    expect(await screen.findByText(/ticked by admin/)).toBeInTheDocument()
    expect(
      screen.queryByText('This probe is not registered in PRTG yet'),
    ).not.toBeInTheDocument()
  })

  it('keeps quiet once the probe is marked, but lets the tick be reset', async () => {
    await changeLanguage('en')
    registered = true
    const user = userEvent.setup()
    wrap()

    expect(await screen.findByText(/ticked by admin/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(patches).toEqual([{ prtg_registered: false }])
  })
})
