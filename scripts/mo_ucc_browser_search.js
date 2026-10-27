// Missouri UCC debtor search, run inside the person's logged-in Chrome tab
// on https://bsd.sos.mo.gov/LienFiling/LienSearch.aspx (Claude in Chrome).
// See scripts/mo_ucc_browser.py for the whole workflow.
//
// Set window.MO_NAMES (from `mo_ucc_browser.py names`) and paste this. Poll
// window.__mod. It drives the search form in a hidden same-origin iframe
// (Non-Standard search, Organization, Starting With, active filings), reads
// up to MAX_PAGES pages of 10, and for each filing from the last DOC_DAYS
// days opens its filing chain and fetches the image of the first filing
// (a scanned PDF; the secured party is only on it). Images run up to ~1 MB,
// so results go to Downloads in parts, mo_ucc_<timestamp>.json every
// SAVE_EVERY names and when it finishes or stops. It stops when the session ends
// (the frame leaves LienSearch.aspx) or a search is slower than SLOW_MS twice.
// window.__mod.stop = true ends it early.
const NAMES = window.MO_NAMES || [];
const GAP_MS = window.MO_GAP_MS || 15000;
const STEP_MS = 3000, SLOW_MS = 60000, MAX_PAGES = 5, DOC_DAYS = 730, SAVE_EVERY = 25;
const P = 'ctl00_ctl00_ContentPlaceHolderMain_ContentPlaceHolderMainSingle_ppLienSearch_';
const AJAX = 'ctl00_ctl00_ContentPlaceHolderMain_ContentPlaceHolderMainSingle_LienSearchAjaxManager';
const PAGE = '/LienFiling/LienSearch.aspx';
window.__mod = {status: 'running', done: 0, total: NAMES.length, filings: 0, docs: 0, stopped: null, stop: false};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const frame = id => {
  let el = document.getElementById(id);
  if (!el) { el = document.createElement('iframe'); el.id = id; el.style.cssText = 'width:1200px;height:500px'; document.body.appendChild(el); }
  return el;
};
const f = frame('moFrame'), g = frame('moChain');
class Stop extends Error {}
const waitFor = async (cond, what) => {
  const t0 = Date.now();
  while (Date.now() - t0 < SLOW_MS) {
    try { const v = cond(); if (v) return v; } catch (e) {}
    await sleep(300);
  }
  throw new Stop(`slow: ${what}`);
};
// Full page loads (navigation, postbacks) are awaited as a new, complete
// document in the frame: its load event alone was missed now and then
const loaded = async (fr, act, what) => {
  const old = fr.contentDocument;
  act();
  await waitFor(() => fr.contentDocument !== old && fr.contentDocument.readyState === 'complete' && fr.contentDocument.body, what);
  if (!fr.contentWindow.location.pathname.endsWith(PAGE)) throw new Stop(`left the search page at ${what} (logged out?)`);
  await sleep(500);
};
const el = (fr, id) => fr.contentDocument.getElementById(P + id);
// ASP.NET/Telerik postback code walks arguments.caller, which throws when a
// strict-mode function (this script) is on the stack, so page actions run
// from the page's own timer queue with a fresh stack
const inPage = (fr, code) => fr.contentWindow.setTimeout(code, 0);
const b64 = buf => {
  const bytes = new Uint8Array(buf); let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
};
const parseRows = () => [...f.contentDocument.querySelectorAll('tr.rgRow,tr.rgAltRow')].map(r => {
  const lines = r.cells[2].innerText.trim().split('\n').map(s => s.trim()).filter(Boolean);
  const chain = ((r.querySelector('input[onclick*=ShowFileChainReport]') || {}).getAttribute?.('onclick') || '')
    .match(/ShowFileChainReport\('(\d+)','(\d+)','(-?\d+)','(\d+)'\)/);
  return {fileNo: r.cells[1].innerText.trim(), debtor: lines[0], address: lines.slice(1).join(', '),
          date: r.cells[3].innerText.trim(), status: r.cells[4].innerText.trim(), chain: chain ? chain.slice(1) : null};
});
const search = async name => {
  await loaded(f, () => { f.src = PAGE; }, `open search for ${name}`);
  if (el(f, 'ddlSearchType').value !== '1') {
    el(f, 'ddlSearchType').value = '1';
    inPage(f, `__doPostBack('${P.replace(/_/g, '$')}ddlSearchType', '')`);
    await waitFor(() => el(f, 'ddlIncludeRecords'), 'non-standard form');
  }
  if (!f.contentDocument.querySelector('input[id$=OrgName]')) {
    inPage(f, `document.getElementById('${P}rbNonSearchDebtorType_1').click()`);
    await waitFor(() => f.contentDocument.querySelector('input[id$=OrgName]'), 'organization form');
  }
  f.contentDocument.querySelector('input[id$=OrgName]').value = name;
  el(f, 'ddlIncludeRecords').value = '0';
  el(f, 'rbNonSearchStandardResults_0').checked = true;
  // The search sometimes answers in place (Telerik ajax) rather than with a
  // new page, so wait for the results themselves; the form page has none
  inPage(f, `document.getElementById('${P}stdbtnSearch_LinkStandardButton').click()`);
  await waitFor(() => {
    const D = f.contentDocument;
    return D.readyState === 'complete' && D.body &&
      (D.querySelector('tr.rgRow,tr.rgAltRow') || /No results found/i.test(D.body.innerText));
  }, `search ${name}`);
  if (!f.contentWindow.location.pathname.endsWith(PAGE)) throw new Stop(`left the search page at ${name} (logged out?)`);
  await sleep(500);
  const rows = parseRows();
  for (let page = 2; page <= MAX_PAGES; page++) {
    const next = f.contentDocument.querySelector('.rgPageNext');
    const info = (f.contentDocument.querySelector('.rgInfoPart') || {}).innerText || '';
    const m = info.match(/Page (\d+) of (\d+)/);
    if (!next || !m || +m[1] >= +m[2]) break;
    await sleep(STEP_MS);
    inPage(f, `document.querySelector('.rgPageNext').click()`);
    await waitFor(() => ((f.contentDocument.querySelector('.rgInfoPart') || {}).innerText || '').includes(`Page ${page} of`), `page ${page} of ${name}`);
    await sleep(300);
    rows.push(...parseRows());
  }
  return rows;
};
const fetchDoc = async row => {
  const [fileNo, queue, order, xml] = row.chain;
  await loaded(g, () => { g.src = `${PAGE}?PageMode=2&QueueItemID=${queue}&OrderID=${order}&XMLID=${xml}&fileNumber=${fileNo}`; }, `chain ${fileNo}`);
  row.chainTypes = [...g.contentDocument.querySelectorAll('tr.rgRow,tr.rgAltRow')].map(r => (r.cells[2] || {}).innerText?.trim());
  const link = g.contentDocument.querySelector('input[value="View Document"]');
  const arg = link && (link.getAttribute('onclick') || '').match(/ajaxRequest\('([^']+)'\)/);
  if (!arg) { row.doc = null; return; }
  let url = null;
  g.contentWindow.open = u => { url = String(u); return null; };
  inPage(g, `${AJAX}.ajaxRequest('${arg[1]}')`);
  await waitFor(() => url, `document link ${fileNo}`);
  const r = await fetch(url);
  if (!(r.headers.get('content-type') || '').includes('pdf')) { row.doc = null; return; }
  row.doc = b64(await r.arrayBuffer());
  window.__mod.docs++;
};
let unsaved = [];
const save = () => {
  const blob = new Blob([JSON.stringify({results: unsaved, stopped: window.__mod.stopped})], {type: 'application/json'});
  unsaved = [];
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'mo_ucc_' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
  a.click();
};
window.__modRun = (async () => {
  const cutoff = Date.now() - DOC_DAYS * 864e5;
  try {
    for (const name of NAMES) {
      if (window.__mod.stop) { window.__mod.stopped = 'stopped by hand'; break; }
      // The portal stalls now and then (stopped 3 times in its first 15
      // names, 2026-10-27): one retry after a minute before giving up
      let rows;
      try { rows = await search(name); } catch (e) {
        if (!(e instanceof Stop) || !e.message.startsWith('slow')) throw e;
        await sleep(60000);
        rows = await search(name);
      }
      for (const row of rows) {
        if (row.chain && new Date(row.date).getTime() >= cutoff) { await sleep(STEP_MS); await fetchDoc(row); }
        delete row.chain;
      }
      unsaved.push({query: name, rows});
      window.__mod.done++;
      window.__mod.filings += rows.length;
      if (unsaved.length >= SAVE_EVERY) save();
      await sleep(GAP_MS);
    }
    window.__mod.status = 'finished';
  } catch (e) {
    window.__mod.status = e instanceof Stop ? 'stopped' : 'error: ' + e.message;
    window.__mod.stopped = e.message;
  }
  save();
})();
