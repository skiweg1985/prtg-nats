import { QueryClient } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { ApiError } from '@/api/client'
import { AppProviders } from '@/app/providers'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { ErrorDetails } from '@/components/ui/ErrorDetails'
import i18n, { changeLanguage } from '@/i18n'

function wrap(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <AppProviders client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </AppProviders>,
  )
}

interface Row {
  id: string
  name: string
  count: number
}

const rows: Row[] = [
  { id: '1', name: 'berlin-01', count: 3 },
  { id: '2', name: 'hamburg-01', count: 7 },
  { id: '3', name: 'munich-01', count: 1 },
]

const columns: Column<Row>[] = [
  {
    key: 'name',
    header: 'Name',
    sortValue: (row) => row.name,
    searchValue: (row) => row.name,
    cell: (row) => row.name,
  },
  {
    key: 'count',
    header: 'Count',
    sortValue: (row) => row.count,
    cell: (row) => String(row.count),
  },
]

describe('DataTable', () => {
  it('renders every row', () => {
    wrap(
      <DataTable rows={rows} columns={columns} rowKey={(row) => row.id} emptyTitle="none" />,
    )
    expect(screen.getByText('berlin-01')).toBeInTheDocument()
    expect(screen.getByText('munich-01')).toBeInTheDocument()
  })

  it('filters as the operator types', async () => {
    const user = userEvent.setup()
    wrap(
      <DataTable rows={rows} columns={columns} rowKey={(row) => row.id} emptyTitle="none" />,
    )
    await user.type(screen.getByRole('searchbox'), 'hamburg')

    expect(screen.getByText('hamburg-01')).toBeInTheDocument()
    expect(screen.queryByText('berlin-01')).not.toBeInTheDocument()
  })

  it('shows the empty state when there is nothing to show', () => {
    wrap(
      <DataTable
        rows={[]}
        columns={columns}
        rowKey={(row) => row.id}
        emptyTitle="No probe is enrolled yet."
        emptyHint="Add one on the server."
      />,
    )
    expect(screen.getByText('No probe is enrolled yet.')).toBeInTheDocument()
    expect(screen.getByText('Add one on the server.')).toBeInTheDocument()
  })

  it('shows a loading state instead of an empty one while fetching', () => {
    const { container } = wrap(
      <DataTable
        rows={undefined}
        columns={columns}
        rowKey={(row) => row.id}
        isLoading
        emptyTitle="No probe is enrolled yet."
      />,
    )
    expect(screen.queryByText('No probe is enrolled yet.')).not.toBeInTheDocument()
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
  })
})

describe('ErrorDetails', () => {
  const unreachable = new ApiError(
    {
      code: 'probe.unreachable',
      message_key: 'errors.probe.unreachable',
      params: { probe: 'berlin-01' },
      fields: [],
      details: 'ssh: connect to host berlin-01 port 22: Connection timed out',
      correlation_id: 'abc123',
      retryable: true,
    },
    502,
  )

  it('answers what failed, why, and what to do', async () => {
    changeLanguage('en')
    wrap(<ErrorDetails error={unreachable} step="check_reachable" target="berlin-01" />)

    expect(
      screen.getByText(/did not answer over the management channel/),
    ).toBeInTheDocument()
    expect(screen.getByText(/The host is down/)).toBeInTheDocument()
    expect(screen.getByText(/Check that the host is reachable/)).toBeInTheDocument()
    // The step name is translated, the technical output is not.
    expect(screen.getByText('Check reachability')).toBeInTheDocument()
  })

  it('keeps the technical output behind a disclosure and never translates it', async () => {
    const user = userEvent.setup()
    wrap(<ErrorDetails error={unreachable} />)

    expect(screen.queryByText(/Connection timed out/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /technical details/i }))
    expect(screen.getByText(/Connection timed out/)).toBeInTheDocument()
    expect(screen.getByText(/abc123/)).toBeInTheDocument()
  })

  it('renders the same error in German', async () => {
    wrap(<ErrorDetails error={unreachable} />)
    await act(async () => changeLanguage('de'))

    expect(
      screen.getByText(/über den Management-Kanal nicht geantwortet/),
    ).toBeInTheDocument()
    expect(screen.getByText(/Der Host ist aus/)).toBeInTheDocument()
    await act(async () => changeLanguage('en'))
  })

  it('falls back to a generic message for an error it has no key for', async () => {
    changeLanguage('en')
    wrap(<ErrorDetails error={new Error('boom')} />)
    expect(screen.getByText('An unexpected error occurred.')).toBeInTheDocument()
  })
})

describe('i18n runtime', () => {
  it('resolves a backend message key with its parameters', () => {
    expect(i18n.t('errors.auth.permission_denied', { permission: 'sensor.deploy' })).toContain(
      'sensor.deploy',
    )
  })
})
