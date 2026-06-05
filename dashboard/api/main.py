"""
Nursing Home Acquisition Feed API — v1
Three endpoints: feed list, single deal, CSV export.
"""

import csv
import io
import os
import psycopg2
import psycopg2.extras
from datetime import date
from typing import Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import get_config

config = get_config()
app = FastAPI(title="Nursing Home Live Feed", version="1.0.0")

_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_conn():
    conn = psycopg2.connect(config.database_url)
    psycopg2.extras.register_uuid()
    return conn


# ── Feed list ─────────────────────────────────────────────────

@app.get("/api/feed")
def get_feed(
    state:      Optional[str] = Query(None, description="2-letter state code e.g. VA"),
    date_from:  Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to:    Optional[str] = Query(None, description="YYYY-MM-DD"),
    deal_type:  Optional[str] = Query(None, description="chow | edgar | rss"),
    operator:   Optional[str] = Query(None, description="Search acquirer or operator name"),
    limit:      int           = Query(20, le=100),
    offset:     int           = Query(0),
):
    """
    Returns paginated list of nursing home ownership changes.
    Filterable by state, date range, and source type.
    """
    conn = get_conn()
    try:
        filters = ["d.stage NOT IN ('dismissed')"]
        params  = []

        if state:
            filters.append("%s = ANY(d.states)")
            params.append(state.upper())
        if date_from:
            filters.append("d.acquisition_date >= %s")
            params.append(date_from)
        if date_to:
            filters.append("d.acquisition_date <= %s")
            params.append(date_to)
        if deal_type:
            filters.append("s.source_type = %s")
            params.append(deal_type)
        if operator:
            filters.append("(d.acquiring_entity ILIKE %s OR d.seller_entity ILIKE %s OR EXISTS (SELECT 1 FROM unnest(d.operator_names) op WHERE op ILIKE %s))")
            params += [f"%{operator}%", f"%{operator}%", f"%{operator}%"]

        where = " AND ".join(filters)

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    d.id,
                    d.acquiring_entity,
                    d.seller_entity,
                    d.states,
                    d.facility_count,
                    d.deal_value_m,
                    d.acquisition_date,
                    d.financing_amount_m,
                    d.lender,
                    d.operator_names,
                    d.facility_names,
                    d.created_at,
                    a.url        AS source_url,
                    a.title      AS source_title,
                    s.source_type,
                    s.name       AS source_name,
                    -- CCNs from CMS matches for linking to mother website
                    ARRAY_AGG(DISTINCT m.ccn)
                        FILTER (WHERE m.ccn IS NOT NULL) AS ccns,
                    (SELECT COUNT(*)
                     FROM deals d2
                     WHERE d2.acquiring_entity = d.acquiring_entity
                     AND d2.states = d.states
                     AND d2.acquisition_date = d.acquisition_date
                     AND d2.id != d.id
                     AND d2.stage NOT IN ('dismissed')
                    ) AS also_reported_count
                FROM deals d
                JOIN articles a ON a.id = d.article_id
                LEFT JOIN sources s ON s.id = a.source_id
                LEFT JOIN cms_matches m ON m.deal_id = d.id
                WHERE {where}
                GROUP BY d.id, a.url, a.title, s.source_type, s.name
                ORDER BY COALESCE(d.acquisition_date, d.created_at::date) DESC, d.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [limit, offset])

            cols = [desc[0] for desc in cur.description]
            deals = []
            for row in cur.fetchall():
                deal = dict(zip(cols, row))
                deal["id"]              = str(deal["id"])
                deal["acquisition_date"] = str(deal["acquisition_date"]) if deal["acquisition_date"] else None
                deal["created_at"]      = str(deal["created_at"])
                deals.append(deal)

            # Total count
            cur.execute(f"""
                SELECT COUNT(DISTINCT d.id)
                FROM deals d
                JOIN articles a ON a.id = d.article_id
                LEFT JOIN sources s ON s.id = a.source_id
                WHERE {where}
            """, params)
            total = cur.fetchone()[0]

        return {"deals": deals, "total": total, "offset": offset, "limit": limit}
    finally:
        conn.close()


# ── Single deal ───────────────────────────────────────────────

