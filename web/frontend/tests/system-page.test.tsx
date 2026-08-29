import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { SystemPage } from '@/features/infrastructure/SystemPage'
import { changeLanguage } from '@/i18n'

/**
 * Verify, backup, export and restart existed as endpoints and handlers for as
 * long as the docs promised a page for them. What the tests pin down: the two
 * destructive ones ask first, and the export names what the archive contains
 * before anything runs.
 */

let permissions = ['system.read', 'system.restart', 'job.read']
let restarted = 0
let exported = 0

const BACKUPS = [
  {
    name: 'runtime-2026-08-28.tar.gz',
    kind: 'runtime',
    size_bytes: 1048576,
    created_at: '2026-08-28T02:00:00Z',
    sha256: 'ab'.repeat(32),
    download_url: '/api/v1/system/backups/runtime-2026-08-28.tar.gz',
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
  http.get('/api/v1/system/backups', () => HttpResponse.json(BACKUPS)),
  http.post('/api/v1/system/restart', () => {
    restarted += 1
    return HttpResponse.json(
      { job_id: 'J1', status: 'queued', events_url: '/api/v1/jobs/J1/events' },
      { status: 202 },
    )
  }),
  http.post('/api/v1/system/export', () => {
    exported += 1
    return HttpResponse.json(
      { job_id: 'J2', status: 'queued', events_url: '/api/v1/jobs/J2/events' },
      { status: 202 },
    )
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  permissions = ['system.read', 'system.restart', 'job.read']
  restarted = 0
  exported = 0
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <SystemPage />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('the maintenance page', () => {
  it('restarts only after a confirmation that names the cost', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Restart' }))
    expect(restarted).toBe(0)
    expect(
      screen.getByText(/briefly lose their connection/),
    ).toBeInTheDocument()

    const dialogButtons = screen.getAllByRole('button', { name: 'Restart' })
    await user.click(dialogButtons[dialogButtons.length - 1])
    expect(restarted).toBe(1)
  })

  it('warns what the runtime export contains before it runs', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.click(await screen.findByRole('button', { name: 'Export runtime' }))
    expect(exported).toBe(0)
    expect(screen.getByText(/CA key/)).toBeInTheDocument()
  })

  it('lists the archives with a plain download link', async () => {
    await changeLanguage('en')
    wrap()

    expect(await screen.findByText('runtime-2026-08-28.tar.gz')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute(
      'href',
      '/api/v1/system/backups/runtime-2026-08-28.tar.gz',
    )
  })

  it('keeps the download and the destructive cards from a reader', async () => {
    await changeLanguage('en')
    permissions = ['system.read', 'job.read']
    wrap()

    expect(await screen.findByText('runtime-2026-08-28.tar.gz')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Download' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Restart' })).not.toBeInTheDocument()
  })
})
