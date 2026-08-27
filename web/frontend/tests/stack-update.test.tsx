import { render, screen, waitFor } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { AppProviders } from '@/app/providers'
import { UpdatesPage } from '@/features/updates/UpdatesPage'
import { changeLanguage } from '@/i18n'

/**
 * The update page has to survive the thing it starts.
 *
 * Recreating the stack takes the API away for minutes, so every request the
 * page has open fails at once. A page that renders that as an error tells the
 * operator the update broke, at the exact moment it is working - and the
 * obvious reaction to that message, reloading or pressing again, is the worst
 * thing to do while containers are being swapped.
 *
 * What the tests pin down is the honesty of the three readings underneath it:
 * a repository that cannot be reached is never "up to date", a checkout ahead
 * of the running image is its own state, and a dropped connection while a job
 * is being watched is a restart rather than a failure.
 */

const BASE = {
  running_commit: 'aaaaaaaaaaaa1111',
  checkout_commit: 'aaaaaaaaaaaa1111',
  checkout_dirty: false,
  remote_commit: 'aaaaaaaaaaaa1111',
  branch: 'main',
  state: 'current',
  reachable: true,
  error: '',
  commits: [],
  checked_at: '2026-08-27T10:00:00Z',
  last_update_at: null,
  last_update_commit: '',
  checkout_dir: '/opt/prtg-nats-server',
  available: true,
  unavailable_reason: null,
}

let version: Record<string, unknown> = { ...BASE }
let permissions = ['system.read', 'system.update']

const server = setupServer(
  http.get('/api/v1/auth/state', () =>
    HttpResponse.json({
      setup_required: false,
      authenticated: true,
      dev_auth: false,
      principal: {
        id: 'U1',
        username: 'admin',
        role: 'administrator',
        permissions,
      },
    }),
  ),
  http.get('/api/v1/system/capabilities', () =>
    HttpResponse.json({
      docker: true,
      runtime_state: 'complete',
      dev_auth: false,
      stack_update: true,
    }),
  ),
  http.get('/api/v1/system/update', () => HttpResponse.json(version)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  version = { ...BASE }
  permissions = ['system.read', 'system.update']
  sessionStorage.clear()
})
afterAll(() => server.close())

