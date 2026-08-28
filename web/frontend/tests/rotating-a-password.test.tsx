import { QueryClient } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { CredentialsPage } from '@/features/infrastructure/CredentialsPage'
import { IperfPage } from '@/features/infrastructure/IperfPage'
import { changeLanguage } from '@/i18n'

/**
 * Changing a password cuts what a probe is using and hands it something else.
 *
 * On both pages it ran on a single click. On the endpoints page it also threw
 * the job id away and rendered no failure, so a password went out to every
 * probe measuring against that host and the screen did not change.
 */

const PERMISSIONS = ['credential.read', 'credential.rotate', 'iperf.read', 'iperf.manage']

const ACCOUNTS = [
  { username: 'mpp-berlin-01', is_shared: false, probe_enrolled: true },
  { username: 'mpp-spare', is_shared: false, probe_enrolled: false },
]

const ENDPOINTS = [
  {
    name: 'berlin',
    host: 'iperf.example.test',
    port: 5201,
    username: 'prtg-probe',
    kind: 'iperf3',
    updated_at: '2026-08-01T10:00:00Z',
    has_public_key: true,
    managed: true,
    deployed_to: ['mpp-berlin-01', 'mpp-hamburg-02'],
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
        permissions: PERMISSIONS,
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/credentials', () => HttpResponse.json(ACCOUNTS)),
  http.get('/api/v1/iperf-endpoints', () => HttpResponse.json(ENDPOINTS)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(page: 'credentials' | 'iperf') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={page === 'credentials' ? <CredentialsPage /> : <IperfPage />}
          />
          <Route path="/jobs/:jobId" element={<p>job page</p>} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('changing the password of a NATS account', () => {
  it('asks before it starts, and names the account', async () => {
    await changeLanguage('en')
    let called = 0
    server.use(
      http.post('/api/v1/credentials/mpp-berlin-01/rotate', () => {
        called += 1
        return HttpResponse.json({ job_id: 'J1', status: 'queued' }, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    wrap('credentials')

    const rows = await screen.findAllByRole('button', { name: 'Change password' })
    await user.click(rows[0])

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/mpp-berlin-01/)).toBeInTheDocument()
    // Nothing has been sent while the question is on screen.
    expect(called).toBe(0)

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(called).toBe(0)
  })

  it('follows the job once the question is answered', async () => {
    await changeLanguage('en')
    server.use(
      http.post('/api/v1/credentials/mpp-berlin-01/rotate', () =>
        HttpResponse.json({ job_id: 'J1', status: 'queued' }, { status: 202 }),
      ),
    )
    const user = userEvent.setup()
    wrap('credentials')

    const rows = await screen.findAllByRole('button', { name: 'Change password' })
    await user.click(rows[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(
      within(dialog).getByRole('button', { name: 'Change password' }),
    )

    expect(await screen.findByText('job page')).toBeInTheDocument()
  })
})

describe('changing the password of a measurement endpoint', () => {
  it('says how many probes it reaches before it runs', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap('iperf')

    await user.click(await screen.findByRole('button', { name: 'Change password' }))
    expect(await screen.findByText(/2 probe/)).toBeInTheDocument()
  })

  it('follows the job it starts', async () => {
    await changeLanguage('en')
    server.use(
      http.post('/api/v1/iperf-endpoints/berlin/rotate', () =>
        HttpResponse.json({ job_id: 'J2', status: 'queued' }, { status: 202 }),
      ),
    )
    const user = userEvent.setup()
    wrap('iperf')

    await user.click(await screen.findByRole('button', { name: 'Change password' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(
      within(dialog).getByRole('button', { name: 'Change password' }),
    )

    expect(await screen.findByText('job page')).toBeInTheDocument()
  })

  it('shows the failure it used to swallow', async () => {
    await changeLanguage('en')
    server.use(
      http.post('/api/v1/iperf-endpoints/berlin/rotate', () =>
        HttpResponse.json(
          {
            error: {
              code: 'common.conflict',
              message_key: 'errors.common.conflict',
              params: {},
              fields: [],
              details: 'another job holds this endpoint',
              correlation_id: 'C1',
              retryable: false,
            },
          },
          { status: 409 },
        ),
      ),
    )
    const user = userEvent.setup()
    wrap('iperf')

    await user.click(await screen.findByRole('button', { name: 'Change password' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(
      within(dialog).getByRole('button', { name: 'Change password' }),
    )

    expect(await screen.findByText('What failed')).toBeInTheDocument()
  })
})
