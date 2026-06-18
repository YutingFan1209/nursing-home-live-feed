"""
ucc/ny.py

Unlike KY/NJ/ME, NY's entry point here is a one-time BULK IMPORT from an
already-completed manual analysis (UCC_Filings_Flagged_Analysis.xlsx),
not a live scraper — that file already contains 2,491 real NY UCC rows
matched to CMS nursing-home owner names, with identity-match confidence
flags and related-party detection already done by hand. No reason to
re-scrape what's already sitting there cleaned.

A live-search adapter (NewYorkUCCSource.search(), following the same
pattern as ucc/nj.py) can be added later for ongoing incremental updates
once this backfill is in — not needed to get NY into the pipeline today.

## Row -> Filing grouping

The source spreadsheet's "Flagged Data" sheet has one row per
(Owner, counterparty) relationship, not one row per filing — a single
UCC-1 filing with one secured party AND one co-debtor shows up as two
rows sharing the same (Owner, Date Filed, Lapse Date). This groups rows
back into filings using that triple as the synthetic key (1,519 distinct
filings underneath the 2,491 rows, confirmed against the real file).

Within each group:
  - Rows with Role == "Organization Secured Party" become the
    UCCFiling's secured_party_name (one UCCFiling per secured party,
    since the schema is one-secured-party-per-filing)
  - Rows with Role == "Organization Co-Debtor" in the same group are
    attached as co_debtors on every UCCFiling in that group, and set
    is_related_party=True if the source flagged any of them as a
    related-party structure (family trust/LLC/holding entity)

## Confidence handling

The source file already did identity-match confidence scoring (HIGH /
MEDIUM / LOW), which is a DIFFERENT axis than ucc/lender_classifier.py's
RE/PE relevance check — confidence here is about whether the OWNER NAME
match is real (vs. a name collision with an unrelated person), while
lender_classifier asks whether the LENDER is acquisition-relevant.

LOW confidence rows (147 of 2,491 — confirmed false positives, lender is
an ag/equipment financier) are dropped entirely before import; they
don't belong to a nursing home owner at all per the source's own
methodology. HIGH and MEDIUM rows are both imported, with confidence
carried through on source_confidence so downstream review can still
distinguish them — MEDIUM rows have a plausible match but the owner name
is collision-prone elsewhere in the file, so treat them as needing an
extra glance, not as equally certain as HIGH.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Optional

import pandas as pd

from .base import UCCFiling, UCCSource, IngestMethod

SHEET_NAME = "Flagged Data"


class NewYorkUCCSource(UCCSource):
    state = "NY"
    ingest_method = IngestMethod.BULK
    is_free = True

    def bulk_ingest(self, file_path: str) -> list[UCCFiling]:
        return self.bulk_ingest_from_xlsx(file_path)

    def bulk_ingest_from_xlsx(self, file_path: str) -> list[UCCFiling]:
        df = pd.read_excel(file_path, sheet_name=SHEET_NAME)

        # Drop confirmed false positives per the source's own methodology
        df = df[~df["Match Confidence"].astype(str).str.startswith("LOW")]

        filings: list[UCCFiling] = []
        group_cols = ["Owner", "Date Filed", "Lapse Date"]
        for (owner, date_filed, lapse_date), group in df.groupby(group_cols, dropna=False):
            co_debtor_rows = group[group["Role"] == "Organization Co-Debtor"]
            secured_party_rows = group[group["Role"] == "Organization Secured Party"]

            co_debtors = co_debtor_rows["Counterparty (Lender / Co-Debtor)"].dropna().unique().tolist()
            is_related_party = group["Related Party?"].astype(str).str.upper().eq("YES").any()
            status = str(group["UCC Status"].iloc[0]).lower() if pd.notna(group["UCC Status"].iloc[0]) else "active"
            facilities = str(group["Facility(ies)"].iloc[0]) if pd.notna(group["Facility(ies)"].iloc[0]) else ""

            rows_to_use = secured_party_rows if len(secured_party_rows) else co_debtor_rows
            for _, row in rows_to_use.iterrows():
                confidence = str(row["Match Confidence"])
                filing_number = _synthetic_filing_number(owner, date_filed, lapse_date, row["Counterparty (Lender / Co-Debtor)"])
                filings.append(UCCFiling(
                    state=self.state,
                    debtor_name=str(owner),
                    secured_party_name=str(row["Counterparty (Lender / Co-Debtor)"]),
                    filing_number=filing_number,
                    filing_date=_to_date(date_filed),
                    lapse_date=_to_date(lapse_date),
                    status=status,
                    collateral_description=facilities or None,
                    source_url="ny_xlsx_import:UCC_Filings_Flagged_Analysis.xlsx",
                    co_debtors=co_debtors,
                    is_related_party=bool(is_related_party),
                    source_confidence=confidence,
                    raw={"associate_id": row.get("Associate ID"), "role": row["Role"]},
                ))

        # The source spreadsheet has genuine duplicate rows (confirmed: 269
        # of 1,982 — same owner/date/lender repeated identically). Dedupe
        # here rather than relying on the DB's is_duplicate check to catch
        # it later, since duplicate filing_numbers would otherwise produce
        # duplicate synthetic article URLs in the same batch.
        seen_hashes = set()
        deduped = []
        for f in filings:
            h = f.dedup_hash()
            if h not in seen_hashes:
                seen_hashes.add(h)
                deduped.append(f)
        return deduped


def _synthetic_filing_number(owner, date_filed, lapse_date, secured_party) -> str:
    """The cleaned export doesn't carry the original NY filing number, so
    build a stable synthetic one from the grouping key + secured party —
    deterministic across re-imports, used for dedup_hash."""
    key = f"{owner}|{date_filed}|{lapse_date}|{secured_party}"
    return "NYX-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _to_date(value) -> Optional[date]:
    if pd.isna(value):
        return None
    if isinstance(value, date):
        return value
    ts = pd.to_datetime(value, errors="coerce")
    return ts.date() if pd.notna(ts) else None
