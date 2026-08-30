import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { IperfPage } from '@/features/infrastructure/IperfPage'
import { changeLanguage } from '@/i18n'

/**
 * Setting up a measurement endpoint means signing in to somebody's machine as
 * an administrator. What the tests below pin down is the order that makes that
 * acceptable: the host's keys are read and shown first, and the fields that
 * carry a credential do not exist until somebody has looked at them.
 */

let scanned = 0
let provisioned: unknown = null
let assignment: { action: string; body: unknown } | null = null

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
    holders: [
      {
        probe: 'mpp-berlin',
        endpoints_held: 1,
        uses_default_alias: true,
        parameter_line: '',
      },
    ],
  },
  {
    name: 'provider',
    host: 'iperf.provider.example',
    port: 5201,
    username: 'customer',
    kind: 'iperf3',
    updated_at: '2026-08-01T10:00:00Z',
    has_public_key: true,
    managed: false,
    holders: [],
  },
]

const PROBES = [
  { id: 'P1', nats_username: 'mpp-berlin', display_name: 'Berlin', host: 'a.example' },
  { id: 'P2', nats_username: 'mpp-hamburg', display_name: 'Hamburg', host: 'b.example' },
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
        permissions: ['iperf.read', 'iperf.manage', 'sensor.deploy'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/iperf-endpoints', () => HttpResponse.json(ENDPOINTS)),
  http.get('/api/v1/probes', () => HttpResponse.json(PROBES)),
  http.post('/api/v1/iperf-endpoints/:name/:action', async ({ request, params }) => {
    if (params.action !== 'deploy' && params.action !== 'revoke') {
      return new HttpResponse(null, { status: 404 })
    }
    assignment = { action: String(params.action), body: await request.json() }
    return HttpResponse.json(
      { job_id: 'J2', status: 'queued', events_url: '/api/v1/jobs/J2/events' },
      { status: 202 },
    )
  }),
  http.post('/api/v1/iperf-endpoints/host-keys', () => {
    scanned += 1
    return HttpResponse.json({
      host: 'iperf.example.test',
      ssh_port: 22,
      already_pinned: false,
      keys: [
        {
          line: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample',
          algorithm: 'ssh-ed25519',
          fingerprint: 'SHA256:2f1c9d3bExampleFingerprintValue',
        },
      ],
    })
  }),
  http.post('/api/v1/iperf-endpoints', async ({ request }) => {
    provisioned = await request.json()
    return HttpResponse.json(
      { job_id: 'J1', status: 'queued', events_url: '/api/v1/jobs/J1/events' },
      { status: 202 },
    )
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  scanned = 0
  provisioned = null
  assignment = null
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <IperfPage />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('IperfPage', () => {
  it('marks an endpoint somebody else operates', async () => {
    await changeLanguage('en')
    wrap()

    expect(await screen.findByText('berlin')).toBeInTheDocument()
    expect(screen.getByText('provider')).toBeInTheDocument()
    // Its password is not ours to rotate, so the button is not offered.
    const rotate = screen.getAllByRole('button', { name: /change password/i })
    expect(rotate).toHaveLength(1)
  })

  it('leads to the endpoint, and says which one nothing measures against', async () => {
    await changeLanguage('en')
    wrap()

    // The row is the way in: what a PRTG object needs is per probe, so the
    // answer cannot be a column here.
    expect(await screen.findByRole('link', { name: 'berlin' })).toHaveAttribute(
      'href',
      '/infrastructure/iperf/berlin',
    )
    const notDeployed = screen.getAllByText('not deployed')
    expect(notDeployed).toHaveLength(1)
    expect(notDeployed[0].closest('tr')).toHaveTextContent('provider')
  })

  it('asks for no credential before the host keys have been seen', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: /set up endpoint/i }))

    // The form starts with what identifies the host, and nothing that
    // authenticates: an administrator password typed now would travel to an
    // address nobody has verified.
    expect(screen.queryByLabelText(/^password$/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/administrator/i)).not.toBeInTheDocument()
    // Announced, though. Which step asks for the sign-in used to be something
    // you found out by pressing the button.
    expect(screen.getByText('2. Sign in once and set it up')).toBeInTheDocument()
    expect(screen.getByText(/asks for an account that may become root/)).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('berlin'), 'berlin')
    await user.type(screen.getByPlaceholderText('iperf.example.com'), 'iperf.example.test')
    await user.click(screen.getByRole('button', { name: /read host keys/i }))

    await waitFor(() => expect(scanned).toBe(1))
    // Only now, and with the fingerprint on screen next to them.
    expect(await screen.findByText(/SHA256:2f1c9d3b/)).toBeInTheDocument()
    expect(screen.getByLabelText(/administrator/i)).toBeInTheDocument()
  })

  it('sends the accepted keys along with the sign-in', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: /set up endpoint/i }))
    await user.type(screen.getByPlaceholderText('berlin'), 'berlin')
    await user.type(screen.getByPlaceholderText('iperf.example.com'), 'iperf.example.test')
    await user.click(screen.getByRole('button', { name: /read host keys/i }))
    await screen.findByText(/SHA256:2f1c9d3b/)

    await user.type(screen.getByLabelText(/^password$/i), 'hunter2')
    await user.type(screen.getByLabelText(/source network/i), '203.0.113.7/32')
    await user.click(screen.getByRole('button', { name: /^set up$/i }))

    await waitFor(() => expect(provisioned).not.toBeNull())
    // The keys travel with the request: the server pins them before it signs
    // in, which is what makes the acceptance above mean anything.
    expect(provisioned).toMatchObject({
      name: 'berlin',
      host: 'iperf.example.test',
      host_keys: ['ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample'],
      ssh_source_cidr: '203.0.113.7/32',
      admin: { username: 'root', password: 'hunter2' },
    })
  })
  it('opens the probe list from the count, and offers only the direction that applies', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    // The count in the "deployed to" column is the way in. It used to be text
    // nobody could act on, which left revoking to a terminal.
    await screen.findByText('berlin')
    await user.click(screen.getAllByRole('button', { name: '1' })[0])

    expect(await screen.findByText('Hamburg')).toBeInTheDocument()
    // Nothing is selected yet, so neither direction has anything to do.
    expect(screen.getByRole('button', { name: /deploy to 0/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /revoke from 0/i })).toBeDisabled()

    await user.click(screen.getByRole('checkbox', { name: /hamburg/i }))
    // Hamburg does not hold it, so this can only be a deployment.
    expect(screen.getByRole('button', { name: /deploy to 1/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /revoke from 0/i })).toBeDisabled()
  })

  it('sends a revoke for the probes that already hold the endpoint', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByText('berlin')
    await user.click(screen.getAllByRole('button', { name: '1' })[0])
    await screen.findByText('Berlin')

    await user.click(screen.getByRole('checkbox', { name: /berlin/i }))
    await user.click(screen.getByRole('button', { name: /revoke from 1/i }))

    await waitFor(() => expect(assignment).not.toBeNull())
    // Only the probe that was picked, and only the direction that applies to
    // it: the two are separate jobs because they can fail independently.
    expect(assignment).toEqual({
      action: 'revoke',
      body: { probes: ['mpp-berlin'] },
    })
  })

  it('hands out what the far side has to run, in the dialog that asks for the result', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: /register a foreign one/i }))

    // Collapsed to start with: whoever already has the password and the key
    // should not have to scroll past a shell script to enter them.
    const help = screen.getByText(/how the operator sets this endpoint up/i)
    const box = help.closest('details')
    expect(box).not.toBeNull()
    expect(box).not.toHaveAttribute('open')

    await user.click(help)
    expect(box).toHaveAttribute('open')

    // The two lines that decide whether the endpoint the record describes can
    // authenticate a probe at all: the hash iperf3 reads, and the service
    // actually started with the credentials.
    expect(screen.getByText(/\{\$IPERF_USER\}\$PASSWORD/)).toBeInTheDocument()
    expect(screen.getByText(/--authorized-users-path/)).toBeInTheDocument()
    expect(screen.getByText(/--rsa-private-key-path/)).toBeInTheDocument()
    // And what has to come back, next to the fields that take it.
    expect(screen.getByText(/only its hash is on disk/)).toBeInTheDocument()
  })
})
