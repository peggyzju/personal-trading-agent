import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5174,          // 5173 留给 desktop-companion，避开端口冲突
    strictPort: true,    // 端口被占就报错，不静默抢别的端口/被别的项目顶掉
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
})
