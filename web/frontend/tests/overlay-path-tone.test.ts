import { describe, expect, it } from 'vitest'

import { pathTone } from '@/features/infrastructure/overlayMode'

describe('pathTone', () => {
  // A tunnel whose last handshake is hours old was listed as a plain "Tunnel"
  // while the probe behind it was offline. It is a fault like a missing one.
  it('treats a stale handshake like a missing one', () => {
    expect(pathTone('on', 'stale')).toBe('danger')
    expect(pathTone('auto', 'stale')).toBe('warn')
    expect(pathTone('on', 'no_handshake')).toBe('danger')
  })

  it('leaves the path a probe is meant to take uncoloured', () => {
    expect(pathTone('on', 'tunnel')).toBe('neutral')
    expect(pathTone('auto', 'direct')).toBe('neutral')
    expect(pathTone('off', 'stale')).toBe('neutral')
  })
})
