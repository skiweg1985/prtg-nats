import { defineConfig, mergeConfig } from 'vitest/config'

import viteConfig from './vite.config'

// Kept apart from vite.config.ts: Vite's own config type does not carry `test`,
// and merging here means the aliases and plugins are defined exactly once.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./tests/setup.ts'],
      css: false,
    },
  }),
)
