// Massachusetts UCC lender search, run inside a real Chrome tab (Claude in
// Chrome).
//
// corp.sec.state.ma.us's UCC search is an ASP.NET form whose options post
// back on every change: the default "Article 9" method only searches
// debtors, so "Begins With" has to be picked before the Secured Party box can
// be ticked. In a live session open
// https://corp.sec.state.ma.us/corpweb/UCCSearch/UCCSearch.aspx in the
// person's Chrome, paste this into the page (javascript_tool) and poll
// window.__ma. For each lender it fills the real form in a hidden frame of
// the same page (Begins With, Secured Party only, filed since BEGIN), clicks
// Search, then opens each UCC-1's Filing History page (the result's own
// link) for its debtors, ~1.5 s between pages. It stops at the first page
// that doesn't look like the portal's. When done it saves
// ma_ucc_lenders_<timestamp>.txt to Downloads, one filing per line in
// scripts/ingest_lender_rows.py's format; load it with
//   venv/bin/python3 scripts/ingest_lender_rows.py MA ~/Downloads/ma_ucc_lenders_*.txt
// Set window.__ma.stop = true to end early (what's found is still saved).
// First run 2026-11-06.
const BEGIN = window.MA_BEGIN || (() => {
  const d = new Date(); d.setFullYear(d.getFullYear() - 2);
  return [d.getMonth() + 1, d.getDate(), d.getFullYear()].map(n => String(n).padStart(2, '0')).join('/');
})();
const LENDERS = window.MA_LENDERS || ["SECRETARY OF HOUSING AND URBAN DEVELOPMENT","UNITED STATES DEPARTMENT OF HOUSING","GREYSTONE","LUMENT","NEWPOINT","BERKADIA","LANCASTER POLLARD","ORIX REAL ESTATE CAPITAL","DWIGHT CAPITAL","MONTICELLO","GMCC","CAPITAL FUNDING","CAPITAL FINANCE","MIDCAP","WHITE OAK HEALTHCARE","OXFORD FINANCE","CIBC","BANKWELL","METROPOLITAN COMMERCIAL BANK","OMEGA HEALTHCARE","SABRA","CTW INVESTMENT","CARETRUST","NATIONAL HEALTH INVESTORS","LTC PROPERTIES","WELLTOWER","VENTAS","SECRETARY OF U.S. DEPARTMENT OF HOUSING","FORBRIGHT","VALLEY NATIONAL BANK","VNB NEW YORK","EAST WEST BANK","IKARIA","VIUM CAPITAL","TEXAS CAPITAL BANK","POPULAR BANK","BANK HAPOALIM","ONB DHF","DMT SPE","ECAPITAL","MERCHANTS BANK OF INDIANA","BOKF","ISRAEL DISCOUNT BANK","DIME COMMUNITY BANK","CONNECTONE","YAKAR","DBF SPE"];  // = ucc/lender_search.py LENDER_TERMS
const GAP_MS = 1500;
window.__ma = {status: 'running', begin: BEGIN, done: 0, lenders: LENDERS.length, hits: 0, filings: [], stopped: null, stop: false};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const frame = document.createElement('iframe');
frame.style.cssText = 'position:absolute;left:-9999px;width:800px;height:600px';
document.body.appendChild(frame);
const nextLoad = () => new Promise((res, rej) => {
  const t = setTimeout(() => rej(new Error('page load timed out')), 90000);
  frame.onload = () => { clearTimeout(t); res(frame.contentDocument); };
});
const act = async fn => { const p = nextLoad(); fn(); const d = await p; await sleep(GAP_MS); return d; };
const el = (d, id) => d.getElementById('MainContent_' + id);
const cellLines = td => td.innerHTML.split(/<br\s*\/?>/i).map(s => {
  const x = document.createElement('div'); x.innerHTML = s; return x.textContent.trim();
}).filter(Boolean);
// rows of the Filing History table under a bold header ("Debtor(s)", ...)
const section = (d, title) => {
  const head = [...d.querySelectorAll('tr')].find(tr => tr.textContent.trim() === title);
  const out = [];
  for (let tr = head && head.nextElementSibling; tr && !tr.style.fontWeight; tr = tr.nextElementSibling)
    for (const td of tr.cells) { const l = cellLines(td); if (l.length) out.push(l); }
  return out;
};

async function searchLender(name) {
  let d = await act(() => { frame.src = '/CorpWeb/UCCSearch/UCCSearch.aspx'; });
  if (!el(d, 'btnSearch')) throw new Error('search form missing');
  if (!el(d, 'rdoSearchO').checked) d = await act(() => el(d, 'rdoSearchO').click());
  if (el(d, 'UCCSearchMethodO').value !== 'B') d = await act(() => {
    const s = el(d, 'UCCSearchMethodO'); s.value = 'B';
    s.dispatchEvent(new d.defaultView.Event('change', {bubbles: true}));
  });
  if (!el(d, 'chkSecuredParty').checked) d = await act(() => el(d, 'chkSecuredParty').click());
  if (el(d, 'chkDebtor').checked) d = await act(() => el(d, 'chkDebtor').click());
  if (el(d, 'UCCSearchMethodO').value !== 'B' || !el(d, 'chkSecuredParty').checked || el(d, 'chkDebtor').checked)
    throw new Error('form options did not stick');
  el(d, 'txtName').value = name;
  el(d, 'txtStartDate').value = BEGIN;
  d = await act(() => el(d, 'btnSearch').click());
  if (!/UCC Search Results/.test(d.body.textContent)) throw new Error('no results page');
  const rows = [];
  for (const tr of d.querySelectorAll('tr')) {
    const c = [...tr.cells].map(td => td.textContent.trim());
    const link = tr.querySelector('a[href*="UCCFilingHistory"]');
    if (c.length === 8 && link && c[4] === 'UCC-1') rows.push({sp: c[0], filing: c[5], filed: c[7], href: link.href});
  }
  return rows;
}

window.__maRun = (async () => {
  const seen = new Set();
  try {
    for (const lender of LENDERS) {
      if (window.__ma.stop) { window.__ma.stopped = 'stopped by hand'; break; }
      const rows = await searchLender(lender);
      window.__ma.hits += rows.length;
      for (const r of rows) {
        if (seen.has(r.filing)) continue;
        seen.add(r.filing);
        const h = await fetch(r.href).then(x => x.text());
        const d = new DOMParser().parseFromString(h, 'text/html');
        if (!/Filing History/.test(d.body.textContent)) throw new Error('unexpected page for ' + r.filing);
        const debtors = section(d, 'Debtor(s)').map(l => l[0]);
        window.__ma.filings.push({...r, lender, debtors});
        await sleep(GAP_MS);
      }
      window.__ma.done++;
    }
    window.__ma.status = 'finished';
  } catch (e) {
    window.__ma.status = 'stopped';
    window.__ma.stopped = `${e.message} at lender ${window.__ma.done + 1}`;
  }
  frame.remove();
  const iso = s => { const [mm, d2, yy] = s.split('/'); return `${yy}-${mm}-${d2}`; };
  const lines = window.__ma.filings.map(f =>
    [f.filing, iso(f.filed), f.sp, f.debtors.join(' ; ')].map(s => s.replace(/\|/g, '/')).join(' | '));
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([lines.join('\n') + '\n'], {type: 'text/plain'}));
  a.download = 'ma_ucc_lenders_' + new Date().toISOString().replace(/[:.]/g, '-') + '.txt';
  a.click();
})();
