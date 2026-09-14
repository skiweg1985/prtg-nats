import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // Vite inlines assets under 4 KB as data: URIs, and a few of the fontsource
    // subsets are that small. The Caddyfile's CSP allows fonts only from
    // 'self', so the browser blocked exactly those subsets. Serving them as
    // files keeps the policy strict.
    assetsInlineLimit: (filePath) => (filePath.endsWith('.woff2') ? false : undefined),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // The API is same-origin behind the reverse proxy in production, so the
    // client always calls /api/v1 relative. In development the proxy stands in
    // for that, which also keeps the session cookie first-party.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: false,
      },
    },
  },
})
