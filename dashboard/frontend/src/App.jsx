import { useState, useEffect, useMemo, useCallback } from "react";

const FACILITY_BASE = import.meta.env.VITE_FACILITY_BASE_URL || "https://www.medicare.gov/care-compare/details/nursing-home";
const DATA_URL = import.meta.env.BASE_URL + "deals.json";

// Calendar-date comparison, not elapsed hours — a deal from yesterday
// afternoon shouldn't still read "Today" this morning just because it's
// under 24h old.
const localDateStr = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

// A bare "YYYY-MM-DD" is a calendar date. new Date() reads it as UTC
// midnight, which in US time zones is the previous evening, so every
// acquisition date used to display one day early.
const parseDay = (v) => {
  const m = typeof v === "string" && /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(v);
};

const fmtDate = (v) => {
  if (!v) return null;
  const d = parseDay(v);
  const now = new Date();
  const dStr = localDateStr(d);
  const todayStr = localDateStr(now);
  if (dStr === todayStr) return "Today";

  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (dStr === localDateStr(yesterday)) return "Yesterday";

  const diffDays = Math.round((new Date(todayStr) - new Date(dStr)) / (1000 * 60 * 60 * 24));
  if (diffDays < 0) {
    const inDays = -diffDays;
    if (inDays === 1) return "Tomorrow";
    if (inDays < 7) return `In ${inDays} days`;
    return `Expected ${d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`;
  }
  if (diffDays < 7) return `${diffDays} days ago`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
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
  chow:  { label: "CMS record",     name: "CMS change-of-ownership data", tip: "CMS Provider Enrollment and ownership data — federal records of a completed change of ownership" },
  edgar: { label: "SEC Filing",     name: "SEC EDGAR 8-K", tip: "SEC EDGAR 8-K — a publicly traded company's own filing" },
  rss:   { label: "News",           name: "News report", tip: "Trade press or news article" },
  ucc:   { label: "UCC Filing",     name: "State UCC-1 filing", tip: "State UCC-1 financing statement — a record of a loan, not of a sale" },
  con:   { label: "State filing",   name: "State certificate-of-need / change-of-ownership filing", tip: "State certificate-of-need or change-of-ownership notice — filed before the deal closes" },
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
// Leases and control changes aren't sales: out of the deal counts
const countsAsDeal = (d) => !["control_change", "lease", "refinancing", "non_deal_lien", "amendment"].includes(d.deal_type);

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
      style={{ ...linkBtn, color: "#1F2328", textDecoration: "underline dotted", textUnderlineOffset: 3 }}>
      {name}
    </button>
  );
}

const headlineText = (d) => (d.headline || []).map(([t]) => t).join("");

const buyerText = (d) =>
  (d.buyer && d.buyer_disclosure === "cms" ? `${d.buyer} (named in CMS ownership data, not in the source)` : d.buyer)
  || (d.buyer_disclosure === "described" ? `Not named; described as ${d.buyer_description}`
    : d.buyer_disclosure === "undisclosed" ? "Not disclosed" : d.buyer_disclosure === "not_named" ? "Not named in the source" : null);

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
        {deal.source_title || deal.source_url} ↗
      </a>
      {deal.ucc_state === "PA" && (
        <button type="button" onClick={copy} style={{ ...btnGhost, fontSize: 12, padding: "0 12px" }}>
          {copied ? "Copied" : "Copy filing #"}
        </button>
      )}
    </span>
  );
}

const muted = { color: "#5F636A", fontWeight: 400 };

// Names in headlines arrive as the source wrote them; only shouting (ALL
// CAPS from CMS and UCC portals) is recased, never a lower-case description
const shoutCase = (t) => /[a-z]/.test(t) ? t : smartCase(t);

// The headline arrives as [text, style] segments (pipeline/deal_labels.py)
function Headline({ deal }) {
  return (deal.headline || []).map(([text, style], i) =>
    style === "muted" ? <span key={i} style={muted}>{text}</span>
    : style === "undisclosed" ? <span key={i} style={{ ...muted, fontStyle: "italic" }}>{text}</span>
    : <span key={i}>{shoutCase(text)}</span>);
}

