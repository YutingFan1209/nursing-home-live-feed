import requests, csv, io, psycopg2, psycopg2.extras
from datetime import datetime, timezone, date, timedelta

conn = psycopg2.connect('postgresql://postgres:testpass@localhost:5432/nh_alerts_test')
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
        cur.execute("INSERT INTO deals (article_id, acquiring_entity, seller_entity, operator_names, states, facility_count, acquisition_date, stage, dedup_hash) VALUES (%s,%s,%s,%s,%s,1,%s,'confirmed',%s) ON CONFLICT (dedup_hash) DO NOTHING", (article_id, buyer, seller, [buyer], [state], eff_date.isoformat(), dedup))
        inserted += 1

conn.commit()
conn.close()
print(f'Inserted {inserted} CHOW deals')