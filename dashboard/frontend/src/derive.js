// Display-only fields derived from a deals.json record. Nothing here changes
// stored values: acquisition_date means different things by source (an
// effective date on CMS records, an expected close in news, a filing date on
// UCC-1s), so the card shows a role-labeled date instead of the raw field.

// Short source labels and their dot colors (always shown with the label)
export const SOURCE_LABEL = {
  chow: "CMS", con: "State CON", ucc: "State UCC-1", edgar: "SEC EDGAR", rss: "News",
};
// CSS variables from src/tokens.css
export const SOURCE_COLOR = {
  chow: "var(--source-chow)", con: "var(--source-con)", ucc: "var(--source-ucc)", edgar: "var(--source-edgar)", rss: "var(--source-rss)",
};

// Text colors on white, all 4.5:1 or better
export const STATUS_LABEL = {
  confirmed: "Confirmed", expected: "Expected", reported: "Reported", unconfirmed: "Unconfirmed", on_file: "On file",
  likely_closed: "Likely closed",
};
export const STATUS_COLOR = {
  confirmed: "var(--status-confirmed)", expected: "var(--status-expected)", reported: "var(--status-reported)",
  unconfirmed: "var(--status-unconfirmed)", on_file: "var(--status-on-file)",
  likely_closed: "var(--status-reported)",
};

// A UCC-1 is "confirmed" in the export only in the sense that it's on file:
// it records a loan, not that any sale happened.
export const displayStatus = (d) => d.source_type === "ucc" ? "on_file" : d.status;

const day = (v) => (v ? String(v).slice(0, 10) : null);

// The date the card leads with, and what it is
export function primaryDate(d) {
  const published = day(d.published_at);
  switch (d.source_type) {
    case "chow":
      return { role: "Effective", date: day(d.acquisition_date) };
    case "ucc":
      return { role: "Filed", date: day(d.acquisition_date) || published };
    case "con":
      // published_at is the date the state's filing or notice was posted;
      // Hawaii's list only dates the decision
      return { role: d.source_name?.startsWith("Hawaii") ? "Decision posted" : "Filed", date: published };
    case "edgar":
      return { role: "Filed", date: published };
    default:
      if (["expected", "unconfirmed"].includes(d.status) && d.acquisition_date) {
        return { role: "Expected close", date: day(d.acquisition_date) };
      }
      return { role: "Reported", date: published };
  }
}

// The source's own deal date, when the card leads with a different one
export function otherDate(d) {
  const p = primaryDate(d);
  const a = day(d.acquisition_date);
  if (!a || a === p.date) return null;
  if (d.source_type === "con") {
    if (d.source_name?.startsWith("New York")) return { role: "Council vote", date: a };
    return { role: d.status === "confirmed" ? "Expected close (in filing)" : "Expected close", date: a };
  }
  if (d.source_type === "rss" || d.source_type === "edgar") {
    return { role: d.status === "expected" || d.status === "unconfirmed" ? "Expected close" : "Deal date in source", date: a };
  }
  return null;
}

// An expected deal whose closing date has passed: the sale has most likely
// closed and CMS hasn't recorded the new owner yet (2026-11-04)
export function closePassed(d, today = new Date().toISOString().slice(0, 10)) {
  const a = day(d.acquisition_date);
  return d.status === "expected" && a && a < today && !d.source_name?.startsWith("New York") ? a : null;
}

// Publication for news, from the source URL's domain
export function publication(d) {
  try {
    return new URL(d.source_url).hostname.replace(/^www\./, "");
  } catch { return null; }
}

// One plain-language line under the status
export function statusMeaning(d) {
  const s = displayStatus(d);
  if (s === "on_file") return "Recorded with the state; we have not checked whether it has since lapsed or been terminated.";
  if (s === "confirmed") {
    return d.source_type === "chow" ? "CMS records show this change of ownership."
      : "CMS ownership data shows a new owner at the facility within 6 months of this record.";
  }
  if (s === "expected") {
    return d.source_type === "con"
      ? "Filed with the state before closing. This is not a completed ownership transfer."
      : "Reported as pending. This is not a completed ownership transfer.";
  }
  if (d.assumed_closed) {
    return "Filed or reported before closing. Its expected close date (or 90 days after filing) has passed, so it has likely closed, but nothing confirms it; CMS records don't show the new owner yet.";
  }
  if (s === "unconfirmed") {
    return "Filed or reported as pending over 6 months ago, with no record of completion. Don't treat it as completed.";
  }
  return "Reported in the news or a company filing; CMS records don't confirm it yet.";
}

export function howCollected(d) {
  if (d.source_type === "chow") {
    return d.source_url?.includes("y2hd-n93e")
      ? "New owner in the monthly CMS Nursing Home Ownership file"
      : "CMS Skilled Nursing Facility Change of Ownership file (quarterly)";
  }
  if (d.source_type === "ucc") {
    return d.individual_filing
      ? `${d.ucc_state || d.states?.[0] || "State"} UCC portal, searched by the names of people listed as owners in CMS data`
      : `${d.ucc_state || d.states?.[0] || "State"} UCC portal, searched by nursing home owner and lender names`;
  }
  if (d.source_type === "con") return `${d.source_name}, checked by this tracker`;
  if (d.source_type === "edgar") return "SEC EDGAR full-text search of 8-K filings";
  if (/google alert/i.test(d.source_name || "")) return "Google Alerts email; extracted automatically from the article";
  return `${d.source_name || "News"} RSS feed; extracted automatically from the article`;
}

