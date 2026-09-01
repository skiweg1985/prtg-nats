import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { changeLanguage } from '@/i18n'

/**
 * A certificate about to expire is one problem, and it has a card of its own.
 *
 * The warnings card left those out of its list and then decided whether to
 * draw itself from the unfiltered count - so a run of certificate warnings
 * put an empty card on the dashboard.
 */

const CERTIFICATE = {
  kind: 'server',
  subject: 'CN=nats.example.test',
  issuer: 'CN=prtg-nats CA',
  not_after: '2026-09-01T00:00:00Z',
  days_remaining: 5,
  sha256: 'aa',
  subject_alt_names: [],
  status: 'expiring_soon',
  key_matches: true,
}

const DASHBOARD = {
  system: {
    site: { nats_endpoint: 'nats://nats.example.test:4222', prtg_core_ip: null, is_configured: true },
    nats: {
      available: true,
      healthy: true,
      connections: 2,
      server_name: 'nats',
      version: '2.10.0',
      uptime: '1d',
      jetstream: null,
    },
    capabilities: { docker: true, runtime_state: 'ready' },
    containers: [],
  },
  probe_total: 1,
  probe_healthy: 1,
  probe_degraded: 0,
  probe_unreachable: 0,
  probe_pending: 0,
  probe_prtg_missing: 0,
  probes_with_deviations: 0,
  failed_jobs_24h: 0,
  running_jobs: 0,
  expiring_certificates: [CERTIFICATE],
  alerts: [
    {
      id: 'A1',
      kind: 'certificate.expiring_soon',
      severity: 'warning',
      object_type: 'certificate',
      object_ref: 'server',
      object_label: 'server',
      params: {},
      first_seen_at: '2026-08-27T10:00:00Z',
      last_seen_at: '2026-08-27T10:00:00Z',
      acknowledged_at: null,
    },
  ],
  recent_jobs: [],
  recent_audit: [],
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
        permissions: ['system.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/dashboard', () => HttpResponse.json(DASHBOARD)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('the warnings card', () => {
  it('stays away when every warning already has its own card', async () => {
    await changeLanguage('en')
    wrap()

    expect(await screen.findByText('Expiring certificates')).toBeInTheDocument()
    expect(screen.queryByText('Active warnings')).not.toBeInTheDocument()
  })

  it('appears for a warning that is about a probe', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({
          ...DASHBOARD,
          alerts: [
            ...DASHBOARD.alerts,
            {
              ...DASHBOARD.alerts[0],
              id: 'A2',
              kind: 'probe.unreachable',
              severity: 'critical',
              object_type: 'probe',
              object_label: 'mpp-berlin-01',
            },
          ],
        }),
      ),
    )
    wrap()

    expect(await screen.findByText('Active warnings')).toBeInTheDocument()
  })
})

/**
 * A probe stuck mid-enrollment or never entered in PRTG used to be in no
 * number at all: "all good" showed over both.
 */
describe('the probes no status colour counts', () => {
  it('shows the two catch-up tiles only when they are non-zero', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({ ...DASHBOARD, probe_pending: 1, probe_prtg_missing: 2 }),
      ),
    )
    wrap()

    expect(await screen.findByText('Enrollment open')).toBeInTheDocument()
    expect(screen.getByText('PRTG missing')).toBeInTheDocument()
  })

  it('keeps them away at zero', async () => {
    await changeLanguage('en')
    wrap()

    await screen.findByText(/certificate/i)
    expect(screen.queryByText('Enrollment open')).not.toBeInTheDocument()
    expect(screen.queryByText('PRTG missing')).not.toBeInTheDocument()
  })
})