function renderPage() {
  return render(
    <AppProviders>
      <MemoryRouter>
        <UpdatesPage />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('UpdatesPage', () => {
  it('says which commit is running, not just that something is', async () => {
    await changeLanguage('en')
    renderPage()

    expect(await screen.findByText('Up to date')).toBeInTheDocument()
    // All three readings show it, which is the point: running, checkout and
    // branch are separate answers that happen to agree here.
    expect(screen.getAllByText('aaaaaaaaaaaa')).toHaveLength(3)
  })

  it('never reports an unreachable repository as up to date', async () => {
    /**
     * A deploy key that stopped working. Folding this into "current" would
     * leave an installation sitting on an old version, reporting itself
     * healthy, for as long as nobody read the log.
     */
    await changeLanguage('en')
    version = {
      ...BASE,
      state: 'unreachable',
      reachable: false,
      remote_commit: '',
      error: 'Permission denied (publickey)',
    }
    renderPage()

    expect(await screen.findByText('Repository unreachable')).toBeInTheDocument()
    expect(screen.getByText(/Permission denied/)).toBeInTheDocument()
    expect(screen.queryByText('Up to date')).not.toBeInTheDocument()
  })

  it('tells a pulled-but-unbuilt checkout apart from a current one', async () => {
    /**
     * `git pull` on the host without a rebuild. The checkout matches the
     * branch, so a single version comparison would call this up to date -
     * while the stack still runs the older code.
     */
    await changeLanguage('en')
    version = {
      ...BASE,
      state: 'rebuild_pending',
      checkout_commit: 'bbbbbbbbbbbb2222',
      remote_commit: 'bbbbbbbbbbbb2222',
    }
    renderPage()

    expect(await screen.findByText('Rebuild pending')).toBeInTheDocument()
    expect(
      screen.getByText(/pulled on this host without rebuilding/),
    ).toBeInTheDocument()
  })

  it('offers no button while the checkout has uncommitted changes', async () => {
    await changeLanguage('en')
    version = {
      ...BASE,
      state: 'update_available',
      checkout_dirty: true,
      remote_commit: 'cccccccccccc3333',
      commits: [{ sha: 'cccccccccccc3333', subject: 'A new sensor', date: '2026-08-26T09:00:00Z' }],
    }
    renderPage()

    const button = await screen.findByRole('button', { name: 'Install the update' })
    expect(button).toBeDisabled()
    expect(screen.getByText(/Commit or discard them/)).toBeInTheDocument()
  })

  it('names what the update would bring in', async () => {
    await changeLanguage('en')
    version = {
      ...BASE,
      state: 'update_available',
      remote_commit: 'cccccccccccc3333',
      commits: [
        { sha: 'cccccccccccc3333', subject: 'A new sensor', date: '2026-08-26T09:00:00Z' },
        { sha: 'dddddddddddd4444', subject: 'Fix the lookup', date: '2026-08-25T09:00:00Z' },
      ],
    }
    renderPage()

    expect(await screen.findByText('A new sensor')).toBeInTheDocument()
    expect(screen.getByText('Fix the lookup')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Install the update' }),
    ).toBeEnabled()
  })

  it('explains itself instead of offering a button it cannot honour', async () => {
    /**
     * The bootstrap case: an installation updating *to* the version that
     * introduces this has no updater image yet. A button here would fail on
     * press; the page says which single command fixes it instead.
     */
    await changeLanguage('en')
    version = {
      ...BASE,
      available: false,
      unavailable_reason: 'updater_image_missing',
    }
    renderPage()

    expect(
      await screen.findByText(/The updater image does not exist yet/),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Install the update' }),
    ).not.toBeInTheDocument()
  })

  it('treats the API going away mid-job as a restart, not a failure', { timeout: 20_000 }, async () => {
    /**
     * The heart of it. The job is being watched, the recreate begins, and
     * every request starts failing. The page must switch to waiting and keep
     * knocking on /health - not render the connection error it is getting.
     */
    await changeLanguage('en')
    sessionStorage.setItem('prtg-nats:update-job', 'J1')

    const health = vi.fn(() => HttpResponse.json({ status: 'ok' }))
    server.use(
      // The job request fails the way it does when the container is gone.
      http.get('/api/v1/jobs/J1', () => HttpResponse.error()),
      http.get('/api/v1/jobs/J1/log', () => HttpResponse.error()),
      http.get('/health', health),
    )

    renderPage()

    // The generous timeout is the retry policy, not slowness: a failed request
    // is retried twice with backoff before the page concludes anything. In
    // practice the detached status arrives first and this path is the safety
    // net for a connection that drops before it does.
    expect(
      await screen.findByText('The stack is being replaced', undefined, {
        timeout: 10_000,
      }),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/The service has not come back/),
    ).not.toBeInTheDocument()

    // And it keeps asking, rather than settling into the error state.
    await waitFor(() => expect(health).toHaveBeenCalled(), { timeout: 6000 })
  })

  it('says when it was last updated, once it has been', async () => {
    /**
     * The question still open once the state reads current. An installation
     * is up to date either because it was updated an hour ago or because
     * nothing has changed in months, and those are different situations.
     */
    await changeLanguage('en')
    version = {
      ...BASE,
      last_update_at: '2026-08-27T09:00:00Z',
      last_update_commit: 'eeeeeeeeeeee5555',
    }
    renderPage()

    expect(await screen.findByText('Last updated from here')).toBeInTheDocument()
    expect(screen.getByText('eeeeeeeeeeee')).toBeInTheDocument()
  })

  it('leaves the row out when nothing was updated from here', async () => {
    /**
     * An empty row would read as "never updated". The truth is narrower:
     * updates run from the host leave no record in this database.
     */
    await changeLanguage('en')
    renderPage()

    expect(await screen.findByText('Up to date')).toBeInTheDocument()
    expect(screen.queryByText('Last updated from here')).not.toBeInTheDocument()
  })
})
