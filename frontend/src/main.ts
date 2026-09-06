import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import { startPwa } from './pwa'

const app = createApp(App)

app.mount('#app')
void startPwa({ registerServiceWorker: import.meta.env.PROD })
