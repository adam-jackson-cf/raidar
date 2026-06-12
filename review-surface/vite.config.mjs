import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(here, 'src') },
  },
  server: {
    port: 5951,
    proxy: {
      '/api': `http://localhost:${process.env.REVIEW_SURFACE_PORT || 5950}`,
      '/artifacts': `http://localhost:${process.env.REVIEW_SURFACE_PORT || 5950}`,
    },
  },
});
