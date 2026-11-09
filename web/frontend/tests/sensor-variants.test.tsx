import { FIELD } from './field-query'
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
  settings: [
    ...SCHEMA.settings,
    { name: 'IDENTITY', type: 'string', groups: ['peap', 'eap-tls'], required: true },
    {
      name: 'NO_VERIFY_SERVER',
      type: 'boolean',
      groups: ['peap', 'eap-tls'],
    },
  ],
  credentials: [
    {
      name: 'PSK',
      type: 'string',
      sensitive: true,
      groups: ['psk'],
      required: true,
    },
    {
      name: 'PASSWORD',
      type: 'string',
      sensitive: true,
      groups: ['peap'],
      required: true,
    },
  ],
  files: [
    {
      name: 'CA_CERT',
      kind: 'certificate',
      max_bytes: 65536,
      extension: '.pem',
      required: true,
      groups: ['peap', 'eap-tls'],
      required_unless: 'NO_VERIFY_SERVER',
    },
    {
      name: 'CLIENT_CERT',
      kind: 'certificate',
      max_bytes: 65536,
      extension: '.pem',
      groups: ['eap-tls'],
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
    const password = screen.getByLabelText(/^Password/, FIELD) as HTMLInputElement
    expect(password.value).toBe('')
    expect(password.type).toBe('password')
  })

  it('sends the certificate before the profile that names its path', async () => {
    const user = userEvent.setup()
    wrap()
    await user.click(await screen.findByRole('button', { name: /variante anlegen|add variant/i }))

    await user.type(screen.getByLabelText(/^Name of the variant/, FIELD), 'gaeste')
    await user.type(screen.getByLabelText(/^Network name/, FIELD), 'Guest')
    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'psk')
    await user.upload(
      screen.getByLabelText(/^CA certificate/, FIELD),
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
    await user.type(screen.getByLabelText(/^Name of the variant/, FIELD), 'gaeste')

    expect(screen.getByRole('button', { name: /speichern|save/i })).toBeDisabled()
    // Named the way the form names them, not by their profile keys.
    expect(
      screen.getByText('Required fields are still missing: Network name, Sign-in method'),
    ).toBeInTheDocument()
  })

  it('labels the fields for a person, the profile key muted beside them', async () => {
    const user = userEvent.setup()
    wrapGrouped()
    await user.click(
      await screen.findByRole('button', { name: /variante anlegen|add variant/i }),
    )
    const method = screen.getByRole('combobox', { name: 'Sign-in method' })
    expect(screen.getByText('auth')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'PEAP/MSCHAPv2' })).toHaveValue('peap')
    // The help is the translated one, read with the field.
    expect(method).toHaveAccessibleDescription(
      'Decides which of the other fields are needed.',
    )
  })

  it('shows only the fields of the chosen auth kind', async () => {
    const user = userEvent.setup()
    wrapGrouped()
    await user.click(
      await screen.findByRole('button', { name: /variante anlegen|add variant/i }),
    )

    // A PSK variant has no business seeing the PEAP password or the client
    // certificate - the schema says so via groups, and now the dialog reads it.
    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'psk')
    expect(screen.getByLabelText(/^WPA2 passphrase/, FIELD)).toBeInTheDocument()
    expect(screen.queryByLabelText(/^Password/, FIELD)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/^Client certificate/, FIELD)).not.toBeInTheDocument()
    // The CA certificate verifies the RADIUS server, so a PSK variant has
    // no use for it either.
    expect(screen.queryByLabelText(/^CA certificate/, FIELD)).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'peap')
    expect(screen.queryByLabelText(/^WPA2 passphrase/, FIELD)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/^Password/, FIELD)).toBeInTheDocument()
  })

  it('stops asking for the CA once the variant waives the check', async () => {
    const user = userEvent.setup()
    wrapGrouped()
    await user.click(
      await screen.findByRole('button', { name: /variante anlegen|add variant/i }),
    )
    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'peap')

    // The CA certificate is what verifies the RADIUS server. Asking for it
    // while the variant has just said not to verify leaves a form that
    // cannot be submitted and a file that the run would ignore.
    const ca = screen.getByLabelText(/^CA certificate/, FIELD) as HTMLInputElement
    // Required is the norm and carries no mark; the exception says so.
    const head = ca.labels?.[0].parentElement
    expect(head).toHaveTextContent('CA certificate')
    expect(head).not.toHaveTextContent(/optional/)

    await user.click(screen.getByLabelText(/^Skip server check/, FIELD))
    expect(head).toHaveTextContent(/optional/)
    // Still offered, because a variant may verify and name no authority
    // only by saying so - the field just stops being demanded.
    expect(ca).toBeInTheDocument()
  })

  it('names the chosen file in the upload box', async () => {
    const user = userEvent.setup()
    wrapGrouped()
    await user.click(
      await screen.findByRole('button', { name: /variante anlegen|add variant/i }),
    )
    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'eap-tls')

    // The box is the whole control: the native input draws a grey button and
    // "no file chosen", which was read as text rather than as something to
    // click. It prompts while empty and carries the file name once there is
    // one - and the field name stays its accessible name either way.
    const picker = screen.getByLabelText(/^Client certificate/, FIELD) as HTMLInputElement
    const box = picker.labels?.[1]
    expect(box).toHaveTextContent(/datei w\u00e4hlen|choose file/i)

    await user.upload(
      picker,
      new File(['-----BEGIN CERTIFICATE-----'], 'probe.pem', {
        type: 'application/x-pem-file',
      }),
    )
    await waitFor(() => expect(box).toHaveTextContent('probe.pem'))
  })

  it('shows a field that names several kinds under each of them', async () => {
    const user = userEvent.setup()
    wrapGrouped()
    await user.click(
      await screen.findByRole('button', { name: /variante anlegen|add variant/i }),
    )

    // The user name reaches the RADIUS server under PEAP and under EAP-TLS
    // alike. Declared for one kind only, the form hid it from the other, and
    // the variant then failed on the probe with "requires an identity".
    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'peap')
    expect(screen.getByLabelText(/^Identity/, FIELD)).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'eap-tls')
    expect(screen.getByLabelText(/^Identity/, FIELD)).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'psk')
    expect(screen.queryByLabelText(/^Identity/, FIELD)).not.toBeInTheDocument()
  })

  it('treats a grouped credential as required only while its group applies', async () => {
    const user = userEvent.setup()
    wrapGrouped()
    await user.click(
      await screen.findByRole('button', { name: /variante anlegen|add variant/i }),
    )
    await user.type(screen.getByLabelText(/^Name of the variant/, FIELD), 'gaeste')
    await user.type(screen.getByLabelText(/^Network name/, FIELD), 'Guest')
    await user.selectOptions(screen.getByLabelText(/^Sign-in method/, FIELD), 'psk')

    // The PSK is the whole credential of this kind - without it the variant
    // fails on every run, which used to surface only in PRTG.
    expect(screen.getByRole('button', { name: /speichern|save/i })).toBeDisabled()
    await user.type(screen.getByLabelText(/^WPA2 passphrase/, FIELD), 'wpa2-passphrase')
    expect(screen.getByRole('button', { name: /speichern|save/i })).toBeEnabled()
  })

  it('names the probes a deletion reaches before it happens', async () => {
    const user = userEvent.setup()
    wrap()

    await screen.findByText('standort-nord')
    await user.click(screen.getByRole('button', { name: /more actions|weitere aktionen/i }))
    await user.click(screen.getByRole('menuitem', { name: /entfernen|remove/i }))

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
