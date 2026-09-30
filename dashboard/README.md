# Dashboard (frontend)

The live site is a static React app: it fetches `deals.json` directly and has no backend. (A FastAPI backend and a Railway deployment existed in June 2026; both were removed on 2026-09-30 after Railway had stopped deploying on 2026-07-01. They're in git history if ever needed.)

## Stack
- React 18 + Vite, no component library (inline styles)
- Data: `deals.json` + `feed.xml`, produced by `scripts/export_deals.py`

## Running locally

```bash
# From the repo root: export current data into Vite's public/ folder
mkdir -p dashboard/frontend/public
venv/bin/python3 scripts/export_deals.py dashboard/frontend/public/deals.json   # also writes feed.xml next to it

cd dashboard/frontend
npm install
npm run dev          # http://localhost:3000/nursing-home-live-feed/
```

`public/deals.json` and `public/feed.xml` are gitignored — they're local copies of the data, not source.

## Build and deploy

`npm run build` writes `dist/`; deploying means copying `dist/index.html` and replacing `assets/*.js` on the `gh-pages` branch. Exact steps are in the root README's Deployment section.

## Environment
- `VITE_FACILITY_BASE_URL` (optional, read from the repo-root `.env`): deals with a CCN link to `{VITE_FACILITY_BASE_URL}/{CCN}`; defaults to Medicare Care Compare.
