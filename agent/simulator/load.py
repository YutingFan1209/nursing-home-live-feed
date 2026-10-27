"""
Puts a synthetic world into its own database, nh_sim, in the same Postgres
container, and hands back the resolver's real ToolBox pointed at it. The
tools then run their real SQL against a world whose answers are known.

The schema is copied from the real database (pg_dump -s), so the tools see
the same tables, indexes and pg_trgm. Nothing is ever written to the real
database: this refuses to run unless the target is named nh_sim.
"""
from __future__ import annotations

import subprocess
from urllib.parse import urlparse, urlunparse

from agent.simulator.world import World

SIM_DB = "nh_sim"


def _with_db(url: str, db: str) -> str:
    u = urlparse(url)
    return urlunparse(u._replace(path=f"/{db}"))


def create_sim_db(real_url: str) -> str:
    """Drop and recreate nh_sim with the real schema. Returns its URL."""
    import psycopg2
    sim_url = _with_db(real_url, SIM_DB)
    if urlparse(real_url).path.lstrip("/") == SIM_DB:
        raise SystemExit("DATABASE_URL already points at nh_sim; refusing to dump it onto itself")
    admin = psycopg2.connect(_with_db(real_url, "postgres"))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {SIM_DB}")
        cur.execute(f"CREATE DATABASE {SIM_DB}")
    admin.close()
    schema = subprocess.run(["pg_dump", "-s", "--no-owner", "--no-privileges", real_url],
                            check=True, capture_output=True, text=True).stdout
    # a newer pg_dump than the server writes settings the server doesn't know
    schema = "\n".join(l for l in schema.splitlines() if not l.startswith("SET transaction_timeout"))
    subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", sim_url], input=schema,
                   check=True, capture_output=True, text=True)
    return sim_url


def load_world(sim_url: str, w: World) -> None:
    import psycopg2
    from psycopg2.extras import execute_values
    assert urlparse(sim_url).path.lstrip("/") == SIM_DB
    conn = psycopg2.connect(sim_url)
    with conn, conn.cursor() as cur:
        execute_values(cur, """INSERT INTO cms_facilities (ccn, provider_name, legal_business_name, provider_address,
                               provider_city, provider_state, bed_count, five_star_rating) VALUES %s""",
                       [(h.ccn, h.name, h.legal if h.cms_shows_sale else (h.seller or h.legal), h.address,
                         h.city, h.state, 120, 3) for h in w.homes])
        execute_values(cur, """INSERT INTO cms_ownership_records (ccn, provider_name, owner_name, owner_type, owner_role,
                               ownership_percentage, provider_state, ownership_start_date) VALUES %s""",
                       [(o["ccn"], "", o["owner_name"], o["owner_type"], o["owner_role"],
                         o["ownership_percentage"], "", o["ownership_start_date"]) for o in w.owners])
        execute_values(cur, "INSERT INTO chow_seen_records (ccn, buyer_name, effective_date) VALUES %s",
                       [(r["ccn"], r["buyer_name"], r["effective_date"]) for r in w.chow_seen])
        execute_values(cur, """INSERT INTO ucc_filings (state, filing_number, debtor_name, secured_party, filing_date,
                               filing_type, collateral_description, status) VALUES %s""",
                       [(f["state"], f["filing_number"], f["debtor_name"], f["secured_party"], f["filing_date"],
                         f["filing_type"], f["collateral_description"], f["status"]) for f in w.filings])
    conn.close()


def sim_toolbox(sim_url: str, w: World):
    """The real ToolBox on nh_sim, with the world's CHOW rows and no web lookups."""
    import psycopg2
    from agent.tools import ToolBox
    tb = ToolBox(psycopg2.connect(sim_url))
    tb._chow_rows = w.chow_csv
    tb.property_fetch = lambda *a, **k: None      # no county records in the synthetic world
    return tb