@app.get("/api/feed/{deal_id}")
def get_deal(deal_id: str):
    """Full detail for a single deal including all CMS matches."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.*, a.url AS source_url, a.title AS source_title,
                       a.raw_text, s.name AS source_name, s.source_type
                FROM deals d
                JOIN articles a ON a.id = d.article_id
                LEFT JOIN sources s ON s.id = a.source_id
                WHERE d.id = %s
            """, (deal_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Deal not found")
            cols = [d[0] for d in cur.description]
            deal = dict(zip(cols, row))
            deal["id"]         = str(deal["id"])
            deal["article_id"] = str(deal["article_id"])
            for f in ("created_at", "updated_at", "acquisition_date", "recheck_after"):
                if deal.get(f):
                    deal[f] = str(deal[f])

            # CMS matches with CCNs for linking
            cur.execute("""
                SELECT ccn, provider_name, provider_state,
                       ownership_start_date, match_score, owner_type
                FROM cms_matches
                WHERE deal_id = %s
                ORDER BY match_score DESC
            """, (deal_id,))
            mcols = [d[0] for d in cur.description]
            deal["cms_matches"] = [
                {**dict(zip(mcols, r)),
                 "ownership_start_date": str(dict(zip(mcols, r)).get("ownership_start_date") or "")}
                for r in cur.fetchall()
            ]

        return deal
    finally:
        conn.close()


# ── CSV export ────────────────────────────────────────────────

@app.get("/api/feed/export/csv")
def export_csv(
    state:     Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
):
    """Download current filtered deals as CSV."""
    conn = get_conn()
    try:
        filters = ["d.stage NOT IN ('dismissed')"]
        params  = []
        if state:
            filters.append("%s = ANY(d.states)")
            params.append(state.upper())
        if date_from:
            filters.append("d.acquisition_date >= %s")
            params.append(date_from)
        if date_to:
            filters.append("d.acquisition_date <= %s")
            params.append(date_to)

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    d.acquisition_date,
                    d.acquiring_entity,
                    d.seller_entity,
                    array_to_string(d.states, ', ')        AS states,
                    d.facility_count,
                    d.deal_value_m,
                    array_to_string(d.operator_names, '; ') AS operators,
                    array_to_string(d.facility_names, '; ') AS facilities,
                    s.source_type                          AS source_type,
                    a.url                                  AS source_url,
                    ARRAY_AGG(DISTINCT m.ccn)
                        FILTER (WHERE m.ccn IS NOT NULL)   AS ccns
                FROM deals d
                JOIN articles a ON a.id = d.article_id
                LEFT JOIN sources s ON s.id = a.source_id
                LEFT JOIN cms_matches m ON m.deal_id = d.id
                WHERE {' AND '.join(filters)}
                GROUP BY d.id, d.acquisition_date, d.acquiring_entity,
                         d.seller_entity, d.states, d.facility_count,
                         d.deal_value_m, d.operator_names, d.facility_names,
                         s.source_type, a.url
                ORDER BY d.acquisition_date DESC NULLS LAST
                LIMIT 5000
            """, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(cols)
        writer.writerows(rows)
        output.seek(0)

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=nh_ownership_changes.csv"}
        )
    finally:
        conn.close()


# ── Stats (for the feed header) ───────────────────────────────

@app.get("/api/stats")
def get_stats():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE stage IN ('confirmed','verified')) AS confirmed,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '90 days') AS last_90_days
                FROM deals
                WHERE stage != 'dismissed'
            """)
            row = cur.fetchone()
            stats = {"total": row[0], "confirmed": row[1], "last_90_days": row[2]}

            cur.execute("""
                SELECT COUNT(DISTINCT s) FROM deals, unnest(states) AS s
                WHERE stage != 'dismissed'
            """)
            stats["states_covered"] = cur.fetchone()[0]

            cur.execute("""
                SELECT unnest(states) AS state, COUNT(*) AS cnt
                FROM deals WHERE stage != 'dismissed'
                GROUP BY state ORDER BY cnt DESC LIMIT 10
            """)
            stats["top_states"] = [{"state": r[0], "count": r[1]} for r in cur.fetchall()]

        return stats
    finally:
        conn.close()


# ── Health ────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}
