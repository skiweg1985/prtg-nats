import { QueryClient } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { ProbeListPage } from '@/features/probes/ProbeListPage'
import { changeLanguage } from '@/i18n'

/**
 * Which route a probe is reached over. After a branch office lost power and
 * its probe came back on a new address, whether management still got through
 * - and over which address - was only in the API log. The page answers it in
 * two lines, the list marks the probes management reaches over the tunnel.
 */

function summary(overrides: Record<string, unknown> = {}) {
  return {
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
    helper_version: 8,
    helper_outdated: false,
    prtg_registered: true,
    managed_over_tunnel: false,
    ...overrides,
  }
}

function detail(overrides: Record<string, unknown> = {}) {
  return {
    summary: summary(),
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
    ...overrides,
  }
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
        permissions: ['probe.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/probes" element={<ProbeListPage />} />
          <Route path="/probes/:probeId" element={<ProbeDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

function row(label: string): HTMLElement {
  const term = screen.getByText(label, { selector: 'dt' })
  return term.parentElement as HTMLElement
}

describe('the routes on the probe page', () => {
  it('names the management route with its address and the NATS route', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(
          detail({
            management_route: {
              host: '10.99.0.2',
              via: 'tunnel',
              observed_at: new Date().toISOString(),
              stale: false,
            },
            nats_route: 'direct',
          }),
        ),
      ),
    )
    wrap('/probes/P1')

    await screen.findByText('Management via', { selector: 'dt' })
    const management = row('Management via')
    expect(within(management).getByText('Tunnel')).toBeInTheDocument()
    expect(within(management).getByText('10.99.0.2')).toBeInTheDocument()
    expect(management.querySelector('dd > span')).not.toHaveClass('text-ink-3')
    expect(within(row('NATS via')).getByText('Direct')).toBeInTheDocument()
    // The site address sits under the route as its fallback, and nowhere
    // else on the card: there is no separate host row any more.
    expect(
      within(management).getByText('Fallback: site address 198.51.100.29'),
    ).toBeInTheDocument()
    expect(screen.getAllByText(/198\.51\.100\.29/)).toHaveLength(1)
    expect(screen.queryByText('Management host', { selector: 'dt' })).not.toBeInTheDocument()
  })

  it('dims a route older than the staleness window', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes/P1', () =>
        HttpResponse.json(
          detail({
            management_route: {
              host: '198.51.100.29',
              via: 'site',
              observed_at: '2026-01-01T10:00:00Z',
              stale: true,
            },
            nats_route: 'tunnel',
          }),
        ),
      ),
    )
    wrap('/probes/P1')

    await screen.findByText('Management via', { selector: 'dt' })
    const management = row('Management via')
    expect(within(management).getByText('Site address')).toBeInTheDocument()
    expect(management.querySelector('dd > span')).toHaveClass('text-ink-3')
    expect(within(row('NATS via')).getByText('Tunnel')).toBeInTheDocument()
  })

  it('says so when management has not reached the probe yet', async () => {
    await changeLanguage('de')
    server.use(http.get('/api/v1/probes/P1', () => HttpResponse.json(detail())))
    wrap('/probes/P1')

    await screen.findByText('Management über', { selector: 'dt' })
    expect(
      within(row('Management über')).getByText('Noch nicht erreicht'),
    ).toBeInTheDocument()
    expect(within(row('Management über')).getByText('198.51.100.29')).toBeInTheDocument()
    expect(within(row('NATS über')).getByText('Unbekannt')).toBeInTheDocument()
  })
})

describe('the probe list', () => {
  it('marks the probes managed over the tunnel', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([
          summary({ id: 'P1', nats_username: 'mpp-berlin-01', managed_over_tunnel: true }),
          summary({ id: 'P2', nats_username: 'mpp-hamburg-01', probe_name: 'hamburg-01' }),
        ]),
      ),
    )
    wrap('/probes')

    const berlin = (await screen.findByText('mpp-berlin-01')).closest('tr')
    const hamburg = screen.getByText('mpp-hamburg-01').closest('tr')
    expect(within(berlin as HTMLElement).getByText('Tunnel')).toBeInTheDocument()
    expect(within(hamburg as HTMLElement).queryByText('Tunnel')).toBeNull()
  })
})