// ---- UCC-1 cards (Financing tab) ----

// Words that don't tell two entity names apart ("Alpine Manor OpCo LLC" vs
// "Alpine Manor Nursing Center")
const NAME_NOISE = new Set(("llc l l c inc incorporated corp corporation co company lp llp ltd pllc pc " +
  "opco propco op prop operations operating operator holdings holding " +
  "center centre ctr the of and at for dba d b a snf nursing rehabilitation rehab healthcare health care " +
  "skilled facility home homes").split(" "));
const nameWords = (name) => new Set(String(name || "").toLowerCase().replace(/&/g, " and ")
  .replace(/[^a-z0-9]+/g, " ").split(" ").filter(w => w && !NAME_NOISE.has(w)));

// Share of the shorter name's words found in the other (0..1)
export function nameOverlap(a, b) {
  const A = nameWords(a), B = nameWords(b);
  if (!A.size || !B.size) return String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase() ? 1 : 0;
  let common = 0;
  for (const w of A) if (B.has(w)) common++;
  return common / Math.min(A.size, B.size);
}

// A debtor field can name several entities ("X Realty LLC; X Opco LLC")
export const debtorParts = (debtor) => String(debtor || "").split(/\s*;\s*/).filter(Boolean);

// Shown only when some named entity isn't the title under another spelling
export const sameName = (a, b, min = 0.6) => debtorParts(a).every(p => nameOverlap(p, b) >= min);

export const lenderIsKnown = (d) => !!d.lender && !d.lender.startsWith("Not available");

// The CMS facility a UCC card is about: exactly one, and not a person's
// possible homes (an individual filing names no home)
export const uccFacilityCcn = (d) => {
  const ccns = (d.ccns || []).filter(Boolean);
  return !d.individual_filing && ccns.length === 1 ? ccns[0] : null;
};

// NY's June 2026 name-only backfill has made-up filing numbers (NYX-…)
export const realFilingNumber = (d) => d.ucc_search_text ? null : d.ucc_filing_number || null;

const DAY_MS = 86400000;
// "12 days", "3 months", "2 years"
export function spanText(days) {
  const n = Math.abs(days);
  if (n < 60) return `${n} day${n === 1 ? "" : "s"}`;
  const months = Math.round(n / 30.44);
  if (months < 24) return `${months} months`;
  return `${Math.round(n / 365.25)} years`;
}

// At most two "why now" lines, fact first. Each { tag, text }, or [] when
// there's neither a CMS fact nor a reading (no invented fallback).
// fmt formats a YYYY-MM-DD date; name recases a name.
export function uccWhyNow(d, fmt = (x) => x, name = (x) => x) {
  const lines = [];
  const filed = day(d.acquisition_date) || day(d.published_at);
  const sale = d.cms_sale;
  if (sale?.date && filed) {
    const days = Math.round((Date.parse(filed) - Date.parse(sale.date)) / DAY_MS);
    const whom = [sale.buyer && `to ${name(sale.buyer)}`, sale.seller && `(from ${name(sale.seller)})`].filter(Boolean).join(" ");
    const when = days === 0 ? "Filed on the effective date of" : `Filed ${spanText(days)} ${days > 0 ? "after" : "before"}`;
    lines.push({ tag: "Per CMS", text: `${when} a sale CMS recorded${whom ? ` ${whom}` : ""}, effective ${fmt(sale.date)}.` });
  } else if (d.ucc_context?.owner_since && uccFacilityCcn(d)) {
    // the home's latest owner start date in CMS, not necessarily the borrower's
    lines.push({ tag: "Per CMS", text: `CMS lists the home's current owner since ${fmt(d.ucc_context.owner_since)}.` });
  }
  const label = d.ucc_context?.label;
  if (label && !(sale && label === "Likely purchase financing")) lines.push({ tag: "Inferred", text: `${label}.` });
  return lines.slice(0, 2);
}

// Same lender, same filing day, same first CCN: one card listing every
// filing number. Filings without a named lender (NJ) are never merged.
export function mergeUccDuplicates(deals) {
  const out = [], byKey = new Map();
  for (const d of deals) {
    const ccn = (d.ccns || []).filter(Boolean)[0];
    if (d.source_type !== "ucc" || !lenderIsKnown(d) || !ccn) { out.push(d); continue; }
    const key = `${d.lender.toLowerCase()}|${day(d.published_at)}|${ccn}`;
    const first = byKey.get(key);
    if (first) { first.merged.push(d); continue; }
    const item = { ...d, merged: [d] };
    byKey.set(key, item);
    out.push(item);
  }
  return out.map(d => d.merged?.length > 1 ? d : (d.merged ? d.merged[0] : d));
}

export const BATCH_MIN = 3;
// 3+ visible filings by one lender on one day: one expandable card. Run
// after mergeUccDuplicates, on the current page. Items: { batch: [...] } or a deal.
export function groupUccBatches(deals) {
  const key = (d) => d.source_type === "ucc" && lenderIsKnown(d) ? `${d.lender.toLowerCase()}|${day(d.published_at)}` : null;
  const counts = new Map();
  for (const d of deals) { const k = key(d); if (k) counts.set(k, (counts.get(k) || 0) + 1); }
  const out = [], open = new Map();
  for (const d of deals) {
    const k = key(d);
    if (!k || counts.get(k) < BATCH_MIN) { out.push(d); continue; }
    if (!open.has(k)) { const b = { batch: [] }; open.set(k, b); out.push(b); }
    open.get(k).batch.push(d);
  }
  return out;
}
