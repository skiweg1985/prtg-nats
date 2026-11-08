import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { JobDetailPage } from '@/features/jobs/JobPages'
import { changeLanguage } from '@/i18n'

/**
 * A retry asks for the permission of the job it repeats, not only job.retry.
 * The server refuses either way; the button follows so nobody is offered a
 * refusal.
 */

function principal(permissions: string[]) {
  return {
    authenticated: true,
    setup_required: false,
    dev_auth: false,
    principal: {
      user_id: 'U2',
      username: 'operator',
      display_name: 'operator',
      roles: ['operator'],
      permissions,
      locale: 'en',
      is_development: false,
      must_change_password: false,
    },
  }
}

const JOB = {
  id: 'J1',
  type: 'certificate.renew',
  status: 'failed',
  target_type: 'system',
  target_id: null,
  target_label: 'NATS server certificate',
  progress: 0,
  current_step: null,
  requested_by_name: 'admin',
  trigger: 'user',
  created_at: '2026-08-27T10:00:00Z',
  started_at: '2026-08-27T10:00:00Z',
  finished_at: '2026-08-27T10:01:00Z',
  duration_seconds: 60,
  blocked_reason_key: null,
  blocked_by_job_id: null,
  error_code: null,
  steps: [],
  payload: {},
  result: null,
  error_params: null,
  error_details: null,
  retry_of_job_id: null,
  retry_permissions: ['certificate.renew'],
}

const OPERATOR = ['job.read', 'job.retry', 'probe.read']

const server = setupServer(
  http.get('/api/v1/auth/state', () => HttpResponse.json(principal(OPERATOR))),
  http.get('/api/v1/jobs/J1', () => HttpResponse.json(JOB)),
  http.get('/api/v1/jobs/J1/log', () => HttpResponse.json([])),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/jobs/J1']}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('the retry button', () => {
  it('stays hidden when the job type needs a permission the user lacks', async () => {
    await changeLanguage('en')
    wrap()

    await screen.findByText('NATS server certificate')
    expect(screen.queryByRole('button', { name: 'Run again' })).not.toBeInTheDocument()
  })

  it('shows when the user holds both permissions', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/auth/state', () =>
        HttpResponse.json(principal([...OPERATOR, 'certificate.renew'])),
      ),
    )
    wrap()

    expect(await screen.findByRole('button', { name: 'Run again' })).toBeInTheDocument()
  })

  it('stays hidden for a job type the server no longer knows', async () => {
    await changeLanguage('en')
    server.use(
      http.get('/api/v1/auth/state', () =>
        HttpResponse.json(principal([...OPERATOR, 'certificate.renew'])),
      ),
      http.get('/api/v1/jobs/J1', () =>
        HttpResponse.json({ ...JOB, retry_permissions: null }),
      ),
    )
    wrap()

    await screen.findByText('NATS server certificate')
    expect(screen.queryByRole('button', { name: 'Run again' })).not.toBeInTheDocument()
  })
})
