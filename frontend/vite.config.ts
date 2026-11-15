import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    proxy: {
      '/movies': 'http://127.0.0.1:8000',
      '/recommendations': 'http://127.0.0.1:8000',
    },
  },
});
