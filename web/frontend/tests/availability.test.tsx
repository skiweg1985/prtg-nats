import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { AvailabilityPage } from '@/features/watch/AvailabilityPage'
import { parseLabels } from '@/features/watch/DeviceDialog'
import { changeLanguage } from '@/i18n'

/**
 * The dashboard a support desk reads.
 *
 * Two things it has to get right, and both are about not lying: a device
 * whose probe stopped reporting is unknown rather than green, and a page
 * that is receiving nothing at all says so instead of showing three hundred
 * unknown devices with no explanation.
 */

function device(overrides: Record<string, unknown> = {}) {
  return {
    id: 'D1',
    display_name: 'Till printer 1',
    address: '10.10.0.31',
    probe_id: 'P1',
    probe_name: 'Hamburg',
    method: 'icmp',
    port: null,
    labels: { team: 'support', site: 'hamburg' },
    enabled: true,
    failure_threshold: 3,
    notes: null,
    state: 'up',
    observed_at: '2026-09-01T09:59:00Z',
    rtt_ms: 1.4,
    error: null,
    stale: false,
    ...overrides,
  }
}

let overview = {
  devices: [device()],
  up: 1,
  down: 0,
  unknown: 0,
  labels: { team: ['kasse', 'support'], site: ['hamburg'] },
  receiving: true,
}

let posted: Record<string, unknown>[] = []

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
        permissions: ['watch.read', 'watch.manage', 'probe.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/watch/overview', ({ request }) => {
    const labels = new URL(request.url).searchParams.getAll('label')
    if (labels.length === 0) return HttpResponse.json(overview)
    const devices = overview.devices.filter((entry) =>
      labels.every((pair) => {
        const [key, value] = pair.split(':')
        return (entry.labels as Record<string, string>)[key] === value
      }),
    )
    return HttpResponse.json({ ...overview, devices, up: devices.length })
  }),
  http.get('/api/v1/watch/outages', () => HttpResponse.json([])),
  http.get('/api/v1/probes', () =>
    HttpResponse.json([
      { id: 'P1', nats_username: 'mpp-hamburg-01', display_name: 'Hamburg' },
    ]),
  ),
  http.post('/api/v1/watch/devices', async ({ request }) => {
    posted.push((await request.json()) as Record<string, unknown>)
    return HttpResponse.json(device())
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  posted = []
  overview = {
    devices: [device()],
    up: 1,
    down: 0,
    unknown: 0,
    labels: { team: ['kasse', 'support'], site: ['hamburg'] },
    receiving: true,
  }
})
afterAll(() => server.close())

function wrap(entry = '/availability') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/availability" element={<AvailabilityPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('the availability dashboard', () => {
  it('shows a device with its last measurement', async () => {
    await changeLanguage('en')
    wrap()

    expect(await screen.findByText('Till printer 1')).toBeInTheDocument()
    expect(screen.getByText('10.10.0.31')).toBeInTheDocument()
    expect(screen.getByText(/Hamburg/)).toBeInTheDocument()
  })

  it('counts a stale device as unknown, however green it last was', async () => {
    // The probe stopped reporting. The device may well be running - what is
    // gone is the measurement, and saying "reachable" here would be a wall
    // of green that stopped being true hours ago.
    await changeLanguage('en')
    overview = {
      ...overview,
      devices: [device({ state: 'up', stale: true })],
      up: 0,
      unknown: 1,
    }
    wrap()

    await screen.findByText('Till printer 1')
    // "Reachable" appears once, as the counter's own label - not a second
    // time as this device's badge, which is the whole assertion.
    expect(screen.getAllByText('Reachable')).toHaveLength(1)
    expect(screen.getAllByText('Unknown')).toHaveLength(2)
  })

  it('explains itself when nothing is being received', async () => {
    await changeLanguage('en')
    overview = { ...overview, receiving: false, up: 0, unknown: 1 }
    wrap()

    expect(
      await screen.findByText(/not receiving measurements right now/),
    ).toBeInTheDocument()
  })

  it('filters by label from the URL, so a site can bookmark its own devices', async () => {
    await changeLanguage('en')
    overview = {
      ...overview,
      devices: [
        device(),
        device({
          id: 'D2',
          display_name: 'Card terminal 2',
          labels: { team: 'kasse', site: 'hamburg' },
        }),
      ],
    }
    wrap('/availability?label=team:kasse')

    expect(await screen.findByText('Card terminal 2')).toBeInTheDocument()
    expect(screen.queryByText('Till printer 1')).not.toBeInTheDocument()
  })

  it('adds a device with the labels typed as lines', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Add device' }))
    await user.type(screen.getByPlaceholderText('Till printer 1'), 'Printer 9')
    await user.type(screen.getByPlaceholderText('10.10.0.31'), '10.10.0.99')
    await user.selectOptions(screen.getByRole('combobox', { name: /Probe/ }), 'P1')
    await user.type(
      screen.getByPlaceholderText(/team: support/),
      'team: kasse\nsite: hamburg',
    )
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(posted).toEqual([
      {
        display_name: 'Printer 9',
        address: '10.10.0.99',
        probe_id: 'P1',
        method: 'icmp',
        port: null,
        labels: { team: 'kasse', site: 'hamburg' },
        failure_threshold: 3,
        enabled: true,
        notes: null,
      },
    ])
  })

  it('does not offer to save a TCP check without a port', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Add device' }))
    await user.type(screen.getByPlaceholderText('Till printer 1'), 'Terminal')
    await user.type(screen.getByPlaceholderText('10.10.0.31'), '10.10.0.55')
    await user.selectOptions(screen.getByRole('combobox', { name: /Probe/ }), 'P1')
    await user.selectOptions(screen.getByRole('combobox', { name: /Check/ }), 'tcp')

    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})

describe('the label editor', () => {
  it('reads one pair per line', () => {
    expect(parseLabels('team: support\nsite: hamburg')).toEqual({
      team: 'support',
      site: 'hamburg',
    })
  })

  it('keeps a value that contains a colon', () => {
    expect(parseLabels('note: see: the manual')).toEqual({
      note: 'see: the manual',
    })
  })

  it('drops a half-typed line instead of losing the ones above it', () => {
    expect(parseLabels('team: support\nsite')).toEqual({ team: 'support' })
  })
})
