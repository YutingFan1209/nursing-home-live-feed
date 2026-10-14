// California UCC search, run inside a real Chrome tab (Claude in Chrome).
// See scripts/ca_ucc_browser.py for the whole workflow.
//
// On https://bizfileonline.sos.ca.gov/search/ucc, set window.CA_NAMES (from
// `ca_ucc_browser.py names`), paste this into the page (javascript_tool) and
// poll window.__ca. It calls the page's own search API for each name, ~5 s
// apart, stops at the first non-JSON answer or one without rows (Incapsula
// block) and saves ca_ucc_<timestamp>.json to Downloads either way. The
// portal's server error for a single name is recorded and skipped; five in a
// row stop the run. Set window.__ca.stop = true to end early (results are
// still saved). Only financing statements (RECORD_TYPE "UCC") are kept, to
// keep the file small; ingest does the debtor-name matching.
// window.CA_SEARCH_TYPE = 'SECURED_PARTY' searches lenders instead
// (`ca_ucc_browser.py lenders`): 20 s apart, and only filings from the last
// two years are kept, since a lender can hold thousands of old ones.
const NAMES = window.CA_NAMES || [];
const SEARCH_TYPE = window.CA_SEARCH_TYPE || 'DEBTOR';
const LENDER = SEARCH_TYPE === 'SECURED_PARTY';
const GAP_MS = LENDER ? 20000 : 5000;
const since = new Date(); since.setFullYear(since.getFullYear() - 2);
const recent = d => { const [m, day, y] = (d || '').split('/'); return y && new Date(+y, m - 1, +day) >= since; };
window.__ca = {status: 'running', done: 0, total: NAMES.length, filings: 0, stopped: null, stop: false, results: []};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const save = () => {
  const blob = new Blob([JSON.stringify({search_type: SEARCH_TYPE, results: window.__ca.results, stopped: window.__ca.stopped})], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'ca_ucc_' + SEARCH_TYPE.toLowerCase() + '_' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
  a.click();
};
window.__caRun = (async () => {
  let inARow = 0;
  try {
    for (const name of NAMES) {
      if (window.__ca.stop) { window.__ca.stopped = 'stopped by hand'; break; }
      const r = await fetch('/api/Records/uccsearch', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({SEARCH_VALUE: name, STATUS: 'ALL', RECORD_TYPE_ID: '0',
          FILING_DATE: {start: null, end: null}, LAPSE_DATE: {start: null, end: null}}),
      });
      let j = null;
      try { j = JSON.parse(await r.text()); } catch (e) {}
      // The portal's own server error for some names (HTTP 400, "Object
      // reference not set..."), not a block: record it and go on
      if (j && j.internalerror) {
        window.__ca.results.push({query: name, rows: {}, error: j.internalerror});
        window.__ca.done++; window.__ca.errors = (window.__ca.errors || 0) + 1;
        if (++inARow >= 5) { window.__ca.stopped = `5 server errors in a row, last at ${name}`; break; }
        await sleep(GAP_MS); continue;
      }
      inARow = 0;
      if (r.status !== 200 || !j || typeof j !== 'object' || !('rows' in j)) {
        window.__ca.stopped = `HTTP ${r.status} at ${name}`; break;
      }
      const rows = Object.fromEntries(Object.entries(j.rows || {}).filter(([, row]) => row.RECORD_TYPE === 'UCC' && (!LENDER || recent(row.FILING_DATE))));
      window.__ca.results.push({query: name, rows});
      window.__ca.done++;
      window.__ca.filings += Object.keys(rows).length;
      await sleep(GAP_MS);
    }
    window.__ca.status = 'finished';
  } catch (e) { window.__ca.status = 'error: ' + e.message; }
  save();
})();
