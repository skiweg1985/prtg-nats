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
 * The dashboard answers "is there anything to do?" with one list: a row per
 * kind of problem, each leading to the page where it is dealt with. The
 * counters stay below it for the overview.
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
    capabilities: { docker: true, runtime_state: 'complete' },
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

const BERLIN = {
  id: 'P1',
  nats_username: 'mpp-berlin-01',
  display_name: 'Berlin office',
  probe_name: 'berlin-01',
}

const UNREACHABLE_ALERT = {
  ...DASHBOARD.alerts[0],
  id: 'A2',
  kind: 'probe.unreachable',
  severity: 'critical',
  object_type: 'probe',
  object_ref: 'mpp-berlin-01',
  object_label: 'mpp-berlin-01',
}

function rowFor(text: string | RegExp) {
  return screen.getByText(text).closest('a') as HTMLAnchorElement
}

describe('the attention list', () => {
  it('lists an expiring certificate once, by name, and leads to the certificates', async () => {
    await changeLanguage('en')
    wrap()

    expect(await screen.findByText('Needs attention')).toBeInTheDocument()
    expect(screen.getByText('One certificate expires soon')).toBeInTheDocument()
    expect(screen.getByText('CN=nats.example.test')).toBeInTheDocument()
    expect(rowFor('One certificate expires soon')).toHaveAttribute(
      'href',
      '/infrastructure/certificates',
    )
    // The stored warning about the same certificate is not a second row.
    expect(screen.queryByText(/expires in 5 days/)).not.toBeInTheDocument()
  })

  it('names a single unreachable probe and opens it', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes', () => HttpResponse.json([BERLIN])),
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({
          ...DASHBOARD,
          probe_unreachable: 1,
          alerts: [UNREACHABLE_ALERT],
        }),
      ),
    )
    wrap()

    // The way the fleet list names it, not by its NATS account.
    expect(await screen.findByText('Berlin office is not answering')).toBeInTheDocument()
    expect(rowFor('Berlin office is not answering')).toHaveAttribute('href', '/probes/P1')
  })

  it('folds several probes with the same problem into one row', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes', () => HttpResponse.json([BERLIN])),
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({
          ...DASHBOARD,
          probe_unreachable: 2,
          alerts: [
            UNREACHABLE_ALERT,
            { ...UNREACHABLE_ALERT, id: 'A3', object_ref: 'mpp-hamburg-01' },
          ],
        }),
      ),
    )
    wrap()

    expect(await screen.findByText('2 probes are not answering')).toBeInTheDocument()
    expect(screen.getByText('Berlin office, mpp-hamburg-01')).toBeInTheDocument()
    expect(rowFor('2 probes are not answering')).toHaveAttribute(
      'href',
      '/probes?status=unreachable',
    )
  })

  it('trusts the live count over a warning the sync has not cleared yet', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({
          ...DASHBOARD,
          expiring_certificates: [],
          alerts: [UNREACHABLE_ALERT],
        }),
      ),
    )
    wrap()

    expect(await screen.findByText('Nothing needs attention.')).toBeInTheDocument()
    expect(screen.queryByText(/not answering/)).not.toBeInTheDocument()
  })

  it('leads failed jobs to the filtered job list', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({ ...DASHBOARD, failed_jobs_24h: 3 }),
      ),
    )
    wrap()

    expect(await screen.findByText('3 jobs failed in the last 24 hours')).toBeInTheDocument()
    expect(rowFor('3 jobs failed in the last 24 hours')).toHaveAttribute(
      'href',
      '/jobs?status=failed',
    )
  })
})

/**
 * A probe stuck mid-enrollment or never entered in PRTG used to be in no
 * number at all: "all good" showed over both.
 */
describe('the probes no status colour counts', () => {
  it('lists both, each leading to the probes it counts', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({ ...DASHBOARD, probe_pending: 1, probe_prtg_missing: 2 }),
      ),
    )
    wrap()

    expect(await screen.findByText('One probe has an open enrollment')).toBeInTheDocument()
    expect(rowFor('One probe has an open enrollment')).toHaveAttribute(
      'href',
      '/probes?status=pending&status=enrolled',
    )
    expect(rowFor('2 probes are not registered in PRTG yet')).toHaveAttribute(
      'href',
      '/probes?filter=prtg',
    )
  })

  it('keeps them away at zero', async () => {
    await changeLanguage('en')
    wrap()

    await screen.findByText('Needs attention')
    expect(screen.queryByText(/open enrollment/)).not.toBeInTheDocument()
    expect(screen.queryByText(/not registered in PRTG/)).not.toBeInTheDocument()
  })
})

/**
 * The certificates page sends anyone with a missing certificate to the
 * dashboard to initialise. A half-initialised runtime must find the action
 * there too, or that link leads nowhere.
 */
describe('the initialisation banner', () => {
  it.each(['missing', 'partial'])('offers to initialise a %s runtime', async (state) => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/dashboard', () =>
        HttpResponse.json({
          ...DASHBOARD,
          system: {
            ...DASHBOARD.system,
            capabilities: { docker: true, runtime_state: state },
          },
        }),
      ),
    )
    wrap()

    expect(
      await screen.findByText('This installation has not been initialised'),
    ).toBeInTheDocument()
  })

  it('stays away once the runtime is complete', async () => {
    await changeLanguage('en')
    wrap()

    await screen.findByText('Needs attention')
    expect(
      screen.queryByText('This installation has not been initialised'),
    ).not.toBeInTheDocument()
  })
})
