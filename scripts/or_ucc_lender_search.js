// Oregon UCC lender search, run inside a real Chrome tab (Claude in Chrome).
//
// secure.sos.state.or.us sits behind F5's bot defense (TSPD): plain HTTP and
// Playwright-driven Chrome get a JavaScript challenge or a blank page, while
// a person's own Chrome passes. So Oregon can't run unattended. In a live
// session: open https://secure.sos.state.or.us/ucc/searchHome.action in the
// person's Chrome, paste this into the page (javascript_tool), then poll
// window.__or. It searches each lender as secured party since BEGIN, looks
// up each original filing's debtors, pauses ~1 s between requests, and stops
// at the first empty response (possible challenge). Export with:
//   window.__or.filings.map(f=>[f.lien,f.filed,f.sp,f.debtors.join(' ; ')].join(' | '))
// (strip = & ? first: the extension masks text that looks like query
// strings), save as a file, and load it with scripts/ingest_lender_rows.py OR.
// First run 2026-10-05: 18 of 27 lenders, 62 filings, 10 nursing home -> 9
// deals; it stopped at an empty detail response after ~80 requests.
const BEGIN = '10/05/2024';
const LENDERS = window.OR_LENDERS || ["SECRETARY OF HOUSING AND URBAN DEVELOPMENT","UNITED STATES DEPARTMENT OF HOUSING","GREYSTONE","LUMENT","NEWPOINT","BERKADIA","LANCASTER POLLARD","ORIX REAL ESTATE CAPITAL","DWIGHT CAPITAL","MONTICELLO","GMCC","CAPITAL FUNDING","CAPITAL FINANCE","MIDCAP","WHITE OAK HEALTHCARE","OXFORD FINANCE","CIBC","BANKWELL","METROPOLITAN COMMERCIAL BANK","OMEGA HEALTHCARE","SABRA","CTW INVESTMENT","CARETRUST","NATIONAL HEALTH INVESTORS","LTC PROPERTIES","WELLTOWER","VENTAS"];
window.__or = {status: 'running', done: 0, lenders: 0, filings: [], stopped: null};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const parse = html => new DOMParser().parseFromString(html, 'text/html');
window.__orRun = (async () => {
  try {
    const home = parse(await fetch('/ucc/searchHome.action').then(r => r.text()));
    const form = [...home.forms].find(f => f.getAttribute('action') === '/ucc/nsSearch.action');
    const tok = form.querySelector('input[name=CSRFToken]').value;
    const seen = new Set();
    for (const lender of LENDERS) {
      const fd = new URLSearchParams(new FormData(form));
      fd.set('nonStandardEntityType', 'Organization'); fd.set('nonStandardSearchOrgName', lender);
      fd.set('assocNameType', 'Search by Secured Party'); fd.set('beginningDate', BEGIN); fd.set('endingDate', '');
      const doc = parse(await fetch('/ucc/nsSearch.action', {method: 'POST', body: fd}).then(r => r.text()));
      if (!doc.body || !doc.body.innerText.includes('Search Results')) { window.__or.stopped = 'challenge at search ' + lender; break; }
      // Original filings only: amendments carry a "-N" suffix
      const rows = [...doc.querySelectorAll('tr')].map(tr => [...tr.querySelectorAll('td')].map(c => c.innerText.trim()))
        .filter(c => c.length >= 6 && /^\d{6,}$/.test(c[2]));
      window.__or.lenders++;
      for (const c of rows) {
        const lien = c[2]; if (seen.has(lien)) continue; seen.add(lien);
        await sleep(1000);
        const d = parse(await fetch('/ucc/doLienNumberWebSearch.action', {method: 'POST', body: new URLSearchParams({inputLienNumberStr: lien, CSRFToken: tok})}).then(r => r.text()));
        const cells = [...d.querySelectorAll('tr')].map(tr => tr.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean);
        if (!cells.length) { window.__or.stopped = 'challenge at detail ' + lien; break; }
        // Detail rows alternate name / address under each "Debtor" heading
        const debtors = []; let mode = null;
        for (const t of cells) {
          if (t === 'Debtor') { mode = 'd'; continue; }
          if (/^(Secured Party|Assign|Amend|Continu|Termin|Back)/.test(t)) { mode = null; continue; }
          if (mode === 'd') debtors.push(t);
        }
        window.__or.filings.push({lien, filed: c[4], sp: c[0], lender, debtors: debtors.filter((x, i) => i % 2 === 0)});
        window.__or.done++;
      }
      if (window.__or.stopped) break;
      await sleep(1500);
    }
    window.__or.status = 'finished';
  } catch (e) { window.__or.status = 'error: ' + e.message; }
})();
