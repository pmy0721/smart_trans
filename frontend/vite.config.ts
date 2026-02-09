import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 25173,
    strictPort: true,
    proxy: {
      '/api': 'http://43.138.60.74:28000',
      '/uploads': 'http://43.138.60.74:28000',
    },
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
})
