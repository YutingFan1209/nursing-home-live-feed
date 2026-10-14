import { useState, useEffect, useMemo, useCallback } from "react";
import { SOURCE_LABEL, SOURCE_COLOR, STATUS_LABEL, STATUS_COLOR, displayStatus, primaryDate, otherDate,
  publication, statusMeaning, howCollected } from "./derive.js";

const FACILITY_BASE = import.meta.env.VITE_FACILITY_BASE_URL || "https://www.medicare.gov/care-compare/details/nursing-home";
const DATA_URL = import.meta.env.BASE_URL + "deals.json";

// A bare "YYYY-MM-DD" is a calendar date. new Date() reads it as UTC
// midnight, which in US time zones is the previous evening, so every
// acquisition date used to display one day early.
const parseDay = (v) => {
  const m = typeof v === "string" && /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(v);
};

const fmtM = (v) => v != null ? `$${Number(v).toFixed(1)}M` : null;

// Names arrive in whatever case the source used -- CMS and most UCC portals
// shout in ALL CAPS, news/CHOW use proper case. Recase only single-case
// strings, so deliberate mixed case ("HarborChase", "McKnight's") is kept.
const KEEP_UPPER = new Set([
  "LLC", "LLP", "LLLP", "LP", "PLLC", "PC", "USA", "US", "NA", "N.A.", "II", "III", "IV", "VI", "VII",
  "CIBC", "HUD", "FHA", "REIT", "SNF", "SNFS", "ALF", "HCC", "CCRC", "PACE", "LTC", "HHS", "CMS", "UCC",
  "KKR", "MSN", "BMO", "PNC", "TD", "RBC", "CIT", "GE", "HCP", "CNL", "NHI", "LTCI", "MPT",
  "NY", "NJ", "PA", "OH", "KY", "CA", "TX", "FL", "IL", "MA", "MD", "VA", "NC", "SC", "GA", "TN", "MI", "WI",
  "MN", "MO", "IA", "KS", "AZ", "NM", "NV", "UT", "WA", "WV", "CT", "RI", "NH", "VT", "DC", "AR", "ND", "SD",
  "MT", "WY",
  // omitted on purpose: LA, CO (Co. = Company), DE, AL, MS, ID, OK, IN, ME, OR, HI, NE -- also ordinary words
]);
const BRAND_CASE = { JPMORGAN: "JPMorgan", MIDCAP: "MidCap", HARBORCHASE: "HarborChase", LENDINGTREE: "LendingTree",
  MONTICELLOAM: "MonticelloAM" };
const KEEP_LOWER = new Set(["of", "and", "at", "the", "for", "in", "on", "by", "to", "as", "a", "an", "de", "du"]);

