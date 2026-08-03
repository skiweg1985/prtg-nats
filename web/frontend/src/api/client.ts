import type { ApiErrorBody } from './types'

export const API_BASE = '/api/v1'

/**
 * An error the server described. Carries the whole envelope, so the interface
 * can show a translated sentence, the untranslated technical detail and the
 * correlation id from one object.
 */
export class ApiError extends Error {
  readonly body: ApiErrorBody
  readonly httpStatus: number

  constructor(body: ApiErrorBody, httpStatus: number) {
    super(body.code)
    this.name = 'ApiError'
    this.body = body
    this.httpStatus = httpStatus
  }

  get code(): string {
    return this.body.code
  }

  get retryable(): boolean {
    return this.body.retryable
  }

  static network(cause: unknown): ApiError {
    return new ApiError(
      {
        code: 'network.unreachable',
        message_key: 'errors.network.unreachable',
        params: {},
        fields: [],
        details: cause instanceof Error ? cause.message : String(cause),
        correlation_id: null,
        retryable: true,
      },
      0,
    )
  }
}

interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
  query?: Record<string, string | number | boolean | undefined | null>
}

function buildUrl(path: string, query: RequestOptions['query']): string {
  const url = `${API_BASE}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  }
  const serialised = params.toString()
  return serialised ? `${url}?${serialised}` : url
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, query } = options

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      // The session is an HttpOnly cookie. Nothing sensitive is ever kept in
      // JavaScript, which is also why there is no token to attach here.
      credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw ApiError.network(cause)
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload: unknown = text ? JSON.parse(text) : null

  if (!response.ok) {
    const envelope = (payload as { error?: ApiErrorBody } | null)?.error
    throw new ApiError(
      envelope ?? {
        code: 'internal.unexpected',
        message_key: 'errors.internal.unexpected',
        params: {},
        fields: [],
        details: `HTTP ${response.status}`,
        correlation_id: response.headers.get('X-Correlation-ID'),
        retryable: response.status >= 500,
      },
      response.status,
    )
  }

  return payload as T
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query']) =>
    request<T>(path, { query }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
