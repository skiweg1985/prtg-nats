import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { InviteDialog } from '@/features/infrastructure/IperfEndpointDialogs'
import { changeLanguage } from '@/i18n'

/**
 * The whole backend for this - tokens, bootstrap script, callback, worker -
 * existed with tests, and could only be reached with curl. This is the third
 * way onto the endpoint list, for the host this platform cannot SSH to.
 */

const INVITATION = {
  id: '01IPF',
  kind: 'iperf',
  name: 'filiale-sued',
  expected_host: null,
  iperf_port: 5201,
  username: 'prtg-probe',
  ssh_source_cidr: null,
  expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  created_by_name: 'admin',
  redeemed_at: null,
  revoked_at: null,
  source_ip: null,
  job_id: null,
}

const ISSUED = {
  ...INVITATION,
  token: 'secret-token',
  command: 'curl -fsSL http://nats.example.test/enroll/T/iperf-bootstrap.sh | sudo bash',
  ca_sha256: 'e7b40c61ca52b201eb3a6b7d57083067283d42a9265c828cebea574796df35a2',
}

let invitation: Record<string, unknown> | null = null
let createdBodies: Record<string, unknown>[] = []

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
        permissions: ['iperf.read', 'iperf.manage', 'job.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.post('/api/v1/iperf-endpoints/enrollment/tokens', async ({ request }) => {
    createdBodies.push((await request.json()) as Record<string, unknown>)
    invitation = { ...INVITATION }
    return HttpResponse.json(ISSUED, { status: 201 })
  }),
  http.get('/api/v1/iperf-endpoints/enrollment/tokens/:id', () =>
    invitation === null
      ? HttpResponse.json(
          { error: { code: 'enrollment.token_invalid' } },
          { status: 404 },
        )
      : HttpResponse.json(invitation),
  ),
  http.get('/api/v1/jobs/:id', ({ params }) =>
    HttpResponse.json({
      id: params.id,
      type: 'iperf.enroll',
      status: 'successful',
      steps: [{ name: 'write_record', status: 'succeeded' }],
      created_at: new Date().toISOString(),
    }),
  ),
  http.get('/api/v1/jobs/:id/log', () => HttpResponse.json([])),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  invitation = null
  createdBodies = []
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <InviteDialog onClose={() => {}} />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('enrolling an iperf endpoint by invitation', () => {
  it('shows the command with the fingerprint and the secret warning', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('filiale-sued'), 'filiale-sued')
    await user.click(screen.getByRole('button', { name: /create the invitation/i }))

    expect(await screen.findByText(ISSUED.command)).toBeInTheDocument()
    expect(screen.getByText(ISSUED.ca_sha256)).toBeInTheDocument()
    expect(screen.getByText(/treat the command as a secret/i)).toBeInTheDocument()
    expect(createdBodies[0]).toMatchObject({
      name: 'filiale-sued',
      ttl_minutes: 60,
    })
  })

  it('moves on by itself when the host reports in, and links the endpoint', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('filiale-sued'), 'filiale-sued')
    await user.click(screen.getByRole('button', { name: /create the invitation/i }))
    await screen.findByText(/waiting for the host/i)

    invitation = {
      ...INVITATION,
      redeemed_at: new Date().toISOString(),
      job_id: 'JOB9',
    }

    await waitFor(
      () => expect(screen.getByText('The endpoint is set up')).toBeInTheDocument(),
      { timeout: 6000 },
    )
    expect(screen.getByRole('link', { name: /to the endpoint/i })).toHaveAttribute(
      'href',
      '/infrastructure/iperf/filiale-sued',
    )
  }, 10_000)

  it('stops waiting once the invitation can no longer be used', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('filiale-sued'), 'filiale-sued')
    await user.click(screen.getByRole('button', { name: /create the invitation/i }))
    await screen.findByText(/waiting for the host/i)

    invitation = { ...INVITATION, revoked_at: new Date().toISOString() }

    await waitFor(
      () =>
        expect(
          screen.getByText(/can no longer be used/i),
        ).toBeInTheDocument(),
      { timeout: 6000 },
    )
    expect(screen.queryByText(/waiting for the host/i)).not.toBeInTheDocument()
  }, 10_000)
})
