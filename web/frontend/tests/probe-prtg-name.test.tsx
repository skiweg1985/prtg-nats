import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { SettingsPage } from '@/features/misc/Pages'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { ProbeListPage } from '@/features/probes/ProbeListPage'
import { changeLanguage } from '@/i18n'

/**
 * The name PRTG uses today, read by the probe's PRTG object id once somebody
 * linked the two. It sits next to the enrollment name, a difference is a note
 * and not an error, and every way of not knowing it says why. Linking is
 * always a person's choice: the matching name is only preselected.
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

const DETAIL = {
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
    prtg_object_id: null,
  },
  observed: null,
  sensors: [],
  deviations: [],
  notes: null,
  labels: {},
}

function nameAnswer(overrides: Record<string, unknown>) {
  return {
    state: 'ok',
    object_id: '2041',
    enrollment_name: 'berlin-01',
    name_in_prtg: 'berlin-01',
    differs: false,
    ...overrides,
  }
}

let linked: unknown[] = []
let savedSettings: unknown[] = []

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
        permissions: ['probe.read', 'probe.update', 'system.read', 'system.settings'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json([SUMMARY])),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(DETAIL)),
  http.get('/api/v1/probes/P1/prtg-name', () => HttpResponse.json(nameAnswer({}))),
  http.get('/api/v1/prtg/probe-names', () => HttpResponse.json({ state: 'ok', names: [] })),
  http.get('/api/v1/probes/P1/prtg-link/candidates', () =>
    HttpResponse.json({
      suggested_object_id: '2041',
      candidates: [
        { object_id: '2041', name: 'berlin-01', status: 'CONNECTED' },
        { object_id: '1', name: 'Local Probe', status: 'CONNECTED' },
      ],
    }),
  ),
  http.put('/api/v1/probes/P1/prtg-link', async ({ request }) => {
    linked.push(await request.json())
    return HttpResponse.json(nameAnswer({}))
  }),
  http.get('/api/v1/prtg/settings', () =>
    HttpResponse.json({
      core_url: 'https://prtg.example.test:1616',
      token_configured: true,
      ca_bundle_configured: false,
    }),
  ),
  http.put('/api/v1/prtg/settings', async ({ request }) => {
    savedSettings.push(await request.json())
    return HttpResponse.json({
      core_url: 'https://prtg.example.test:1616',
      token_configured: true,
      ca_bundle_configured: false,
    })
  }),
  http.get('/api/v1/system/capabilities', () =>
    HttpResponse.json({ docker: false, runtime_state: 'complete' }),
  ),
  http.get('/api/v1/users', () => HttpResponse.json([])),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
beforeEach(async () => {
  linked = []
  savedSettings = []
  await changeLanguage('en')
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/probes" element={<ProbeListPage />} />
          <Route path="/probes/:probeId" element={<ProbeDetailPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

async function prtgRow() {
  const label = await screen.findByText('Name in PRTG')
  return label.closest('div') as HTMLElement
}

describe('the name in PRTG on the probe page', () => {
  it('sits next to the enrollment name and marks a difference as a note', async () => {
    server.use(
      http.get('/api/v1/probes/P1/prtg-name', () =>
        HttpResponse.json(nameAnswer({ name_in_prtg: 'Berlin Office', differs: true })),
      ),
    )
    wrap('/probes/P1')

    const row = await prtgRow()
    expect(await within(row).findByText('Berlin Office')).toBeInTheDocument()
    expect(within(row).getByText('Differs from the enrollment name')).toBeInTheDocument()
    expect(screen.getByText('Enrollment name').closest('div')).toHaveTextContent('berlin-01')
  })

  it('shows no note when the names agree', async () => {
    wrap('/probes/P1')

    const row = await prtgRow()
    await within(row).findByText('berlin-01')
    expect(within(row).queryByText(/Differs/)).not.toBeInTheDocument()
  })

  it('names the link by the object id, the one term for it', async () => {
    wrap('/probes/P1')

    const row = await prtgRow()
    expect(await within(row).findByText('Object id 2041')).toBeInTheDocument()
  })

  it.each([
    ['not_configured', /PRTG core URL or API token is missing/],
    ['unreachable', /PRTG cannot be reached/],
    ['rejected', /PRTG refused the API token/],
    ['not_found', /no longer has a probe with object id 2041/],
  ])('says why there is no name when the state is %s', async (state, reason) => {
    server.use(
      http.get('/api/v1/probes/P1/prtg-name', () =>
        HttpResponse.json(nameAnswer({ state, name_in_prtg: null })),
      ),
    )
    wrap('/probes/P1')

    const row = await prtgRow()
    expect(await within(row).findByText(reason)).toBeInTheDocument()
  })

  it('says it in German as well', async () => {
    await changeLanguage('de')
    server.use(
      http.get('/api/v1/probes/P1/prtg-name', () =>
        HttpResponse.json(nameAnswer({ name_in_prtg: 'Berlin Office', differs: true })),
      ),
    )
    wrap('/probes/P1')

    const label = await screen.findByText('Name in PRTG')
    const row = label.closest('div') as HTMLElement
    expect(
      await within(row).findByText('Weicht vom Namen bei der Einbindung ab'),
    ).toBeInTheDocument()
  })
})

describe('linking a probe to its object in PRTG', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/probes/P1/prtg-name', () =>
        HttpResponse.json(
          nameAnswer({
            state: 'not_linked',
            object_id: null,
            name_in_prtg: null,
          }),
        ),
      ),
    )
  })

  it('preselects the matching name and links only on confirmation', async () => {
    const user = userEvent.setup()
    wrap('/probes/P1')

    const row = await prtgRow()
    await user.click(await within(row).findByRole('button', { name: 'Link' }))
    const dialog = await screen.findByRole('dialog', {
      name: 'Link to the probe in PRTG',
    })

    const suggested = await within(dialog).findByRole('radio', {
      name: /berlin-01/,
    })
    expect(suggested).toBeChecked()
    expect(within(dialog).getByText('Same name as at enrollment')).toBeInTheDocument()
    expect(within(dialog).getByRole('radio', { name: /Local Probe/ })).toHaveAccessibleName(
      /1.*CONNECTED/,
    )
    expect(linked).toEqual([])

    await user.click(within(dialog).getByRole('button', { name: 'Link' }))
    await waitFor(() => expect(linked).toEqual([{ object_id: '2041' }]))
  })

  it('takes an id entered by hand and says where to find it', async () => {
    const user = userEvent.setup()
    wrap('/probes/P1')

    const row = await prtgRow()
    await user.click(await within(row).findByRole('button', { name: 'Link' }))
    const dialog = await screen.findByRole('dialog')

    const field = within(dialog).getByLabelText(/^Object id/)
    expect(within(dialog).getByText(/id=/)).toBeInTheDocument()
    await user.type(field, '2077')
    await user.click(within(dialog).getByRole('button', { name: 'Link' }))
    await waitFor(() => expect(linked).toEqual([{ object_id: '2077' }]))
  })
})

describe('the name in PRTG in the probe list', () => {
  it('appears under the probe when it differs', async () => {
    server.use(
      http.get('/api/v1/prtg/probe-names', () =>
        HttpResponse.json({
          state: 'ok',
          names: [
            {
              nats_username: 'mpp-berlin-01',
              object_id: '2041',
              name_in_prtg: 'Berlin Office',
              differs: true,
            },
          ],
        }),
      ),
    )
    wrap('/probes')

    expect(await screen.findByText('In PRTG: Berlin Office')).toBeInTheDocument()
  })

  it('says why the names are missing when PRTG cannot be reached', async () => {
    server.use(
      http.get('/api/v1/prtg/probe-names', () =>
        HttpResponse.json({ state: 'unreachable', names: [] }),
      ),
    )
    wrap('/probes')

    expect(await screen.findByText(/PRTG cannot be reached/)).toBeInTheDocument()
  })

  it('stays quiet without a token', async () => {
    server.use(
      http.get('/api/v1/prtg/probe-names', () =>
        HttpResponse.json({ state: 'not_configured', names: [] }),
      ),
    )
    wrap('/probes')

    await screen.findByText('berlin-01')
    expect(screen.queryByText(/API token/)).not.toBeInTheDocument()
  })
})

describe('the PRTG settings', () => {
  it('never shows the stored token and keeps it when the field stays empty', async () => {
    const user = userEvent.setup()
    wrap('/settings')

    const field = await screen.findByLabelText(/^API token/)
    expect(field).toHaveValue('')
    expect(field).toHaveAttribute('type', 'password')

    const card = field.closest('section') as HTMLElement
    await user.click(within(card).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(savedSettings).toHaveLength(1))
    expect(savedSettings[0]).not.toHaveProperty('token')
  })
})