// Never present a multi-home owner's filing as tied to one home
function OwnerOf({ deal }) {
  const homes = deal.possible_homes || [];
  if (homes.length === 1) {
    const url = homeUrl(homes[0].ccn);
    return <>Owner of {url ? <a href={url} target="_blank" rel="noreferrer" style={lnk}>{smartCase(homes[0].name)} ↗</a> : smartCase(homes[0].name)}</>;
  }
  // "Owner of X, Y, Z +8 more", or the unmatched line, from the export
  return <>{shoutCase(deal.owner_line || "")}</>;
}

// One clamped line under the headline; the full text is under Details.
function description(deal) {
  if (deal.individual_filing) return <OwnerOf deal={deal} />;
  if (deal.summary && ["rss", "edgar", "con"].includes(deal.source_type)) return <>{deal.summary}</>;
  if (deal.source_type === "ucc") {
    if (deal.lender?.startsWith("Not available")) return <>Lender not disclosed (NJ's free search doesn't return it)</>;
    return lenderKnown(deal) ? <>Lender: {deal.lender}{deal.lender_category && ` · ${deal.lender_category}`}</> : null;
  }
  const facilities = deal.facility_names || [];
  return facilities.length > 1 ? <>{facilities.join(", ")}</> : null;
}

// One figure on the right: amount, else facility count. deals.json has no
// bed counts. Control changes carry no price.
function keyFigure(deal) {
  if (deal.individual_filing) {
    const n = deal.possible_homes?.length || 0;
    return n > 1 ? { value: n, label: "possible homes" } : null;
  }
  if (deal.deal_value_m != null && deal.deal_type !== "control_change") return { value: fmtM(deal.deal_value_m), label: "price" };
  if (deal.financing_amount_m != null) return { value: fmtM(deal.financing_amount_m), label: "loan" };
  if (deal.facility_count > 1) return { value: deal.facility_count, label: "facilities" };
  if (deal.beds) return { value: deal.beds.toLocaleString(), label: "beds" };
  return null;
}

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
  return <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnkBlock}>{deal.source_title || deal.source_url} ↗</a>;
}

