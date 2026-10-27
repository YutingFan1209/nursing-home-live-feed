// Wisconsin UCC debtor search, run inside the person's logged-in Chrome tab
// on https://wims.dfi.wi.gov/uccsearch (Claude in Chrome). See
// scripts/wi_ucc_browser.py for the whole workflow.
//
// 1. Paste this XHR patch, then run one search by hand (Organization, any
//    name), so the page's request and its bearer token land in window.__wiReq:
//
//    (() => { const P = XMLHttpRequest.prototype; if (P.__wiPatched) return; P.__wiPatched = true;
//      const o = P.open, s = P.send, h = P.setRequestHeader;
//      P.open = function (m, u) { this.__u = u; this.__h = {}; return o.apply(this, arguments); };
//      P.setRequestHeader = function (k, v) { this.__h[k] = v; return h.apply(this, arguments); };
//      P.send = function (b) { if (String(this.__u).includes('getuccsearchresults'))
//        window.__wiReq = {url: this.__u, headers: this.__h, body: b}; return s.apply(this, arguments); }; })()
//
// 2. Set window.WI_NAMES (from `wi_ucc_browser.py names`) and paste the rest.
//    Poll window.__wid. It replays the request for each name, GAP_MS apart,
//    reading up to MAX_PAGES pages of PAGE_SIZE, and saves wi_ucc_<timestamp>.json
//    to Downloads when it finishes or stops. It stops at the first non-JSON
//    answer (a captcha or an expired login) and when answers slow down.
//    window.__wid.stop = true ends it early.
const NAMES = window.WI_NAMES || [];
const GAP_MS = window.WI_GAP_MS || 15000;
const SLOW_MS = 10000, PAGE_SIZE = 50, MAX_PAGES = 4;
const base = JSON.parse(window.__wiReq.body), headers = window.__wiReq.headers, url = window.__wiReq.url;
window.__wid = {status: 'running', done: 0, total: NAMES.length, filings: 0, stopped: null, stop: false, results: []};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const save = () => {
  const blob = new Blob([JSON.stringify({results: window.__wid.results, stopped: window.__wid.stopped})], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'wi_ucc_' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
  a.click();
};
const xhr = body => new Promise((res, rej) => {
  const x = new XMLHttpRequest();
  x.open('POST', url);
  for (const [k, v] of Object.entries(headers)) x.setRequestHeader(k, v);
  x.onload = () => res({status: x.status, text: x.responseText});
  x.onerror = () => rej(new Error('network'));
  x.send(JSON.stringify(body));
});
const KEEP = ['lienNumber', 'filingType', 'filingDate', 'lapseDate', 'lienStatus', 'city', 'state', 'fullName', 'securedPartyFullName', 'lienGuid'];
window.__widRun = (async () => {
  try {
    outer: for (const name of NAMES) {
      if (window.__wid.stop) { window.__wid.stopped = 'stopped by hand'; break; }
      const rows = [];
      for (let page = 1; page <= MAX_PAGES; page++) {
        const t0 = Date.now();
        const r = await xhr({...base, searchType: 'debtor', searchMethod: 'startsWith', searchValue1: name,
                             searchValue2: '', searchValue3: '', pageNumber: page, pageSize: PAGE_SIZE});
        const ms = Date.now() - t0;
        let j = null;
        try { j = JSON.parse(r.text); } catch (e) {}
        if (r.status !== 200 || !j || !j.succeeded || !j.data) { window.__wid.stopped = `HTTP ${r.status} at ${name}`; break outer; }
        rows.push(...(j.data.data || []).map(row => Object.fromEntries(KEEP.map(k => [k, row[k]]))));
        if (ms > SLOW_MS) {
          window.__wid.results.push({query: name, rows});
          window.__wid.stopped = `slow answer (${Math.round(ms / 1000)} s) after ${name}`;
          break outer;
        }
        if (!j.data.hasNextPage) break;
        await sleep(3000);
      }
      window.__wid.results.push({query: name, rows});
      window.__wid.done++;
      window.__wid.filings += rows.length;
      await sleep(GAP_MS);
    }
    window.__wid.status = 'finished';
  } catch (e) { window.__wid.status = 'error: ' + e.message; }
  save();
})();
