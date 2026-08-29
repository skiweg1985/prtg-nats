import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import type { ParameterSchema } from '@/api/types'
import { AppProviders } from '@/app/providers'
import { SensorVariants } from '@/features/sensors/SensorVariants'

/**
 * What the variant card has to get right is not layout, it is the handling of
 * secrets and the order of requests: a stored password is never shown and
 * never wiped by an empty field, and the certificate has to reach the server
 * before the profile that names its path.
 */

const SCHEMA: ParameterSchema = {
  parameters: [{ name: '--profile', type: 'string', description: '' }],
  settings: [
    { name: 'SSID', type: 'string', required: true, maps_to: '--ssid' },
    {
      name: 'AUTH',
      type: 'choice',
      choices: ['psk', 'peap', 'eap-tls'],
      required: true,
    },
  ],
  credentials: [{ name: 'PASSWORD', type: 'string', sensitive: true }],
  files: [{ name: 'CA_CERT', kind: 'certificate', max_bytes: 65536, extension: '.pem' }],
  supports_profiles: true,
  default_parameter_line: '',
}

let requests: { path: string; body: unknown }[] = []

const server = setupServer(
  http.get('/api/v1/auth/state', () =>
    HttpResponse.json({
      authenticated: true,
      setup_required: false,
      dev_auth: false,
      principal: {
        user_id: 'u1',
        username: 'admin',
        display_name: 'Admin',
        roles: ['admin'],
        permissions: ['sensor.read', 'sensor.configure'],
        locale: 'en',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/probes', () =>
    HttpResponse.json([
      {
        id: 'p1',
        nats_username: 'mpp-nord',
        display_name: 'Nord',
        ssh_host: '192.0.2.10',
        status: 'healthy',
        deviations: 0,
        sensors: [],
        observed_at: null,
      },
    ]),
  ),
  http.get('/api/v1/sensors/wlan-auth/profiles', () =>
    HttpResponse.json([
      {
        sensor: 'wlan-auth',
        name: 'standort-nord',
        updated_at: '2026-08-04T10:00:00Z',
        probes: ['mpp-nord'],
        files: [
          {
            key: 'CA_CERT',
            filename: 'CA_CERT.pem',
            size_bytes: 1234,
            sha256: 'a'.repeat(64),
            probe_path: '/etc/prtg-nats/sensors/wlan-auth/files/standort-nord/CA_CERT.pem',
          },
        ],
        parameter_line: '--profile standort-nord',
      },
    ]),
  ),
  http.get('/api/v1/sensors/wlan-auth/profiles/standort-nord', () =>
    HttpResponse.json({
      sensor: 'wlan-auth',
      name: 'standort-nord',
      updated_at: '2026-08-04T10:00:00Z',
      probes: ['mpp-nord'],
      files: [],
      parameter_line: '--profile standort-nord',
      values: { SSID: 'Corporate', AUTH: 'peap' },
      secrets_set: ['PASSWORD'],
    }),
  ),
  http.put('/api/v1/sensors/wlan-auth/profiles/:profile', async ({ request }) => {
    requests.push({ path: new URL(request.url).pathname, body: await request.json() })
    return HttpResponse.json({ job_id: 'j1', status: 'queued', events_url: '' })
  }),
  http.put(
    '/api/v1/sensors/wlan-auth/profiles/:profile/files/:key',
    async ({ request }) => {
      requests.push({ path: new URL(request.url).pathname, body: await request.json() })
      return HttpResponse.json({
        key: 'CA_CERT',
        filename: 'CA_CERT.pem',
        size_bytes: 5,
        sha256: 'b'.repeat(64),
        probe_path: '/etc/prtg-nats/sensors/wlan-auth/files/nord/CA_CERT.pem',
      })
    },
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  requests = []
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <SensorVariants sensorName="wlan-auth" schema={SCHEMA} />
      </MemoryRouter>
    </AppProviders>,
  )
}

/** The wlan-auth shape: an AUTH switch and fields that belong to one kind. */
const GROUPED_SCHEMA: ParameterSchema = {
  ...SCHEMA,
  credentials: [
    { name: 'PSK', type: 'string', sensitive: true, group: 'psk', required: true },
    {
      name: 'PASSWORD',
      type: 'string',
      sensitive: true,
      group: 'peap',
      required: true,
    },
  ],
  files: [
    { name: 'CA_CERT', kind: 'certificate', max_bytes: 65536, extension: '.pem' },
    {
      name: 'CLIENT_CERT',
      kind: 'certificate',
      max_bytes: 65536,
      extension: '.pem',
      group: 'eap-tls',
      required: true,
    },
  ],
}

function wrapGrouped() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>
        <SensorVariants sensorName="wlan-auth" schema={GROUPED_SCHEMA} />
      </MemoryRouter>
    </AppProviders>,
  )
}

describe('SensorVariants', () => {
  it('lists a variant with the line that selects it in PRTG', async () => {
    wrap()
    expect(await screen.findByText('standort-nord')).toBeInTheDocument()
    expect(screen.getByText('--profile standort-nord')).toBeInTheDocument()
    // The certificate is described, not offered for download.
    expect(screen.getByText('CA_CERT.pem')).toBeInTheDocument()
  })

  it('renders nothing for a sensor that takes everything from PRTG', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = render(
      <AppProviders client={client}>
        <MemoryRouter>
          <SensorVariants
            sensorName="aruba-uplink"
            schema={{ ...SCHEMA, supports_profiles: false, settings: [], credentials: [], files: [] }}
          />
        </MemoryRouter>
      </AppProviders>,
    )
    expect(container.textContent).toBe('')
  })

  it('fills the settings back in but never the password', async () => {
    const user = userEvent.setup()
    wrap()
    await user.click(await screen.findByRole('button', { name: /bearbeiten|edit/i }))

    await waitFor(() => expect(screen.getByDisplayValue('Corporate')).toBeInTheDocument())
    const password = screen.getByLabelText(/PASSWORD/i) as HTMLInputElement
    expect(password.value).toBe('')
    expect(password.type).toBe('password')
  })

  it('sends the certificate before the profile that names its path', async () => {
    const user = userEvent.setup()
    wrap()
    await user.click(await screen.findByRole('button', { name: /variante anlegen|add variant/i }))

    await user.type(screen.getByLabelText(/name/i), 'gaeste')
    await user.type(screen.getByLabelText(/SSID/i), 'Guest')
    await user.selectOptions(screen.getByLabelText(/AUTH/i), 'psk')
    await user.upload(
      screen.getByLabelText(/CA_CERT/i),
      new File(['cert!'], 'radius-ca.pem', { type: 'application/x-pem-file' }),
    )
    // The file is read asynchronously; the hint changes once it is in hand.
    await waitFor(() => expect(screen.getByText('radius-ca.pem')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /speichern|save/i }))

    await waitFor(() => expect(requests.length).toBe(2))
    // The sensor checks that a path from the profile exists; the other order
    // would deploy a profile pointing at a file the probe has not seen.
    expect(requests[0].path).toContain('/files/CA_CERT')
    expect(requests[1].path).toMatch(/\/profiles\/gaeste$/)
    expect(requests[1].body).toMatchObject({
      values: { SSID: 'Guest', AUTH: 'psk' },
    })
  })

  it('will not save a variant that is missing a required setting', async () => {
    const user = userEvent.setup()
    wrap()
    await user.click(await screen.findByRole('button', { name: /variante anlegen|add variant/i }))
    await user.type(screen.getByLabelText(/name/i), 'gaeste')

    expect(screen.getByRole('button', { name: /speichern|save/i })).toBeDisabled()
  })

  it('shows only the fields of the chosen auth kind', async () => {
    const user = userEvent.setup()
    wrapGrouped()
    await user.click(
      await screen.findByRole('button', { name: /variante anlegen|add variant/i }),
    )

    // A PSK variant has no business seeing the PEAP password or the client
    // certificate - the schema says so via groups, and now the dialog reads it.
    await user.selectOptions(screen.getByLabelText(/^AUTH/), 'psk')
    expect(screen.getByLabelText(/^PSK/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/^PASSWORD/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/^CLIENT_CERT/)).not.toBeInTheDocument()
    // The ungrouped CA certificate applies to every kind.
    expect(screen.getByLabelText(/^CA_CERT/)).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText(/^AUTH/), 'peap')
    expect(screen.queryByLabelText(/^PSK/)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/^PASSWORD/)).toBeInTheDocument()
  })

  it('treats a grouped credential as required only while its group applies', async () => {
    const user = userEvent.setup()
    wrapGrouped()
    await user.click(
      await screen.findByRole('button', { name: /variante anlegen|add variant/i }),
    )
    await user.type(screen.getByLabelText(/name/i), 'gaeste')
    await user.type(screen.getByLabelText(/^SSID/), 'Guest')
    await user.selectOptions(screen.getByLabelText(/^AUTH/), 'psk')

    // The PSK is the whole credential of this kind - without it the variant
    // fails on every run, which used to surface only in PRTG.
    expect(screen.getByRole('button', { name: /speichern|save/i })).toBeDisabled()
    await user.type(screen.getByLabelText(/^PSK/), 'wpa2-passphrase')
    expect(screen.getByRole('button', { name: /speichern|save/i })).toBeEnabled()
  })

  it('names the probes a deletion reaches before it happens', async () => {
    const user = userEvent.setup()
    wrap()

    await screen.findByText('standort-nord')
    await user.click(screen.getByRole('button', { name: /entfernen|remove/i }))

    // Named, not counted - and nothing was deleted yet.
    expect(
      await screen.findByText(/removed from the server and from these probes/),
    ).toHaveTextContent('mpp-nord')
    expect(
      requests.filter(
        (entry) => entry.path.includes('profiles') && entry.body === null,
      ),
    ).toHaveLength(0)
  })
})
