// Ohio UCC lender search, run inside a real Chrome tab (Claude in Chrome).
//
// Playwright is blocked by ucc.ohiosos.gov (see memory oh-ucc-ip-blocked);
// the person's own Chrome works. In a live session:
// 1. Open https://ucc.ohiosos.gov/search, go to the Secured Party tab,
//    enter any organization name and click Search once, after pasting the
//    XHR patch below, so the page's own request (with its Authorization
//    header) is captured in window.__ohReq.
// 2. Paste the runner below. It replays that request for each lender as a
//    secured-party search (including filings lapsed within a year), 3 s
//    apart, keeps filings from SINCE on, and stops at the first non-JSON
//    response. The response already lists debtors and secured parties, so
//    no detail lookups are needed (27 requests in all).
// 3. Export, stripping = & ? (the extension masks query-string-like text):
//    const s=new Set(); window.__oh.rows.filter(r=>!s.has(r.n)&&s.add(r.n))
//      .map(r=>[r.n,r.d,r.sp,r.debtors,r.id].join(' | ').replace(/[=&?]/g,' '))
//    and load with: scripts/ingest_lender_rows.py OH <file>
// First run 2026-10-05: 27 lenders, 1,414 filings, 173 from the last two
// years -> 55 nursing home filings -> 44 deals, no slowdown.

// --- XHR patch (paste first) ---
if (!window.__ohPatched) {
  const o = XMLHttpRequest.prototype.open, s = XMLHttpRequest.prototype.send, h = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; this.__h = {}; return o.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) { this.__h[k] = v; return h.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) { if (String(this.__u).includes('ohiosearch')) window.__ohReq = {body: b, headers: this.__h}; return s.apply(this, arguments); };
  window.__ohPatched = true;
}

// --- runner (paste after one manual search) ---
const SINCE = '2024-10-05';
const LENDERS = window.OH_LENDERS || ["SECRETARY OF HOUSING AND URBAN DEVELOPMENT","UNITED STATES DEPARTMENT OF HOUSING","GREYSTONE","LUMENT","NEWPOINT","BERKADIA","LANCASTER POLLARD","ORIX REAL ESTATE CAPITAL","DWIGHT CAPITAL","MONTICELLO","GMCC","CAPITAL FUNDING","CAPITAL FINANCE","MIDCAP","WHITE OAK HEALTHCARE","OXFORD FINANCE","CIBC","BANKWELL","METROPOLITAN COMMERCIAL BANK","OMEGA HEALTHCARE","SABRA","CTW INVESTMENT","CARETRUST","NATIONAL HEALTH INVESTORS","LTC PROPERTIES","WELLTOWER","VENTAS"];
const base = JSON.parse(window.__ohReq.body), headers = window.__ohReq.headers;
window.__oh = {status: 'running', lenders: 0, rows: [], total: 0, stopped: null};
const xhr = body => new Promise((res, rej) => { const x = new XMLHttpRequest(); x.open('POST', '/api/ohiosearch'); for (const [k, v] of Object.entries(headers)) x.setRequestHeader(k, v); x.onload = () => res({status: x.status, text: x.responseText}); x.onerror = () => rej(new Error('network')); x.send(JSON.stringify(body)); });
window.__ohRun = (async () => {
  try {
    for (const lender of LENDERS) {
      const r = await xhr({...base, debtorSearch: false, securedPartySearch: true, organizationName: lender, activeFilingStatusPlusLapseWithinAYear: true});
      let j; try { j = JSON.parse(r.text); } catch (e) { j = null; }
      if (r.status !== 200 || !j || !('data' in j)) { window.__oh.stopped = `HTTP ${r.status} at ${lender}`; break; }
      for (const row of (j.data || [])) {
        window.__oh.total++;
        const d = (row.finacialStatementDate || '').slice(0, 10);
        if (d >= SINCE) window.__oh.rows.push({n: row.finacialStatementNumber, d, debtors: (row.debtorList || []).join(' ; '), sp: (row.securePartyList || []).join(' ; '), id: row.entityId, lender});
      }
      window.__oh.lenders++;
      await new Promise(r => setTimeout(r, 3000));
    }
    window.__oh.status = 'finished';
  } catch (e) { window.__oh.status = 'error: ' + e.message; }
})();
