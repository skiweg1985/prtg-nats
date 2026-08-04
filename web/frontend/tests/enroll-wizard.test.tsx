import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { EnrollWizard } from '@/features/probes/EnrollWizard'
import { changeLanguage } from '@/i18n'

/**
 * What the wizard has to get right is not layout, it is the order of events:
 * a name the server would reject must not become an invitation, the command
 * has to be the one the operator is meant to paste, and the page has to move
 * on by itself when the host reports in.
 */

const INVITATION = {
  id: '01ABC',
  kind: 'probe',
  nats_username: 'mpp-berlin',
  probe_name: 'berlin',
  expected_host: '192.0.2.10',
  expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  created_by_name: 'admin',
  redeemed_at: null,
  revoked_at: null,
  source_ip: null,
  job_id: null,
}

const ISSUED = {
  ...INVITATION,
  token: 'a-token',
  command: 'curl -fsSL http://nats.example.test/nats-ca.pem -o /tmp/ca.pem && …',
  ca_sha256: 'e7b40c61ca52b201eb3a6b7d57083067283d42a9265c828cebea574796df35a2',
}

/**
 * The server's view of the one invitation, which is not the open list.
 *
 * The callback redeems the invitation and writes its job id in one request:
 * the record leaves the open list exactly when it gains the job. So the
 * handlers below keep the two apart - a redeemed invitation is readable by id
 * and absent from the list, the way the API actually behaves.
 */
let invitation: Record<string, unknown> | null = null
let openInvitations: unknown[] = []
let createdBodies: Record<string, unknown>[] = []

