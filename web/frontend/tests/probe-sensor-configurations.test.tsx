import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { ProbeSensorPage } from '@/features/probes/ProbeSensorPage'
import { changeLanguage } from '@/i18n'

/**
 * The configurations a wlan-auth helper holds on a probe. Changing a
 * parameter in PRTG makes a new one; the old one pauses after three
 * intervals unasked and can be removed here at once.
 */

const removed: string[] = []

function probeDetail() {
  return {
    summary: {
      id: 'P1',
      nats_username: 'mpp-berlin-01',
      display_name: 'Berlin',
      host: '198.51.100.29',
      probe_name: 'berlin-01',
      status: 'healthy',
    },
    sensors: [
      {
        name: 'wlan-auth',
        status: 'current',
        desired_version: '18',
        installed_version: '18',
        tool_name: null,
        installed_tool_version: null,
        interfaces: ['wlan0'],
        helper_state: 'listening',
      },
    ],
  }
}

function job(label: string, idle: boolean) {
  return {
    label,
    interface: 'wlan0',
    ssid: 'Example-Guest',
    stage: 'dhcp',
    result: idle ? 'failed' : 'ok',
    code: idle ? 'auth-failed-eap' : 'ok',
    age: idle ? 400 : 30,
    asked_every: 60,
    asked_ago: idle ? 400 : 10,
    interval: 60,
    idle,
    expires_in: 3200,
    sensor_id: idle ? null : '2041',
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
        username: 'olga',
        display_name: 'Olga',
        roles: ['operator'],
        permissions: ['probe.read', 'sensor.read', 'sensor.configure', 'sensor_log.read'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes/P1', () => HttpResponse.json(probeDetail())),
  http.get('/api/v1/probes/P1/prtg-name', () => HttpResponse.json({ state: 'unlinked' })),
  http.get('/api/v1/sensor-logs/timeline', () =>
    HttpResponse.json({
      probe: 'mpp-berlin-01',
      sensor: 'wlan-auth',
      start: '2026-11-08T00:00:00Z',
      until: '2026-11-08T01:00:00Z',
      bucket_seconds: 900,
      retained_since: '2026-10-27T00:00:00Z',
      streams: [],
    }),
  ),
  http.get('/api/v1/probes/P1/sensors/wlan-auth/jobs', () =>
    HttpResponse.json([job('a1b2c3d4e5f6', false), job('0f0e0d0c0b0a', true)]),
  ),
  http.delete('/api/v1/probes/P1/sensors/wlan-auth/jobs/:label', ({ params }) => {
    removed.push(String(params.label))
    return new HttpResponse(null, { status: 204 })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
beforeEach(async () => {
  await changeLanguage('en')
  removed.length = 0
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter initialEntries={['/probes/P1/sensors/wlan-auth']}>
        <Routes>
          <Route path="/probes/:probeId/sensors/:sensorName" element={<ProbeSensorPage />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('configurations on the probe', () => {
  it('lists what the probe measures and which configuration is paused', async () => {
    show()
    expect(await screen.findByText('Configurations on the probe')).toBeInTheDocument()
    expect(await screen.findByText('Measured')).toBeInTheDocument()
    expect(screen.getByText('Paused, not asked')).toBeInTheDocument()
    expect(screen.getByText(/sensor 2041/)).toBeInTheDocument()
    expect(screen.getByText(/last auth-failed-eap/)).toBeInTheDocument()
  })

  it('removes a leftover after asking', async () => {
    show()
    await screen.findByText('Paused, not asked')
    const buttons = screen.getAllByRole('button', { name: 'Remove' })
    await userEvent.click(buttons[1])
    const confirm = await screen.findByRole('dialog')
    expect(confirm).toHaveTextContent('Remove the configuration for Example-Guest?')
    await userEvent.click(
      Array.from(confirm.querySelectorAll('button')).find(
        (button) => button.textContent === 'Remove',
      ) as HTMLElement,
    )
    await waitFor(() => expect(removed).toEqual(['0f0e0d0c0b0a']))
  })
})
