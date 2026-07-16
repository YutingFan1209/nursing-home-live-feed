#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== Syncing pipeline code from main ==="
git checkout main -- main.py config.py scraper pipeline matcher alerts ucc db requirements.txt

echo "=== Running pipeline ==="
venv/bin/python3 main.py --no-alerts

echo "=== Exporting deals.json ==="
psql postgresql://postgres:testpass@localhost:5432/nh_alerts_test -t -A -c "
SELECT json_build_object(
  'deals', json_agg(
    json_build_object(
      'id', d.id,
      'acquiring_entity', d.acquiring_entity,
      'seller_entity', d.seller_entity,
      'states', d.states,
      'facility_count', d.facility_count,
      'deal_value_m', d.deal_value_m,
      'acquisition_date', d.acquisition_date,
      'operator_names', d.operator_names,
      'facility_names', d.facility_names,
      'created_at', d.created_at,
      'lender', d.lender,
      'ucc_confirmed', COALESCE(d.ucc_confirmed, false),
      'source_type', s.source_type,
      'source_name', s.name,
      'source_url', a.url,
      'source_title', a.title
    ) ORDER BY d.created_at DESC
  ),
  'total', COUNT(*)
)
FROM deals d
LEFT JOIN articles a ON a.id = d.article_id
LEFT JOIN sources s ON s.id = a.source_id
WHERE d.stage != 'dismissed';
" > deals.json

echo "=== Copying frontend build to root ==="
cp dashboard/frontend/dist/index.html index.html
cp dashboard/frontend/dist/assets/*.js assets/

echo "=== Pushing to GitHub Pages ==="
git add deals.json index.html assets/
git diff --cached --quiet && echo "No new deals, skipping push." || \
  git commit -m "Data refresh $(date '+%Y-%m-%d %H:%M')" && git push origin gh-pages

echo "=== Done ==="
