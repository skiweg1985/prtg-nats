import { QueryClient } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { changeLanguage } from '@/i18n'

/**
 * The return channel on the probe page: when the probe last delivered, how
 * full its buffer is, the gaps it reported, and a warning once it has gone
 * silent - with the repair one click away for those allowed to start it.
 */

function detail(returnChannel: Record<string, unknown> | null) {
  return {
    summary: {
      id: 'P1',
      nats_username: 'mpp-berlin-01',
      display_name: null,
      host: '198.51.100.29',
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
      helper_version: 12,
      helper_outdated: false,
      prtg_registered: true,
      managed_over_tunnel: false,
      channel_silent: returnChannel?.silent ?? false,
    },
    inventory: {
      ssh_host: '198.51.100.29',
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
    prtg_registered_at: null,
    prtg_registered_by: null,
    management_route: null,
    nats_route: null,
    return_channel: returnChannel,
  }
}

function channel(overrides: Record<string, unknown> = {}) {
  return {
    last_contact_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    buffer_bytes: 5_242_880,
    buffer_limit: 20_971_520,
    gap_count: 2,
    last_gap_at: new Date(Date.now() - 60 * 60_000).toISOString(),
    observed_at: new Date().toISOString(),
    certificate_expires: '2099-01-31',
    service: 'active',
    silent: false,
    ...overrides,
  }
}

function authState(permissions: string[]) {
  return http.get('/api/v1/auth/state', () =>
    HttpResponse.json({
      authenticated: true,
      setup_required: false,
      dev_auth: false,
      principal: {
        user_id: 'U1',
        username: 'admin',
        display_name: 'admin',
        roles: ['operator'],
        permissions,
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  )
}

const server = setupServer(authState(['probe.read', 'probe.update']))

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/probes/P1']}>
        <Routes>
          <Route path='/probes/:probeId' element={<ProbeDetailPage />} />
          <Route path='/jobs/:jobId' element={<p>job page</p>} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

async function channelRow(label: string): Promise<HTMLElement> {
  const term = await screen.findByText(label, { selector: 'dt' })
  return term.parentElement as HTMLElement
}

describe('the return channel on the probe page', () => {
  it('shows the last report, the buffer and the gaps', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () => HttpResponse.json(detail(channel()))),
    )
    wrap()

    const row = await channelRow('Return channel')
    expect(within(row).getByText(/Last report/)).toBeInTheDocument()
    expect(within(row).getByText(/Buffer 25% full/)).toBeInTheDocument()
    expect(within(row).getByText(/2 gaps/)).toBeInTheDocument()
    expect(within(row).queryByText('Silent')).not.toBeInTheDocument()
  })

  it('warns when the probe has gone silent', async () => {
    await changeLanguage('de')
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(detail(channel({ silent: true, gap_count: 0 }))),
      ),
    )
    wrap()

    const row = await channelRow('Rückkanal')
    expect(within(row).getByText('Stumm')).toBeInTheDocument()
    expect(within(row).getByText(/Keine Lücken/)).toBeInTheDocument()
  })

  it('says when nothing has arrived yet', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(
          detail(
            channel({
              last_contact_at: null,
              buffer_bytes: null,
              buffer_limit: null,
              gap_count: 0,
              last_gap_at: null,
            }),
          ),
        ),
      ),
    )
    wrap()

    const row = await channelRow('Return channel')
    expect(within(row).getByText('Nothing delivered yet')).toBeInTheDocument()
  })

  it('leaves the row out for a probe without a return channel', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () => HttpResponse.json(detail(null))),
    )
    wrap()

    await screen.findByText('Management via', { selector: 'dt' })
    expect(
      screen.queryByText('Return channel', { selector: 'dt' }),
    ).not.toBeInTheDocument()
  })

  it('starts the repair and opens its job', async () => {
    await changeLanguage('en')
    let requested = false
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(detail(channel({ silent: true }))),
      ),
      http.post('/api/v1/probes/P1/return-channel/repair', () => {
        requested = true
        return HttpResponse.json(
          {
            job_id: 'J1',
            status: 'queued',
            events_url: '/api/v1/jobs/J1/events',
          },
          { status: 202 },
        )
      }),
    )
    wrap()

    const row = await channelRow('Return channel')
    await userEvent.click(within(row).getByRole('button', { name: 'Set up again' }))
    expect(await screen.findByText('job page')).toBeInTheDocument()
    expect(requested).toBe(true)
  })

  it('offers no repair on a healthy channel', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(
          detail(
            channel({
              gap_count: 3,
              // Gaps from last week are history, not something to repair.
              last_gap_at: new Date(Date.now() - 7 * 86_400_000).toISOString(),
            }),
          ),
        ),
      ),
    )
    wrap()

    const row = await channelRow('Return channel')
    expect(within(row).getByText(/3 gaps/)).toBeInTheDocument()
    expect(
      within(row).queryByRole('button', { name: 'Set up again' }),
    ).not.toBeInTheDocument()
  })

  it('offers the repair once the certificate has expired', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(
          detail(
            channel({
              gap_count: 0,
              last_gap_at: null,
              certificate_expires: '2020-01-31',
              certificate_expired: true,
            }),
          ),
        ),
      ),
    )
    wrap()

    const row = await channelRow('Return channel')
    expect(within(row).getByText('Certificate expired')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Set up again' })).toBeInTheDocument()
  })

  it('offers no repair without the permission for it', async () => {
    await changeLanguage('en')
    server.use(
      authState(['probe.read']),
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(detail(channel({ silent: true }))),
      ),
    )
    wrap()

    const row = await channelRow('Return channel')
    expect(within(row).getByText('Silent')).toBeInTheDocument()
    expect(
      within(row).queryByRole('button', { name: 'Set up again' }),
    ).not.toBeInTheDocument()
  })
})
