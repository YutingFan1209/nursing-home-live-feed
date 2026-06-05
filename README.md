# Nursing Home Live Feed

Automated pipeline that scrapes nursing home acquisition news, extracts deal entities via Claude AI, matches them against CMS ownership and Care Compare datasets, and surfaces confirmed ownership changes to a shared research team dashboard.

## Architecture

```
DISCOVERY          PROCESSING              STORAGE          TEAM
──────────         ──────────              ───────          ────
RSS feeds    →                         →  Postgres    →  Dashboard
SEC EDGAR    →  Claude extraction      →  S3 archive  →  Email digest
Google News  →  CMS matcher            →  Match queue →  CSV export
             →  Dedup + stage tracker  →
```

## Project structure

```
nursing-home-live-feed/
├── db/
│   ├── schema.sql          # Full Postgres schema
│   └── migrations/         # Future schema changes
├── cms/
│   ├── fetch_cms.py        # Download CMS datasets
│   └── loader.py           # Parse + upsert into Postgres
├── scraper/
│   ├── sources.py          # Registered source definitions
│   ├── rss.py              # RSS feed scraper
│   ├── edgar.py            # SEC EDGAR 8-K watcher
│   └── googlenews.py       # Google News sweep
├── pipeline/
│   ├── extractor.py        # Claude-powered deal extraction
│   ├── dedup.py            # Deal deduplication logic
│   └── confidence.py       # Match confidence scoring
├── matcher/
│   ├── ownership.py        # CMS Ownership dataset matcher
│   ├── carecompare.py      # Care Compare enrichment
│   └── recheck.py          # Re-check pending deals
├── alerts/
│   └── digest.py           # Daily email digest (SendGrid)
├── dashboard/
│   ├── api/                # FastAPI backend
│   └── frontend/           # React frontend
├── infra/
│   ├── eventbridge.tf      # AWS EventBridge cron (Terraform)
│   └── cloudrun.yaml       # GCP Cloud Run alternative
├── main.py                 # Pipeline orchestrator
├── config.py               # Env-based config
├── requirements.txt
└── .env.example
```

## Setup

### 1. Prerequisites
- Python 3.11+
- Postgres 15+ (RDS or Cloud SQL)
- Anthropic API key
- SendGrid API key (for alerts)

### 2. Install dependencies
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 4. Initialize database
```bash
psql $DATABASE_URL < db/schema.sql
```

### 5. Load initial CMS data
```bash
python -m cms.fetch_cms
python -m cms.loader
```

### 6. Run pipeline manually
```bash
python main.py
```

### 7. Deploy scheduler
```bash
# AWS
terraform apply infra/

# GCP
gcloud run jobs deploy nursing-home-live-feed --config infra/cloudrun.yaml
```

## Pipeline stages

Each deal moves through these stages:

| Stage | Meaning |
|---|---|
| `detected` | Article scraped, entities extracted, no CMS match yet |
| `pending_cms` | Partial CMS match — ownership date not yet updated |
| `confirmed` | Full CMS match with ownership record |
| `unresolved` | No match found after 90-day re-check window |

## CMS datasets used

| Dataset | Socrata ID | Refresh cadence |
|---|---|---|
| Nursing Home Ownership | `qhpq-qrm6` | Monthly |
| Care Compare (providers) | `4pq5-n9py` | Weekly |

## Development

```bash
# Run tests
pytest tests/

# Run a single article through the pipeline
python -c "from pipeline.extractor import extract_deals; print(extract_deals('YOUR ARTICLE TEXT'))"
```
