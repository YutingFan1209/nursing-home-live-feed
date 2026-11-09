// Ohio nursing home licence list, run inside the person's own Chrome tab on
// https://publicapps.odh.ohio.gov/eid/reports/EID_Report_Criteria.aspx
// (Claude in Chrome). See scripts/licence_browser.py for the whole workflow.
//
// It submits ODH's own report form the way the page does (FACILITY LISTING,
// owners included, State of Ohio licence, Nursing Home, ACTIVE, all
// counties), asks the output page for its "Comma Delimited Text" export and
// saves the text to Downloads as oh_licence_<timestamp>.json
// (window.OHL_DRY = true keeps it in window.__ohl.csv instead). Three page
// requests in all, a few seconds apart. Poll window.__ohl.
(async () => {
  window.__ohl = {status: 'running', step: '', rows: null, error: null};
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const parse = html => new DOMParser().parseFromString(html, 'text/html');
  const field = (form, suffix) => [...form.elements].find(e => e.name && e.name.endsWith(suffix));
  // ASP.NET postback from a fetched form: its fields as the browser would send them
  const post = async (form, url, set, button) => {
    const fd = new FormData(form);
    for (const [k, v] of Object.entries(set)) {
      fd.delete(k);
      for (const one of [].concat(v)) fd.append(k, one);
    }
    if (button) fd.append(button.name, button.value);
    const r = await fetch(url, {method: 'POST', body: new URLSearchParams(fd), credentials: 'include'});
    if (!r.ok) throw new Error(`HTTP ${r.status} at ${window.__ohl.step}`);
    return r;
  };
  try {
    const CRITERIA = '/eid/reports/EID_Report_Criteria.aspx';
    window.__ohl.step = 'criteria page';
    let doc = parse(await (await fetch(CRITERIA, {credentials: 'include'})).text());
    let form = doc.forms[0];
    const reports = field(form, 'ddlReports');
    // Choosing the report is itself a postback that adds the owner and status fields
    window.__ohl.step = 'choose FACILITY LISTING';
    await sleep(2000);
    doc = parse(await (await post(form, CRITERIA, {[reports.name]: 'facility_listing', __EVENTTARGET: reports.name, __EVENTARGUMENT: ''})).text());
    form = doc.forms[0];
    const need = ['ddl_Include_Owner', 'rbltype', 'lbxProviders', 'lbxStatus', 'lbxCounties', 'btSubmit'];
    const missing = need.filter(n => !field(form, n));
    if (missing.length) throw new Error('form changed, missing ' + missing.join(', '));
    window.__ohl.step = 'run report';
    await sleep(2000);
    let r = await post(form, CRITERIA, {
      __EVENTTARGET: '', __EVENTARGUMENT: '',
      [field(form, 'ddl_Include_Owner').name]: 'YES',
      [field(form, 'rbltype').name]: 'L',
      [field(form, 'lbxProviders').name]: '17',          // Nursing Home
      [field(form, 'lbxStatus').name]: '01',             // ACTIVE
      [field(form, 'lbxCounties').name]: [...field(form, 'lbxCounties').options][0].value,  // all counties
    }, field(form, 'btSubmit'));
    if (!/Report_Output\.aspx/i.test(r.url)) throw new Error('report did not open (still on ' + new URL(r.url).pathname + ')');
    doc = parse(await r.text());
    form = doc.forms[0];
    if (!field(form, 'ddlExport')) throw new Error('output page has no export menu');
    window.__ohl.step = 'export CSV';
    await sleep(2000);
    r = await post(form, r.url, {[field(form, 'ddlExport').name]: 'csv'}, field(form, 'btnExport'));
    const csv = await r.text();
    if (!csv.startsWith('"NAME","FACID"')) throw new Error('export is not the expected CSV: ' + csv.slice(0, 80));
    window.__ohl.rows = (csv.match(/"OHL\d+"/g) || []).length;
    if (window.OHL_DRY) { window.__ohl.csv = csv; window.__ohl.status = 'done (dry run, not saved)'; return; }
    const blob = new Blob([JSON.stringify({state: 'OH', fetched_at: new Date().toISOString(), csv})], {type: 'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'oh_licence_' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
    document.body.appendChild(a); a.click(); a.remove();
    window.__ohl.status = 'done';
  } catch (e) {
    window.__ohl.status = 'stopped';
    window.__ohl.error = String(e && e.message || e);
  }
})();
