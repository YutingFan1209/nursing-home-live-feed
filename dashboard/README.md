# Dashboard — Phase 6

Full-stack team dashboard: FastAPI backend + React frontend.

## Stack
- **Backend**: FastAPI + psycopg2 (Python)
- **Frontend**: React 18 + Vite (no component library — custom CSS-in-JS)
- **Fonts**: Syne (display) + DM Sans (body) + DM Mono (code/labels)

## Running locally

### Backend
```bash
# From project root
pip install -r requirements.txt
uvicorn dashboard.api.main:app --reload --port 8000
```
API docs available at: http://localhost:8000/docs

### Frontend
```bash
cd dashboard/frontend
npm install
npm run dev
# Opens at http://localhost:3000
```

## Environment variable
```bash
# Optional — defaults to http://localhost:8000
VITE_API_URL=https://your-internal-domain.org
```

## Deploying

### Backend (FastAPI)
Any WSGI/ASGI host works. Recommended:
```bash
# With gunicorn + uvicorn workers
gunicorn dashboard.api.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Frontend
```bash
cd dashboard/frontend
npm run build        # outputs to dist/
# Serve dist/ with nginx, Caddy, or upload to S3 + CloudFront
```

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/stats` | Dashboard stats + top states + weekly volume |
| GET | `/api/deals` | Paginated deal list with filters |
| GET | `/api/deals/{id}` | Full deal detail with CMS matches + annotations |
| POST | `/api/deals/{id}/annotations` | Add team note |
| PATCH | `/api/deals/{id}/stage` | Update deal stage (verify/dismiss) |
| GET | `/api/export` | CSV export with optional filters |
| GET | `/api/health` | Health check |

## Filter params for `/api/deals`

| Param | Example | Description |
|---|---|---|
| `stage` | `confirmed` | Filter by pipeline stage |
| `state` | `VA` | Filter by 2-letter state code |
| `operator` | `cascadia` | Fuzzy search on acquirer/operator name |
| `confidence` | `high` | Filter by match confidence |
| `tag` | `regulatory` | Filter by annotation tag |
| `limit` | `50` | Page size (max 200) |
| `offset` | `0` | Pagination offset |

## What the team can do

- **View** all deals with stage, confidence, states, value, and CMS match count
- **Filter** by state (clickable stat bar), stage, confidence, operator name, or tag
- **Open** any deal to see full CMS match details, source article, and prior owner
- **Verify** or **Dismiss** a deal with one click
- **Annotate** with a note tagged as research / regulatory / follow-up / flagged
- **Export** current filtered view to CSV for analysis
