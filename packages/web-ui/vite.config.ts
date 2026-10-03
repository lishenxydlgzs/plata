import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/workspace/',
  server: { proxy: { '/api': 'http://127.0.0.1:8200', '/health': 'http://127.0.0.1:8200', '/media': 'http://127.0.0.1:8200' } },
  build: { outDir: 'dist', sourcemap: false },
});
