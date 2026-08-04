import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import type { ParameterSchema } from '@/api/types'
import { AppProviders } from '@/app/providers'
import { ParameterReference } from '@/features/sensors/SensorPages'

function wrap(children: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </AppProviders>,
  )
}

/** Shaped like sensors/aruba-uplink and sensors/wlan-auth actually declare. */
const schema: ParameterSchema = {
  parameters: [
    {
      name: '--host',
      type: 'string',
      required: true,
      description: 'Address of the Aruba gateway of this site.',
      source: 'prtg',
      prtg_placeholder: '%host',
    },
    {
      name: '--primary',
      type: 'choice',
      choices: ['wired', 'cellular'],
      default: 'wired',
      description: 'Which uplink kind is the main path.',
    },
    {
      name: '--ssid',
      type: 'string',
      description: 'The SSID to authenticate against.',
    },
    {
      name: '--target',
      type: 'string',
      repeatable: true,
      description: 'A target on the internet. Repeatable.',
    },
  ],
  settings: [{ name: 'SSID', type: 'string', maps_to: '--ssid' }],
  credentials: [],
  files: [],
  supports_profiles: true,
  default_parameter_line: '--host %host',
}

describe('ParameterReference', () => {
  it('lists every parameter with its meaning', () => {
    wrap(<ParameterReference schema={schema} />)
    expect(screen.getByText('--host')).toBeInTheDocument()
    expect(screen.getByText('--target')).toBeInTheDocument()
    expect(
      screen.getByText('Address of the Aruba gateway of this site.'),
    ).toBeInTheDocument()
  })

  it('shows the placeholder for a value PRTG substitutes', () => {
    wrap(<ParameterReference schema={schema} />)
    // Once in the recommended line, once as the default of --host.
    expect(screen.getAllByText(/%host/).length).toBeGreaterThan(0)
  })

  it('names the choices instead of the bare type', () => {
    wrap(<ParameterReference schema={schema} />)
    expect(screen.getByText('wired | cellular')).toBeInTheDocument()
  })

  it('marks what a variant can supply, so PRTG may leave it out', () => {
    wrap(<ParameterReference schema={schema} />)
    // The badge names the profile key behind the parameter, in either language.
    expect(screen.getByText(/(variant|Variante).*SSID/i)).toBeInTheDocument()
  })

  it('falls back to the README hint when a sensor declares nothing', () => {
    wrap(<ParameterReference schema={null} />)
    expect(screen.getByText(/README/)).toBeInTheDocument()
  })
})
