import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { readFileSync } from 'node:fs'

const appPackage = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))

// https://vitejs.dev/config/
export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appPackage.version),
  },
  plugins: [
    vue(),
    Components({
      dts: false,
      resolvers: [ElementPlusResolver()],
    }),
  ],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'element-plus',
              test: /node_modules[\\/]element-plus[\\/]/,
            },
            {
              name: 'vue-vendor',
              test: /node_modules[\\/](@vue|vue)[\\/]/,
            },
            {
              name: 'content-vendor',
              test: /node_modules[\\/](marked|dompurify)[\\/]/,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
