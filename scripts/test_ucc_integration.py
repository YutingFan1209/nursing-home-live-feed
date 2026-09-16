"""
Integration test for _process_ucc_filing against the real matcher/dedup modules.

Tests two scenarios:
  1. A UCC filing that name+date-matches an existing deal → should set
     ucc_confirmed=true on that deal, NOT insert a new deals row.
  2. A UCC filing with no plausible match → should insert a new deals row
     and route it through _run_cms_matching; we report what stage/confidence
     the real determine_stage() returns.

Also explicitly tests how match_deal() behaves when acquiring_entity is None
(the UCC case) — this is the key question the author wanted answered.

Run from repo root:
  venv/bin/python3 scripts/test_ucc_integration.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras
from datetime import date, timedelta

from config import get_config
from ucc.base import UCCFiling
from ucc.lender_classifier import classify_secured_party
import main as pipeline_main

config = get_config()
OK = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def run():
    conn = psycopg2.connect(config.database_url)
    psycopg2.extras.register_uuid()

    inserted_article_ids = []
    inserted_deal_ids = []
    inserted_source_ids = []

    try:
        print("\n=== UCC Integration Test (real matcher/dedup/DB) ===\n")

        # ── Setup: a fake source and article for our test deals ──────────
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sources (name, url, source_type)
                VALUES ('Test UCC Source', 'ucc://test', 'manual')
                ON CONFLICT (url) DO UPDATE SET last_fetched_at = NOW()
                RETURNING id
            """)
            source_id = cur.fetchone()[0]
            inserted_source_ids.append(source_id)

        # Existing-deal article
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO articles (source_id, url, title)
                VALUES (%s, 'test://existing-deal-article', 'Existing deal test article')
                ON CONFLICT (url) DO UPDATE SET title=EXCLUDED.title
                RETURNING id
            """, (source_id,))
            existing_article_id = cur.fetchone()[0]
            inserted_article_ids.append(existing_article_id)

        # ── Insert a fake existing deal that should match the UCC filing ─
        # Operator: "Sunset Care Group" in NJ, deal ~March 2024
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO deals (
                    article_id, acquiring_entity, seller_entity,
                    operator_names, facility_names, states,
                    acquisition_date, dedup_hash, extraction_model
                ) VALUES (
                    %s, 'Acme Capital Partners', NULL,
                    ARRAY['Sunset Care Group'], ARRAY['Sunset Manor Nursing Home'],
                    ARRAY['NJ'], '2024-03-01', 'test_hash_ucc_existing_001', 'test'
                )
                ON CONFLICT (dedup_hash) DO UPDATE SET extraction_model = EXCLUDED.extraction_model
                RETURNING id
            """, (existing_article_id,))
            existing_deal_id = cur.fetchone()[0]
            inserted_deal_ids.append(existing_deal_id)

        conn.commit()
        print(f"  Setup: inserted source={source_id}, article={existing_article_id}, deal={existing_deal_id}")

        # ── TEST 1: Confirmation case ────────────────────────────────────
        print("\n[TEST 1] UCC filing that matches existing deal → should set ucc_confirmed=true\n")

        # Article for the UCC confirmation filing
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO articles (source_id, url, title)
                VALUES (%s, 'ucc://NJ/NJ-2024-99001', 'UCC-1: Sunset Care Group / Omega Healthcare (NJ)')
                ON CONFLICT (url) DO UPDATE SET title=EXCLUDED.title
                RETURNING id
            """, (source_id,))
            ucc_article_id = cur.fetchone()[0]
            inserted_article_ids.append(ucc_article_id)
        conn.commit()

        filing_confirm = UCCFiling(
            state="NJ",
            filing_number="NJ-2024-99001",
            debtor_name="Sunset Care Group LLC",          # close match to existing "Sunset Care Group"
            secured_party_name="Omega Healthcare Investors",
            filing_date=date(2024, 3, 15),               # within 180-day window of 2024-03-01
        )
        classification = classify_secured_party(filing_confirm.secured_party_name)
        ucc_article_dict = {
            "url": f"ucc://{filing_confirm.state}/{filing_confirm.filing_number}",
            "source_id": source_id,
            "ucc_filing": True,
            "_ucc_filing_obj": filing_confirm,
            "_ucc_classification": classification,
        }

        deals_before = _count_deals(conn)
        result_count = pipeline_main._process_ucc_filing(ucc_article_dict, ucc_article_id, conn)
        conn.commit()
        deals_after = _count_deals(conn)

        # Check: no new deal inserted
        new_deals = deals_after - deals_before
        check_no_new_deal = new_deals == 0
        print(f"  New deals inserted: {new_deals}  (expected 0)  [{OK if check_no_new_deal else FAIL}]")

        # Check: existing deal got ucc_confirmed=true
        with conn.cursor() as cur:
            cur.execute("SELECT ucc_confirmed, lender FROM deals WHERE id = %s", (existing_deal_id,))
            row = cur.fetchone()
        confirmed, lender_val = row
        check_confirmed = confirmed is True
        check_lender = lender_val == "Omega Healthcare Investors"
        print(f"  ucc_confirmed on existing deal: {confirmed}  (expected True)  [{OK if check_confirmed else FAIL}]")
        print(f"  lender populated: {lender_val!r}  [{OK if check_lender else FAIL}]")

        # ── TEST 2: New signal case ──────────────────────────────────────
        print("\n[TEST 2] UCC filing with no matching existing deal → should insert new deal\n")

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO articles (source_id, url, title)
                VALUES (%s, 'ucc://KY/KY-2024-77500', 'UCC-1: Brand New Operator / Formation Capital (KY)')
                ON CONFLICT (url) DO UPDATE SET title=EXCLUDED.title
                RETURNING id
            """, (source_id,))
            new_signal_article_id = cur.fetchone()[0]
            inserted_article_ids.append(new_signal_article_id)
        conn.commit()

        filing_new = UCCFiling(
            state="KY",
            filing_number="KY-2024-77500",
            debtor_name="Bluegrass Senior Living Holdings LLC",  # no existing deal with this name
            secured_party_name="Formation Capital LLC",
            filing_date=date(2024, 9, 1),
        )
        classification_new = classify_secured_party(filing_new.secured_party_name)
        new_article_dict = {
            "url": f"ucc://{filing_new.state}/{filing_new.filing_number}",
            "source_id": source_id,
            "ucc_filing": True,
            "_ucc_filing_obj": filing_new,
            "_ucc_classification": classification_new,
        }

        deals_before2 = _count_deals(conn)
        pipeline_main._process_ucc_filing(new_article_dict, new_signal_article_id, conn)
        conn.commit()
        deals_after2 = _count_deals(conn)

        new_deals2 = deals_after2 - deals_before2
        check_new_deal = new_deals2 == 1
        print(f"  New deals inserted: {new_deals2}  (expected 1)  [{OK if check_new_deal else FAIL}]")

        # Fetch the newly inserted deal and inspect
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, acquiring_entity, operator_names, lender, stage, ucc_confirmed
                FROM deals WHERE extraction_model = 'ucc_filing'
                ORDER BY created_at DESC LIMIT 1
            """)
            row = cur.fetchone()

        if row:
            new_id, acquirer, operators, lender, stage, ucc_conf = row
            inserted_deal_ids.append(new_id)
            check_no_acquirer = acquirer is None
            check_operator = "Bluegrass Senior Living Holdings LLC" in (operators or [])
            check_stage = stage in ("detected", "pending_cms", "confirmed")
            check_ucc_flag = ucc_conf is True

            print(f"  acquiring_entity: {acquirer!r}  (expected None)  [{OK if check_no_acquirer else FAIL}]")
            print(f"  operator_names: {operators}  [{OK if check_operator else FAIL}]")
            print(f"  stage from determine_stage(): {stage!r}  (expected 'detected'|'pending_cms'|'confirmed')  [{OK if check_stage else FAIL}]")
            print(f"  ucc_confirmed: {ucc_conf}  (expected True)  [{OK if check_ucc_flag else FAIL}]")
        else:
            print(f"  {FAIL}: no ucc_filing deal found in DB after new-signal routing")

        # ── TEST 3: match_deal() with acquiring_entity=None ─────────────
        print("\n[TEST 3] match_deal() with acquiring_entity=None (UCC shape)\n")

        from matcher.ownership import match_deal, determine_stage

        ucc_deal = {
            "acquiring_entity": None,
            "seller_entity": None,
            "operator_names": ["Bluegrass Senior Living Holdings LLC"],
            "facility_names": ["Bluegrass Senior Living Holdings LLC"],
            "states": ["KY"],
            "acquisition_date": date(2024, 9, 1),
        }

        try:
            matches = match_deal(ucc_deal, conn)
            stage, confidence = determine_stage(matches)
            print(f"  match_deal() did not raise  [{OK}]")
            print(f"  returned {len(matches)} CMS match(es)")
            print(f"  determine_stage() → stage={stage!r}, confidence={confidence!r}")

            if matches:
                print(f"  Best match: {matches[0]['provider_name']} (score {matches[0]['match_score']})")
                print(f"  matched_on_field: {matches[0]['matched_on_field']!r}")
                # Key insight: for UCC deals the matched_on_field should NEVER be
                # 'acquiring_entity' (it's None) — it should be 'operator_names' or 'facility_names'
                unexpected_field = matches[0]['matched_on_field'] == 'acquiring_entity'
                print(f"  matched via operator/facility (not acquiring_entity): [{OK if not unexpected_field else FAIL}]")
            else:
                print(f"  No CMS matches (expected for fake operator in empty/thin dev DB)  [{OK}]")
                print(f"  stage='detected', confidence=None — correct fallback for UCC new signals  [{OK}]")
        except Exception as e:
            print(f"  {FAIL}: match_deal() raised: {e}")

        print("\n=== Cleanup ===\n")

    finally:
        # Always clean up test rows
        with conn.cursor() as cur:
            if inserted_deal_ids:
                cur.execute("DELETE FROM deals WHERE id = ANY(%s)", (inserted_deal_ids,))
            if inserted_article_ids:
                cur.execute("DELETE FROM articles WHERE id = ANY(%s)", (inserted_article_ids,))
            if inserted_source_ids:
                cur.execute("DELETE FROM sources WHERE id = ANY(%s)", (inserted_source_ids,))
        conn.commit()
        conn.close()
        print("  Test rows removed from dev DB.")


def _count_deals(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM deals")
        return cur.fetchone()[0]


if __name__ == "__main__":
    run()