// A facility's page on this site (?ccn=), named when CMS details are known
function FacilityLink({ ccn, facilities, name }) {
  const f = facilities?.[ccn];
  const label = smartCase(f?.name || name || `CCN ${ccn}`);
  return (
    <a href={`?ccn=${ccn}`} style={lnkBlock}>
      {label}{f?.city ? `, ${smartCase(f.city)}` : ""} <span style={{ color: "#5F636A", marginLeft: 4 }}>({ccn})</span>
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
    text: s.headline || s.source_title, url: s.source_url, record: s.merged || s.id === deal.id ? null : s.id,
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

function DealCard({ deal, expanded, onToggle, searchForms, onEntity, history, facilities }) {
  const src = SOURCE[deal.source_type] || SOURCE.rss;
  const date = fmtDate(deal.acquisition_date) || fmtDate(deal.created_at);
  const ccns = deal.ccns?.filter(Boolean) || [];
  const tag = cardTag(deal);
  const figure = keyFigure(deal);
  const detailsId = `details-${deal.id}`;
  const individual = deal.individual_filing;
  const desc = description(deal);
  const buyer = buyerText(deal);
  const statusColor = deal.status === "confirmed" ? "#14543F" : deal.status === "expected" ? "#6B4A0B"
    : deal.status === "unconfirmed" ? "#8A3B12" : "#3F4248";

  return (
    <article id={`record-${deal.id}`} style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, marginBottom: 10 }}>
      <div className="nh-card-row" style={{ padding: "18px 20px", display: "flex", gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, color: "#5F636A", marginBottom: 6 }}>
            {[date, deal.states?.join(", "), src.label,
              deal.status === "expected" && !date?.startsWith("Expected") && "Pending",
              deal.status === "unconfirmed" && "Unconfirmed",
              deal.sources?.length > 1 && `${deal.sources.length} sources`].filter(Boolean).join(" · ")}
          </div>
          {tag && (
            <div style={{ marginBottom: 6 }}>
              <span title={tag.tip} style={{ display: "inline-block", fontSize: 12, fontWeight: 600,
                color: tag.fg, background: tag.bg, padding: "2px 8px", borderRadius: 4 }}>{tag.label}</span>
            </div>
          )}
          <h2 style={{ fontSize: 17, fontWeight: 600, lineHeight: 1.35, color: "#1F2328", margin: 0, overflowWrap: "anywhere" }}>
            <Headline deal={deal} />
          </h2>
          {desc && (
            <div className="nh-clamp" style={{ fontSize: 14, color: "#3F4248", marginTop: 6 }}>{desc}</div>
          )}
        </div>
        <div className="nh-card-side" style={{ flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end",
          justifyContent: "space-between", textAlign: "right", maxWidth: 120 }}>
          {figure ? (
            <div>
              <div style={{ fontSize: 17, fontWeight: 600, color: "#1F2328", fontVariantNumeric: "tabular-nums" }}>{figure.value}</div>
              <div style={{ fontSize: 12, color: "#5F636A" }}>{figure.label}</div>
            </div>
          ) : <span />}
          <button type="button" onClick={() => onToggle(deal.id)} aria-expanded={expanded} aria-controls={detailsId}
            style={{ ...linkBtn, color: "#5F636A", fontSize: 13, textDecoration: "underline", textUnderlineOffset: 3 }}>
            {expanded ? "Hide details" : "Details"}
          </button>
        </div>
      </div>

      {expanded && (
        <div id={detailsId} style={{ borderTop: "1px solid #E3E0D8", padding: "16px 20px", background: "#FAFAF8", borderRadius: "0 0 10px 10px" }}>
          <p style={{ fontSize: 14, fontWeight: 600, color: statusColor, marginBottom: 10 }}>{deal.status_detail}</p>
          {deal.summary && (
            <p style={{ fontSize: 14, lineHeight: 1.55, color: "#3F4248", marginBottom: 14 }}>{deal.summary}</p>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 14, marginBottom: 14 }}>
            <Field label="Type">{DEAL_TYPE[deal.deal_type]?.label || deal.deal_type}</Field>
            {buyer && <Field label="Buyer">{deal.buyer ? <EntityLink name={deal.buyer} onPick={onEntity} /> : buyer}</Field>}
            {deal.seller && <Field label="Seller"><EntityLink name={deal.seller} onPick={onEntity} /></Field>}
            {deal.ucc_debtor && (
              <Field label={`Borrower (debtor)${individual ? " — individual" : ""}`}><EntityLink name={deal.ucc_debtor} onPick={onEntity} /></Field>
            )}
            {lenderKnown(deal) && (
              <Field label="Lender"><EntityLink name={deal.lender} onPick={onEntity} />{deal.lender_category && <div style={{ fontSize: 12, color: "#5F636A" }}>{deal.lender_category}</div>}</Field>
            )}
            {!individual && deal.operator_names?.length > 0 && (
              <Field label={deal.source_type === "ucc" ? "Linked CMS owner(s)" : "Operator(s)"}>
                <NameList names={deal.operator_names} onPick={onEntity} />
              </Field>
            )}
            {!individual && deal.facility_names?.length > 0 && <Field label="Facilities">{deal.facility_names.join(", ")}</Field>}
            {deal.facility_count && <Field label="Facility count">{deal.facility_count}</Field>}
            {deal.beds && <Field label="Certified beds (CMS)">{deal.beds.toLocaleString()}</Field>}
            {deal.deal_type !== "control_change" && fmtM(deal.deal_value_m) && <Field label="Price">{fmtM(deal.deal_value_m)}</Field>}
            {fmtM(deal.financing_amount_m) && <Field label="Loan amount">{fmtM(deal.financing_amount_m)}</Field>}
            {deal.acquisition_date && <Field label={deal.status === "expected" ? "Expected / filing date" : "Effective date"}>{deal.acquisition_date}</Field>}
            <Field label="First found by this tracker">{deal.found_date || "—"}</Field>
            <Field label="Facility type">{FACILITY_TYPE[deal.facility_type] || deal.facility_type}</Field>
            {deal.ucc_also_filed?.length > 0 && <Field label="Same loan also filed">{deal.ucc_also_filed.join(", ")}</Field>}
            {deal.source_type !== "ucc" && deal.ucc_confirmed && (
              <Field label="Corroboration">Matches a state UCC-1 financing statement</Field>
            )}
          </div>

          {individual && (
            <div style={{ marginBottom: 12 }}>
              <div style={dl}>{deal.match_type === "multi_home" ? `${deal.possible_homes.length} possible homes`
                : deal.match_type === "single_home" ? "Home owned by this person" : "Possible homes"}</div>
              <div style={{ ...dv, marginBottom: 4 }}>
                {deal.match_type === "multi_home"
                  ? `This person owns or controls each of these ${deal.ucc_state} homes in CMS records. The filing doesn't say which, if any, it concerns.`
                  : deal.match_type === "single_home"
                  ? `The only ${deal.ucc_state} home this person owns or controls in CMS records. The filing itself doesn't name a home.`
                  : "Filed against an individual; no matching home in CMS ownership records."}
              </div>
              {(deal.possible_homes || []).map(h => (
                <div key={h.ccn} style={dv}><FacilityLink ccn={h.ccn} facilities={facilities} name={h.name} /></div>
              ))}
            </div>
          )}

          {!individual && ccns.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={dl}>Facilities in CMS records{history?.length > 0 ? ` · ${history.length} other record${history.length > 1 ? "s" : ""} in this tracker` : ""}</div>
              <div style={{ display: "flex", gap: "0 14px", flexWrap: "wrap", marginTop: 2 }}>
                {ccns.slice(0, 12).map(ccn => <FacilityLink key={ccn} ccn={ccn} facilities={facilities} />)}
                {ccns.length > 12 && <span style={{ ...dv, alignSelf: "center" }}>+{ccns.length - 12} more</span>}
              </div>
            </div>
          )}

          {(deal.sources?.length > 1 || deal.cms_event) && <Timeline deal={deal} />}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
            <div>
              <div style={dl}>Source · {deal.source_name || src.name}</div>
              <SourceLink deal={deal} searchForms={searchForms} />
            </div>
            <div>
              <div style={dl}>Cite this record</div>
              <CopyLink id={deal.id} />
            </div>
          </div>
        </div>
      )}
    </article>
  );
}

