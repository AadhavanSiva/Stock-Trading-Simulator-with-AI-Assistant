# Portfolio Tracker — web UI

The Angular front end for the FastAPI backend in `../api`. See the
"Angular UI + FastAPI" section of the main README for setup.

```bash
npm install
npm start          # http://localhost:8200, proxies /api to 127.0.0.1:8000
npm run test:ci    # unit tests in headless Chrome
npm run build      # production build in dist/web/browser
```

Layout:

- `src/app/core` — API client, auth (JWT in localStorage), interceptor, guards, notices
- `src/app/shared` — formatting pipes, the price chart, the gain/loss indicator, ticker search
- `src/app/pages` — one folder per route: login, portfolio, stock, buy, sell, search

Money arrives from the API as decimal strings and is only ever formatted,
never computed on, in the browser.
