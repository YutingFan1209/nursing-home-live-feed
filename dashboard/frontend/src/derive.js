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
};
export const STATUS_COLOR = {
  confirmed: "var(--status-confirmed)", expected: "var(--status-expected)", reported: "var(--status-reported)",
  unconfirmed: "var(--status-unconfirmed)", on_file: "var(--status-on-file)",
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

// Publication for news, from the source URL's domain
export function publication(d) {
  try {
    return new URL(d.source_url).hostname.replace(/^www\./, "");
  } catch { return null; }
}

// One plain-language line under the status
export function statusMeaning(d) {
  const s = displayStatus(d);
  if (s === "on_file") return "A UCC-1 financing statement on file with the state: a record of the loan, not of a sale.";
  if (s === "confirmed") {
    return d.source_type === "chow" ? "CMS records show this change of ownership."
      : "CMS ownership data shows a new owner at the facility within 6 months of this record.";
  }
  if (s === "expected") {
    return d.source_type === "con"
      ? "Filed with the state before closing. This is not a completed ownership transfer."
      : "Reported as pending. This is not a completed ownership transfer.";
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
