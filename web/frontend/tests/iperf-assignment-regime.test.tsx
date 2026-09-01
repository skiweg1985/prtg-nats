import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import type { IperfEndpoint } from '@/api/types'
import { IperfProbesDialog } from '@/features/infrastructure/IperfProbesDialog'
import { endpointsHeldByProbe } from '@/features/infrastructure/iperfProfiles'
import { changeLanguage } from '@/i18n'

/**
 * The one place the regime can still change back.
 *
 * A probe that holds exactly one endpoint carries the "default" profile, and
 * the sensor reads address, port and user out of it. Handing that probe a
 * second endpoint takes the alias away: PRTG objects that name no server go
 * red, and objects that name one keep measuring without a sign-in - which
 * nobody notices. The warning has to be on screen before the button is
 * pressed, not in the job afterwards.
 */

const PROBES = [
  { id: 'P1', nats_username: 'alone', display_name: 'Alone', host: 'a.example' },
  { id: 'P2', nats_username: 'sharing', display_name: 'Sharing', host: 'b.example' },
  { id: 'P3', nats_username: 'empty', display_name: 'Empty', host: 'c.example' },
]

function holder(probe: string, held: number) {
  return {
    probe,
    endpoints_held: held,
    uses_default_alias: held === 1,
    parameter_line: held === 1 ? '' : '--profile berlin',
  }
}

// "alone" holds one endpoint, "sharing" holds two, "empty" holds none.
const ENDPOINTS: IperfEndpoint[] = [
  {
    name: 'berlin',
    host: 'iperf.example.test',
    port: 5201,
    username: 'prtg-probe',
    kind: 'iperf3',
    updated_at: null,
    has_public_key: true,
    managed: true,
    holders: [holder('alone', 1), holder('sharing', 2)],
  },
  {
    name: 'hamburg',
    host: 'iperf2.example.test',
    port: 5201,
    username: 'prtg-probe',
    kind: 'iperf3',
    updated_at: null,
    has_public_key: true,
    managed: true,
    holders: [holder('sharing', 2)],
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
        permissions: ['iperf.read', 'iperf.manage', 'sensor.deploy'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json(PROBES)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

/** Opened on hamburg, so "alone" is a probe about to receive its second. */
function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <IperfProbesDialog
          endpoint={ENDPOINTS[1]}
          heldByProbe={endpointsHeldByProbe(ENDPOINTS)}
          onClose={() => {}}
        />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('assigning an endpoint', () => {
  it('counts what each probe holds from the endpoints it is given', () => {
    const held = endpointsHeldByProbe(ENDPOINTS)
    expect(held.get('alone')).toBe(1)
    expect(held.get('sharing')).toBe(2)
    expect(held.get('empty')).toBeUndefined()
  })

  it('warns before the second endpoint takes the alias away', async () => {
    await changeLanguage('en')
    wrap()

    expect(
      screen.queryByText(/Existing sensors on these probes stop measuring/),
    ).not.toBeInTheDocument()

    await userEvent.click(await screen.findByLabelText('Alone'))

    const title = await screen.findByText(
      'Existing sensors on these probes stop measuring',
    )
    // Both outcomes have to be named: going red is the loud one, measuring on
    // without a sign-in is the one nobody sees.
    expect(title.parentElement).toHaveTextContent(/default/)
    expect(title.parentElement).toHaveTextContent(/without a sign-in/)
    expect(title.parentElement).toHaveTextContent(/red/)
  })

  it('says the alias comes back when the second one is taken away', async () => {
    await changeLanguage('en')
    wrap()

    await userEvent.click(await screen.findByLabelText(/Sharing/))

    expect(
      await screen.findByText(/get the "default" profile back/),
    ).toHaveTextContent('sharing')
    expect(
      screen.queryByText(/Existing sensors on these probes stop measuring/),
    ).not.toBeInTheDocument()
  })

  it('stays quiet for a probe that holds none yet', async () => {
    await changeLanguage('en')
    wrap()

    await userEvent.click(await screen.findByLabelText('Empty'))

    expect(
      screen.queryByText(/Existing sensors on these probes stop measuring/),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/profile back/)).not.toBeInTheDocument()
  })
})
