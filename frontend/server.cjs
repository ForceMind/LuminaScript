const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');
const fs = require('fs');
const app = express();
app.disable('x-powered-by');

app.use((req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'same-origin');
    res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    next();
});

function parseRuntimeFile(filePath) {
    try {
        if (!fs.existsSync(filePath)) return {};
        const raw = fs.readFileSync(filePath, 'utf8');
        const result = {};
        for (const lineRaw of raw.split(/\r?\n/)) {
            const line = String(lineRaw || '').trim();
            if (!line || line.startsWith('#')) continue;
            const idx = line.indexOf('=');
            if (idx <= 0) continue;
            const key = line.slice(0, idx).trim();
            const value = line.slice(idx + 1).trim().replace(/^['"]|['"]$/g, '');
            if (key) result[key] = value;
        }
        return result;
    } catch (err) {
        return {};
    }
}

const runtimePath = path.resolve(__dirname, '..', '.lumina_runtime');
const runtime = parseRuntimeFile(runtimePath);

const BACKEND_PORT = Number(process.env.BACKEND_PORT || runtime.BACKEND_PORT || 8000);
const FRONTEND_PORT = Number(process.env.FRONTEND_PORT || runtime.FRONTEND_PORT || 8600);
const API_URL = "http://127.0.0.1:" + BACKEND_PORT;

console.log("启动前端服务器...");
console.log("代理目标:", API_URL);

// 1. 配置 API 代理
app.use('/api', createProxyMiddleware({ 
    target: API_URL, 
    changeOrigin: true,
    xfwd: true, // Auto-add x-forwarded-for headers so backend sees real IP
    pathRewrite: { '^/api': '' },
    proxyTimeout: 600000,
    timeout: 600000,
    on: {
        error: (err, req, res) => {
            const errorCode = String(err && err.code || 'UNKNOWN');
            const timedOut = errorCode === 'ETIMEDOUT' || errorCode === 'ESOCKETTIMEDOUT';
            const statusCode = timedOut ? 504 : 502;
            console.error('API proxy request failed:', {
                method: String(req && req.method || ''),
                path: String(req && req.url || '').split('?')[0],
                error_code: errorCode,
                status: statusCode,
            });
            if (!res || res.destroyed) return;
            if (res.headersSent) {
                res.end();
                return;
            }
            res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
            res.end(JSON.stringify({
                error: {
                    code: timedOut ? 'api_backend_timeout' : 'api_backend_unavailable',
                    message: timedOut ? 'API 后端响应超时' : 'API 后端暂时不可用',
                },
            }));
        },
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
