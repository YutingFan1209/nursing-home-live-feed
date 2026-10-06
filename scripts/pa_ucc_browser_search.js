// Pennsylvania UCC debtor search, run inside a real Chrome tab (Claude in
// Chrome). See scripts/pa_ucc_browser.py for the whole workflow.
//
// On https://file.dos.pa.gov/search/ucc, set window.PA_NAMES (from
// `pa_ucc_browser.py names`), paste this into the page (javascript_tool) and
// poll window.__pa. It calls the page's own search API for each name, ~3 s
// apart, stops at the first non-JSON answer (Incapsula challenge), and saves
// pa_ucc_<timestamp>.json to Downloads either way. Set window.__pa.stop = true
// to end early (results are still saved).
const NAMES = window.PA_NAMES || [];
const GAP_MS = 3000;
window.__pa = {status: 'running', done: 0, total: NAMES.length, filings: 0, stopped: null, stop: false, results: []};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const save = () => {
  const blob = new Blob([JSON.stringify({results: window.__pa.results, stopped: window.__pa.stopped})], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'pa_ucc_' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
  a.click();
};
window.__paRun = (async () => {
  try {
    for (const name of NAMES) {
      if (window.__pa.stop) { window.__pa.stopped = 'stopped by hand'; break; }
      const r = await fetch('/api/Records/uccsearch', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({SEARCH_VALUE: '', SEARCH_TYPE: 'DEBTOR', NAME_TYPE_ID: '2', ORGANIZATION_NAME: name,
          INDIVIDUAL_NAME: {FIRST_NAME: '', MIDDLE_NAME: '', LAST_NAME: '', SUFFIX: ''},
          SEARCH_CITY: '', SEARCH_STATE: '', SEARCH_LAPSED: true, FILING_DATE: {start: null, end: null}}),
      });
      let j = null;
      try { j = JSON.parse(await r.text()); } catch (e) {}
      if (r.status !== 200 || !j || typeof j !== 'object' || !('rows' in j)) {
        window.__pa.stopped = `HTTP ${r.status} at ${name}`; break;
      }
      const rows = j.rows || {};
      window.__pa.results.push({query: name, rows});
      window.__pa.done++;
      window.__pa.filings += Object.keys(rows).length;
      await sleep(GAP_MS);
    }
    window.__pa.status = 'finished';
  } catch (e) { window.__pa.status = 'error: ' + e.message; }
  save();
})();
