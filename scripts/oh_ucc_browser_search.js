// Ohio UCC debtor search, run inside a real Chrome tab (Claude in Chrome).
// See scripts/oh_ucc_browser.py for the whole workflow.
//
// Same capture-and-replay as scripts/oh_ucc_lender_search.js: on
// https://ucc.ohiosos.gov/search, paste the XHR patch from that file, run one
// search by hand on the Debtor tab with Organization selected (any name), so
// the page's own request and its Authorization header land in window.__ohReq.
// Then set window.OH_NAMES (from `oh_ucc_browser.py names`) and paste this.
// Poll window.__ohd. It replays that request for each name, GAP_MS apart,
// and saves oh_ucc_<timestamp>.json to Downloads when it finishes or stops.
// It stops at the first non-JSON answer and when answers slow down (OH
// slowed to 13-23 s responses after ~160 searches in an hour on 2026-09-21,
// the warning sign before its blocks). window.__ohd.stop = true ends early.
const NAMES = window.OH_NAMES || [];
const GAP_MS = window.OH_GAP_MS || 12000;  // 20000 after a slowdown the day before (2026-10-15)
const SLOW_MS = 10000;
const base = JSON.parse(window.__ohReq.body), headers = window.__ohReq.headers;
window.__ohd = {status: 'running', done: 0, total: NAMES.length, filings: 0, stopped: null, stop: false, results: []};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const save = () => {
  const blob = new Blob([JSON.stringify({results: window.__ohd.results, stopped: window.__ohd.stopped})], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'oh_ucc_' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
  a.click();
};
const xhr = body => new Promise((res, rej) => {
  const x = new XMLHttpRequest();
  x.open('POST', '/api/ohiosearch');
  for (const [k, v] of Object.entries(headers)) x.setRequestHeader(k, v);
  x.onload = () => res({status: x.status, text: x.responseText});
  x.onerror = () => rej(new Error('network'));
  x.send(JSON.stringify(body));
});
const KEEP = ['finacialStatementNumber', 'debtorList', 'securePartyList', 'transactionCode', 'finacialStatementDate', 'lapseDate', 'entityId'];
window.__ohdRun = (async () => {
  try {
    for (const name of NAMES) {
      if (window.__ohd.stop) { window.__ohd.stopped = 'stopped by hand'; break; }
      const t0 = Date.now();
      const r = await xhr({...base, debtorSearch: true, securedPartySearch: false, organizationName: name});
      const ms = Date.now() - t0;
      let j = null;
      try { j = JSON.parse(r.text); } catch (e) {}
      if (r.status !== 200 || !j || !('data' in j)) { window.__ohd.stopped = `HTTP ${r.status} at ${name}`; break; }
      const rows = (j.data || []).map(row => Object.fromEntries(KEEP.map(k => [k, row[k]])));
      window.__ohd.results.push({query: name, rows});
      window.__ohd.done++;
      window.__ohd.filings += rows.length;
      if (ms > SLOW_MS) { window.__ohd.stopped = `slow answer (${Math.round(ms / 1000)} s) after ${name}`; break; }
      await sleep(GAP_MS);
    }
    window.__ohd.status = 'finished';
  } catch (e) { window.__ohd.status = 'error: ' + e.message; }
  save();
})();
