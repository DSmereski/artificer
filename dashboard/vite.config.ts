import { defineConfig } from 'vite';

export default defineConfig({
  // Relative asset base so dist/index.html works when Lively loads it over
  // file:// — absolute "/assets/..." resolves to the drive root there and
  // 404s, leaving the wallpaper blank. "./assets/..." resolves correctly.
  base: './',
  // In dev, proxy /api to the gateway so CORS is avoided.
  server: {
    port: 5175,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8766',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    // Put Lively manifests in dist root via copyPublicDir (they live in /public)
    outDir: 'dist',
    emptyOutDir: true,
  },
});
