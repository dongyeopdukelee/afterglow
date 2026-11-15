import { defineConfig } from 'vite';

// Development-only: keep an interactive preview separate from test servers.
const apiProxy = process.env.AFTERGLOW_API_PROXY ?? 'http://127.0.0.1:8000';

export default defineConfig({
  server: {
    proxy: {
      '/movies': apiProxy,
      '/recommendations': apiProxy,
    },
  },
});
