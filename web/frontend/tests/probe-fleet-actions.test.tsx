import { QueryClient } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeListPage } from '@/features/probes/ProbeListPage'
import { changeLanguage } from '@/i18n'

/**
 * Acting on a selection instead of on one probe at a time.
 *
 * The list already carries the two facts that decide who needs what - a helper
 * that is behind, a probe that drifted. What is asserted here is that those
 * facts reach the buttons: an action nobody in the selection can use is not
 * offered, and one that is offered goes out as a single request naming exactly
 * the probes it applies to.
 */

function probe(overrides: Record<string, unknown>) {
  return {
    id: 'P0',
    nats_username: 'mpp-probe',
    display_name: null,
    host: '192.0.2.1',
    probe_name: null,
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
    ...overrides,
  }
}

const PROBES = [
  // Behind, and reachable over the channel: the one row a helper update is for.
  probe({
    id: 'P1',
    nats_username: 'mpp-berlin-01',
    probe_name: 'berlin-01',
    helper_version: 3,
    helper_outdated: true,
  }),
  // Behind, but reports no helper version at all - enrolled before updates
  // were signed, so the channel cannot reach it.
  probe({
    id: 'P2',
    nats_username: 'mpp-hamburg-01',
    probe_name: 'hamburg-01',
    helper_version: null,
    helper_outdated: true,
    deviation_count: 2,
  }),
  probe({ id: 'P3', nats_username: 'mpp-munich-01', probe_name: 'munich-01' }),
]

let posted: { url: string; body: unknown }[] = []

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
        permissions: [
          'probe.read',
          'probe.update',
          'probe.reconcile',
          'deployment.create',
        ],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json(PROBES)),
  http.post('/api/v1/probes/actions/:action', async ({ params, request }) => {
    posted.push({ url: String(params.action), body: await request.json() })
    return HttpResponse.json(
      { job_id: 'J1', status: 'queued', events_url: '/api/v1/jobs/J1/events' },
      { status: 202 },
    )
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  posted = []
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/probes']}>
        <Routes>
          <Route path="/probes" element={<ProbeListPage />} />
          <Route path="/jobs/:jobId" element={<p>job page</p>} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('acting on a selection of probes', () => {
  it('offers an action only while the selection can use it', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByText('mpp-berlin-01')
    // Nothing selected, nothing offered.
    expect(screen.queryByRole('button', { name: 'Update helper' })).toBeNull()

    // A probe that reports no helper version cannot be updated over the
    // channel, so the action stays away even though the row says "outdated".
    await user.click(screen.getByLabelText('P2'))
    expect(screen.queryByRole('button', { name: 'Update helper' })).toBeNull()
    // It did drift, so fixing deviations is on offer for the same selection.
    expect(screen.getByRole('button', { name: 'Fix deviations' })).toBeInTheDocument()

    await user.click(screen.getByLabelText('P1'))
    expect(screen.getByRole('button', { name: 'Update helper' })).toBeInTheDocument()
  })

  it('names the targets before it starts, and sends only those', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByText('mpp-berlin-01')
    await user.click(screen.getByLabelText('P1'))
    await user.click(screen.getByLabelText('P3'))
    await user.click(screen.getByRole('button', { name: 'Update helper' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('mpp-berlin-01')).toBeInTheDocument()
    expect(within(dialog).getByText('mpp-munich-01')).toBeInTheDocument()
    expect(posted).toEqual([])

    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }))

    expect(await screen.findByText('job page')).toBeInTheDocument()
    expect(posted).toEqual([
      { url: 'helper-update', body: { probe_ids: ['P1', 'P3'] } },
    ])
  })

  it('leaves out the probes the action cannot touch, and says so', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByText('mpp-berlin-01')
    await user.click(screen.getByLabelText('P1'))
    await user.click(screen.getByLabelText('P2'))
    await user.click(screen.getByLabelText('P3'))
    await user.click(screen.getByRole('button', { name: 'Update helper' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).queryByText('mpp-hamburg-01')).toBeNull()
    expect(within(dialog).getByText(/left out/)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }))
    expect(posted).toEqual([
      { url: 'helper-update', body: { probe_ids: ['P1', 'P3'] } },
    ])
  })

  it('goes straight ahead when the selection is a single probe', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByText('mpp-berlin-01')
    await user.click(screen.getByLabelText('P1'))
    await user.click(screen.getByRole('button', { name: 'Update helper' }))

    // One probe is what a single click does on the detail page; asking again
    // would be the step this whole endpoint exists to remove.
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(await screen.findByText('job page')).toBeInTheDocument()
    expect(posted).toEqual([{ url: 'helper-update', body: { probe_ids: ['P1'] } }])
  })

  it('narrows the list to the rows a filter is about', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByText('mpp-munich-01')

    await user.click(screen.getByRole('button', { name: 'Helper outdated only' }))
    expect(screen.getByText('mpp-berlin-01')).toBeInTheDocument()
    expect(screen.getByText('mpp-hamburg-01')).toBeInTheDocument()
    expect(screen.queryByText('mpp-munich-01')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'With deviations only' }))
    expect(screen.queryByText('mpp-berlin-01')).toBeNull()
    expect(screen.getByText('mpp-hamburg-01')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByText('mpp-munich-01')).toBeInTheDocument()
  })

  it('does not call a filtered-out fleet an empty one', async () => {
    await changeLanguage('en')
    // A fleet where nothing has drifted - so the filter matches nothing while
    // there is plenty enrolled.
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([
          probe({ id: 'P9', nats_username: 'mpp-solo-01', probe_name: 'solo-01' }),
        ]),
      ),
    )
    const user = userEvent.setup()
    wrap()

    await screen.findByText('mpp-solo-01')
    await user.click(screen.getByRole('button', { name: 'With deviations only' }))

    expect(screen.getByText('No probe matches these filters.')).toBeInTheDocument()
    // Telling somebody to enrol their first probe when they have one is how an
    // empty state stops being read at all.
    expect(screen.queryByText('No probe is enrolled yet.')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Add probe' })).toBeNull()
  })
})
