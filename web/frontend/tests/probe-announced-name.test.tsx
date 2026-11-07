import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { ProbeListPage } from '@/features/probes/ProbeListPage'
import { changeLanguage } from '@/i18n'

/**
 * The name stored with a probe is the one it announced when it was enrolled.
 * PRTG lets anyone rename the probe afterwards and never tells us, so calling
 * that value "the name in PRTG" shows a name PRTG may no longer know. What is
 * pinned here is that both places the value appears say what it really is,
 * and that the probe id is named as what identifies the probe.
 */

const SUMMARY = {
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
}

const PROBE_DETAIL = {
  summary: SUMMARY,
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
        permissions: ['probe.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json([SUMMARY])),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(PROBE_DETAIL)),
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

describe('the name a probe announced at enrollment', () => {
  it('is labelled as such on the probe page, not as the name in PRTG', async () => {
    await changeLanguage('en')
    wrap('/probes/P1')

    const label = await screen.findByText('Enrollment name')
    expect(label.closest('div')).toHaveTextContent('berlin-01')
    // The name in PRTG has a row of its own (#137); this one never claims it.
    expect(label.closest('div')).not.toHaveTextContent(/name in PRTG/i)
    expect(screen.getByText(/PRTG may call it something else.*probe id/i)).toBeInTheDocument()
  })

  it('says the same in German', async () => {
    await changeLanguage('de')
    wrap('/probes/P1')

    const label = await screen.findByText('Name bei der Einbindung')
    expect(label.closest('div')).not.toHaveTextContent(/Name in PRTG/)
    expect(screen.getByText(/In PRTG kann sie anders heißen.*Probe-Id/)).toBeInTheDocument()
  })

  it('is marked as the enrollment name where the probe list shows it', async () => {
    await changeLanguage('en')
    wrap('/probes')

    const name = await screen.findByText('berlin-01')
    expect(name).toHaveAttribute('title', expect.stringMatching(/^Enrollment name/))
  })

  it('leaves a display name somebody chose here unmarked', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([{ ...SUMMARY, display_name: 'Berlin office' }]),
      ),
    )
    wrap('/probes')

    const name = await screen.findByText('Berlin office')
    expect(name).not.toHaveAttribute('title')
  })
})