function smartCase(text) {
  if (!text || typeof text !== "string") return text;
  const letters = text.replace(/[^A-Za-z]/g, "");
  if (!letters || (/[a-z]/.test(letters) && /[A-Z]/.test(letters))) return text;  // already mixed case
  // a one-word all-caps name this short is an acronym (PGIM, BHI), not shouting
  if (/^[A-Z]{2,5}$/.test(text.trim())) return text.trim();
  let first = true;
  return text.split(/(\s+|\/|-)/).map(tok => {
    if (!/[A-Za-z]/.test(tok)) return tok;
    const upper = tok.toUpperCase();
    const bare = upper.replace(/[^A-Z.]/g, "");
    const isFirst = first; first = false;
    if (BRAND_CASE[bare]) return tok.replace(/[A-Za-z]+/, BRAND_CASE[bare]);
    if (KEEP_UPPER.has(bare) || KEEP_UPPER.has(bare.replace(/\./g, ""))) return upper;
    // consonant-only tokens (JMB, HCR) are initialisms, not words
    if (/^[B-DF-HJ-NP-TV-Z]{2,4}$/.test(bare)) return upper;
    const lower = tok.toLowerCase();
    if (!isFirst && KEEP_LOWER.has(lower)) return lower;
    return lower
      .replace(/(^|[^a-z'])([a-z])/g, (m, pre, c) => pre + c.toUpperCase())    // O'Neil, St.Mary
      .replace(/^(\W*)Mc([a-z])/, (m, pre, c) => pre + "Mc" + c.toUpperCase())  // McKinley
      .replace(/'S\b/g, "'s")                                                   // Mary's
      .replace(/^(\W*)O'([a-z])/, (m, pre, c) => pre + "O'" + c.toUpperCase());  // O'Connor
  }).join("");
}

function withDisplayCase(deal) {
  return {
    ...deal,
    acquiring_entity: smartCase(deal.acquiring_entity),
    seller_entity:    smartCase(deal.seller_entity),
    buyer:            smartCase(deal.buyer),
    seller:           smartCase(deal.seller),
    lender:           smartCase(deal.lender),
    ucc_debtor:       smartCase(deal.ucc_debtor),
    operator_names:   deal.operator_names?.map(smartCase),
    facility_names:   deal.facility_names?.map(smartCase),
  };
}

const SOURCE = {
  chow:  { label: "CMS",     name: "CMS change-of-ownership data", tip: "CMS Provider Enrollment and ownership data — federal records of a completed change of ownership" },
  edgar: { label: "SEC EDGAR",     name: "SEC EDGAR 8-K", tip: "SEC EDGAR 8-K — a publicly traded company's own filing" },
  rss:   { label: "News",           name: "News report", tip: "Trade press or news article" },
  ucc:   { label: "State UCC-1",     name: "State UCC-1 filing", tip: "State UCC-1 financing statement — a record of a loan, not of a sale" },
  con:   { label: "State CON",   name: "State certificate-of-need / change-of-ownership filing", tip: "State certificate-of-need or change-of-ownership notice — filed before the deal closes" },
};

// deal_type comes from scripts/export_deals.py (pipeline/deal_labels.py)
const DEAL_TYPE = {
  ownership_change: { label: "Ownership change (sale)" },
  control_change:   { label: "Control change (no sale)" },
  lease:            { label: "Lease" },
  financing:        { label: "Financing" },
  acquisition_financing: { label: "Acquisition financing" },
  refinancing:      { label: "Refinancing" },
  non_deal_lien:    { label: "Non-deal lien" },
  amendment:        { label: "UCC-3 amendment" },
};

const STATUS = {
  confirmed:   "Confirmed",
  reported:    "Reported",
  expected:    "Expected",
  unconfirmed: "Unconfirmed",
};

const FACILITY_TYPE = {
  skilled_nursing: "Skilled nursing",
  senior_living:   "Other senior living",
  hospital:        "Hospital",
  non_us:          "Outside the US",
  unknown:         "Not stated in the source",
};

// Ownership changes, plus the two events that move control without a sale.
// Everything else is a loan or lien of some kind.
const OWNERSHIP_TYPES = new Set(["ownership_change", "control_change", "lease"]);
const TABS = [
  { key: "", label: "Ownership changes" },
  { key: "financing", label: "Financing" },
];
const inTab = (d, tab) => tab === "financing" ? !OWNERSHIP_TYPES.has(d.deal_type) : OWNERSHIP_TYPES.has(d.deal_type);

// Card tags, one per card at most, only for exceptions. Text on fill is 4.5:1 or better.
const TAGS = {
  individual: { label: "Individual filing",     bg: "#EEE6F4", fg: "#4B2D6B",
    tip: "UCC-1 filed against a person who owns nursing homes, not against a facility — often a personal loan" },
  control:    { label: "Control change",        bg: "#F5E9D2", fg: "#6B4A0B",
    tip: "Control of the facility changes without a sale (e.g. a parent becoming the active parent, a new manager)" },
  lease:      { label: "Lease",                 bg: "#ECEAE4", fg: "#4A4A45",
    tip: "Operations move to a new operator under a lease; no sale" },
  acq:        { label: "Acquisition financing", bg: "#E3EAF6", fg: "#23406F",
    tip: "A loan for a purchase: the source says so, or the UCC-1 was filed within 6 months of the facility's owner changing in CMS records" },
  cms:        { label: "CMS confirmed",         bg: "#E4F1EC", fg: "#14543F",
    tip: "CMS records show a new owner at this facility within 6 months of this date" },
};

function cardTag(d) {
  if (d.individual_filing) return TAGS.individual;
  if (d.deal_type === "control_change") return TAGS.control;
  if (d.deal_type === "lease") return TAGS.lease;
  if (d.deal_type === "acquisition_financing") return TAGS.acq;
  if (d.deal_type === "ownership_change" && (d.source_type === "chow" || d.cms_confirmed_on)) return TAGS.cms;
  return null;
}

const lenderKnown = (d) => d.lender && !d.lender.startsWith("Not available");
const homeUrl = (ccn) => FACILITY_BASE ? `${FACILITY_BASE}/${ccn}` : null;
const permalink = (id) => `${window.location.origin}${window.location.pathname}?id=${id}`;

const US_STATES = [
  "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
  "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
  "VA","WA","WV","WI","WY","DC"
];

// Every name a deal involves, by role -- the company view and entity filter
// match on these case-insensitively.
const ENTITY_ROLES = [
  ["buyer",    d => [d.buyer]],
  ["seller",   d => [d.seller]],
  ["borrower", d => [d.ucc_debtor]],
  ["lender",   d => [lenderKnown(d) ? d.lender : null]],
  ["operator", d => d.operator_names || []],
];
const normName = (n) => (n || "").trim().toLowerCase();
const dealRoles = (d, entity) => {
  const key = normName(entity);
  return ENTITY_ROLES.filter(([, get]) => get(d).some(n => n && normName(n) === key)).map(([role]) => role);
};

function EntityLink({ name, onPick }) {
  if (!name || !onPick) return <span>{name}</span>;
  return (
    <button type="button" onClick={() => onPick(name)} title={`All records involving ${name}`}
      style={{ ...linkBtn, minHeight: 32, color: "#1F2328", textDecoration: "underline dotted", textUnderlineOffset: 3 }}>
      {name}
    </button>
  );
}

const headlineText = (d) => (d.headline || []).map(([t]) => t).join("");

// One column list drives the CSV and the data dictionary on the methodology page
const CSV_COLUMNS = [
  ["id", d => d.id, "Record ID. Open a record at <site>?id=<id>."],
  ["permalink", d => permalink(d.id), "Link to this record on the site."],
  ["headline", d => headlineText(d), "The record's headline as shown on the site."],
  ["date", d => d.acquisition_date || d.created_at?.slice(0, 10), "Effective or filing date from the source; the date found if the source gives none."],
  ["found_date", d => d.found_date, "Date this tracker first found the record."],
  ["status", d => STATUS[d.status] || d.status, "Confirmed (CMS record, a state UCC filing, or CMS shows a new owner within 6 months), Expected (filed or reported before closing), Unconfirmed (filed or reported as pending over 6 months ago, still not confirmed), or Reported (news or a company filing, not yet confirmed)."],
  ["status_detail", d => d.status_detail, "What the status rests on."],
  ["deal_type", d => d.deal_type, "ownership_change (a sale), control_change (control moves without a sale), lease, financing, acquisition_financing, refinancing, non_deal_lien, or amendment (UCC-3)."],
  ["facility_type", d => d.facility_type, "skilled_nursing (includes any record with a nursing home in it), senior_living, hospital, non_us, or unknown (the source doesn't say)."],
  ["tag", d => cardTag(d)?.label, "The tag shown on the card, if any."],
  ["source", d => SOURCE[d.source_type]?.name || d.source_type, "Kind of source."],
  ["source_name", d => d.source_name, "The specific source (feed, agency or filing system)."],
  ["source_url", d => d.source_url, "Link to the source document or search page."],
  ["source_title", d => d.source_title, "Title of the source document."],
  ["buyer", d => d.buyer, "Buyer as named in the source."],
  ["buyer_disclosure", d => d.buyer_disclosure, "named; described (unnamed, but the source describes them); undisclosed (the source says so, or a state filing has no buyer field); not_named (the source doesn't say); cms (the source doesn't name them, and CMS lists exactly one new owner)."],
  ["buyer_description", d => d.buyer_description, "How the source describes an unnamed buyer, e.g. 'a national REIT'."],
  ["seller", d => d.seller, "Seller as named in the source."],
  ["borrower", d => d.ucc_debtor, "UCC filings: the debtor named on the filing."],
  ["lender", d => d.lender, "Lender or secured party."],
  ["lender_category", d => d.lender_category, "UCC filings: kind of lender, from the secured party's name."],
  ["states", d => (d.states || []).join(" "), "Two-letter state codes."],
  ["facility_count", d => d.facility_count, "Number of facilities, as reported."],
  ["facilities", d => (d.facility_names || []).join("; "), "Facility names. For individual filings these are possible homes, not confirmed links."],
  ["match_type", d => d.match_type, "Individual filings: single_home, multi_home or unmatched. Others: ccn_matched or no_ccn."],
  ["ccns", d => (d.ccns || []).filter(Boolean).join(" "), "CMS Certification Numbers of matched facilities."],
  ["ccn_match", d => d.ccn_match, "How the CCNs were found: cms_matcher (the pipeline's CMS matching) or exact_facility_name (a facility name identical to exactly one CMS facility in the state)."],
  ["beds", d => d.beds, "CMS certified beds across the record's facilities; blank unless every facility is matched."],
  ["cms_confirmed_on", d => d.cms_confirmed_on, "Date CMS shows a new owner at a matched facility, if within 6 months of this record."],
  ["cms_new_owner", d => d.cms_event?.owner, "The new owner CMS lists, when it lists exactly one."],
  ["grouped_into", d => d.grouped_into, "If set, this record is another source for the deal with this ID, and shows on that deal's card."],
  ["source_count", d => d.sources?.length, "Number of sources behind this record's card (including duplicates merged in the database)."],
  ["deal_value_m", d => d.deal_value_m, "Sale price in $ millions, as reported."],
  ["financing_amount_m", d => d.financing_amount_m, "Loan amount in $ millions, as reported."],
  ["display_status", d => STATUS_LABEL[displayStatus(d)], "Status as shown on the site: the status column, except UCC-1 filings show On file (a record of the loan, not of a sale)."],
  ["date_role", d => primaryDate(d).role, "What primary_date is: Effective (CMS records), Expected close (news of a pending deal that gives a closing date), Filed (state filings and notices, UCC-1s, SEC filings), Decision posted (Hawaii's list, which dates only the decision), or Reported (news, publication date)."],
  ["primary_date", d => primaryDate(d).date, "The date shown on the card, as described by date_role. The date column keeps the source's own date field."],
  ["source_label", d => SOURCE_LABEL[d.source_type] || d.source_type, "Short source type: CMS, State CON, State UCC-1, SEC EDGAR or News."],
  ["publication", d => d.source_type === "rss" ? publication(d) : null, "News only: the publication, from the source URL's domain."],
];

function downloadCsv(rows, name) {
  const cell = v => v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
  const url = URL.createObjectURL(new Blob([rows.map(r => r.map(cell).join(",")).join("\n")], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `${name}.csv` });
  a.click();
  URL.revokeObjectURL(url);
}
const downloadDeals = (deals, name) => downloadCsv(
  [CSV_COLUMNS.map(([h]) => h), ...deals.map(d => CSV_COLUMNS.map(([, get]) => get(d)))], name || "nursing-home-deals");
const downloadDictionary = () => downloadCsv(
  [["column", "description"], ...CSV_COLUMNS.map(([h, , desc]) => [h, desc])], "nursing-home-deals-data-dictionary");

// Easter egg: the Konami code sends a flock of little houses up the page.
const KONAMI = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "b", "a"];

function useKonami(onUnlock) {
  useEffect(() => {
    let progress = 0;
    const onKey = (e) => {
      if (e.target.closest?.("input, select, textarea")) return;
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      progress = key === KONAMI[progress] ? progress + 1 : (key === KONAMI[0] ? 1 : 0);
      if (progress === KONAMI.length) { progress = 0; onUnlock(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onUnlock]);
}

function HouseParade({ onDone }) {
  const houses = useMemo(() => Array.from({ length: 28 }, (_, i) => ({
    left: Math.random() * 100,
    delay: Math.random() * 1.6,
    duration: 3 + Math.random() * 2.5,
    size: 18 + Math.random() * 22,
    glyph: i % 7 === 0 ? "🏡" : "🏠",
  })), []);
  useEffect(() => { const t = setTimeout(onDone, 6500); return () => clearTimeout(t); }, [onDone]);
  return (
    <div onClick={onDone} style={{ position: "fixed", inset: 0, zIndex: 1000, pointerEvents: "auto", overflow: "hidden" }}>
      {houses.map((h, i) => (
        <span key={i} className="nh-house" style={{ position: "absolute", bottom: -60, left: `${h.left}%`,
          fontSize: h.size, animation: `nh-float ${h.duration}s ease-in ${h.delay}s forwards` }}>{h.glyph}</span>
      ))}
      <div style={{ position: "absolute", left: "50%", top: "38%", transform: "translate(-50%, -50%)",
        background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: "18px 24px",
        boxShadow: "0 12px 40px rgba(0,0,0,0.15)", textAlign: "center", maxWidth: "calc(100vw - 32px)",
        animation: "nh-pop 0.35s ease-out" }}>
        <div style={{ fontSize: 28, marginBottom: 6 }}>🏠</div>
        <div style={{ fontSize: 15, fontWeight: 700, color: "#111827" }}>You found the secret room.</div>
        <div style={{ fontSize: 13, color: "#6b7280", marginTop: 4 }}>Now go call your grandparents 💛</div>
      </div>
    </div>
  );
}

// NJ's portal has no per-filing URL, but its search wizard accepts a
// replayed POST of tokens scripts/export_deals.py fetches at export time
// (deals.json `ucc_search_forms`), so this submits the filing-number search
// in a new tab. PA has no equivalent at all (verified 2026-09-23), so it
// gets a copy button to paste into the portal's search box instead.
function UccSource({ deal, searchForm }) {
  const [copied, setCopied] = useState(false);
  const label = `${deal.ucc_state} UCC-1 filing #${deal.ucc_filing_number}`;

  if (searchForm) {
    return (
      <form method="post" action={searchForm.action} target="_blank" style={{ margin: 0 }}>
        {Object.entries(searchForm.fields).map(([name, value]) =>
          <input key={name} type="hidden" name={name} value={value} />)}
        <input type="hidden" name={searchForm.filing_number_field} value={deal.ucc_filing_number} />
        <button type="submit" style={{ ...linkBtn, ...lnk }}>
          {label} ↗
        </button>
      </form>
    );
  }

  const copy = () => navigator.clipboard?.writeText(deal.ucc_filing_number)
    .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });

  return (
    <span style={{ display: "inline-flex", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
      <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnkBlock}>
        {deal.ucc_state} UCC-1 filing #{deal.ucc_filing_number} ↗
      </a>
      {deal.ucc_state === "PA" && (
        <button type="button" onClick={copy} style={{ ...btnGhost, fontSize: 12, padding: "0 12px" }}>
          {copied ? "Copied" : "Copy filing #"}
        </button>
      )}
    </span>
  );
}

// Names arrive as the source wrote them; only shouting (ALL CAPS from CMS
// and UCC portals) is recased, never a lower-case description
const shoutCase = (t) => /[a-z]/.test(t) ? t : smartCase(t);

// Long lists (11 opcos in one filing) show the first few and a toggle
function NameList({ names, onPick, max = 4 }) {
  const [all, setAll] = useState(false);
  const shown = all ? names : names.slice(0, max);
  return <>
    {shown.map((n, i) => <span key={n}>{i > 0 && ", "}<EntityLink name={n} onPick={onPick} /></span>)}
    {names.length > max && (
      <button type="button" onClick={() => setAll(a => !a)} style={{ ...linkBtn, ...lnk, fontSize: 13, marginLeft: 6 }}>
        {all ? "show fewer" : `+${names.length - max} more`}</button>
    )}
  </>;
}

function Field({ label, children }) {
  return <div><div style={dl}>{label}</div><div style={dv}>{children}</div></div>;
}

function CopyLink({ id }) {
  const [copied, setCopied] = useState(false);
  const url = permalink(id);
  const copy = (e) => {
    if (!navigator.clipboard) return;  // fall back to following the link
    e.preventDefault();
    navigator.clipboard.writeText(url).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  };
  return <a href={url} onClick={copy} style={lnkBlock}>{copied ? "Link copied" : "Permalink to this record"}</a>;
}

function SourceLink({ deal, searchForms }) {
  if (deal.source_type === "chow") {
    return deal.source_url?.includes("y2hd-n93e")
      ? <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnkBlock}>CMS Nursing Home Ownership Data ↗</a>
      : <a href="https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership"
          target="_blank" rel="noreferrer" style={lnkBlock}>CMS SNF Change of Ownership Dataset ↗</a>;
  }
  if (deal.ucc_filing_number) return <UccSource deal={deal} searchForm={searchForms?.[deal.ucc_state]} />;
  if (!deal.source_url) return <span>No link available</span>;
  return <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnkBlock}>{publication(deal) || deal.source_url} ↗</a>;
}

// A facility's page on this site (?ccn=), named when CMS details are known
function FacilityLink({ ccn, facilities, name }) {
  const f = facilities?.[ccn];
  const label = smartCase(f?.name || name || `CCN ${ccn}`);
  return (
    <a href={`?ccn=${ccn}`} style={{ ...lnkBlock, minHeight: 32, display: "inline" }}>
      {label}{f?.city ? `, ${smartCase(f.city)}` : ""} <span style={{ color: "#5F636A", marginLeft: 4, whiteSpace: "nowrap" }}>({ccn})</span>
    </a>
  );
}

function FacilityPage({ ccn, facilities, allDeals, onBack }) {
  const f = facilities[ccn];
  const records = allDeals
    .filter(d => (d.ccns || []).includes(ccn) || (d.possible_homes || []).some(h => h.ccn === ccn))
    .sort((a, b) => (b.acquisition_date || b.created_at || "").localeCompare(a.acquisition_date || a.created_at || ""));
  const possible = (d) => d.individual_filing && d.match_type === "multi_home";
  return (
    <article style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, padding: "18px 20px 24px" }}>
      <button type="button" onClick={onBack} style={{ ...linkBtn, color: "#23406F", fontSize: 14 }}>← All records</button>
      <h2 style={{ fontSize: 22, fontWeight: 700, margin: "6px 0 4px" }}>{f ? smartCase(f.name) : `CCN ${ccn}`}</h2>
      <p style={{ fontSize: 14, color: "#5F636A", marginBottom: 14 }}>
        {[f?.city && `${smartCase(f.city)}, ${f.state}`, `CMS Certification Number ${ccn}`,
          f?.beds && `${f.beds.toLocaleString()} certified beds`].filter(Boolean).join(" · ")}
        {homeUrl(ccn) && <> · <a href={homeUrl(ccn)} target="_blank" rel="noreferrer" style={{ ...lnk, fontSize: 14 }}>CMS facility profile ↗</a></>}
      </p>

      <h3 style={{ fontSize: 16, fontWeight: 600, margin: "10px 0 6px" }}>Direct owners in CMS ownership data</h3>
      {f?.owners?.length ? (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {f.owners.map(([owner, start], i) => (
            <li key={i} style={{ display: "flex", gap: 10, fontSize: 14, color: "#3F4248", padding: "3px 0" }}>
              <span style={{ color: "#5F636A", minWidth: 96, fontVariantNumeric: "tabular-nums" }}>{start ? `since ${start}` : "—"}</span>
              <span>{shoutCase(owner)}</span>
            </li>
          ))}
        </ul>
      ) : <p style={{ fontSize: 14, color: "#5F636A" }}>{f ? "CMS lists no direct owners for this facility (common for nonprofits)." : "No CMS details on file for this facility."}</p>}

      <h3 style={{ fontSize: 16, fontWeight: 600, margin: "18px 0 6px" }}>Records in this tracker ({records.length})</h3>
      <ol style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {records.map(d => {
          const tag = cardTag(d);
          return (
            <li key={d.id} style={{ display: "flex", gap: 10, padding: "2px 0", flexWrap: "wrap", alignItems: "center", borderTop: "1px solid #F0EEE8" }}>
              <span style={{ color: "#5F636A", minWidth: 96, fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
                {d.acquisition_date || d.created_at?.slice(0, 10)}</span>
              <span style={{ fontSize: 13, color: "#5F636A", minWidth: 92 }}>{SOURCE[d.source_type]?.label}</span>
              <a href={`?id=${d.id}`} style={{ ...lnkBlock, flex: "1 1 260px", color: "#1F2328", fontSize: 14 }}>
                {headlineText(d).replace(/[A-Z][A-Z .,&'-]{3,}/g, m => shoutCase(m))}</a>
              {possible(d) ? <span style={{ fontSize: 12, color: "#4B2D6B" }}>possible home (owner has {d.possible_homes.length})</span>
                : tag && <span style={{ fontSize: 12, fontWeight: 600, color: tag.fg, background: tag.bg, padding: "2px 8px", borderRadius: 4 }}>{tag.label}</span>}
            </li>
          );
        })}
      </ol>
    </article>
  );
}

// Every source behind the card, plus the CMS change that confirms it, by date
function Timeline({ deal }) {
  const rows = (deal.sources || []).map(s => ({
    date: s.date, key: s.id,
    what: (SOURCE[s.source_type]?.name || s.source_name || "Source") + (s.merged ? " (merged duplicate)" : ""),
    text: s.headline || publication(s), url: s.source_url, record: s.merged || s.id === deal.id ? null : s.id,
    current: s.id === deal.id,
  }));
  if (deal.cms_event && deal.source_type !== "chow") {
    const e = deal.cms_event;
    rows.push({ date: e.date, key: "cms", what: e.source,
      text: e.owner ? `${shoutCase(e.owner)} listed as owner` : e.owner_count > 1 ? `${e.owner_count} new owners listed` : "New owner listed",
      url: homeUrl(e.ccn) });
  }
  rows.sort((a, b) => (a.date || "").localeCompare(b.date || ""));
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={dl}>Sources and timeline{deal.sources?.length > 1 ? ` · ${deal.sources.length} sources for this deal` : ""}</div>
      <ol style={{ listStyle: "none", padding: 0, margin: "4px 0 0" }}>
        {rows.map(r => (
          <li key={r.key} style={{ display: "flex", gap: 10, fontSize: 13, color: "#3F4248", padding: "4px 0", flexWrap: "wrap" }}>
            <span style={{ color: "#5F636A", minWidth: 84, fontVariantNumeric: "tabular-nums" }}>{r.date}</span>
            <span style={{ flex: "1 1 260px" }}>
              <strong style={{ fontWeight: 600 }}>{r.what}</strong>{r.current && " (this card)"}: {r.text && shoutCase(r.text)}
              {r.url && <> · <a href={r.url} target="_blank" rel="noreferrer" style={{ ...lnk, fontSize: 13 }}>source ↗</a></>}
              {r.record && <> · <a href={`?id=${r.record}`} style={{ ...lnk, fontSize: 13 }}>record</a></>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// "Sep 25, 2026" -- the card's dates are absolute, each labeled by its role
const fmtFull = (v) => v ? parseDay(v).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : null;

const isPersonalLoan = (d) => d.deal_type === "non_deal_lien" && /personal loan/.test(headlineText(d));
// Transaction type for the meta row
const typeLabel = (d) => d.individual_filing ? "Individual filing"
  : isPersonalLoan(d) ? "Likely personal loan"
  : d.deal_type === "ownership_change" ? "Ownership change"
  : DEAL_TYPE[d.deal_type]?.label || d.deal_type;

const FINANCING_TYPES = new Set(["financing", "acquisition_financing", "refinancing", "non_deal_lien", "amendment"]);

// Missing values are never filled in; this says why there is none
const missingBuyer = (d) => d.buyer_disclosure === "undisclosed" ? "Undisclosed"
  : d.buyer_disclosure === "not_named" ? "Not named in source"
  : d.buyer_disclosure === "described" && d.buyer_description ? d.buyer_description
  : "Not reported";

const firstPlus = (names) => names.length > 1 ? `${names[0]} +${names.length - 1}` : names[0];
const joinNote = (...parts) => parts.filter(Boolean).join(" · ") || null;
const bedsNote = (n) => n ? `${Number(n).toLocaleString()} beds` : null;

// The four columns of a collapsed card: who, counterparty, where, when.
// Each is { label, value, missing, note, ccn }.
function cardColumns(d, facilities) {
  const ucc = d.source_type === "ucc";
  const financing = ucc || FINANCING_TYPES.has(d.deal_type);
  const states = (d.states || []).join(", ");
  const ccns = (d.ccns || []).filter(Boolean);
  const price = d.deal_type === "ownership_change" ? fmtM(d.deal_value_m) : null;
  const cols = [];

  // 1. Buyer / Lender / Operator
  if (financing) {
    const nj = d.lender?.startsWith("Not available");
    cols.push(lenderKnown(d)
      ? { label: "Lender", value: d.lender, note: fmtM(d.financing_amount_m) }
      : { label: "Lender", value: nj ? "Not returned by NJ's search" : "Not reported", missing: true, note: fmtM(d.financing_amount_m) });
  } else if (d.deal_type === "lease" || d.deal_type === "control_change") {
    const op = d.buyer || (d.deal_type === "control_change" ? d.operator_names?.[0] : null);
    cols.push(op ? { label: "Operator", value: op } : { label: "Operator", value: missingBuyer(d), missing: true });
  } else if (d.buyer) {
    cols.push({ label: "Buyer", value: d.buyer, note: d.buyer_disclosure === "cms" ? "Per CMS, not the source" : null });
  } else {
    cols.push({ label: "Buyer", value: missingBuyer(d), missing: true });
  }

  // 2. Seller / Borrower / Landlord / Prior operator
  if (d.individual_filing) {
    cols.push(d.ucc_debtor ? { label: "Filed against (individual)", value: d.ucc_debtor }
      : { label: "Filed against (individual)", value: "Name not shown", missing: true });
  } else if (ucc) {
    cols.push(d.ucc_debtor ? { label: "Borrower", value: d.ucc_debtor } : { label: "Borrower", value: "Not reported", missing: true });
  } else if (financing) {
    const ops = d.operator_names || [];
    cols.push(ops.length ? { label: "Borrower", value: firstPlus(ops), note: "Named as operator in source" }
      : { label: "Borrower", value: "Not reported", missing: true });
  } else {
    const label = d.deal_type === "lease" ? "Landlord or prior operator"
      : d.deal_type === "control_change" ? "Prior operator" : "Seller";
    const seller = d.seller && d.seller !== d.buyer ? d.seller : null;
    cols.push(seller ? { label, value: seller } : { label, value: "Not reported", missing: true });
  }

  // 3. Facility / Facilities / Possible homes
  const one = (ccn, name) => {
    const f = facilities?.[ccn];
    // the name the source uses; CMS's legal name is under Details
    return { value: smartCase(name || f?.name), note: joinNote(f?.state || states, bedsNote(f?.beds || d.beds), price) };
  };
  if (d.individual_filing || (ucc && ccns.length)) {
    const homes = d.individual_filing ? (d.possible_homes || []) : ccns.map(c => ({ ccn: c, name: facilities?.[c]?.name || `CCN ${c}` }));
    if (homes.length === 1) cols.push({ label: d.individual_filing ? "Home" : "Facility", ...one(homes[0].ccn, homes[0].name), ccn: homes[0].ccn });
    else if (homes.length > 1) cols.push({ label: `Possible homes (${homes.length})`, value: firstPlus(homes.map(h => smartCase(h.name))), note: states });
    else cols.push({ label: "Possible homes", value: "None found in CMS records", missing: true, note: states });
  } else if (ucc) {
    cols.push({ label: "Facility", value: "Not matched to a CMS facility", missing: true, note: states });
  } else {
    const names = d.facility_names || [];
    const count = d.facility_count || names.length || null;
    if (count === 1 && names.length) cols.push({ label: "Facility", ...one(ccns.length === 1 ? ccns[0] : null, names[0]) });
    else if (count === 1) cols.push({ label: "Facility", value: "Not named in source", missing: true, note: joinNote(states, price) });
    else if (count > 1) cols.push({ label: "Facilities", value: count.toLocaleString(), note: joinNote(states, price) });
    else cols.push({ label: "Facilities", value: "Not reported", missing: true, note: joinNote(states, price) });
  }

  // 4. Role-labeled date
  const p = primaryDate(d);
  cols.push({ label: p.role, value: fmtFull(p.date) || "Not reported", missing: !p.date,
    note: d.found_date ? `Found ${fmtFull(d.found_date)}` : null });
  return cols;
}

function Column({ col }) {
  const value = col.missing
    ? <span style={{ color: "#5F636A", fontStyle: "italic", fontWeight: 400 }}>{col.value}</span>
    : col.ccn ? <a href={`?ccn=${col.ccn}`} style={{ color: "#1F2328", textDecoration: "underline", textUnderlineOffset: 3,
        display: "inline-flex", alignItems: "center", minHeight: 32 }}>{col.value}</a>
    : shoutCase(col.value);
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 12, color: "#5F636A", marginBottom: 2 }}>{col.label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: "#1F2328", lineHeight: 1.35, overflowWrap: "anywhere" }}>{value}</div>
      {col.note && <div style={{ fontSize: 12, color: "#5F636A", marginTop: 2 }}>{col.note}</div>}
    </div>
  );
}

function SourceDot({ type }) {
  return <span aria-hidden="true" style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%",
    background: SOURCE_COLOR[type] || "#5F636A", flexShrink: 0 }} />;
}

function SourceLegend() {
  return (
    <span style={{ display: "inline-flex", gap: "0 12px", flexWrap: "wrap", alignItems: "center" }}>
      {Object.keys(SOURCE_LABEL).map(k => (
        <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><SourceDot type={k} />{SOURCE_LABEL[k]}</span>
      ))}
    </span>
  );
}

function Section({ title, children }) {
  return (
    <section style={{ minWidth: 0 }}>
      <h3 style={{ ...dl, fontSize: 12, marginBottom: 8 }}>{title}</h3>
      {children}
    </section>
  );
}

// label / value rows inside a Details section
function Row({ label, children }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(96px, 38%) 1fr", gap: 10, fontSize: 14, padding: "3px 0", alignItems: "baseline" }}>
      <div style={{ color: "#5F636A" }}>{label}</div>
      <div style={{ color: "#1F2328", minWidth: 0, overflowWrap: "anywhere" }}>{children}</div>
    </div>
  );
}

const missingText = (t) => <span style={{ color: "#5F636A", fontStyle: "italic" }}>{t}</span>;

function DealCard({ deal, expanded, onToggle, searchForms, onEntity, history, facilities, chowFreshness }) {
  const detailsId = `details-${deal.id}`;
  const individual = deal.individual_filing;
  const ccns = deal.ccns?.filter(Boolean) || [];
  const status = displayStatus(deal);
  const cols = cardColumns(deal, facilities);
  const other = otherDate(deal);
  const names = deal.facility_names || [];

  return (
    <article id={`record-${deal.id}`} style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, marginBottom: 10 }}>
      <div style={{ padding: "12px 18px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13, color: "#3F4248", minHeight: 32 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }} title={SOURCE[deal.source_type]?.tip}>
            <SourceDot type={deal.source_type} />{SOURCE_LABEL[deal.source_type] || deal.source_type}
          </span>
          <span aria-hidden="true" style={{ color: "#8A8D93" }}>·</span>
          <span>{typeLabel(deal)}</span>
          <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 14 }}>
            <span style={{ fontWeight: 600, color: STATUS_COLOR[status] }}>{STATUS_LABEL[status] || status}</span>
            <button type="button" onClick={() => onToggle(deal.id)} aria-expanded={expanded} aria-controls={detailsId}
              style={{ ...linkBtn, minHeight: 32, color: "#23406F", fontSize: 13, textDecoration: "underline", textUnderlineOffset: 3 }}>
              {expanded ? "Hide details" : "Details"}
            </button>
          </span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: "10px 16px", marginTop: 8 }}>
          {cols.map((c, i) => <Column key={i} col={c} />)}
        </div>
      </div>

      {expanded && (
        <div id={detailsId} className="nh-details" style={{ borderTop: "1px solid #E3E0D8", padding: "16px 18px",
          background: "#FAFAF8", borderRadius: "0 0 10px 10px" }}>
          <Section title="Transaction overview">
            <Row label="Type">{DEAL_TYPE[deal.deal_type]?.label || deal.deal_type}{individual && " · filed against an individual"}
              {isPersonalLoan(deal) && " (likely personal loan)"}</Row>
            {cols.slice(0, 2).map((c, i) => (
              <Row key={i} label={c.label}>
                {c.missing ? missingText(c.value)
                  : <EntityLink name={c.value} onPick={entityNames(deal).includes(c.value) ? onEntity : null} />}
                {c.note && <span style={{ color: "#5F636A" }}> · {c.note}</span>}
              </Row>
            ))}
            {!individual && deal.operator_names?.length > 0 && deal.source_type === "ucc" && (
              <Row label="Linked CMS owner(s)"><NameList names={deal.operator_names} onPick={onEntity} /></Row>
            )}
            {!individual && (names.length > 0 || deal.facility_count) && (
              <Row label={deal.facility_count === 1 ? "Facility" : "Facilities"}>
                {names.length ? names.join(", ") : missingText("Names aren't in the source")}
                {deal.facility_count > names.length && names.length > 0 && <span style={{ color: "#5F636A" }}> (source names {names.length} of {deal.facility_count})</span>}
                {deal.facility_count > 1 && !names.length && <span style={{ color: "#5F636A" }}> · {deal.facility_count} facilities</span>}
              </Row>
            )}
            {individual && (
              <Row label={deal.match_type === "multi_home" ? "Possible homes" : deal.match_type === "single_home" ? "Home" : "Possible homes"}>
                <div style={{ color: "#3F4248", marginBottom: 2 }}>
                  {deal.match_type === "multi_home"
                    ? `This person owns or controls each of these ${deal.ucc_state || ""} homes in CMS records. The filing doesn't say which, if any, it concerns.`
                    : deal.match_type === "single_home"
                    ? `The only ${deal.ucc_state || ""} home this person owns or controls in CMS records. The filing itself doesn't name a home.`
                    : "No matching home in CMS ownership records."}
                </div>
                {(deal.possible_homes || []).map(h => <div key={h.ccn} style={{ padding: "4px 0" }}><FacilityLink ccn={h.ccn} facilities={facilities} name={h.name} /></div>)}
              </Row>
            )}
            {!individual && ccns.length > 0 && (
              <Row label={deal.source_type === "ucc" && ccns.length > 1 ? "Possible homes (CMS)" : "CMS facilities"}>
                {ccns.slice(0, 12).map(ccn => <div key={ccn} style={{ padding: "4px 0" }}><FacilityLink ccn={ccn} facilities={facilities} /></div>)}
                {ccns.length > 12 && <div>+{ccns.length - 12} more</div>}
                {history?.length > 0 && <div style={{ color: "#5F636A", fontSize: 13 }}>{history.length} other record{history.length > 1 ? "s" : ""} in this tracker at {ccns.length > 1 ? "these facilities" : "this facility"}</div>}
              </Row>
            )}
            <Row label="Location">{deal.states?.length ? deal.states.join(", ") : missingText("Not reported")}</Row>
            {deal.beds && <Row label="Certified beds (CMS)">{deal.beds.toLocaleString()}</Row>}
            {deal.deal_type !== "control_change" && fmtM(deal.deal_value_m) && <Row label="Price">{fmtM(deal.deal_value_m)}</Row>}
            {fmtM(deal.financing_amount_m) && <Row label="Loan amount">{fmtM(deal.financing_amount_m)}</Row>}
            <Row label="Facility type">{FACILITY_TYPE[deal.facility_type] || deal.facility_type}</Row>
            {(() => { const p = primaryDate(deal); return <Row label={p.role}>{p.date ? fmtFull(p.date) : missingText("Not reported")}</Row>; })()}
            {other && <Row label={other.role}>{fmtFull(other.date)}</Row>}
            {deal.published_at && primaryDate(deal).date !== deal.published_at.slice(0, 10) && <Row label="Published">{fmtFull(deal.published_at)}</Row>}
            <Row label="Found by tracker">{deal.found_date ? fmtFull(deal.found_date) : missingText("Not recorded")}</Row>
            {deal.ucc_also_filed?.length > 0 && <Row label="Same loan also filed">{deal.ucc_also_filed.join(", ")}</Row>}
          </Section>

          <Section title="Verification">
            <Row label="Status"><span style={{ fontWeight: 600, color: STATUS_COLOR[status] }}>{STATUS_LABEL[status]}</span></Row>
            <p style={{ fontSize: 14, color: "#1F2328", margin: "2px 0 4px" }}>{statusMeaning(deal)}</p>
            <Row label="Source type">{SOURCE[deal.source_type]?.name || deal.source_type}</Row>
            <Row label="CMS match">
              {ccns.length || deal.possible_homes?.length
                ? (individual && deal.match_type === "multi_home" ? `Possible homes only (${deal.possible_homes.length})`
                  : `Matched to ${ccns.length || deal.possible_homes.length} CMS facilit${(ccns.length || deal.possible_homes.length) > 1 ? "ies" : "y"}`)
                : "Not matched to a CMS facility"}
              {deal.cms_confirmed_on && `; CMS shows a new owner from ${fmtFull(deal.cms_confirmed_on)}`}
              {chowFreshness && <div style={{ color: "#5F636A", fontSize: 13 }}>{chowFreshness}</div>}
            </Row>
            {deal.source_type !== "ucc" && deal.ucc_confirmed && <Row label="Corroboration">Matches a state UCC-1 financing statement</Row>}
          </Section>

          {deal.summary && (
            <Section title="Narrative context">
              <p style={{ fontSize: 14, lineHeight: 1.55, color: "#3F4248" }}>{deal.summary}</p>
            </Section>
          )}

          <Section title="Source documentation">
            <Row label="Original source"><SourceLink deal={deal} searchForms={searchForms} /></Row>
            <Row label="How collected">{howCollected(deal)}</Row>
            <Row label="Sources">{deal.sources?.length || 1}</Row>
            {(deal.sources?.length > 1 || deal.cms_event) && <Timeline deal={deal} />}
            <Row label="Record ID"><code style={{ fontSize: 12 }}>{deal.id}</code></Row>
            <Row label="Cite this record"><CopyLink id={deal.id} /></Row>
            <Row label="Something wrong?">
              <a href={mailUrl(`Correction: record ${deal.id.slice(0, 8)}`, `Record: ${permalink(deal.id)}\n\nWhat's wrong:\n\nSource showing the correct information:\n`)}
                style={{ ...lnk, display: "inline-flex", alignItems: "center", minHeight: 32 }}>Report an error with this record</a>
            </Row>
          </Section>
        </div>
      )}
    </article>
  );
}
const entityNames = (d) => [d.buyer, d.seller, d.ucc_debtor, lenderKnown(d) ? d.lender : null, ...(d.operator_names || [])].filter(Boolean);


// Public project inbox for corrections and feedback (email, so no account is needed)
const FEEDBACK_EMAIL = "nhlivefeed@gmail.com";

// Tile-grid US map (one square per state, laid out roughly where the state
// sits): shading = records in the current tab and filters, click filters
// the list to that state. Restored from the Oct 5 version (07d919bf).
const TILE_POS = {
  AK:[0,0],ME:[11,0],VT:[10,1],NH:[11,1],WA:[1,2],ID:[2,2],MT:[3,2],ND:[4,2],MN:[5,2],IL:[6,2],WI:[7,2],MI:[8,2],NY:[9,2],RI:[10,2],MA:[11,2],
  OR:[1,3],NV:[2,3],WY:[3,3],SD:[4,3],IA:[5,3],IN:[6,3],OH:[7,3],PA:[8,3],NJ:[9,3],CT:[10,3],
  CA:[1,4],UT:[2,4],CO:[3,4],NE:[4,4],MO:[5,4],KY:[6,4],WV:[7,4],VA:[8,4],MD:[9,4],DE:[10,4],
  AZ:[2,5],NM:[3,5],KS:[4,5],AR:[5,5],TN:[6,5],NC:[7,5],SC:[8,5],DC:[9,5],
  OK:[4,6],LA:[5,6],MS:[6,6],AL:[7,6],GA:[8,6],HI:[0,7],TX:[4,7],FL:[9,7],
};
const STATE_NAMES = {AL:"Alabama",AK:"Alaska",AZ:"Arizona",AR:"Arkansas",CA:"California",CO:"Colorado",CT:"Connecticut",DE:"Delaware",DC:"District of Columbia",FL:"Florida",GA:"Georgia",HI:"Hawaii",ID:"Idaho",IL:"Illinois",IN:"Indiana",IA:"Iowa",KS:"Kansas",KY:"Kentucky",LA:"Louisiana",ME:"Maine",MD:"Maryland",MA:"Massachusetts",MI:"Michigan",MN:"Minnesota",MS:"Mississippi",MO:"Missouri",MT:"Montana",NE:"Nebraska",NV:"Nevada",NH:"New Hampshire",NJ:"New Jersey",NM:"New Mexico",NY:"New York",NC:"North Carolina",ND:"North Dakota",OH:"Ohio",OK:"Oklahoma",OR:"Oregon",PA:"Pennsylvania",RI:"Rhode Island",SC:"South Carolina",SD:"South Dakota",TN:"Tennessee",TX:"Texas",UT:"Utah",VT:"Vermont",VA:"Virginia",WA:"Washington",WV:"West Virginia",WI:"Wisconsin",WY:"Wyoming"};
// Light to dark green; counts are very skewed (NY has hundreds of UCC
// filings), so buckets are quantiles of the states that have any records.
// Text on the two darkest shades is white, the rest dark: 4.5:1 or better.
const MAP_SHADES = ["#E2EFEA", "#B7D6CA", "#7FB3A0", "#2E6F5E", "#14432F"];

function mapBuckets(counts) {
  const vals = Object.values(counts).filter(v => v > 0).sort((a, b) => a - b);
  if (!vals.length) return [];
  return [0.2, 0.4, 0.6, 0.8].map(q => vals[Math.min(vals.length - 1, Math.floor(q * vals.length))]);
}

function StatesMap({ counts, selected, onPick, what }) {
  const [hover, setHover] = useState(null);
  const cuts = mapBuckets(counts);
  const shade = (n) => MAP_SHADES[cuts.filter(c => n > c).length];
  const shown = hover || selected;
  const n = shown ? (counts[shown] || 0) : null;
  return (
    <div style={{ maxWidth: 520 }}>
      <div style={{ fontSize: 13, color: "#3F4248", minHeight: 20, marginBottom: 8 }}>
        {shown
          ? <><strong>{STATE_NAMES[shown]}</strong> · {n.toLocaleString()} record{n === 1 ? "" : "s"}{shown === selected && !hover ? " (filtered)" : ""}</>
          : <span style={{ color: "#5F636A" }}>{what} by state. Hover for the count; click to filter the list.</span>}
      </div>
      <div role="group" aria-label={`${what} by state`}
        style={{ display: "grid", gridTemplateColumns: "repeat(12, minmax(0, 1fr))", gap: 3 }}
        onMouseLeave={() => setHover(null)}>
        {Object.entries(TILE_POS).map(([s, [c, r]]) => {
          const v = counts[s] || 0;
          const bg = v ? shade(v) : "#F0EEE8";
          const dark = v && MAP_SHADES.indexOf(bg) >= 3;
          return (
            <button key={s} type="button" aria-label={`${STATE_NAMES[s]}: ${v} records`} aria-pressed={selected === s}
              disabled={!v}
              onMouseEnter={() => setHover(s)} onFocus={() => setHover(s)} onBlur={() => setHover(null)}
              onClick={() => onPick(selected === s ? "" : s)}
              style={{ gridColumn: c + 1, gridRow: r + 1, aspectRatio: "1", padding: 0,
                border: selected === s ? "2px solid #1F2328" : "1px solid transparent",
                borderRadius: 4, background: bg, cursor: v ? "pointer" : "default",
                color: dark ? "#fff" : (v ? "#1F2328" : "#8A8D93"),
                fontSize: 11, fontWeight: 600, lineHeight: 1, font: "inherit" }}>
              <span style={{ fontSize: 11, fontWeight: 600 }}>{s}</span>
            </button>
          );
        })}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10, fontSize: 12, color: "#5F636A" }}>
        <span>Fewer</span>
        {MAP_SHADES.map(c => <span key={c} style={{ width: 16, height: 10, borderRadius: 2, background: c }} />)}
        <span>More</span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: "#F0EEE8", border: "1px solid #E3E0D8" }} /> None
        </span>
      </div>
    </div>
  );
}

