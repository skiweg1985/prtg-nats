import { QueryClient } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { OverlayPage } from '@/features/infrastructure/OverlayPage'
import { changeLanguage } from '@/i18n'

/**
 * What this page has to get right is not the list of peers - it is the
 * difference between a mode and what a probe is doing with it. A probe set to
 * "auto" that is on the tunnel is working, and it also means somebody's
 * ordinary route is down; a row that reads the same as a healthy one would
 * hide exactly the thing the overlay was built to surface.
 */

let enabled = true
let modeRequests: unknown[] = []
let enableRequests: unknown[] = []
let permissions = ['overlay.read', 'overlay.manage', 'overlay.enable', 'probe.read']

const PEERS = [
  {
    nats_username: 'mpp-berlin',
    address: '10.83.1.0',
    public_key: 'A'.repeat(43) + '=',
    mode: 'auto',
    last_state: 'direct',
  },
  {
    nats_username: 'mpp-hamburg',
    address: '10.83.1.1',
    public_key: 'B'.repeat(43) + '=',
    mode: 'auto',
    last_state: 'tunnel',
  },
  {
    nats_username: 'mpp-koeln',
    address: '10.83.1.2',
    public_key: 'C'.repeat(43) + '=',
    mode: 'on',
    last_state: 'no_handshake',
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
        permissions,
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/overlay', () =>
    HttpResponse.json({
      enabled,
      endpoint: 'nats.example.test:51820',
      endpoint_host: 'nats.example.test',
      port: 51820,
      subnet: '10.83.0.0/16',
      hub_address: '10.83.0.1',
      hub_public_key: 'D'.repeat(43) + '=',
      default_mode: 'auto',
      interface_up: true,
      peers: PEERS,
    }),
  ),
  http.get('/api/v1/probes', () =>
    HttpResponse.json(
      PEERS.map((peer, index) => ({
        id: `P${index}`,
        nats_username: peer.nats_username,
        display_name: null,
        host: 'probe.example.test',
        probe_name: null,
        status: 'ok',
        service: 'active',
        package_version: '2.1.0',
        ca_state: 'current',
        nats_connection: 'connected',
        sensor_count: 0,
        deviation_count: 0,
        observed_at: '2026-08-31T10:00:00Z',
        stale: false,
        running_job_id: null,
        error_code: null,
        helper_version: 9,
        helper_outdated: false,
        prtg_registered: true,
      })),
    ),
  ),
  http.post('/api/v1/overlay/enable', async ({ request }) => {
    enableRequests.push(await request.json())
    return HttpResponse.json({ enabled: true })
  }),
  http.post('/api/v1/overlay/peers/mode', async ({ request }) => {
    modeRequests.push(await request.json())
    return HttpResponse.json(
      { job_id: 'J1', status: 'queued', events_url: '/api/v1/jobs/J1/events' },
      { status: 202 },
    )
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  enabled = true
  modeRequests = []
  enableRequests = []
  permissions = ['overlay.read', 'overlay.manage', 'overlay.enable', 'probe.read']
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <OverlayPage />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('the overlay page', () => {
  it('separates the mode from the path the probe is actually on', async () => {
    await changeLanguage('en')
    wrap()

    // Berlin and Hamburg are both in auto. Only one of them is on the tunnel,
    // and that is the row worth noticing.
    expect(await screen.findByText('mpp-berlin')).toBeInTheDocument()
    const table = within(screen.getByRole('table'))
    expect(table.getAllByText('Auto')).toHaveLength(2)
    expect(table.getByText('Direct')).toBeInTheDocument()
    expect(table.getByText('Tunnel')).toBeInTheDocument()
    expect(table.getByText('No handshake')).toBeInTheDocument()
  })

  it('offers to turn the overlay on rather than naming a shell command', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    enabled = false
    wrap()

    expect(
      await screen.findByText(/overlay is off for this installation/i),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Turn the overlay on' }))
    await user.type(screen.getByPlaceholderText('nats.example.com'), 'vpn.example.com')
    await user.click(screen.getByRole('button', { name: 'Turn on' }))

    expect(enableRequests).toEqual([{ endpoint_host: 'vpn.example.com' }])
  })

  it('hides the switch from anyone who may not press it', async () => {
    await changeLanguage('en')
    enabled = false
    permissions = ['overlay.read', 'overlay.manage', 'probe.read']
    wrap()

    expect(
      await screen.findByText(/overlay is off for this installation/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Turn the overlay on' }),
    ).not.toBeInTheDocument()
  })

  it('warns before switching a probe off, because that is the way back out', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByLabelText('mpp-berlin'))
    await user.click(screen.getByRole('button', { name: 'Change mode' }))
    await user.click(screen.getByRole('radio', { name: /Off/ }))

    expect(screen.getByText(/does not answer there is refused/)).toBeInTheDocument()
    expect(modeRequests).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Apply' }))
    // No force flag: the interface never asks the backend to skip the check
    // that keeps a probe from being switched off through the tunnel that the
    // switch takes down. That override is a command-line decision.
    expect(modeRequests).toEqual([{ probe_ids: ['P0'], mode: 'off' }])
  })
})
