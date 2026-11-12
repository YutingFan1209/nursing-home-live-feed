// Visit counts through GoatCounter: no cookies, no personal data, so no
// consent banner. Empty code = off (local dev, tests, or before the account
// exists). Counted: page views (feed, record, home and methodology pages,
// no filter values in the path), downloads, source link clicks, and which
// filters are used. Typed search text, company names and dates are never
// sent: those count only as "used".
const GOATCOUNTER_CODE = "hefti-feed";

// Filters whose values are fixed choices (a state, a source type), safe to send
const VALUE_FILTERS = ["state", "sourceType", "ccnStatus", "tab", "refi", "liens", "since"];

// Sent as GoatCounter's counting pixel with only these fields. Their count.js
// isn't used: it adds the page's query string to every hit, which carries
// typed search text and company names (?q=, ?entity=), 2026-11-02.
let on = false, firstView = true;

function send(vars) {
  if (!on) return;
  const q = { p: vars.path, t: vars.title || vars.path, s: String(window.screen?.width || ""),
    b: navigator.webdriver ? "150" : "0", rnd: Math.random().toString(36).slice(2, 7) };
  if (vars.event) q.e = "true";
  // Where a visitor came from, once per visit; just the site, no path
  else if (firstView) {
    firstView = false;
    try { if (document.referrer) q.r = new URL(document.referrer).origin; } catch { /* no referrer */ }
  }
  new Image().src = `https://${GOATCOUNTER_CODE}.goatcounter.com/count?${new URLSearchParams(q)}`;
}

export function startAnalytics() {
  on = !!GOATCOUNTER_CODE && !["localhost", "127.0.0.1"].includes(location.hostname);
}

// One view per page the person sees: "/" for the feed, whatever the filters
export function countPage({ id, ccn, view }) {
  const path = id ? `/record/${id.slice(0, 8)}` : ccn ? `/home/${ccn}` : view ? `/${view}` : "/";
  send({ path, title: document.title });
}

export function countEvent(path, title) {
  send({ path, title: title || path, event: true });
}

// Any link off the site (filings, notices, articles, CMS profiles), by host
export function countOutboundClicks() {
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.("a[href^='http']");
    if (!a || a.host === location.host) return;
    countEvent(`source-click/${a.hostname.replace(/^www\./, "")}`);
  }, { capture: true });
}

// Changed filters since the last call, debounced by the caller
export function countFilters(now, before) {
  for (const k of Object.keys(now)) {
    if (!now[k] || now[k] === before[k]) continue;
    countEvent(VALUE_FILTERS.includes(k) ? `filter/${k}/${now[k]}` : `filter/${k}/used`);
  }
}
