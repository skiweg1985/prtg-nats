import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import type { ParameterSchema, SensorDetail } from '@/api/types'
import { AppProviders } from '@/app/providers'
import { ParameterCard, PrtgCard } from '@/features/sensors/SensorPages'

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

describe('ParameterCard', () => {
  // The same five facts the old two-card layout guaranteed, now on the one
  // surface that replaced it.
  it('lists every fillable parameter with its meaning', () => {
    wrap(<ParameterCard sensorName="aruba-uplink" schema={schema} />)
    expect(screen.getByText('--target')).toBeInTheDocument()
    expect(
      screen.getByText(/Which uplink kind is the main path/),
    ).toBeInTheDocument()
  })

  it('keeps the PRTG-substituted parameters, folded away', () => {
    wrap(<ParameterCard sensorName="aruba-uplink" schema={schema} />)
    // --host is not a form field - PRTG fills it - but its placeholder and
    // meaning stay reachable: in the folded section and the recommended line.
    expect(screen.getByText('--host')).toBeInTheDocument()
    expect(screen.getAllByText(/%host/).length).toBeGreaterThan(0)
    expect(
      screen.getByText(/Address of the Aruba gateway of this site\./),
    ).toBeInTheDocument()
  })

  it('offers the choices instead of a bare text field', () => {
    wrap(<ParameterCard sensorName="aruba-uplink" schema={schema} />)
    expect(screen.getByRole('option', { name: 'wired' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'cellular' })).toBeInTheDocument()
  })

  it('marks what a variant can supply, so PRTG may leave it out', () => {
    wrap(<ParameterCard sensorName="aruba-uplink" schema={schema} />)
    // The hint names the profile key behind the parameter, in either language.
    expect(screen.getByText(/(variant|Variante).*SSID/i)).toBeInTheDocument()
  })

  it('renders nothing at all for a sensor without parameters', () => {
    const { container } = wrap(
      <ParameterCard sensorName="plain" schema={null} />,
    )
    // A card that could only say "nothing here" would be noise.
    expect(container.querySelector('section')).toBeNull()
  })
})

/**
 * The one translation every setup has to make: the repository path is
 * script/wlan-auth.py, the PRTG dropdown shows wlan-auth.py.
 */
describe('PrtgCard', () => {
  const sensor: SensorDetail = {
    name: 'wlan-auth',
    version: '6',
    description: 'WLAN auth',
    needs_interface: true,
    requires_privileged_helper: true,
    iperf_kind: null,
    supports_profiles: true,
    installed_on: 1,
    outdated_on: 0,
    files: [
      {
        slot: 'script',
        relative_path: 'script/wlan-auth.py',
        size_bytes: 1000,
        sha256: 'ab'.repeat(32),
      },
    ],
    parameter_schema: null,
    readme: `# wlan-auth

**Create the sensor in PRTG** and select \`wlan-auth.py\`.

## Credentials

| Setting | Value |
| --- | --- |
| User | probe |

> Keep the credentials on the probe.

- Copy the profile.
- Create the sensor.

\`\`\`shell
iperf3 --version
\`\`\`

See [internet-speed](../internet-speed/README.md), the
[credentials section](#credentials), or the
[iperf documentation](https://software.es.net/iperf/).
`,
    profile_template: null,
    installations: [],
  }

  it('names the script as the PRTG dropdown shows it', () => {
    wrap(<PrtgCard sensor={sensor} />)
    expect(screen.getByText('wlan-auth.py')).toBeInTheDocument()
    expect(screen.queryByText('script/wlan-auth.py')).not.toBeInTheDocument()
  })

  it("renders the sensor's own manual as rich Markdown", async () => {
    const user = userEvent.setup()
    wrap(<PrtgCard sensor={sensor} />)
    await user.click(screen.getByText(/manual|README/i))

    expect(await screen.findByRole('heading', { level: 1, name: 'wlan-auth' })).toHaveAttribute(
      'id',
      'wlan-auth',
    )
    expect(screen.getByText('Create the sensor in PRTG')).toHaveProperty(
      'tagName',
      'STRONG',
    )
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('blockquote')).toHaveTextContent(
      'Keep the credentials on the probe.',
    )
    expect(screen.getByRole('list')).toBeInTheDocument()
    expect(screen.getByText('wlan-auth.py', { selector: 'p code' })).toBeInTheDocument()
    expect(
      screen.getByText('iperf3 --version', { selector: 'pre code' }),
    ).toBeInTheDocument()
  })

  it('keeps manual links useful and safe', async () => {
    const user = userEvent.setup()
    wrap(<PrtgCard sensor={sensor} />)
    await user.click(screen.getByText(/manual|README/i))

    expect(await screen.findByRole('link', { name: 'internet-speed' })).toHaveAttribute(
      'href',
      '/sensors/internet-speed',
    )
    expect(screen.getByRole('link', { name: 'credentials section' })).toHaveAttribute(
      'href',
      '#credentials',
    )
    expect(screen.getByRole('heading', { level: 2, name: 'Credentials' })).toHaveAttribute(
      'id',
      'credentials',
    )
    expect(screen.getByRole('link', { name: 'iperf documentation' })).toHaveAttribute(
      'target',
      '_blank',
    )
    expect(screen.getByRole('link', { name: 'iperf documentation' })).toHaveAttribute(
      'rel',
      'noreferrer noopener',
    )
  })

  it('does not insert HTML from a manual into the page', async () => {
    const user = userEvent.setup()
    const { container } = wrap(
      <PrtgCard
        sensor={{
          ...sensor,
          readme:
            '<img src="missing" onerror="alert(1)">\n\nSafe text with an [unsafe link](javascript:alert(1)).',
        }}
      />,
    )
    await user.click(screen.getByText(/manual|README/i))
    await screen.findByText(/Safe text with an/)

    expect(container.querySelector('img')).not.toBeInTheDocument()
    expect(screen.getByText(/Safe text with an/)).toBeInTheDocument()
    expect(
      screen.getByText('unsafe link', { selector: 'a' }).getAttribute('href'),
    ).not.toMatch(/^javascript:/)
  })
})
