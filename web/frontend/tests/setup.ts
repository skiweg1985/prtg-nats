import '@testing-library/jest-dom/vitest'

// jsdom does not implement matchMedia, which the theme provider reads on mount.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

// Neither does it implement EventSource, which the live log subscribes to.
class StubEventSource {
  close() {}
  addEventListener() {}
  onerror: ((event: Event) => void) | null = null
}
Object.defineProperty(window, 'EventSource', { writable: true, value: StubEventSource })
