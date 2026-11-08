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

describe('the stored name of a probe not linked to PRTG', () => {
  it('heads the page and is not repeated as a row of its own', async () => {
    await changeLanguage('en')
    wrap('/probes/P1')

    expect(await screen.findByRole('heading', { name: 'berlin-01' })).toBeInTheDocument()
    expect(screen.getAllByText('berlin-01')).toHaveLength(1)
    expect(screen.queryByText('Enrollment name')).not.toBeInTheDocument()
  })

  it('is the name the list shows, without a hover note', async () => {
    await changeLanguage('en')
    wrap('/probes')

    const name = await screen.findByText('berlin-01')
    expect(name).not.toHaveAttribute('title')
  })

  it('gives way to a display name somebody chose here', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([{ ...SUMMARY, display_name: 'Berlin office' }]),
      ),
    )
    wrap('/probes')

    expect(await screen.findByText('Berlin office')).toBeInTheDocument()
    expect(screen.queryByText('berlin-01')).not.toBeInTheDocument()
  })
})