// "in 3 days" / "2 hours ago"
function relTime(iso, now) {
  if (!iso) return null;
  const t = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? parseDay(iso).getTime() : new Date(iso).getTime();
  const diff = t - now, abs = Math.abs(diff);
  const [n, unit] = abs < 3600e3 ? [Math.max(1, Math.round(abs / 60e3)), "minute"]
    : abs < 86400e3 ? [Math.round(abs / 3600e3), "hour"] : [Math.round(abs / 86400e3), "day"];
  const s = `${n} ${unit}${n === 1 ? "" : "s"}`;
  return diff >= 0 ? `in ${s}` : `${s} ago`;
}

const FRESH_GROUPS = [["chow", "CMS"], ["con", "State CON"], ["ucc", "State UCC-1"], ["edgar", "SEC EDGAR"], ["rss", "News"]];

// When each source was last checked and when the next check is due. The
// collection runs by hand, so "due" is when a source's last check goes
// stale, not a scheduled run. Times tick live.
function SourceSchedule({ freshness }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 60e3); return () => clearInterval(t); }, []);
  const rows = freshness.sources || [];
  if (!rows.length) return <p style={{ fontSize: 13, color: "#5F636A" }}>The refresh schedule appears after the next data update.</p>;
  const cell = { padding: "6px 8px", borderBottom: "1px solid #F0EEE8", fontSize: 13, verticalAlign: "top" };
  return (
    <div>
      <p style={{ fontSize: 13, color: "#3F4248", marginBottom: 8, maxWidth: 640 }}>
        Sources are checked by hand, not on a fixed schedule. <strong>Next check due</strong> is when a source's last check
        goes stale given how often it publishes; for the CMS files it's when the next file is expected.
      </p>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 520 }}>
          <thead><tr>{["Source", "Publishes", "Last checked", "Next check due"].map(h =>
            <th key={h} style={{ ...cell, textAlign: "left", color: "#5F636A", fontWeight: 600, borderBottom: "1px solid #C9C5BB" }}>{h}</th>)}</tr></thead>
          <tbody>
            {FRESH_GROUPS.flatMap(([type, label]) => rows.filter(r => r.type === type).map(r => {
              const due = r.next_due && (/^\d{4}-\d{2}-\d{2}$/.test(r.next_due) ? parseDay(r.next_due).getTime() : new Date(r.next_due).getTime());
              const overdue = due && due < now;
              return (
                <tr key={r.name}>
                  <td style={cell}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><SourceDot type={type} />{r.name}</span>
                    {type === "ucc" && freshness.ucc_last_new_filing && (
                      <div style={{ color: "#5F636A", fontSize: 12, marginTop: 2 }}>
                        Last new filing: {Object.entries(freshness.ucc_last_new_filing)
                          .map(([st, ts]) => `${st} ${relTime(ts, now)}`).join(" · ")}
                      </div>
                    )}
                  </td>
                  <td style={{ ...cell, color: "#3F4248" }}>{r.cadence}</td>
                  <td style={{ ...cell, color: "#3F4248", whiteSpace: "nowrap" }} title={r.last_checked || ""}>
                    {r.last_checked ? relTime(r.last_checked, now) : <em style={{ color: "#8A2D2D" }}>Never recorded</em>}</td>
                  <td style={{ ...cell, whiteSpace: "nowrap", fontWeight: overdue ? 600 : 400, color: overdue ? "#8A2D2D" : "#3F4248" }}
                    title={r.next_due || ""}>
                    {r.next_due ? (overdue ? `Overdue (${relTime(r.next_due, now).replace(" ago", "")})` : relTime(r.next_due, now)) : "—"}</td>
                </tr>
              );
            }))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Feedback opens the reader's email app with a prefilled subject and template;
// no data is collected on this site. encodeURIComponent, not URLSearchParams:
// mail apps show URLSearchParams' "+" as literal plus signs.
const mailUrl = (subject, body) =>
  `mailto:${FEEDBACK_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;

function FeedbackSection() {
  const items = [
    ["Report an error", "A wrong party, date, status or facility on a record.",
      mailUrl("Correction", "Record link (copy it from the record's Details):\n\nWhat's wrong:\n\nSource showing the correct information:\n")],
    ["Suggest a source", "A state filing system, dataset or publication we should check.",
      mailUrl("Source suggestion", "Source (name and link):\n\nWhat it covers (states, kind of record):\n")],
    ["Tell us how you use it", "What you're researching, what's missing, what's confusing.",
      mailUrl("Feedback", "How I use the tracker:\n\nWhat would make it more useful:\n")],
  ];
  return (
    <section aria-labelledby="nh-feedback" style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10,
      padding: "14px 18px", marginTop: 28 }}>
      <h2 id="nh-feedback" style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>Help improve this tracker</h2>
      <p style={{ fontSize: 14, color: "#3F4248", marginBottom: 8, maxWidth: 640 }}>
        Researchers, journalists and policy staff use these records. If something is wrong, missing or unclear, please say so.
        Each link opens an email to the project with a short template filled in. No account is needed. If your email app doesn't open, write to{" "}
        <a href={`mailto:${FEEDBACK_EMAIL}`} style={lnk}>{FEEDBACK_EMAIL}</a>.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "4px 16px" }}>
        {items.map(([title, desc, url]) => (
          <div key={title}>
            <a href={url} style={{ ...lnkBlock, fontWeight: 600 }}>{title}</a>
            <div style={{ fontSize: 13, color: "#5F636A" }}>{desc}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Methodology({ chowDate, corrections, onBack }) {
  const h = { fontSize: 18, fontWeight: 600, margin: "28px 0 8px" };
  const p = { fontSize: 15, lineHeight: 1.65, color: "#3F4248", marginBottom: 10, maxWidth: 680 };
  const li = { ...p, marginBottom: 6 };
  return (
    <article style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, padding: "20px 20px 28px" }}>
      <button type="button" onClick={onBack} style={{ ...linkBtn, color: "#23406F", fontSize: 14 }}>← Back to the feed</button>
      <h2 style={{ fontSize: 22, fontWeight: 700, margin: "8px 0 12px" }}>Methodology</h2>
      <p style={p}>This tracker collects signs that a US skilled nursing facility (nursing home) is changing hands,
        from public records and the trade press. Most records are gathered and summarized automatically. Read each
        record's status and source before relying on it.</p>

      <h3 style={h}>What counts as a deal</h3>
      <ul style={{ paddingLeft: 20 }}>
        <li style={li}><strong>Ownership change (sale):</strong> a buyer acquires a facility, its operations or its owner.</li>
        <li style={li}><strong>Financing:</strong> a loan secured by a facility or its operator. A loan the source ties to a purchase
          is <em>acquisition financing</em>.</li>
      </ul>

      <h3 style={h}>Shown, but not counted as deals</h3>
      <ul style={{ paddingLeft: 20 }}>
        <li style={li}><strong>Control changes:</strong> control passes without a sale, e.g. a parent organization becoming
          the active parent, or a new manager. No buyer or price is shown.</li>
        <li style={li}><strong>Leases:</strong> operations move to a new operator under a lease.</li>
        <li style={li}><strong>Refinancings</strong> (hidden unless you tick "Show refinancings"): new loans on a facility
          whose owner hasn't changed.</li>
        <li style={li}><strong>Non-deal liens:</strong> UCC filings by equipment vendors, registered agents and small-business
          lenders, which are rarely about a sale.</li>
        <li style={li}><strong>Other senior living</strong> (assisted or independent living, memory care with no nursing home),
          hospitals, and records whose source doesn't say what kind of facility: hidden unless you tick
          "Include senior living and other" under More filters. Deals outside the US are removed.</li>
      </ul>

      <h3 style={h}>Status</h3>
      <ul style={{ paddingLeft: 20 }}>
        <li style={li}><strong>Confirmed:</strong> a CMS change-of-ownership record; or CMS ownership data shows a new owner at
          the facility within 6 months of the record (checked again every time the data is published, so pending deals
          become Confirmed once CMS catches up).</li>
        <li style={li}><strong>On file:</strong> a UCC-1 financing statement on file with the state. It is a record of a
          loan, not evidence that a sale happened.</li>
        <li style={li}><strong>Expected:</strong> filed with the state or reported before closing. Not yet completed as far
          as we know; the expected date is shown when the source gives one.</li>
        <li style={li}><strong>Unconfirmed:</strong> filed or reported as pending more than six months ago, and still not
          confirmed. The deal may not have closed, or CMS may not have caught up.</li>
        <li style={li}><strong>Reported:</strong> a news report or company filing that CMS data doesn't confirm yet.</li>
      </ul>

      <h3 style={h}>Reading a card</h3>
      <p style={p}>Each card shows its source and transaction type, its status, and four columns: the buyer (or lender,
        or new operator), the other party (seller, borrower, landlord or prior operator), the facility or facilities, and
        a date labeled by what it is:</p>
      <ul style={{ paddingLeft: 20 }}>
        <li style={li}><strong>Effective:</strong> the date a CMS record gives for the change.</li>
        <li style={li}><strong>Expected close:</strong> the closing date a news report gives for a pending deal.</li>
        <li style={li}><strong>Filed:</strong> the date of a state filing or notice, a UCC-1 statement or an SEC filing.
          When a state filing also gives an expected closing date, it is under Details.</li>
        <li style={li}><strong>Reported:</strong> the date a news report was published.</li>
      </ul>
      <p style={p}>"Found" is the day this tracker first found the record. A missing name is never filled in:
        <em> Undisclosed</em> means the source says it isn't disclosed, <em>Not named in source</em> that the source
        doesn't name them, and <em>Not reported</em> that the source doesn't give it. For a filing against a person,
        <em> Possible homes</em> are the homes that person owns in CMS records; none of them is a confirmed link.</p>
      <p style={p}>Sources: <SourceLegend /></p>

      <h3 style={h}>Buyers</h3>
      <p style={p}>The buyer is shown as named in the source. When the source describes the buyer without naming them,
        the description is shown ("a national REIT"). <em>Buyer not disclosed</em> appears only when the source says the
        buyer is undisclosed, or a state filing has no buyer field. When the source names no buyer but CMS ownership data
        lists exactly one new owner at the facility around the deal date, that owner is shown, marked "per CMS". If CMS
        lists several new owners at once, none of them is called the buyer.</p>

      <h3 style={h}>One card per deal</h3>
      <p style={p}>The same deal often appears in several sources: a state filing before closing, news coverage, then the
        CMS record. Records are shown as one card when they are the same kind of event within six months of each other and
        either concern the same CMS facility (with no conflicting buyer), or name the same buyer and seller (with no
        conflicting facility count), or name the same buyer, states and price. Names must match exactly, apart from case,
        punctuation, legal suffixes and words like "Group" or "Holdings"; similar but different names stay separate. The
        card lists every source by date under Details, and each source record keeps its own permalink and its own row in
        the CSV.</p>

      <h3 style={h}>Facilities and beds</h3>
      <p style={p}>Records are matched to CMS facilities (CCNs) by the collection pipeline, or, for state filings and news,
        when a facility name is identical to exactly one CMS facility in the same state. Bed counts are CMS certified beds,
        summed across a record's facilities, and shown only when every facility in the record is matched. Each matched
        facility has its own page listing its direct owners in CMS ownership data and every record in this tracker that
        concerns it, including filings against an owner for whom it is only a possible home.</p>

      <h3 style={h}>Filings against individual owners, and "possible homes"</h3>
      <p style={p}>In New York (and Ohio, when its search can run), UCC filings are also searched by the names
        of people listed as owners in CMS ownership data. A filing against a person doesn't say which nursing home, if any,
        it concerns; many are personal loans. We match the person's name to the homes in that state they own or control
        in CMS records. If there is exactly one, the record
        links to it. If there are several, they are listed as <em>possible homes</em>: none of them is a confirmed link.
        Matching is by first and last name, so a different person with the same name can be matched by mistake.</p>

      <h3 style={h}>Sources and update schedule</h3>
      <ul style={{ paddingLeft: 20 }}>
        <li style={li}>CMS change-of-ownership (CHOW) file: published quarterly{chowDate ? `; the file in use is dated ${chowDate}` : ""}.
          CMS records typically lag a closing by one to two months or more.</li>
        <li style={li}>CMS ownership data: monthly; used to confirm records and to find new owners.</li>
        <li style={li}>State filings (certificate-of-need and change-of-ownership notices) and state UCC-1 financing
          statements, checked state by state.</li>
        <li style={li}>SEC 8-K filings of publicly traded operators and REITs; trade press and news alerts.</li>
        <li style={li}>Sources are checked when the collection runs; the time of the last check is shown at the top of the page.</li>
      </ul>

      <h3 style={h}>Errors and corrections</h3>
      <p style={p}>News and UCC records are extracted and classified automatically and may contain errors: a wrong party,
        type or date. Each record links to its source; check it before citing. To report an error, use "Report an error with this record" in the record's Details, or email{" "}
        <a href={`mailto:${FEEDBACK_EMAIL}`} style={lnk}>{FEEDBACK_EMAIL}</a> with the record's permalink.</p>

      <h3 style={h}>Corrections log</h3>
      {corrections?.length ? (
        <>
          <p style={p}>Records corrected or removed after review, newest first.</p>
          <ul style={{ paddingLeft: 20 }}>
            {corrections.map((c, i) => (
              <li key={i} style={{ ...li, fontSize: 14 }}>
                <span style={{ color: "#5F636A", fontVariantNumeric: "tabular-nums" }}>{c.date}</span>{" · "}
                {c.on_site ? <a href={`?id=${c.id}`} style={{ ...lnk, fontSize: 14 }}>{c.title}</a> : <em>{c.title}</em>}
                {": "}{c.note.replace(/^(Corrected|Removed) \d{4}-\d{2}-\d{2}: /, (m, w) => `${w}: `)}
              </li>
            ))}
          </ul>
        </>
      ) : <p style={p}>No corrections yet.</p>}

      <h3 style={h}>Data versions</h3>
      <p style={p}>Every published version of the data is kept in the site's public repository. To cite the data as it stood
        on a given day, link that day's version of{" "}
        <a href="https://github.com/YutingFan1209/nursing-home-live-feed/commits/gh-pages/deals.json" target="_blank" rel="noreferrer" style={lnk}>deals.json</a>
        {" "}(each entry in that history is a fixed snapshot), along with the record's permalink.</p>

      <h3 style={h}>Data dictionary</h3>
      <p style={p}>Columns in the CSV download.{" "}
        <button type="button" onClick={downloadDictionary} style={{ ...linkBtn, ...lnk, textDecoration: "underline", verticalAlign: "middle" }}>
          Download as CSV</button></p>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 14, width: "100%" }}>
          <thead><tr><th style={th}>Column</th><th style={th}>Description</th></tr></thead>
          <tbody>
            {CSV_COLUMNS.map(([name, , desc]) => (
              <tr key={name}><td style={{ ...td, fontFamily: "monospace", whiteSpace: "nowrap" }}>{name}</td><td style={td}>{desc}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
const th = { textAlign: "left", padding: "8px 10px", borderBottom: "1px solid #C9C5BB", color: "#5F636A", fontWeight: 600 };
const td = { padding: "8px 10px", borderBottom: "1px solid #E3E0D8", verticalAlign: "top", color: "#3F4248" };

function CompanyPanel({ entity, deals, onEntity, onClose }) {
  const roleCounts = {}, counterparties = {}, states = new Set();
  const key = normName(entity);
  for (const d of deals) {
    for (const r of dealRoles(d, entity)) roleCounts[r] = (roleCounts[r] || 0) + 1;
    (d.states || []).forEach(st => states.add(st));
    const names = new Set(ENTITY_ROLES.flatMap(([, get]) => get(d)).filter(n => n && normName(n) !== key));
    for (const n of names) counterparties[n] = (counterparties[n] || 0) + 1;
  }
  const dates = deals.map(d => d.acquisition_date || d.created_at?.slice(0, 10)).filter(Boolean).sort();
  const top = Object.entries(counterparties).sort((a, b) => b[1] - a[1]).slice(0, 6);
  return (
    <div style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ fontSize: 17, fontWeight: 600, color: "#1F2328" }}>{entity}</div>
        <button type="button" onClick={onClose} style={{ ...btnGhost, marginLeft: "auto" }}>Close</button>
      </div>
      <div style={{ fontSize: 13, color: "#5F636A", marginTop: 4 }}>
        {deals.length} record{deals.length !== 1 ? "s" : ""} in this tab
        {Object.keys(roleCounts).length > 0 && " as " + Object.entries(roleCounts).map(([r, n]) => `${r} (${n})`).join(", ")}
        {dates.length > 0 && ` · ${dates[0]} – ${dates[dates.length - 1]}`}
        {states.size > 0 && ` · ${[...states].sort().join(", ")}`}
      </div>
      {top.length > 0 && (
        <div style={{ marginTop: 6, display: "flex", gap: "0 12px", flexWrap: "wrap", alignItems: "center" }}>
          <span style={dl}>Most frequent counterparties</span>
          {top.map(([n, c]) => (
            <button type="button" key={n} onClick={() => onEntity(n)} title={`All records involving ${n}`}
              style={{ ...linkBtn, fontSize: 13, color: "#3F4248" }}>{n} <span style={{ color: "#5F636A", marginLeft: 4 }}>{c}</span></button>
          ))}
        </div>
      )}
    </div>
  );
}

function filterDeals(allDeals, { state, dateFrom, dateTo, search, sourceType, ccnStatus, tab, refi, noLiens, scope, entity, since }) {
  return allDeals.filter(d => {
    // A record grouped into another deal's card shows there, not on its own
    if (d.grouped_into) return false;
    // "New since your last visit": found by the tracker after that time
    if (since && !(d.created_at && d.created_at > since)) return false;
    if (tab !== undefined && !inTab(d, tab)) return false;
    // Skilled-nursing-related records unless "Include senior living and other"
    if (!scope && d.facility_type !== "skilled_nursing") return false;
    // Refinancings are hidden unless asked for; non-deal liens shown unless hidden
    if (!refi && d.deal_type === "refinancing") return false;
    if (noLiens && d.deal_type === "non_deal_lien") return false;
    if (state && !(d.states || []).includes(state)) return false;
    // the date the card shows (Effective, Expected close, Filed, Reported)
    const shownDate = d._date;
    if (dateFrom && shownDate && shownDate < dateFrom) return false;
    if (dateTo && shownDate && shownDate > dateTo) return false;
    if (sourceType && d.source_type !== sourceType) return false;
    if (entity && dealRoles(d, entity).length === 0) return false;
    const hasCcn = (d.ccns || []).filter(Boolean).length > 0;
    if (ccnStatus === "matched" && !hasCcn) return false;
    if (ccnStatus === "unmatched" && hasCcn) return false;
    if (search) {
      const q = search.toLowerCase();
      const haystack = [
        d.buyer, d.seller, d.buyer_description, d.lender, d.ucc_debtor,
        ...(d.operator_names || []),
        ...(d.facility_names || []),
        ...(d.ccns || []),
        d._facilityText,
      ].filter(Boolean).join(" ").toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
}

// When this browser last visited, for "N new since your last visit". A visit
// is a session: the stored time only moves on after 6 hours away, so
// reloading doesn't reset it. Browser storage can be missing; then the line
// just doesn't show.
const VISIT_KEY = "nh-visits";
function readLastVisit() {
  try {
    const now = new Date().toISOString();
    const v = JSON.parse(localStorage.getItem(VISIT_KEY) || "{}");
    if (!v.current || Date.now() - new Date(v.current).getTime() > 6 * 3600 * 1000) {
      v.last = v.current || null;
      v.current = now;
    }
    localStorage.setItem(VISIT_KEY, JSON.stringify(v));
    return v.last;
  } catch { return null; }
}

// Filters live in the URL (shareable/bookmarkable) and can be saved as
// named views in this browser, each remembering when it was last opened so
// it can show how many deals are new since then.
const FILTER_PARAMS = { state: "state", dateFrom: "from", dateTo: "to", search: "q", sourceType: "source", ccnStatus: "ccn",
  tab: "tab", refi: "refi", noLiens: "noliens", scope: "scope", entity: "entity", since: "since" };
const SAVED_VIEWS_KEY = "nh-saved-views";

// Links and saved views from before the tabs carry a deal type ("type=...")
// instead -- map it onto the tabs.
function fromLegacyType(f, type) {
  if (!type || f.tab || f.refi || f.noLiens) return f;
  return { ...f,
    tab: type === "ownership_change" ? "" : "financing",
    refi: type === "all" || type === "refinancing" ? "1" : "",
    noLiens: type === "no_liens" ? "1" : "" };
}

function filtersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const f = Object.fromEntries(Object.entries(FILTER_PARAMS).map(([k, p]) => [k, params.get(p) || ""]));
  return fromLegacyType(f, params.get("type"));
}

function loadSavedViews() {
  try { return JSON.parse(localStorage.getItem(SAVED_VIEWS_KEY)) || []; } catch { return []; }
}

function storeSavedViews(views) {
  try { localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(views)); } catch { /* private mode etc. */ }
}

function viewName(f) {
  return [
    f.entity && `${f.entity} (all roles)`,
    f.search && `"${f.search}"`,
    f.state,
    f.sourceType && (SOURCE[f.sourceType]?.label || f.sourceType),
    f.tab === "financing" && "Financing",
    f.refi && "With refinancings",
    f.noLiens && "No non-deal liens",
    f.scope && "Incl. senior living & other",
    f.since && `New since ${f.since.slice(0, 10)}`,
    f.ccnStatus && (f.ccnStatus === "matched" ? "CCN matched" : "No CCN"),
    (f.dateFrom || f.dateTo) && `${f.dateFrom || "…"} – ${f.dateTo || "…"}`,
  ].filter(Boolean).join(" · ");
}

export default function App() {
  const [allDeals, setAllDeals]   = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading]     = useState(true);
  const [expanded, setExpanded]   = useState(() => new Set([new URLSearchParams(window.location.search).get("id")].filter(Boolean)));
  const [searchForms, setSearchForms] = useState({});
  const [freshness, setFreshness] = useState({});
  const [corrections, setCorrections] = useState([]);
  const initial = useMemo(filtersFromUrl, []);
  const [state, setState]         = useState(initial.state);
  const [dateFrom, setDateFrom]   = useState(initial.dateFrom);
  const [dateTo, setDateTo]       = useState(initial.dateTo);
  const [search, setSearch]       = useState(initial.search);
  const [sourceType, setSourceType] = useState(initial.sourceType);
  const [ccnStatus, setCcnStatus] = useState(initial.ccnStatus);
  const [tab, setTab]             = useState(initial.tab === "financing" ? "financing" : "");
  const [refi, setRefi]           = useState(initial.refi);
  const [noLiens, setNoLiens]     = useState(initial.noLiens);
  const [scope, setScope]         = useState(initial.scope);
  const [since, setSince]         = useState(initial.since);
  const lastVisit = useMemo(readLastVisit, []);
  const [entity, setEntity]       = useState(initial.entity);
  // ?id=<record> opens one record (a permalink); ?view=methodology the methodology page
  const [focusId, setFocusId]     = useState(() => new URLSearchParams(window.location.search).get("id") || "");
  const [view, setView]           = useState(() => new URLSearchParams(window.location.search).get("view") || "");
  const [facilityCcn, setFacilityCcn] = useState(() => new URLSearchParams(window.location.search).get("ccn") || "");
  const [facilities, setFacilities] = useState({});
  const [savedViews, setSavedViews] = useState(loadSavedViews);
  const [offset, setOffset]       = useState(0);
  const [parade, setParade]       = useState(false);
  // Open on load when a link already carries one of the tucked-away filters
  const [aboutOpen, setAboutOpen] = useState(false);  // "About this data" under the count line
  const [panel, setPanel]         = useState("");  // "map" | "schedule" inside About this data
  const [moreOpen, setMoreOpen]   = useState(!!(initial.sourceType || initial.ccnStatus || initial.dateFrom || initial.dateTo || initial.scope));
  const LIMIT = 20;

  useKonami(useCallback(() => setParade(true), []));
  const endParade = useCallback(() => setParade(false), []);

  useEffect(() => {
    console.log("%c🏠 Hi, curious person.", "font-size:16px;font-weight:700");
    console.log("This feed is stitched together from CMS ownership data, state UCC-1 filings, SEC 8-Ks and trade press. There's also a secret: ↑ ↑ ↓ ↓ ← → ← → B A");
  }, []);

  useEffect(() => {
    setLoading(true);
    fetch(DATA_URL + "?t=" + Date.now())
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => {
        // Newest first by the date each card shows (primaryDate)
        const deals = (data.deals || data || []).map(withDisplayCase)
          .map(d => ({ ...d, _date: primaryDate(d).date || d.created_at?.slice(0, 10) || "" }))
          .sort((a, b) => b._date.localeCompare(a._date));
        setAllDeals(deals);
        setSearchForms(data.ucc_search_forms || {});
        setFreshness(data.freshness || {});
        setCorrections(data.corrections || []);
        setFacilities(data.facilities || {});
        // Search also finds records by their CMS facilities' names and cities
        const fac = data.facilities || {};
        for (const d of deals) {
          d._facilityText = (d.ccns || []).map(c => fac[c] ? `${fac[c].name} ${fac[c].city || ""}` : "").join(" ");
        }
      })
      .catch(e => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const currentFilters = { state, dateFrom, dateTo, search, sourceType, ccnStatus, tab, refi, noLiens, scope, entity, since };
  const filterDeps = [state, dateFrom, dateTo, search, sourceType, ccnStatus, tab, refi, noLiens, scope, entity, since];

  useEffect(() => {
    if (focusId || view || facilityCcn) return;  // those pages own the URL
    const params = new URLSearchParams();
    for (const [k, p] of Object.entries(FILTER_PARAMS)) if (currentFilters[k]) params.set(p, currentFilters[k]);
    const qs = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (qs ? `?${qs}` : ""));
  }, [...filterDeps, focusId, view, facilityCcn]);

  // Record and methodology pages are real history entries, so Back works
  useEffect(() => {
    const onPop = () => {
      const p = new URLSearchParams(window.location.search);
      setFocusId(p.get("id") || ""); setView(p.get("view") || ""); setFacilityCcn(p.get("ccn") || "");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  // Links to ?id= / ?ccn= / ?view= stay inside the app instead of reloading
  // the page (and the 7 MB data file); modified clicks still open new tabs
  useEffect(() => {
    const onClick = (e) => {
      const a = e.target.closest?.("a[href^='?']");
      if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const p = Object.fromEntries(new URLSearchParams(a.getAttribute("href")));
      if (!(p.id || p.ccn || p.view)) return;
      e.preventDefault();
      go(p);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  function go(params) {
    const qs = new URLSearchParams(params).toString();
    window.history.pushState(null, "", window.location.pathname + (qs ? `?${qs}` : ""));
    setFocusId(params.id || ""); setView(params.view || ""); setFacilityCcn(params.ccn || "");
    if (params.id) setExpanded(prev => new Set([...prev, params.id]));
    window.scrollTo({ top: 0 });
  }

  const updateSavedViews = (views) => { setSavedViews(views); storeSavedViews(views); };
  const currentName = viewName(currentFilters);
  const isSaved = savedViews.some(v => v.name === currentName);

  function saveView() {
    if (!currentName || isSaved) return;
    updateSavedViews([...savedViews, { name: currentName, filters: currentFilters, lastSeen: new Date().toISOString() }]);
  }

  function openView(view) {
    const f = fromLegacyType(view.filters, view.filters.dealType);
    setState(f.state || ""); setDateFrom(f.dateFrom || ""); setDateTo(f.dateTo || "");
    setSearch(f.search || ""); setSourceType(f.sourceType || ""); setCcnStatus(f.ccnStatus || "");
    setTab(f.tab || ""); setRefi(f.refi || ""); setNoLiens(f.noLiens || ""); setScope(f.scope || "");
    setEntity(f.entity || ""); setOffset(0);
    updateSavedViews(savedViews.map(v => v.name === view.name ? { ...v, lastSeen: new Date().toISOString() } : v));
  }

  const removeView = (name) => updateSavedViews(savedViews.filter(v => v.name !== name));

  const newCounts = useMemo(() => Object.fromEntries(savedViews.map(v => [
    v.name,
    filterDeals(allDeals, fromLegacyType({ tab: "", ...v.filters }, v.filters.dealType))
      .filter(d => d.created_at && new Date(d.created_at) > new Date(v.lastSeen)).length,
  ])), [savedViews, allDeals]);

  // Everything but the tab, so each tab can show its own count
  const filteredAnyTab = useMemo(
    () => filterDeals(allDeals, { ...currentFilters, tab: undefined }),
    [allDeals, ...filterDeps]
  );
  const filtered = useMemo(() => filteredAnyTab.filter(d => inTab(d, tab)), [filteredAnyTab, tab]);
  // Map shading: this tab and these filters, any state
  const stateCounts = useMemo(() => {
    if (panel !== "map") return {};
    const c = {};
    for (const d of filterDeals(allDeals, { ...currentFilters, state: "" })) for (const s of new Set(d.states || [])) c[s] = (c[s] || 0) + 1;
    return c;
  }, [panel, allDeals, ...filterDeps]);
  const tabCounts = useMemo(() => Object.fromEntries(TABS.map(t => [t.key, filteredAnyTab.filter(d => inTab(d, t.key)).length])),
    [filteredAnyTab]);

  // Count line: every record in the data, and what the default view leaves
  // out (refinancings; senior living, hospitals and unspecified facilities;
  // records shown inside another deal's card). The three add up exactly.
  const stats = useMemo(() => {
    if (!allDeals.length) return null;
    const refi = allDeals.filter(d => !d.grouped_into && d.deal_type === "refinancing").length;
    const other = allDeals.filter(d => !d.grouped_into && d.deal_type !== "refinancing" && d.facility_type !== "skilled_nursing").length;
    const grouped = allDeals.filter(d => d.grouped_into).length;
    return { total: allDeals.length, refi, other, grouped };
  }, [allDeals]);
  const breakdown = stats && `${stats.refi.toLocaleString()} refinancings${stats.other > 0 ? ` and ${stats.other} senior living or other records` : ""} hidden by default`
    + (stats.grouped > 0 ? ` · ${stats.grouped} shown inside another card` : "");

  const byCcn = useMemo(() => {
    const m = new Map();
    for (const d of allDeals) for (const c of (d.ccns || [])) if (c) (m.get(c) || m.set(c, []).get(c)).push(d);
    return m;
  }, [allDeals]);

  const historyFor = (deal) => {
    const seen = new Map();
    for (const c of (deal.ccns || [])) for (const d of (byCcn.get(c) || [])) if (d.id !== deal.id) seen.set(d.id, d);
    return [...seen.values()].sort((a, b) =>
      (b.acquisition_date || b.created_at || "").localeCompare(a.acquisition_date || a.created_at || ""));
  };

  const focused = focusId ? allDeals.find(d => d.id === focusId) : null;
  useEffect(() => {
    const base = "Nursing Home Ownership Changes";
    document.title = view === "methodology" ? `Methodology — ${base}`
      : facilityCcn ? `${smartCase(facilities[facilityCcn]?.name || `CCN ${facilityCcn}`)} — ${base}`
      : focused ? `${headlineText(focused)} — ${base}` : base;
  }, [view, focused, facilityCcn, facilities]);
  const deals = filtered.slice(offset, offset + LIMIT);
  const total = filtered.length;

  const newSinceVisit = useMemo(() => lastVisit
    ? allDeals.filter(d => !d.grouped_into && d.facility_type === "skilled_nursing" && d.created_at > lastVisit).length : 0,
    [allDeals, lastVisit]);

  // Facility pages whose name or city matches the search, most records first
  const facilityMatches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (q.length < 3 || !Object.keys(facilities).length) return [];
    const counts = {};
    for (const d of allDeals) {
      if (d.grouped_into) continue;
      for (const c of new Set([...(d.ccns || []), ...(d.possible_homes || []).map(h => h.ccn)])) counts[c] = (counts[c] || 0) + 1;
    }
    return Object.entries(facilities)
      .filter(([, f]) => `${f.name} ${f.city || ""}`.toLowerCase().includes(q))
      .map(([ccn]) => [ccn, counts[ccn] || 0])
      .sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [search, facilities, allDeals]);

  // Clears filters but stays on the current tab
  function clearAll() {
    setState(""); setDateFrom(""); setDateTo("");
    setSearch(""); setSourceType(""); setCcnStatus(""); setRefi(""); setNoLiens(""); setScope(""); setEntity(""); setSince(""); setOffset(0);
  }

  function toggleExpand(id) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const hasFilters = state || dateFrom || dateTo || search || sourceType || ccnStatus || refi || noLiens || scope || entity || since;
  const moreCount = [sourceType, ccnStatus, dateFrom || dateTo, scope].filter(Boolean).length;

  // Opening a company view replaces the other filters -- it's a profile,
  // not a refinement of whatever list you were looking at.
  function pickEntity(name) {
    clearAll();
    setEntity(name);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const fmtDay = iso => iso && new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const fmtStamp = iso => iso && new Date(iso).toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const chowFreshness = freshness.chow_file_date &&
    `CMS CHOW file: ${new Date(freshness.chow_file_date + "T12:00:00").toLocaleDateString(undefined,
      { month: "short", day: "numeric", year: "numeric" })} (quarterly)`;
  const resetPage = (set) => (e) => { set(e.target.type === "checkbox" ? (e.target.checked ? "1" : "") : e.target.value); setOffset(0); };

  return (
    <div style={{ minHeight: "100vh", background: "#F4F3EF", color: "#1F2328",
      fontFamily: "'Public Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" }}>

      <div className="nh-wrap">
        <header style={{ marginBottom: 12 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, letterSpacing: "-0.02em", marginBottom: 6 }}>
            Nursing Home Ownership Changes
          </h1>
          <p style={{ fontSize: 14, color: "#5F636A", lineHeight: 1.6 }}>
            US nursing home ownership changes and financings, from CMS records, state filings, UCC-1s, SEC filings and news.
          </p>
          <div style={{ fontSize: 13, color: "#5F636A", marginTop: 4, display: "flex", gap: "0 16px", flexWrap: "wrap", alignItems: "center" }}>
            {stats && (
              <span title={breakdown}>
                {stats.total.toLocaleString()} records
                {freshness.pipeline_ran_at && <span title={chowFreshness || undefined}> · updated {fmtDay(freshness.pipeline_ran_at)}</span>}
              </span>
            )}
            <button type="button" aria-expanded={aboutOpen} aria-controls="nh-about" onClick={() => setAboutOpen(o => !o)}
              style={{ ...linkBtn, minHeight: 32, color: "#23406F", fontSize: 13 }}>About this data {aboutOpen ? "▴" : "▾"}</button>
          </div>
          {aboutOpen && (
            <div id="nh-about" style={{ fontSize: 13, color: "#5F636A", marginTop: 2 }}>
              {stats && (
                <p>
                  {stats.refi.toLocaleString()} refinancings
                  {stats.other > 0 && ` and ${stats.other} senior living or other records`} hidden by default
                  {stats.grouped > 0 && <span title="Shown in the Details of the deal they're grouped with"> · {stats.grouped} shown inside another card</span>}
                </p>
              )}
              <div style={{ display: "flex", gap: "0 16px", flexWrap: "wrap" }}>
                <a href="?view=methodology" onClick={e => { e.preventDefault(); go({ view: "methodology" }); }}
                  style={{ ...lnkBlock, fontSize: 13 }}>How this works</a>
                {stats && !view && !focusId && !facilityCcn && [["map", "Map by state"], ["schedule", "Source refresh schedule"]].map(([k, label]) => (
                  <button key={k} type="button" aria-expanded={panel === k} aria-controls={`nh-panel-${k}`}
                    onClick={() => setPanel(p => p === k ? "" : k)}
                    style={{ ...linkBtn, color: "#23406F", fontSize: 13 }}>{label} {panel === k ? "▴" : "▾"}</button>
                ))}
              </div>
              {stats && !view && !focusId && !facilityCcn && panel && (
                <div id={`nh-panel-${panel}`} style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, padding: "14px 16px", marginTop: 4 }}>
                  {panel === "map"
                    ? <StatesMap counts={stateCounts} selected={state} what={tab === "financing" ? "Financing records" : "Ownership changes"}
                        onPick={(s) => { setState(s); setOffset(0); }} />
                    : <SourceSchedule freshness={freshness} />}
                </div>
              )}
            </div>
          )}
        </header>

        {view === "methodology" ? (
          <Methodology chowDate={chowFreshness && freshness.chow_file_date} corrections={corrections} onBack={() => go({})} />
        ) : facilityCcn ? (
          loading ? <div style={{ height: 104 }} />
            : <FacilityPage ccn={facilityCcn} facilities={facilities} allDeals={allDeals} onBack={() => go({})} />
        ) : focusId ? (
          <section aria-label="Single record">
            <button type="button" onClick={() => go({})} style={{ ...linkBtn, color: "#23406F", fontSize: 14, marginBottom: 4 }}>← All records</button>
            {loading ? <div style={{ height: 104 }} />
              : focused ? (<>
                {focused.grouped_into && (
                  <p style={{ fontSize: 14, color: "#3F4248", margin: "4px 0 10px" }}>
                    This record is one of several sources for the same deal.{" "}
                    <a href={`?id=${focused.grouped_into}`} style={lnk}>See the deal's card</a>
                  </p>
                )}
                <DealCard deal={focused} expanded={expanded.has(focused.id)} onToggle={toggleExpand}
                  searchForms={searchForms} onEntity={(n) => { go({}); pickEntity(n); }}
                  history={expanded.has(focused.id) ? historyFor(focused) : null}
                  facilities={facilities} chowFreshness={chowFreshness} />
              </>) : <p style={{ fontSize: 15, color: "#5F636A", padding: "24px 0" }}>This record isn't in the current data. It may have been
                  removed as out of scope or merged with another record.</p>}
          </section>
        ) : (<>
        <div style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, padding: 12, marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <label htmlFor="nh-search" className="nh-sr">Search</label>
            <input id="nh-search" type="search" value={search} onChange={resetPage(setSearch)}
              placeholder="Search operator, buyer, borrower, facility or lender"
              style={{ ...ctl, flex: "1 1 220px", minWidth: 0 }} />
            <label htmlFor="nh-state" className="nh-sr">State</label>
            <select id="nh-state" value={state} onChange={resetPage(setState)} style={ctl}>
              <option value="">All states</option>
              {US_STATES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <button type="button" onClick={() => setMoreOpen(o => !o)} aria-expanded={moreOpen} aria-controls="nh-more"
              style={{ ...linkBtn, color: "#23406F", fontSize: 14, padding: "0 6px" }}>
              More filters{moreCount > 0 && ` (${moreCount})`} {moreOpen ? "▴" : "▾"}
            </button>
            {total > 0 && (
              <button type="button" onClick={() => downloadDeals(
                  // each card's grouped records too, right after it
                  filtered.flatMap(d => d.group_size > 1 ? [d, ...allDeals.filter(x => x.grouped_into === d.id)] : [d]),
                  currentName.replace(/[^\w-]+/g, "-").replace(/^-|-$/g, ""))}
                style={{ ...btnGhost, marginLeft: "auto" }} title="Download the filtered records as CSV">
                ↓ CSV
              </button>
            )}
          </div>
          {moreOpen && (
            <div id="nh-more" style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end",
              borderTop: "1px solid #E3E0D8", marginTop: 12, paddingTop: 12 }}>
              <label style={fieldLabel}>Source
                <select value={sourceType} onChange={resetPage(setSourceType)} style={ctl}>
                  <option value="">All sources</option>
                  {Object.entries(SOURCE).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
                </select>
              </label>
              <label style={fieldLabel}>CCN status
                <select value={ccnStatus} onChange={resetPage(setCcnStatus)} style={ctl}>
                  <option value="">Any</option>
                  <option value="matched">Matched to CCN</option>
                  <option value="unmatched">Not yet matched</option>
                </select>
              </label>
              <label style={fieldLabel}>From
                <input type="date" value={dateFrom} onChange={resetPage(setDateFrom)} style={ctl} />
              </label>
              <label style={fieldLabel}>To
                <input type="date" value={dateTo} onChange={resetPage(setDateTo)} style={ctl} />
              </label>
              <label style={checkLabel}>
                <input type="checkbox" checked={!!scope} onChange={resetPage(setScope)} style={checkBox} />
                Include senior living and other
              </label>
              {hasFilters && <button type="button" onClick={clearAll} style={btnGhost}>Clear all</button>}
              {hasFilters && !isSaved && (
                <button type="button" onClick={saveView} style={btnGhost} title="Save these filters in this browser">☆ Save view</button>
              )}
            </div>
          )}
        </div>

        {newSinceVisit > 0 && !since && (
          <p style={{ fontSize: 14, color: "#1F2328", margin: "0 0 4px" }}>
            <strong>{newSinceVisit.toLocaleString()} new</strong> since your last visit ({fmtDay(lastVisit)}) ·{" "}
            <button type="button" onClick={() => { setSince(lastVisit); setOffset(0); }}
              style={{ ...linkBtn, ...lnk, textDecoration: "underline", minHeight: 0, verticalAlign: "baseline" }}>Show them</button>
          </p>
        )}

        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0 16px", borderBottom: "1px solid #E3E0D8", marginBottom: 12 }}>
          <div role="tablist" aria-label="Record type" style={{ display: "flex", gap: 4 }}>
            {TABS.map(t => {
              const active = tab === t.key;
              return (
                <button key={t.key || "ownership"} type="button" role="tab" aria-selected={active}
                  onClick={() => { setTab(t.key); setOffset(0); }}
                  style={{ font: "inherit", fontSize: 15, minHeight: 44, padding: "0 14px", background: "none", border: "none", cursor: "pointer", fontWeight: active ? 600 : 400,
                    color: active ? "#1F2328" : "#5F636A", marginBottom: -1,
                    borderBottom: `2px solid ${active ? "#1F2328" : "transparent"}` }}>
                  {t.label} <span style={{ color: "#5F636A", fontWeight: 400 }}>{tabCounts[t.key]?.toLocaleString()}</span>
                </button>
              );
            })}
          </div>
          {stats && <span style={{ fontSize: 13, color: "#5F636A", marginLeft: "auto", padding: "6px 0" }}><SourceLegend /></span>}
        </div>

        {tab === "financing" && (
          <div style={{ display: "flex", gap: "0 20px", flexWrap: "wrap", marginBottom: 8 }}>
            <label style={checkLabel}>
              <input type="checkbox" checked={!!refi} onChange={resetPage(setRefi)} style={checkBox} />
              Show refinancings
            </label>
            <label style={checkLabel}>
              <input type="checkbox" checked={!!noLiens} onChange={resetPage(setNoLiens)} style={checkBox} />
              Hide non-deal liens
            </label>
          </div>
        )}

        {since && (
          <p style={{ fontSize: 14, color: "#3F4248", margin: "0 0 10px" }}>
            Showing records found since {fmtDay(since)} ·{" "}
            <button type="button" onClick={() => { setSince(""); setOffset(0); }}
              style={{ ...linkBtn, ...lnk, textDecoration: "underline", minHeight: 0, verticalAlign: "baseline" }}>Show all</button>
          </p>
        )}

        {facilityMatches.length > 0 && (
          <div style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, padding: "8px 16px", marginBottom: 12 }}>
            <div style={{ ...dl, marginTop: 6 }}>Facilities matching “{search}”</div>
            {facilityMatches.map(([ccn, n]) => (
              <div key={ccn} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <FacilityLink ccn={ccn} facilities={facilities} />
                <span style={{ fontSize: 13, color: "#5F636A" }}>{facilities[ccn]?.state} · {n} record{n === 1 ? "" : "s"}</span>
              </div>
            ))}
          </div>
        )}

        {entity && (
          <CompanyPanel entity={entity} deals={filtered} onEntity={pickEntity} onClose={() => { setEntity(""); setOffset(0); }} />
        )}

        {savedViews.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
            <span style={{ ...dl, marginBottom: 0, marginRight: 2 }}>Saved</span>
            {savedViews.map(v => {
              const active = v.name === currentName;
              return (
                <span key={v.name} style={{ display: "inline-flex", alignItems: "center", fontSize: 13, borderRadius: 22,
                  background: active ? "#1F2328" : "#fff", color: active ? "#fff" : "#3F4248",
                  border: `1px solid ${active ? "#1F2328" : "#E3E0D8"}` }}>
                  <button type="button" onClick={() => openView(v)}
                    style={{ ...linkBtn, color: "inherit", fontSize: 13, padding: "0 4px 0 12px" }}>
                    ★ {v.name}
                    {newCounts[v.name] > 0 && <span style={{ marginLeft: 6, fontWeight: 600 }}>+{newCounts[v.name]} new</span>}
                  </button>
                  <button type="button" onClick={() => removeView(v.name)} aria-label={`Remove saved view ${v.name}`}
                    style={{ ...linkBtn, color: "inherit", padding: "0 12px 0 6px", minWidth: 32 }}>×</button>
                </span>
              );
            })}
          </div>
        )}

        {loading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} style={{ background: "#fff", border: "1px solid #E3E0D8",
              borderRadius: 10, height: 104, marginBottom: 10, animation: "pulse 1.5s infinite" }} />
          ))
        ) : loadError ? (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <div style={{ fontSize: 14, color: "#B42318" }}>Failed to load data: {loadError}</div>
          </div>
        ) : deals.length === 0 ? (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <div style={{ fontSize: 14, color: "#5F636A" }}>No records match these filters</div>
            {hasFilters && <button type="button" onClick={clearAll} style={{ ...btnGhost, marginTop: 16 }}>Clear filters</button>}
          </div>
        ) : (
          deals.map(deal => (
            <DealCard key={deal.id} deal={deal}
              expanded={expanded.has(deal.id)}
              onToggle={toggleExpand}
              searchForms={searchForms}
              onEntity={pickEntity}
              history={expanded.has(deal.id) ? historyFor(deal) : null}
              facilities={facilities} chowFreshness={chowFreshness} />
          ))
        )}

        {total > LIMIT && (
          <div style={{ display: "flex", justifyContent: "space-between",
            alignItems: "center", marginTop: 20, gap: 8 }}>
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              style={{ ...btnGhost, opacity: offset === 0 ? 0.4 : 1 }}>← Newer</button>
            <span style={{ fontSize: 13, color: "#5F636A", textAlign: "center" }}>
              {offset + 1}–{Math.min(offset + LIMIT, total)} of {total.toLocaleString()}
            </span>
            <button type="button" disabled={offset + LIMIT >= total} onClick={() => setOffset(offset + LIMIT)}
              style={{ ...btnGhost, opacity: offset + LIMIT >= total ? 0.4 : 1 }}>Older →</button>
          </div>
        )}

        </>)}

        {!view && <FeedbackSection />}

        <footer style={{ marginTop: 48, fontSize: 12, color: "#5F636A",
          borderTop: "1px solid #E3E0D8", paddingTop: 20, lineHeight: 1.8 }}>
          Data from CMS ownership and change-of-ownership data, state certificate-of-need
          notices, state UCC-1 filings, SEC EDGAR 8-K filings and trade press. News and UCC
          records are extracted automatically and may contain errors.
          {chowFreshness && ` ${chowFreshness}.`}
          <div style={{ display: "flex", gap: "0 16px", flexWrap: "wrap" }}>
            <a href="?view=methodology" onClick={e => { e.preventDefault(); go({ view: "methodology" }); }}
              style={{ ...lnkBlock, fontSize: 13, color: "#23406F" }}>Methodology &amp; corrections</a>
            <a href={import.meta.env.BASE_URL + "feed.xml"} style={{ ...lnkBlock, fontSize: 13, color: "#5F636A" }}>Subscribe (Atom feed)</a>
          </div>
        </footer>
      </div>

      {parade && <HouseParade onDone={endParade} />}

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body { background: #F4F3EF; }
        select option { background: #fff; color: #1F2328; }
        .nh-wrap { max-width: 880px; margin: 0 auto; padding: 32px 20px; }
        @media (max-width: 600px) { .nh-wrap { padding: 20px 16px; } }
        .nh-details { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px 28px; }
        .nh-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
        button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid #23406F; outline-offset: 2px; }
        @keyframes pulse { 0%,100%{opacity:0.5} 50%{opacity:1} }
        @keyframes nh-float { 0%{transform:translateY(0) rotate(0)} 100%{transform:translateY(-115vh) rotate(20deg)} }
        @keyframes nh-pop { 0%{transform:translate(-50%,-50%) scale(0.85)} 100%{transform:translate(-50%,-50%) scale(1)} }
        @media (prefers-reduced-motion: reduce) { .nh-house { display: none; } }
        input::placeholder { color: #6B6F76; }
      `}</style>
    </div>
  );
}

