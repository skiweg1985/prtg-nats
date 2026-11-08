import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AppProviders } from '@/app/providers'
import { AuthGate } from '@/features/auth/AuthGate'
import { changeLanguage } from '@/i18n'

/**
 * The first administrator needs the code `prtg-nats setup` printed on the
 * host. The form asks for it and says where to find it.
 */

const FRESH = { authenticated: false, setup_required: true, dev_auth: false, principal: null }

let submitted: Record<string, unknown> | null = null

const server = setupServer(
  http.get('/api/v1/auth/state', () => HttpResponse.json(FRESH)),
  http.post('/api/v1/auth/setup', async ({ request }) => {
    submitted = (await request.json()) as Record<string, unknown>
    return HttpResponse.json(
      {
        error: {
          code: 'auth.setup_code_invalid',
          message_key: 'errors.auth.setup_code_invalid',
          params: {},
          retryable: false,
          correlation_id: 'c1',
        },
      },
      { status: 403 },
    )
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => {
  server.resetHandlers()
  submitted = null
})
afterAll(() => server.close())

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AppProviders client={client}>
      <AuthGate>
        <p>application</p>
      </AuthGate>
    </AppProviders>,
  )
}

describe('creating the first administrator', () => {
  it('sends the setup code with the account', async () => {
    await changeLanguage('en')
    const user = userEvent.setup()
    wrap()

    await user.type(await screen.findByLabelText(/Setup code/), 'ABCD-EFGH-JKMN-PQRS')
    await user.type(screen.getByLabelText('User name'), 'admin')
    await user.type(screen.getByLabelText(/^Password/), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(
      await screen.findByText(/The setup code is wrong or has expired/),
    ).toBeInTheDocument()
    expect(submitted).toMatchObject({
      username: 'admin',
      setup_code: 'ABCD-EFGH-JKMN-PQRS',
    })
  })

  it('says where the code comes from', async () => {
    await changeLanguage('en')
    wrap()

    expect(await screen.findByText(/sudo \.\/prtg-nats setup-code/)).toBeInTheDocument()
  })
})
