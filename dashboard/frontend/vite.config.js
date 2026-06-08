import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/nursing-home-live-feed/',
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
