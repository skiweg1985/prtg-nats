import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { RolloutsView } from '@/features/deployments/RolloutsView'
import { changeLanguage } from '@/i18n'

/**
 * "3 of 5 probes succeeded" is the wrong half of the answer.
 *
 * Every field below was already recorded per target when the rollout ran and
 * had nowhere to be seen: which probe failed, with which code, and when it
 * stopped. The row opens onto exactly that.
 */

const DEPLOYMENTS = [
  {
    id: 'D1',
    sensor_name: 'wlan-auth',
    sensor_version: '2.0.0',
    status: 'partially_successful',
    job_id: 'J1',
    dry_run: false,
    requested_by_name: 'admin',
    created_at: '2026-08-27T10:00:00Z',
    targets: [
      {
        probe_id: 'P1',
        probe_label: 'mpp-berlin-01',
        status: 'successful',
        previous_version: '1.0.0',
        error_code: null,
        error_details: null,
        finished_at: '2026-08-27T10:01:00Z',
      },
      {
        probe_id: 'P2',
        probe_label: 'mpp-hamburg-01',
        status: 'failed',
        previous_version: null,
        error_code: 'probe.unreachable',
        error_details: 'ssh: connect to host 192.0.2.11 port 22: No route to host',
        finished_at: '2026-08-27T10:02:00Z',
      },
    ],
  },
]

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
        permissions: ['deployment.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/deployments', () => HttpResponse.json(DEPLOYMENTS)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/deployments']}>
        <Routes>
          <Route path="/deployments" element={<RolloutsView />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('the targets of a rollout', () => {
  it('names the probe that failed, and why', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    // Closed, the row says how many - which is what the list is for.
    expect(await screen.findByText('1 of 2 probes succeeded')).toBeInTheDocument()
    expect(screen.queryByText('mpp-hamburg-01')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Show details' }))

    expect(screen.getByText('mpp-berlin-01')).toBeInTheDocument()
    expect(screen.getByText('mpp-hamburg-01')).toBeInTheDocument()
    // The translated reason - naming the probe it is about - and the
    // machine's own words underneath it.
    expect(
      screen.getByText('The probe "mpp-hamburg-01" did not answer over the management channel.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/No route to host/)).toBeInTheDocument()
  })

  it('says what version the probe came from, and where the sensor lives', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByText('1 of 2 probes succeeded')
    await user.click(screen.getByRole('button', { name: 'Show details' }))

    // The previous_version column existed since the initial schema; this is
    // the first place it is readable.
    expect(screen.getByText('v1.0.0 → v2.0.0')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /To sensor wlan-auth/ })).toHaveAttribute(
      'href',
      '/sensors/wlan-auth',
    )
  })
})
