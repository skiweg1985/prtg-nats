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
 * What the probe page says about how current it is, and what it offers.
 *
 * A probe that stopped answering, or answered hours ago, used to look like a
 * healthy one: green dots from the last answer and no word about its age.
 * The header carried four buttons with two primaries, and fixing deviations
 * named its versions only as checksums.
 */

const HOURS = 60 * 60_000

function detail(overrides: {
  summary?: Record<string, unknown>
  observed?: Record<string, unknown> | null
  management_route?: Record<string, unknown> | null
  sensors?: Record<string, unknown>[]
  deviations?: Record<string, unknown>[]
} = {}) {
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
      observed_at: new Date().toISOString(),
      stale: false,
      running_job_id: null,
      error_code: null,
      helper_version: 13,
      helper_outdated: false,
      prtg_registered: true,
      managed_over_tunnel: false,
      ...overrides.summary,
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
    observed:
      overrides.observed === null
        ? null
        : {
            observed_at: new Date().toISOString(),
            reachable: true,
            service: 'active',
            package_version: '3.10.0-1',
            hostname: 'berlin-probe-01',
            ca_sha256: 'aa',
            config_path: '/etc/paessler/mpprobe/config.yaml',
            probe_id: '11111111-2222-3333-4444-555555555555',
            probe_name: 'berlin-01',
            helper_version: 13,
            helper_sha256: 'bb',
            platform: 'linux-amd64',
            helper_outdated: false,
            error_code: null,
            error_details: null,
            ...overrides.observed,
          },
    sensors: overrides.sensors ?? [],
    deviations: overrides.deviations ?? [],
    notes: null,
    labels: {},
    prtg_registered_at: null,
    prtg_registered_by: null,
    management_route:
      overrides.management_route === undefined ? null : overrides.management_route,
    nats_route: null,
    return_channel: null,
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
        permissions: ['probe.read', 'probe.update', 'probe.delete', 'probe.reconcile'],
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

function serve(body: ReturnType<typeof detail>) {
  server.use(http.get('/api/v1/probes/P1', () => HttpResponse.json(body)))
}

function wrap(path = '/probes/P1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/probes/:probeId" element={<ProbeDetailPage />} />
          <Route path="/jobs/:jobId" element={<p>job page</p>} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

async function row(label: string): Promise<HTMLElement> {
  const term = await screen.findByText(label, { selector: 'dt' })
  return term.parentElement as HTMLElement
}

describe('a probe that does not answer', () => {
  it('names the last contact, the route and the next step', async () => {
    await changeLanguage('en')
    serve(
      detail({
        summary: { status: 'unreachable', service: 'unknown', ca_state: 'unknown' },
        observed: {
          reachable: false,
          service: 'unknown',
          error_code: 'probe.unreachable',
          error_details: 'ssh: connect to host 192.0.2.10 port 22: timed out',
        },
        management_route: {
          host: '198.51.100.7',
          via: 'tunnel',
          observed_at: new Date(Date.now() - 7 * HOURS).toISOString(),
          stale: true,
        },
      }),
    )
    const user = userEvent.setup()
    wrap()

    const banner = (await screen.findByText('The probe does not answer')).closest(
      '[role="status"]',
    ) as HTMLElement
    expect(within(banner).getByText(/Last contact 7 hours ago via Tunnel 198\.51\.100\.7/)).toBeInTheDocument()
    expect(within(banner).getByText(/reachable at 198\.51\.100\.7/)).toBeInTheDocument()
    // The raw error sits under diagnostics, one click away.
    expect(within(banner).queryByText(/timed out/)).not.toBeInTheDocument()
    await user.click(within(banner).getByRole('button', { name: 'Technical details' }))
    expect(await screen.findByText(/timed out/)).toBeInTheDocument()
  })

  it('says when it has never answered at all', async () => {
    await changeLanguage('de')
    serve(
      detail({
        summary: { status: 'unreachable', service: 'unknown', ca_state: 'unknown' },
        observed: { reachable: false, service: 'unknown' },
      }),
    )
    wrap()

    expect(await screen.findByText(/Sie hat noch nie geantwortet/)).toBeInTheDocument()
    expect(screen.getByText(/unter 192\.0\.2\.10 erreichbar/)).toBeInTheDocument()
  })
})

describe('a probe whose last answer is old', () => {
  it('marks the values as last known instead of drawing them green', async () => {
    await changeLanguage('de')
    const then = new Date(Date.now() - 7 * HOURS).toISOString()
    serve(
      detail({
        summary: { stale: true, observed_at: then },
        observed: { observed_at: then },
      }),
    )
    wrap()

    expect(await screen.findByText('Diese Werte sind nicht mehr aktuell')).toBeInTheDocument()
    expect(screen.getByText('Stand vor 7 Stunden')).toBeInTheDocument()
    expect(screen.queryByText(/Zwischenspeicher/)).not.toBeInTheDocument()

    // Still readable, no longer green.
    const service = await row('Dienst')
    expect(within(service).getByText('Aktiv')).toBeInTheDocument()
    expect(service.querySelector('.bg-ok')).toBeNull()
  })

  it('keeps the green for a probe that answered just now', async () => {
    await changeLanguage('en')
    serve(detail())
    wrap()

    const service = await row('Service')
    expect(service.querySelector('.bg-ok')).not.toBeNull()
    expect(screen.queryByText('These values are no longer current')).not.toBeInTheDocument()
  })
})

describe('the actions in the header', () => {
  it('shows two actions and keeps the check report in the menu', async () => {
    await changeLanguage('en')
    serve(detail())
    const user = userEvent.setup()
    wrap()

    expect(await screen.findByRole('button', { name: 'Refresh state' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply configuration' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Run check/ })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: 'Run check as job' })).toBeInTheDocument()
  })

  it('puts the helper update in front when the probe needs it', async () => {
    await changeLanguage('en')
    serve(detail({ summary: { helper_outdated: true, helper_version: 9 }, observed: { helper_version: 9 } }))
    const user = userEvent.setup()
    wrap()

    expect(await screen.findByRole('button', { name: 'Update helper' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply configuration' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: 'Apply configuration' })).toBeInTheDocument()
  })

  it('names the probe in the unenroll confirmation', async () => {
    await changeLanguage('de')
    serve(detail())
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Weitere Aktionen' }))
    await user.click(screen.getByRole('menuitem', { name: 'Ausbinden' }))
    expect(screen.getByRole('dialog', { name: 'berlin-01 ausbinden?' })).toBeInTheDocument()
  })
})

describe('deviations and their fix', () => {
  it('reads as a sentence and shows the versions before anything runs', async () => {
    await changeLanguage('en')
    let executed = false
    serve(
      detail({
        sensors: [
          {
            name: 'dns-resolve',
            status: 'outdated',
            desired_version: '5',
            installed_version: '4',
            installed_sha256: null,
            expected_sha256: null,
            installed_helper_sha256: null,
            interfaces: [],
            helper_state: null,
            tool_name: null,
          },
        ],
        deviations: [
          {
            kind: 'sensor_outdated',
            severity: 'warning',
            object_type: 'sensor',
            object_ref: 'dns-resolve',
            expected: '5',
            actual: '4',
            remediation: 'sensor.deploy',
            params: { probe: 'mpp-berlin-01', sensor: 'dns-resolve' },
          },
        ],
      }),
    )
    server.use(
      http.post('/api/v1/probes/P1/reconcile', ({ request }) => {
        if (new URL(request.url).searchParams.get('dry_run') === 'false') {
          executed = true
          return HttpResponse.json(
            { job_id: 'J9', status: 'queued', events_url: '/api/v1/jobs/J9/events' },
            { status: 202 },
          )
        }
        return HttpResponse.json({
          probe_username: 'mpp-berlin-01',
          deviations: [],
          actions: [
            {
              kind: 'deploy_sensor',
              target: 'dns-resolve',
              description_key: 'plan.deploy_sensor',
              params: { probe: 'mpp-berlin-01', sensor: 'dns-resolve', version: '5' },
              restarts_service: false,
              risk_key: null,
            },
          ],
          restarts_service: false,
          is_empty: false,
        })
      }),
    )
    const user = userEvent.setup()
    wrap('/probes/P1?tab=deviations')

    expect(await screen.findByText('Sensor dns-resolve is outdated (4 → 5)')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Fix deviations' }))
    expect(await screen.findByText('Bring dns-resolve from version 4 to 5')).toBeInTheDocument()
    expect(executed).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Fix deviations now' }))
    expect(await screen.findByText('job page')).toBeInTheDocument()
    expect(executed).toBe(true)
  })
})

describe('the diagnostics tab', () => {
  it('labels its values in words, not field names', async () => {
    await changeLanguage('de')
    serve(detail())
    wrap('/probes/P1?tab=diagnostics')

    expect(await screen.findByText('Konfigurationsdatei')).toBeInTheDocument()
    expect(screen.getByText('CA-Fingerabdruck (SHA-256)')).toBeInTheDocument()
    expect(screen.queryByText('ca_sha256')).not.toBeInTheDocument()
  })
})
