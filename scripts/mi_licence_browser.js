// Michigan nursing home licence list, run inside the person's own Chrome tab
// on https://statelicensing.apps.lara.state.mi.us/ (Claude in Chrome). See
// scripts/licence_browser.py for the whole workflow.
//
// LARA's statewide search lists the active nursing homes (~429) but not their
// licensee; each home's detail record has it, with the licence's original
// effective date. This asks the page's own search for the list (100 a page),
// then each home's detail record, GAP_MS apart (~15 min), and saves them to
// Downloads as mi_licence_<timestamp>.json (window.MIL_DRY = true keeps them
// in window.__mil.rows instead). It stops at the first refused or slow
// answer and saves what it has. Poll window.__mil; window.__mil.stop = true
// ends it early.
(async () => {
  const GAP_MS = window.MIL_GAP_MS || 2000, SLOW_MS = 30000;
  window.__mil = {status: 'running', total: null, done: 0, rows: [], error: null, stop: false};
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const get = async (url, opts) => {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), SLOW_MS);
    try {
      const r = await fetch(url, {credentials: 'include', signal: ctl.signal, ...opts});
      if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
      return await r.json();
    } finally { clearTimeout(timer); }
  };
  const save = () => {
    const out = {state: 'MI', fetched_at: new Date().toISOString(), total: window.__mil.total,
                 stopped: window.__mil.error, rows: window.__mil.rows};
    if (window.MIL_DRY) return;
    const blob = new Blob([JSON.stringify(out)], {type: 'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'mi_licence_' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
    document.body.appendChild(a); a.click(); a.remove();
  };
  try {
    const list = [];
    for (let page = 1; ; page++) {
      const j = await get('/home/search', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({licenseType: 'NursingHome', primaryStatus: 'ACTIVE', county: 'Any',
                              serviceCategory: 'Any', orderBy: 'LicenseName', direction: 'ascending',
                              page, pageSize: 100})});
      window.__mil.total = j.totalItems;
      list.push(...j.searchResults);
      if (page >= j.numPages || !j.searchResults.length) break;
      await sleep(GAP_MS);
    }
    for (const home of list) {
      if (window.__mil.stop) throw new Error('stopped by hand');
      await sleep(GAP_MS);
      const d = await get('/api/details/search/' + home.licenseId);
      window.__mil.rows.push({
        licenseId: d.licenseId, licenseNumber: d.licenseNumber, legacyFacilityId: d.legacyFacilityId,
        licenseName: d.licenseName, addressLine1: d.addressLine1, city: d.city, zip: d.zip,
        licenseeEntityName: d.licenseeEntityName, originalEffectiveDate: d.originalEffectiveDate,
        primaryStatus: d.primaryStatus,
      });
      window.__mil.done++;
    }
    window.__mil.status = 'done';
  } catch (e) {
    window.__mil.status = 'stopped';
    window.__mil.error = String(e && e.message || e);
  }
  save();
})();
