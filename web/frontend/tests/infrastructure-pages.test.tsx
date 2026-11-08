import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import type { ContainerState } from '@/api/types'
import { AppProviders } from '@/app/providers'
import {
  CertificatesPage,
  NatsPage,
  SettingsPage,
  containerState,
} from '@/features/misc/Pages'
import { changeLanguage } from '@/i18n'

/**
 * The infrastructure pages read like an admin template: raw English keys as
 * labels in the German interface, a red dot for a container nobody could ask
 * about, and "Renew" offered for a certificate that was never created.
 */

let docker = true
let certificates: unknown[] = []

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
        permissions: ['system.read', 'certificate.read', 'certificate.renew'],
        locale: 'de',
        is_development: false,
        must_change_password: false,
      },
    }),
  ),
  http.get('/api/v1/system/capabilities', () =>
    HttpResponse.json({ docker, runtime_state: 'missing', dev_auth: false }),
  ),
  http.get('/api/v1/system', () =>
    HttpResponse.json({
      site: {
        nats_fqdn: 'nats.example.test',
        nats_port: 4222,
        nats_host_ip: '192.0.2.10',
        ca_http_port: 8080,
        ca_organization: 'Example',
        prtg_core_ip: '192.0.2.20',
        nats_endpoint: 'nats.example.test:4222',
        is_configured: true,
      },
      nats: {
        available: true,
        healthy: true,
        server_name: 'prtg-nats',
        version: '2.10.0',
        uptime: '3d4h',
        connections: 2,
        jetstream: null,
      },
      containers: [
        {
          name: 'prtg-nats',
          exists: false,
          running: false,
          status: null,
          health: null,
          image: null,
          restart_count: 0,
        },
      ],
      certificates: [],
      capabilities: { docker, runtime_state: 'complete', dev_auth: false },
      runtime_missing: [],
    }),
  ),
  http.get('/api/v1/certificates', () => HttpResponse.json(certificates)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  docker = true
  certificates = []
})
afterAll(() => server.close())

function wrap(page: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>{page}</MemoryRouter>
    </AppProviders>,
  )
}

const CONTAINER: ContainerState = {
  name: 'prtg-nats',
  exists: true,
  running: false,
  status: 'exited',
  health: null,
  image: null,
  restart_count: 0,
}

describe('a container row', () => {
  it('is neutral when nobody could ask, and red only when it is known to be down', () => {
    expect(containerState({ ...CONTAINER, exists: false }, false)).toEqual({
      tone: 'neutral',
      key: 'unknown',
    })
    expect(containerState(CONTAINER, true)).toEqual({ tone: 'danger', key: 'stopped' })
    expect(containerState({ ...CONTAINER, exists: false }, true)).toEqual({
      tone: 'danger',
      key: 'absent',
    })
    expect(containerState({ ...CONTAINER, running: true }, true)).toEqual({
      tone: 'ok',
      key: 'running',
    })
  })
})

describe('the NATS page', () => {
  it('labels its rows in German and keeps the values as NATS reports them', async () => {
    await changeLanguage('de')
    docker = false
    wrap(<NatsPage />)

    expect(await screen.findByText('Läuft seit')).toBeInTheDocument()
    expect(screen.getByText('Servername')).toBeInTheDocument()
    expect(screen.getByText('3d4h')).toBeInTheDocument()
    expect(screen.queryByText('uptime')).not.toBeInTheDocument()
    expect(screen.getByText('Unbekannt')).toBeInTheDocument()
  })
})

describe('the certificates page', () => {
  it('points a missing certificate to the initialisation instead of offering renewal', async () => {
    await changeLanguage('de')
    certificates = [
      {
        kind: 'server',
        path: '/runtime/certs/server.pem',
        status: 'missing',
        subject: null,
        issuer: null,
        not_after: null,
        days_remaining: null,
        sha256: null,
        subject_alt_names: [],
        key_matches: null,
      },
    ]
    wrap(<CertificatesPage />)

    expect(await screen.findByText('Serverzertifikat')).toBeInTheDocument()
    expect(screen.getByText('Gültig bis')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Erneuern' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Zur Initialisierung' })).toHaveAttribute(
      'href',
      '/',
    )
  })
})

describe('the settings page', () => {
  it('names the runtime state instead of printing the code', async () => {
    await changeLanguage('de')
    wrap(<SettingsPage />)

    expect(await screen.findByText('Nicht initialisiert')).toBeInTheDocument()
    expect(screen.queryByText('missing')).not.toBeInTheDocument()
  })
})