const CORRECTIONS_URL = "https://github.com/YutingFan1209/nursing-home-live-feed/issues";

function TagChip({ tag }) {
  return <span style={{ display: "inline-block", fontSize: 12, fontWeight: 600, color: tag.fg, background: tag.bg,
    padding: "2px 8px", borderRadius: 4, whiteSpace: "nowrap" }}>{tag.label}</span>;
}

function TagLegend() {
  return (
    <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "8px 12px", alignItems: "baseline", margin: 0 }}>
      {Object.values(TAGS).map(t => (
        <div key={t.label} style={{ display: "contents" }}>
          <dt><TagChip tag={t} /></dt>
          <dd style={{ margin: 0, fontSize: 15, lineHeight: 1.5, color: "#3F4248" }}>{t.tip}.</dd>
        </div>
      ))}
    </dl>
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
          become Confirmed once CMS catches up); or, for financing, a UCC-1 statement on file with the state
          (a record of the loan, not of a sale).</li>
        <li style={li}><strong>Expected:</strong> filed with the state or reported before closing. Not yet completed as far
          as we know; the expected date is shown when the source gives one.</li>
        <li style={li}><strong>Unconfirmed:</strong> filed or reported as pending more than six months ago, and still not
          confirmed. The deal may not have closed, or CMS may not have caught up.</li>
        <li style={li}><strong>Reported:</strong> a news report or company filing that CMS data doesn't confirm yet.</li>
      </ul>

      <h3 style={h}>Tags</h3>
      <p style={p}>A card carries at most one tag, and only for these exceptions:</p>
      <TagLegend />

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
        type or date. Each record links to its source; check it before citing. To report an error, open an issue at{" "}
        <a href={CORRECTIONS_URL} target="_blank" rel="noreferrer" style={lnk}>{CORRECTIONS_URL.replace("https://", "")}</a>{" "}
        with the record's permalink.</p>

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
    const effectiveDate = d.acquisition_date || d.created_at;
    if (dateFrom && effectiveDate && effectiveDate < dateFrom) return false;
    if (dateTo && effectiveDate && effectiveDate > dateTo + "T99") return false;
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
        const deals = (data.deals || data || []).map(withDisplayCase).sort((a, b) => {
          const da = a.acquisition_date || a.created_at || "";
          const db = b.acquisition_date || b.created_at || "";
          return db.localeCompare(da);
        });
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
  const tabCounts = useMemo(() => Object.fromEntries(TABS.map(t => [t.key, filteredAnyTab.filter(d => inTab(d, t.key)).length])),
    [filteredAnyTab]);

  // Summary line: deals in the default view -- skilled-nursing related, and
  // not leases, control changes, refinancings or liens
  const stats = useMemo(() => {
    if (!allDeals.length) return null;
    const shown = allDeals.filter(d => !d.grouped_into && d.facility_type === "skilled_nursing" && countsAsDeal(d));
    const cutoff90 = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000);
    return { total: shown.length, last90: shown.filter(d => d.acquisition_date && parseDay(d.acquisition_date) >= cutoff90).length };
  }, [allDeals]);

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
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" }}>

      <div className="nh-wrap">
        <header style={{ marginBottom: 20 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, letterSpacing: "-0.02em", marginBottom: 6 }}>
            Nursing Home Ownership Changes
          </h1>
          <p style={{ fontSize: 14, color: "#5F636A", lineHeight: 1.6, maxWidth: 560 }}>
            Skilled nursing facility ownership changes from CMS ownership records,
            state pre-closing filings, UCC-1 financing statements, SEC filings and
            trade press.{" "}
            <a href="?view=methodology" onClick={e => { e.preventDefault(); go({ view: "methodology" }); }}
              style={{ color: "#23406F" }}>How this works</a>
          </p>
          {stats && (
            <p style={{ fontSize: 13, color: "#5F636A", marginTop: 10 }}>
              <span title="Sales and financings of skilled nursing facilities; leases, control changes, refinancings and non-deal liens aren't counted">
                {stats.total.toLocaleString()} deals</span> · {stats.last90.toLocaleString()} in the last 90 days
              {freshness.pipeline_ran_at && <> · <span title={chowFreshness || undefined}>sources checked {fmtStamp(freshness.pipeline_ran_at)}</span></>}
            </p>
          )}
          {newSinceVisit > 0 && !since && !view && !focusId && !facilityCcn && (
            <p style={{ fontSize: 14, color: "#1F2328", marginTop: 6 }}>
              <strong>{newSinceVisit.toLocaleString()} new</strong> since your last visit ({fmtDay(lastVisit)}) ·{" "}
              <button type="button" onClick={() => { setSince(lastVisit); setOffset(0); }}
                style={{ ...linkBtn, ...lnk, textDecoration: "underline", minHeight: 0, verticalAlign: "baseline" }}>Show them</button>
            </p>
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
                  facilities={facilities} />
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

        <div role="tablist" aria-label="Record type" style={{ display: "flex", gap: 4, borderBottom: "1px solid #E3E0D8", marginBottom: 12 }}>
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
              facilities={facilities} />
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

        <footer style={{ marginTop: 48, fontSize: 12, color: "#5F636A",
          borderTop: "1px solid #E3E0D8", paddingTop: 20, lineHeight: 1.8 }}>
          <div style={{ display: "flex", gap: "6px 10px", flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
            <span>Tags:</span>
            {Object.values(TAGS).map(t => <span key={t.label} title={t.tip}><TagChip tag={t} /></span>)}
          </div>
          Data from CMS ownership and change-of-ownership data, state certificate-of-need
          notices, state UCC-1 filings, SEC EDGAR 8-K filings and trade press. News and UCC
          records are extracted automatically and may contain errors.
          {chowFreshness && ` ${chowFreshness}.`}
          <div style={{ display: "flex", gap: "0 16px", flexWrap: "wrap" }}>
            <a href="?view=methodology" onClick={e => { e.preventDefault(); go({ view: "methodology" }); }}
              style={{ ...lnkBlock, fontSize: 13, color: "#23406F" }}>Methodology, tags &amp; corrections</a>
            <a href={import.meta.env.BASE_URL + "feed.xml"} style={{ ...lnkBlock, fontSize: 13, color: "#5F636A" }}>Subscribe (Atom feed)</a>
          </div>
        </footer>
      </div>

      {parade && <HouseParade onDone={endParade} />}

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body { background: #F4F3EF; }
        select option { background: #fff; color: #1F2328; }
        .nh-wrap { max-width: 760px; margin: 0 auto; padding: 32px 20px; }
        @media (max-width: 600px) { .nh-wrap { padding: 20px 16px; } }
        .nh-clamp { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        @media (max-width: 520px) {
          /* the figure and Details sit under the text, so the summary gets the width */
          .nh-card-row { flex-direction: column; gap: 4px !important; }
          .nh-card-side { flex-direction: row !important; align-items: center !important; max-width: none !important;
            justify-content: space-between !important; text-align: left !important; }
          .nh-card-side > div { display: flex; gap: 6px; align-items: baseline; }
        }
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
