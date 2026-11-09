import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
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
let jobs: object[] = []

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
    auth: idle ? 'peap' : 'eap-tls',
    profile: idle ? null : 'corp-certauth',
    bssid: null,
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
  http.get('/api/v1/probes/P1/sensors/wlan-auth/jobs', () => HttpResponse.json(jobs)),
  http.delete('/api/v1/probes/P1/sensors/wlan-auth/jobs/:label', ({ params }) => {
    removed.push(String(params.label))
    return new HttpResponse(null, { status: 204 })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
beforeEach(async () => {
  await changeLanguage('en')
  removed.length = 0
  jobs = [job('a1b2c3d4e5f6', false), job('0f0e0d0c0b0a', true)]
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
  })

  it('shows each configuration in labelled columns', async () => {
    show()
    await screen.findByText('Measured')
    const [current, leftover] = screen.getAllByRole('listitem')
    const value = (row: HTMLElement, label: string) =>
      within(row).getByText(label, { selector: 'dt' }).nextElementSibling?.textContent
    // Two configurations of one SSID, told apart by variant and method.
    expect(value(current, 'Variant')).toBe('corp-certauth')
    expect(value(current, 'Method')).toBe('EAP-TLS')
    expect(value(current, 'Polling interval')).toBe('1m 0s')
    expect(value(current, 'Last asked')).toBe('10s ago')
    expect(value(current, 'Last measurement')).toBe('OK30s ago')
    expect(value(leftover, 'Method')).toBe('PEAP')
    // A failed run is a state with its reason, not words in a sentence.
    expect(value(leftover, 'Last measurement')).toBe(
      'Failed6m 40s agoThe RADIUS server rejected the credentials',
    )
    // The header names every column once.
    for (const label of ['SSID', 'Variant', 'Method', 'Polling interval', 'Last asked']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(2)
    }
  })

  it('keeps the shape of a row whose values are missing', async () => {
    // A configuration registered a moment ago, from a helper that does not
    // name method and variant yet: every column is still there.
    jobs = [
      {
        label: '0a0b0c0d0e0f',
        interface: 'wlan0',
        ssid: 'Example-Guest',
        stage: 'dhcp',
        result: 'pending',
        code: 'not-measured',
        age: null,
        asked_every: null,
        asked_ago: 2,
        interval: 300,
        idle: false,
        expires_in: 3600,
        sensor_id: null,
      },
    ]
    show()
    await screen.findByText('Measured')
    const [row] = screen.getAllByRole('listitem')
    const labels = Array.from(row.querySelectorAll('dt')).map((dt) => dt.textContent)
    expect(labels).toEqual([
      'SSID',
      'Variant',
      'Method',
      'Polling interval',
      'Last asked',
      'Last measurement',
      'Actions',
    ])
    const value = (label: string) =>
      within(row).getByText(label, { selector: 'dt' }).nextElementSibling?.textContent
    expect(value('Variant')).toBe('–')
    expect(value('Method')).toBe('–')
    expect(value('Polling interval')).toBe('–')
    expect(value('Last measurement')).toBe('–')
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
