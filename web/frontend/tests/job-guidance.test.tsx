import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import type { JobDetail } from '@/api/types'
import { JobDetailPage } from '@/features/jobs/JobPages'
import { changeLanguage } from '@/i18n'

/**
 * The job page is where almost every flow ends, so it is where the hand-off
 * to PRTG has to be said. The wizard's success banner only lives while its
 * tab is open; a job opened from the list would otherwise read as finished
 * while two manual steps still remain.
 */

const PROBES = [
  { id: 'P1', nats_username: 'mpp-berlin', display_name: 'Berlin', host: 'a.example' },
]

function job(overrides: Partial<JobDetail>): JobDetail {
  return {
    id: 'J1',
    type: 'probe.enroll',
    status: 'successful',
    target_type: 'probe',
    target_id: 'mpp-berlin',
    target_label: 'mpp-berlin',
    progress: 1,
    current_step: null,
    requested_by_name: 'admin',
    trigger: 'user',
    created_at: '2026-08-01T10:00:00Z',
    started_at: '2026-08-01T10:00:00Z',
    finished_at: '2026-08-01T10:01:00Z',
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
    ...overrides,
  }
}

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
        permissions: ['job.read', 'probe.read', 'job.retry', 'iperf.manage'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () => HttpResponse.json(PROBES)),
  http.get('/api/v1/jobs/J1/log', () => HttpResponse.json([])),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(detail: JobDetail) {
  server.use(http.get('/api/v1/jobs/J1', () => HttpResponse.json(detail)))
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

describe('the job page names the next step', () => {
  it('says the two PRTG steps after a successful enrollment', async () => {
    await changeLanguage('en')
    wrap(job({}))

    expect(await screen.findByText('The probe is enrolled')).toBeInTheDocument()
    // The enrollment job names its probe by NATS account, not record id -
    // the link still has to reach the probe's page.
    const link = screen.getByRole('link', { name: 'Open the probe' })
    expect(link).toHaveAttribute('href', '/probes/P1')
  })

  it('points a finished rollout at PRTG and renders its outcome', async () => {
    await changeLanguage('en')
    wrap(
      job({
        type: 'sensor.deploy',
        target_type: 'deployment',
        target_id: 'D1',
        target_label: 'wlan-auth → 2 probe(s)',
        result: {
          sensor: 'wlan-auth',
          version: '6',
          succeeded: ['mpp-berlin'],
          failed: ['mpp-hamburg'],
          dry_run: false,
        },
      }),
    )

    expect(await screen.findByText('The sensor is on the probes')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'To the sensor' })).toHaveAttribute(
      'href',
      '/sensors/wlan-auth',
    )
    // The outcome is rendered, not dumped: one probe per line, not a JSON blob.
    expect(screen.getByText('1 probe succeeded')).toBeInTheDocument()
    expect(screen.getByText('1 probe failed')).toBeInTheDocument()
    expect(screen.queryByText(/"succeeded"/)).not.toBeInTheDocument()
  })

  it('marks a dry run as one instead of celebrating it', async () => {
    await changeLanguage('en')
    wrap(
      job({
        type: 'sensor.deploy',
        target_type: 'deployment',
        target_id: 'D1',
        target_label: 'wlan-auth → 2 probe(s)',
        result: { sensor: 'wlan-auth', succeeded: [], failed: [], dry_run: true },
      }),
    )

    expect(await screen.findByText('That was a dry run')).toBeInTheDocument()
    expect(screen.queryByText('The sensor is on the probes')).not.toBeInTheDocument()
  })

  it('keeps the raw result reachable for job types without a renderer', async () => {
    await changeLanguage('en')
    wrap(
      job({
        type: 'system.backup',
        target_type: null,
        target_id: null,
        target_label: null,
        result: { archive: 'backup-2026.tar.gz' },
      }),
    )

    expect(await screen.findByText('Raw result')).toBeInTheDocument()
  })

  it('asks for the foreign password again instead of retrying without it', async () => {
    await changeLanguage('en')
    wrap(
      job({
        type: 'iperf.update_foreign_credentials',
        status: 'partially_successful',
        target_type: 'iperf_endpoint',
        target_id: 'provider',
        target_label: 'provider → 2 probe(s)',
        error_code: 'job.partial_failure',
        error_params: { failed: 1 },
      }),
    )

    expect(
      await screen.findByRole('link', { name: 'Enter password again' }),
    ).toHaveAttribute('href', '/infrastructure/iperf/provider')
    expect(screen.queryByRole('button', { name: 'Run again' })).not.toBeInTheDocument()
  })

  it('does not ask for the foreign password again after a successful update', async () => {
    await changeLanguage('en')
    wrap(
      job({
        type: 'iperf.update_foreign_credentials',
        target_type: 'iperf_endpoint',
        target_id: 'provider',
        target_label: 'provider → 2 probe(s)',
      }),
    )

    expect(await screen.findByText('iperf.update_foreign_credentials')).toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: 'Enter password again' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run again' })).not.toBeInTheDocument()
  })
})
