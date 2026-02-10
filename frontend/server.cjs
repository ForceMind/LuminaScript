const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');
const app = express();

const BACKEND_PORT = 8000;
const FRONTEND_PORT = 8600;
const API_URL = "http://127.0.0.1:" + BACKEND_PORT;

console.log("启动前端服务器...");
console.log("代理目标:", API_URL);

// 1. 配置 API 代理
app.use('/api', createProxyMiddleware({ 
    target: API_URL, 
    changeOrigin: true,
    pathRewrite: { '^/api': '' },
    onProxyReq: (proxyReq, req, res) => {
        // console.log('Proxy:', req.path, '->', API_URL + req.path);
    },
    onError: (err, req, res) => {
        console.error('Proxy Error:', err);
        res.status(500).send('Proxy Error');
    }
}));

// 2. 托管静态文件 (dist)
app.use(express.static(path.join(__dirname, 'dist')));

// 3. SPA 回退 (所有其他请求返回 index.html)
app.use((req, res) => {
  res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(FRONTEND_PORT, '0.0.0.0', () => {
  console.log(`Frontend service running at http://0.0.0.0:${FRONTEND_PORT}`);
});