const dl = { fontSize: 11, color: "#5F636A", textTransform: "uppercase",
  letterSpacing: "0.06em", marginBottom: 3, fontWeight: 600 };
const dv = { fontSize: 14, color: "#3F4248", lineHeight: 1.5 };
const lnk = { fontSize: 14, color: "#23406F" };
const lnkBlock = { ...lnk, display: "inline-flex", alignItems: "center", minHeight: 44 };
// Text-styled button that still has a 44px touch target
const linkBtn = { display: "inline-flex", alignItems: "center", minHeight: 44, padding: 0, background: "none",
  border: "none", cursor: "pointer", font: "inherit", textAlign: "left" };
const ctl = { font: "inherit", fontSize: 15, minHeight: 44, background: "#fff", border: "1px solid #C9C5BB", color: "#1F2328",
  borderRadius: 8, padding: "0 12px" };
const fieldLabel = { display: "flex", flexDirection: "column", gap: 4, fontSize: 13, color: "#5F636A" };
const checkLabel = { display: "inline-flex", alignItems: "center", gap: 8, minHeight: 44, fontSize: 14, color: "#3F4248", cursor: "pointer" };
const checkBox = { width: 18, height: 18 };
const btnGhost = { font: "inherit", fontSize: 14, minHeight: 44, background: "#fff", color: "#3F4248",
  border: "1px solid #C9C5BB", borderRadius: 8, padding: "0 14px", cursor: "pointer" };
