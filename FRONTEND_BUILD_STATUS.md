# Frontend production build verification

## Result

**Verified.** The production build succeeds with the pinned dependency ranges in `package.json`.

```text
vite v5.4.21 building for production...
✓ 1568 modules transformed.
dist/index.html                   0.60 kB │ gzip:  0.36 kB
dist/assets/index-*.css          16.33 kB │ gzip:  3.91 kB
dist/assets/index-*.js          191.53 kB │ gzip: 59.53 kB
✓ built in 1.85s
```

Environment used:

- Node.js v22.12.0
- npm 10.9.0
- `npm ci`
- `npm run build`

The generated `frontend/dist/` is not served by the backend. Deploy it with the nginx image in `frontend/Dockerfile` (which also proxies `/api` and WebSocket upgrades), or use `npm run preview` and a reverse proxy.

## Reproduce on Windows

Requirements: Node.js 20+ and npm.

```powershell
cd frontend
Copy-Item .env.example .env
npm ci
npm run build
```

## Backend verification

The backend test suite passes with the pinned `backend/requirements.txt` in a Python 3.11 virtual environment: **69 passed**. `python -m compileall` and `pyflakes` report no issues.
