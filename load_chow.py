import requests, csv, io, psycopg2, psycopg2.extras
from datetime import datetime, timezone, date, timedelta

from config import get_config

config = get_config()
conn = psycopg2.connect(config.database_url)
psycopg2.extras.register_uuid()

with conn.cursor() as cur:
    cur.execute("INSERT INTO sources (name, url, source_type) VALUES ('CMS SNF Change of Ownership', 'https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership', 'chow') ON CONFLICT (url) DO NOTHING RETURNING id")
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT id FROM sources WHERE source_type = 'chow'")
        row = cur.fetchone()
    source_id = row[0]

print('Downloading CHOW data...')
resp = requests.get('https://data.cms.gov/sites/default/files/2026-01/900cec56-f1c8-40cb-9f8a-bf54cae53b90/SNF_CHOW_2026.01.02.csv', timeout=60)
rows = list(csv.DictReader(io.StringIO(resp.text)))
print(f'Got {len(rows)} rows')

cutoff = date.today() - timedelta(days=365)
inserted = 0

for r in rows:
    date_str = r.get('EFFECTIVE DATE','').strip()
    if not date_str: continue
    try:
        eff_date = datetime.strptime(date_str, '%m/%d/%Y').date()
    except: continue
    if eff_date < cutoff: continue
    buyer = r.get('ORGANIZATION NAME - BUYER','').strip()
    seller = r.get('ORGANIZATION NAME - SELLER','').strip()
    ccn = r.get('CCN - BUYER','').strip()
    state = r.get('ENROLLMENT STATE - BUYER','').strip()
    if not buyer or not ccn: continue
    url_key = f"chow://ccn-{ccn}-{date_str.replace('/','')}"
    with conn.cursor() as cur:
        cur.execute("INSERT INTO articles (source_id, url, title, published_at, extraction_done) VALUES (%s, %s, %s, %s, TRUE) ON CONFLICT (url) DO NOTHING RETURNING id", (source_id, url_key, f"[CHOW] {buyer} acquires from {seller}", datetime.combine(eff_date, datetime.min.time()).replace(tzinfo=timezone.utc)))
        row2 = cur.fetchone()
        if not row2: continue
        article_id = row2[0]
        dedup = f"chow-{ccn}-{date_str.replace('/','')}"
        cur.execute("INSERT INTO deals (article_id, acquiring_entity, seller_entity, operator_names, states, facility_count, acquisition_date, stage, confidence, dedup_hash) VALUES (%s,%s,%s,%s,%s,1,%s,'confirmed','high',%s) ON CONFLICT (dedup_hash) DO NOTHING RETURNING id", (article_id, buyer, seller, [buyer], [state], eff_date.isoformat(), dedup))
        deal_row = cur.fetchone()
        if not deal_row: continue
        deal_id = deal_row[0]
        inserted += 1

        # CCN is already known from the CHOW filing itself (CCN - BUYER
        # column) — store it directly as an exact match rather than
        # leaving it to be (re)discovered by fuzzy name matching later.
        cur.execute("SELECT provider_name FROM cms_facilities WHERE ccn = %s", (ccn,))
        pn_row = cur.fetchone()
        provider_name = pn_row[0] if pn_row else None
        cur.execute(
            "INSERT INTO cms_matches (deal_id, ccn, provider_name, owner_name, owner_type, "
            "provider_state, ownership_start_date, match_score, match_method, matched_on_field) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (deal_id, ccn, provider_name, buyer, None, state or None,
             eff_date.isoformat(), 100, 'chow_ccn_direct', 'ccn')
        )

conn.commit()
conn.close()
print(f'Inserted {inserted} CHOW deals')