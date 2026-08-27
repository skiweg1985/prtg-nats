import { QueryClient } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { AppLayout } from '@/layouts/AppLayout'
import { JobListPage } from '@/features/jobs/JobPages'
import { changeLanguage } from '@/i18n'

/**
 * Getting to things.
 *
 * Below 768px the rail is hidden, and the header used to carry the five
 * primary entries and nothing else - infrastructure, the audit trail, updates
 * and the settings were reachable only by typing the address. And the job list
 * took no filter at all, so "failed jobs: 3" on the dashboard led to fifty
 * rows in time order.
 */

let jobRequests: string[] = []

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
          'system.read',
          'probe.read',
          'sensor.read',
          'deployment.read',
          'job.read',
          'audit.read',
          'certificate.read',
          'iperf.read',
          'credential.read',
        ],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/system/capabilities', () =>
    HttpResponse.json({ docker: true, runtime_state: 'ready' }),
  ),
  http.get('/api/v1/jobs', ({ request }) => {
    jobRequests.push(new URL(request.url).search)
    return HttpResponse.json([])
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  jobRequests = []
})
afterAll(() => server.close())

function wrapLayout() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<p>content</p>} />
            <Route path="/audit" element={<p>audit page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('the narrow-screen menu', () => {
  it('reaches the destinations a row of primary entries left behind', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrapLayout()

    await user.click(await screen.findByRole('button', { name: /Menu/ }))
    const panel = document.getElementById('app-menu')
    expect(panel).not.toBeNull()

    for (const label of ['NATS', 'Audit', 'Updates', 'Settings']) {
      expect(within(panel!).getByRole('link', { name: label })).toBeInTheDocument()
    }
  })

  it('closes once it has been used', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrapLayout()

    await user.click(await screen.findByRole('button', { name: /Menu/ }))
    const panel = document.getElementById('app-menu')
    await user.click(within(panel!).getByRole('link', { name: 'Audit' }))

    expect(await screen.findByText('audit page')).toBeInTheDocument()
    expect(document.getElementById('app-menu')).toBeNull()
  })

  it('closes on Escape', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrapLayout()

    await user.click(await screen.findByRole('button', { name: /Menu/ }))
    expect(document.getElementById('app-menu')).not.toBeNull()

    await user.keyboard('{Escape}')
    expect(document.getElementById('app-menu')).toBeNull()
  })
})

describe('the job list filters', () => {
  function wrapJobs(path: string) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <AppProviders client={client}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/jobs" element={<JobListPage />} />
          </Routes>
        </MemoryRouter>
      </AppProviders>,
    )
  }

  it('asks the server for the status in the address', async () => {
    await changeLanguage('en')
    wrapJobs('/jobs?status=failed')

    // Filtered by the server: the list is the last fifty jobs, so filtering
    // what arrived would search a window rather than the history.
    expect(await screen.findByText('No job matches this filter.')).toBeInTheDocument()
    expect(jobRequests).toContain('?status=failed')
  })

  it('ignores a status it does not know', async () => {
    await changeLanguage('en')
    wrapJobs('/jobs?status=whatever')

    await screen.findByRole('button', { name: 'Failed only' })
    expect(jobRequests).toContain('')
  })

  it('puts the filter in the address, so a number elsewhere can link to it', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrapJobs('/jobs')

    await user.click(await screen.findByRole('button', { name: 'Failed only' }))
    expect(jobRequests).toContain('?status=failed')
  })
})