const server = setupServer(
  http.get('/api/v1/credentials', () =>
    HttpResponse.json([
      {
        username: 'prtg-nats',
        is_shared: true,
        has_auth_entry: true,
        probe_enrolled: false,
      },
    ]),
  ),
  http.get('/api/v1/probes', () =>
    HttpResponse.json([
      {
        id: 'P1',
        nats_username: 'mpp-hamburg',
        display_name: null,
        host: '192.0.2.10',
        probe_name: 'hamburg',
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
      },
    ]),
  ),
  http.get('/api/v1/probes/enrollment/tokens', () =>
    HttpResponse.json(openInvitations),
  ),
  http.get('/api/v1/probes/enrollment/tokens/:id', () =>
    invitation === null
      ? HttpResponse.json(
          { error: { code: 'enrollment.token_invalid' } },
          { status: 404 },
        )
      : HttpResponse.json(invitation),
  ),
  http.post('/api/v1/probes/enrollment/tokens', async ({ request }) => {
    createdBodies.push((await request.json()) as Record<string, unknown>)
    invitation = { ...INVITATION }
    openInvitations = [INVITATION]
    return HttpResponse.json(ISSUED, { status: 201 })
  }),
  http.get('/api/v1/jobs/:id', ({ params }) =>
    HttpResponse.json({
      id: params.id,
      type: 'probe.enroll',
      status: 'running',
      steps: [{ name: 'pin_host_key', status: 'succeeded' }],
      created_at: new Date().toISOString(),
    }),
  ),
  http.get('/api/v1/jobs/:id/log', () => HttpResponse.json([])),
)

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'bypass' })
})
afterEach(() => {
  server.resetHandlers()
  invitation = null
  openInvitations = []
  createdBodies = []
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <EnrollWizard />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('EnrollWizard', () => {
  it('refuses a probe name the server would reject, without asking it', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('mpp-berlin-01'), 'mpp-berlin')
    await user.type(
      screen.getByPlaceholderText('multi-platform-probe@berlin'),
      'Testprobe 191',
    )

    // The space is the whole point: on a real installation this reached the
    // job and failed after five successful steps.
    expect(await screen.findByText(/no spaces/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create the command/i })).toBeDisabled()
    expect(createdBodies).toHaveLength(0)
  })

  it('shows the command and the fingerprint once an invitation exists', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('mpp-berlin-01'), 'mpp-berlin')
    await user.click(screen.getByRole('button', { name: /create the command/i }))

    expect(await screen.findByText(ISSUED.command)).toBeInTheDocument()
    // The fingerprint is what makes the plain-HTTP CA fetch safe, so it has to
    // be on screen next to the command, not a click away.
    expect(screen.getByText(ISSUED.ca_sha256)).toBeInTheDocument()
    expect(screen.getByText(/waiting for the probe/i)).toBeInTheDocument()
    expect(createdBodies[0]).toMatchObject({ nats_username: 'mpp-berlin' })
  })

  it('moves on by itself when the host reports in', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('mpp-berlin-01'), 'mpp-berlin')
    await user.click(screen.getByRole('button', { name: /create the command/i }))
    await screen.findByText(/waiting for the probe/i)

    // The host ran the command: the invitation is spent, carries the job it
    // started, and is out of the open list - which is where the wizard used
    // to look, and why this page waited forever on a real installation.
    invitation = {
      ...INVITATION,
      redeemed_at: new Date().toISOString(),
      job_id: 'JOB1',
    }
    openInvitations = []

    await waitFor(
      () => expect(screen.getByText(/the platform takes over/i)).toBeInTheDocument(),
      { timeout: 6000 },
    )
  })

  it('stops waiting once the invitation can no longer be used', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('mpp-berlin-01'), 'mpp-berlin')
    await user.click(screen.getByRole('button', { name: /create the command/i }))
    await screen.findByText(/waiting for the probe/i)

    // Cancelled from somewhere else - a second operator, a second tab. The
    // command on screen would be refused, so a spinner is the wrong thing to
    // keep showing.
    invitation = { ...INVITATION, revoked_at: new Date().toISOString() }
    openInvitations = []

    await waitFor(
      () =>
        expect(screen.getByText(/no longer be used/i)).toBeInTheDocument(),
      { timeout: 6000 },
    )
    expect(screen.queryByText(/waiting for the probe/i)).not.toBeInTheDocument()
  })

  it('warns that the command is a secret', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('mpp-berlin-01'), 'mpp-berlin')
    await user.click(screen.getByRole('button', { name: /create the command/i }))

    expect(await screen.findByText(/treat the command as a secret/i)).toBeInTheDocument()
  })

  it('refuses an address another probe already claims', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(screen.getByPlaceholderText('mpp-berlin-01'), 'mpp-berlin')
    await user.type(screen.getByPlaceholderText('probe.example.com'), '192.0.2.10')

    // Two entries for one address share the management access that lives on
    // the host; retiring either revokes it for both. Observed on a real
    // installation as a probe that was connected to NATS and unreachable.
    expect(await screen.findByText(/already enrolled as "mpp-hamburg"/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create the command/i })).toBeDisabled()
    expect(createdBodies).toHaveLength(0)
  })

  /**
   * The wizard's success banner is the last thing anybody reads before
   * leaving, and two of the steps it describes cannot be taken here: the
   * access key goes into the PRTG core by hand and the probe is approved
   * there. It used to end at "the probe is enrolled", which reads as done.
   */
  async function reachTheEnd(user: ReturnType<typeof userEvent.setup>) {
    server.use(
      http.get('/api/v1/jobs/:id', ({ params }) =>
        HttpResponse.json({
          id: params.id,
          type: 'probe.enroll',
          status: 'successful',
          steps: [{ name: 'pin_host_key', status: 'succeeded' }],
          created_at: new Date().toISOString(),
        }),
      ),
    )
    await user.type(screen.getByPlaceholderText('mpp-berlin-01'), 'mpp-berlin')
    await user.click(screen.getByRole('button', { name: /create the command/i }))
    await screen.findByText(/waiting for the probe/i)

    invitation = {
      ...INVITATION,
      redeemed_at: new Date().toISOString(),
      job_id: 'JOB1',
    }
    openInvitations = []
    await screen.findByText(/the probe is enrolled/i, {}, { timeout: 6000 })
  }

  it('names the two PRTG steps that are still missing', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await reachTheEnd(user)

    expect(screen.getByText(/access-key list/)).toBeInTheDocument()
    expect(screen.getByText(/approve the probe/)).toBeInTheDocument()
  })

  it('links to the probe itself, where the access key can be revealed', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    // The probe reaches the list through the enrolment that just finished.
    server.use(
      http.get('/api/v1/probes', () =>
        HttpResponse.json([
          {
            id: 'P9',
            nats_username: 'mpp-berlin',
            display_name: null,
            host: '192.0.2.11',
            probe_name: 'berlin',
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
          },
        ]),
      ),
    )
    wrap()

    await reachTheEnd(user)

    const link = await screen.findByRole('link', { name: /open the probe/i })
    expect(link).toHaveAttribute('href', '/probes/P9')
  })

  it('falls back to the probe list while the probe is not in it yet', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    // The default handler only knows mpp-hamburg, so the id cannot be looked
    // up - a dead link would be worse than one more click.
    await reachTheEnd(user)

    const link = await screen.findByRole('link', { name: /to the probe list/i })
    expect(link).toHaveAttribute('href', '/probes')
  })

  it('names an account that already exists instead of failing later', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await screen.findByPlaceholderText('mpp-berlin-01')
    await user.type(screen.getByPlaceholderText('mpp-berlin-01'), 'prtg-nats')

    expect(await screen.findByText(/already exists/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create the command/i })).toBeDisabled()
  })
})
