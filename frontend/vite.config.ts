import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

/**
 * Vite configuration for the Sentinel Gujarat SPA.
 *
 * Dev server proxies every non-SPA prefix to the API/Caddy origin so the
 * browser sees a single origin (cookies, WebSocket, media all work).
 * Set VITE_API_TARGET to point at Caddy (http://localhost) or the API
 * container directly (http://localhost:8000).
 */
const target = process.env.VITE_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/ws': { target, changeOrigin: true, ws: true },
      '/mtx': { target, changeOrigin: true },
      '/playback': { target, changeOrigin: true },
      '/media': { target, changeOrigin: true },
      '/healthz': { target, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          antd: ['antd', '@ant-design/icons'],
          leaflet: ['leaflet', 'leaflet.markercluster'],
          charts: ['recharts'],
          hls: ['hls.js'],
        },
      },
    },
  },
});
