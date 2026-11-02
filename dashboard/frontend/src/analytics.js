// Visit counts through GoatCounter: no cookies, no personal data, so no
// consent banner. Empty code = off (local dev, tests, or before the account
// exists). Counted: page views (feed, record, home and methodology pages,
// no filter values in the path), downloads, source link clicks, and which
// filters are used. Typed search text, company names and dates are never
// sent: those count only as "used".
const GOATCOUNTER_CODE = "hefti-feed";

// Filters whose values are fixed choices (a state, a source type), safe to send
const VALUE_FILTERS = ["state", "sourceType", "ccnStatus", "tab", "refi", "liens", "scope", "since"];

const queue = [];

function send(vars) {
  if (!GOATCOUNTER_CODE) return;
  if (window.goatcounter?.count) window.goatcounter.count(vars);
  else queue.push(vars);
}

export function startAnalytics() {
  if (!GOATCOUNTER_CODE || location.hostname === "localhost" || location.hostname === "127.0.0.1") return;
  window.goatcounter = { no_onload: true };
  const s = Object.assign(document.createElement("script"), { async: true, src: "https://gc.zgo.at/count.js" });
  s.dataset.goatcounter = `https://${GOATCOUNTER_CODE}.goatcounter.com/count`;
  s.onload = () => { while (queue.length) window.goatcounter.count(queue.shift()); };
  document.head.appendChild(s);
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
