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

// UCC deals seeded from an individual owner's name (not yet resolved to a
// real facility via CMS ownership lookup) get facility_names set to the
// same value as operator_names — see main.py's NEW_SIGNAL deal builder.
// That's a structural signal, not a guess: don't display the person's name
// as if it were a facility.
const hasUnresolvedFacilities = (deal) => {
  const facilities = deal.facility_names || [];
  const operators = deal.operator_names || [];
  if (facilities.length === 0 || facilities.length !== operators.length) return false;
  return facilities.every((name, i) => name === operators[i]);
};

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
    lender:           smartCase(deal.lender),
    ucc_debtor:       smartCase(deal.ucc_debtor),
    operator_names:   deal.operator_names?.map(smartCase),
    facility_names:   deal.facility_names?.map(smartCase),
  };
}

const SOURCE = {
  chow:  { label: "Federal Record", tip: "CMS Provider Enrollment — verified federal ownership data" },
  edgar: { label: "SEC Filing",     tip: "SEC EDGAR 8-K — publicly traded company filing" },
  rss:   { label: "News",           tip: "Trade press or news article" },
  ucc:   { label: "UCC Filing",     tip: "State UCC-1 financing statement — early signal, not yet confirmed by CMS" },
  con:   { label: "State CON",      tip: "State certificate-of-need / change-of-ownership notice — filed before the deal closes" },
};

// deal_type is derived in scripts/export_deals.py -- UCC from the filing
// type and lender classifier, news from the extracted amounts.
const DEAL_TYPE = {
  ownership_change: { label: "Ownership change" },
  financing:        { label: "Financing" },
  acquisition_financing: { label: "Acquisition financing" },
  refinancing:      { label: "Refinancing" },
  non_deal_lien:    { label: "Non-deal lien" },
  amendment:        { label: "UCC-3 amendment" },
};

// The two tabs. Everything that isn't an ownership change is a loan or lien
// of some kind, so it goes under Financing.
const TABS = [
  { key: "", label: "Ownership changes" },
  { key: "financing", label: "Financing" },
];
const inTab = (d, tab) => tab === "financing" ? d.deal_type !== "ownership_change" : d.deal_type === "ownership_change";

// Card tags, one per card at most. Text on fill is 4.5:1 or better.
const TAGS = {
  cms:        { label: "CMS confirmed",         bg: "#E4F1EC", fg: "#14543F", tip: "Verified against CMS ownership records" },
  acq:        { label: "Acquisition financing", bg: "#E3EAF6", fg: "#23406F", tip: "UCC-1 filed within 6 months of the facility's owner changing in CMS records — a loan to the buyer around closing" },
  general:    { label: "General financing",     bg: "#ECEAE4", fg: "#4A4A45", tip: "A loan or financing statement on the facility or its operator — often, but not always, tied to a sale" },
  individual: { label: "Individual filing",     bg: "#EEE6F4", fg: "#4B2D6B", tip: "UCC-1 filed against a person who is a CMS owner, not against a facility — often a personal loan" },
};

// NY also searches UCC filings by CMS individual owners. A debtor with no
// company word in it is a person -- same test as pipeline/ucc_category.py's
// is_person, which keeps these filings out of the acquisition/refinancing split.
const ENTITY_WORDS = /\b(LLC|L\.?L\.?C|INC|CORP|CORPORATION|COMPANY|CO|LP|L\.?P|LTD|ASSOCIATES|CENTER|HOME|HEALTH|CARE|NURSING|REHAB\w*|OPERATIONS|OPCO|PROPCO|REALTY|HOLDINGS|TRUST|FUND\w*|SERVICES|MANOR|VILLAGE|HOSPITAL|PAVILION|RESIDENCE|GROUP|PARTNERS|ASSOCIATION|SOCIETY|HDFC|FACILITY|SNF)\b/i;
const isIndividualFiling = (d) =>
  d.source_type === "ucc" && d.ucc_state === "NY" && !!d.ucc_debtor && !ENTITY_WORDS.test(d.ucc_debtor);

function cardTag(d) {
  if (isIndividualFiling(d)) return TAGS.individual;
  if (d.deal_type === "acquisition_financing") return TAGS.acq;
  if (d.deal_type === "financing") return TAGS.general;
  if (d.deal_type === "ownership_change" && d.source_type !== "ucc" && d.stage === "confirmed") return TAGS.cms;
  return null;
}

const resolvedFacilities = (d) => hasUnresolvedFacilities(d) ? [] : (d.facility_names || []);
const lenderKnown = (d) => d.lender && !d.lender.startsWith("Not available");

const US_STATES = [
  "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
  "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
  "VA","WA","WV","WI","WY","DC"
];

// Every name a deal involves, by role -- the company view and entity filter
// match on these case-insensitively.
const ENTITY_ROLES = [
  ["buyer",    d => [d.acquiring_entity]],
  ["seller",   d => [d.seller_entity]],
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

const dealSummary = (d) =>
  d.source_type === "ucc"
    ? `${d.deal_type === "amendment" ? "UCC-3 amendment" : "UCC-1 filed"}${lenderKnown(d) ? ` — ${d.lender}` : ""}`
    : d.source_type === "chow"
    ? `CMS change of ownership${d.acquiring_entity ? ` → ${d.acquiring_entity}` : ""}`
    : `${SOURCE[d.source_type]?.label || "News"}: ${[d.acquiring_entity, d.seller_entity].filter(Boolean).join(" ← ") || d.source_title || "deal reported"}`;

const CSV_COLUMNS = [
  ["date", d => d.acquisition_date || d.created_at?.slice(0, 10)],
  ["source", d => SOURCE[d.source_type]?.label || d.source_type],
  ["deal_type", d => DEAL_TYPE[d.deal_type]?.label || d.deal_type],
  ["tag", d => cardTag(d)?.label],
  ["stage", d => d.stage],
  ["buyer", d => d.acquiring_entity],
  ["seller", d => d.seller_entity],
  ["borrower", d => d.ucc_debtor],
  ["lender", d => d.lender],
  ["lender_category", d => d.lender_category],
  ["states", d => (d.states || []).join(" ")],
  ["facility_count", d => d.facility_count],
  ["facilities", d => resolvedFacilities(d).join("; ")],
  ["ccns", d => (d.ccns || []).filter(Boolean).join(" ")],
  ["deal_value_m", d => d.deal_value_m],
  ["financing_amount_m", d => d.financing_amount_m],
  ["source_url", d => d.source_url],
];

function downloadCsv(deals, name) {
  const cell = v => v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
  const lines = [CSV_COLUMNS.map(([h]) => h).join(","),
    ...deals.map(d => CSV_COLUMNS.map(([, get]) => cell(get(d))).join(","))];
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `${name || "nursing-home-deals"}.csv` });
  a.click();
  URL.revokeObjectURL(url);
}

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

// What the record is about when no party is named: the facility, else
// "2 facilities in MA"
function targetPhrase(d) {
  const facilities = resolvedFacilities(d);
  if (facilities.length) return facilities[0] + (facilities.length > 1 ? ` and ${facilities.length - 1} more` : "");
  if (!d.facility_count) return null;
  const count = d.facility_count === 1 ? "a facility" : `${d.facility_count} facilities`;
  return d.states?.length ? `${count} in ${d.states.join(", ")}` : count;
}
const inStates = (d) => d.states?.length ? ` in ${d.states.join(", ")}` : "";

function Headline({ deal }) {
  if (deal.source_type === "ucc") {
    // A UCC-1 records a financing, not an ownership change -- lead with the
    // facility when enrichment has named it, else the borrower. A filing
    // against a person leads with the person: the homes are only "possible".
    const subject = isIndividualFiling(deal) ? deal.ucc_debtor
      : resolvedFacilities(deal)[0] || deal.operator_names?.[0] || deal.ucc_debtor;
    const kind = deal.deal_type === "amendment" ? "UCC-3 amendment"
      : deal.deal_type === "non_deal_lien" ? "likely non-deal lien"
      : deal.deal_type === "acquisition_financing" ? "acquisition financing"
      : deal.deal_type === "refinancing" ? "refinancing" : "UCC-1 financing";
    return <>{subject}<span style={muted}> — {kind}</span></>;
  }

  // A filing sometimes names the same entity on both sides (a propco selling
  // to its own opco) -- "X acquired from X" says nothing
  const buyer = deal.acquiring_entity;
  const seller = deal.seller_entity && deal.seller_entity !== buyer ? deal.seller_entity : null;
  const target = targetPhrase(deal);

  if (deal.deal_type !== "ownership_change") {
    // Lead with the lender: "CIBC financed 4 facilities in PA", not a run
    // of identical "1 facility in MA — financing" rows
    const refi = deal.deal_type === "refinancing";
    const subject = target || [buyer, seller].filter(Boolean).join(" / ");
    const kind = refi ? "refinancing" : "financing";
    if (lenderKnown(deal)) return subject
      ? <>{deal.lender}<span style={muted}> {refi ? "refinanced" : "financed"} </span>{subject}</>
      : <>{deal.lender}<span style={muted}> — {kind}{inStates(deal)}</span></>;
    return subject ? <>{subject}<span style={muted}> — {kind}</span></>
      : <span style={muted}>Facility {kind}{inStates(deal)}</span>;
  }
  if (buyer && seller) return <>{buyer}<span style={muted}> acquired from </span>{seller}</>;
  if (buyer) return target && target !== buyer
    ? <>{buyer}<span style={muted}> acquired </span>{target}</>
    : <>{buyer}<span style={muted}> — new ownership{inStates(deal)}</span></>;
  return <>
    <span style={{ ...muted, fontStyle: "italic" }}>Buyer not disclosed</span>
    {seller ? <><span style={muted}> acquired from </span>{seller}</>
      : target ? <><span style={muted}> · </span>{target}</>
      : <span style={muted}> · ownership change{inStates(deal)}</span>}
  </>;
}

// One clamped line under the headline; the full text is under Details.
function description(deal) {
  if (isIndividualFiling(deal)) {
    // Never present a multi-home owner's filing as tied to one home
    const homes = resolvedFacilities(deal);
    const ccns = (deal.ccns || []).filter(Boolean);
    if (homes.length === 1) {
      return <>Owner of {ccns.length === 1 && FACILITY_BASE
        ? <a href={`${FACILITY_BASE}/${ccns[0]}`} target="_blank" rel="noreferrer" style={lnk}>{homes[0]} ↗</a>
        : homes[0]}</>;
    }
    if (homes.length > 1) {
      return <>Owner of {homes.slice(0, 3).join(", ")}{homes.length > 3 && ` and ${homes.length - 3} more`}</>;
    }
    return <>Filed against a CMS individual owner; no home identified</>;
  }
  if (deal.summary && ["rss", "edgar", "con"].includes(deal.source_type)) return <>{deal.summary}</>;
  if (deal.source_type === "ucc") {
    if (deal.lender?.startsWith("Not available")) return <>Lender not disclosed (NJ's free search doesn't return it)</>;
    return lenderKnown(deal) ? <>Lender: {deal.lender}{deal.lender_category && ` · ${deal.lender_category}`}</> : null;
  }
  const facilities = resolvedFacilities(deal);
  return facilities.length > 1 ? <>{facilities.join(", ")}</> : null;
}

// One figure on the right: amount, else facility count. deals.json has no
// bed counts.
function keyFigure(deal) {
  if (deal.deal_value_m != null) return { value: fmtM(deal.deal_value_m), label: "price" };
  if (deal.financing_amount_m != null) return { value: fmtM(deal.financing_amount_m), label: "loan" };
  if (isIndividualFiling(deal)) {
    const n = resolvedFacilities(deal).length;
    return n > 1 ? { value: n, label: "possible homes" } : null;
  }
  if (deal.facility_count > 1) return { value: deal.facility_count, label: "facilities" };
  return null;
}

function DealCard({ deal, expanded, onToggle, searchForms, onEntity, history, onShowFacility }) {
  const src = SOURCE[deal.source_type] || SOURCE.rss;
  const date = fmtDate(deal.acquisition_date) || fmtDate(deal.created_at);
  const ccns = deal.ccns?.filter(Boolean) || [];
  const tag = cardTag(deal);
  const figure = keyFigure(deal);
  const detailsId = `details-${deal.id}`;
  const individual = isIndividualFiling(deal);
  const facilities = resolvedFacilities(deal);
  const desc = description(deal);

  return (
    <article style={{ background: "#fff", border: "1px solid #E3E0D8", borderRadius: 10, marginBottom: 10 }}>
      <div style={{ padding: "18px 20px", display: "flex", gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, color: "#5F636A", marginBottom: 6 }}>
            {[date, deal.states?.join(", "), src.label].filter(Boolean).join(" · ")}
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
        <div style={{ flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end",
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
          {deal.summary && (
            <p style={{ fontSize: 14, lineHeight: 1.55, color: "#3F4248", marginBottom: 14 }}>{deal.summary}</p>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 14, marginBottom: 14 }}>
            <div><div style={dl}>Type</div><div style={dv}>{DEAL_TYPE[deal.deal_type]?.label || deal.deal_type}</div></div>
            {deal.acquiring_entity && (
              <div><div style={dl}>Buyer</div><div style={dv}><EntityLink name={deal.acquiring_entity} onPick={onEntity} /></div></div>
            )}
            {deal.seller_entity && (
              <div><div style={dl}>Seller</div><div style={dv}><EntityLink name={deal.seller_entity} onPick={onEntity} /></div></div>
            )}
            {deal.ucc_debtor && (
              <div><div style={dl}>Borrower (debtor){individual && " — individual"}</div><div style={dv}><EntityLink name={deal.ucc_debtor} onPick={onEntity} /></div></div>
            )}
            {lenderKnown(deal) && (
              <div><div style={dl}>Lender</div>
                <div style={dv}><EntityLink name={deal.lender} onPick={onEntity} />{deal.lender_category && <div style={{ fontSize: 12, color: "#5F636A" }}>{deal.lender_category}</div>}</div></div>
            )}
            {deal.operator_names?.length > 0 && (
              <div><div style={dl}>{deal.source_type === "ucc" ? "Linked CMS owner(s)" : "Operator(s)"}</div><div style={dv}>{deal.operator_names.map((n, i) => <span key={n}>{i > 0 && ", "}<EntityLink name={n} onPick={onEntity} /></span>)}</div></div>
            )}
            {facilities.length > 0 && (
              <div><div style={dl}>{individual && facilities.length > 1 ? `${facilities.length} possible homes` : "Facilities"}</div><div style={dv}>{facilities.join(", ")}</div></div>
            )}
            {deal.facility_count && (
              <div><div style={dl}>Facility count</div><div style={dv}>{deal.facility_count}</div></div>
            )}
            {fmtM(deal.deal_value_m) && <div><div style={dl}>Price</div><div style={dv}>{fmtM(deal.deal_value_m)}</div></div>}
            {fmtM(deal.financing_amount_m) && <div><div style={dl}>Loan amount</div><div style={dv}>{fmtM(deal.financing_amount_m)}</div></div>}
            {deal.acquisition_date && (
              <div><div style={dl}>Effective date</div><div style={dv}>{deal.acquisition_date}</div></div>
            )}
            {deal.ucc_also_filed?.length > 0 && (
              <div><div style={dl}>Same loan also filed</div><div style={dv}>{deal.ucc_also_filed.join(", ")}</div></div>
            )}
            <div><div style={dl}>Data source</div><div style={dv}>{src.tip}</div></div>
            {deal.source_type !== "ucc" && deal.ucc_confirmed && (
              <div><div style={dl}>Corroboration</div><div style={dv}>Matches a state UCC-1 financing statement</div></div>
            )}
          </div>

          {ccns.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={dl}>CMS Certification Number(s){individual && ccns.length > 1 && " — possible homes, not a confirmed link"}</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                {ccns.map(ccn => (
                  FACILITY_BASE
                    ? <a key={ccn} href={`${FACILITY_BASE}/${ccn}`} target="_blank" rel="noreferrer"
                        style={{ ...lnkBlock, fontFamily: "monospace", padding: "0 4px" }}>{ccn} ↗</a>
                    : <span key={ccn} style={{ fontSize: 13, fontFamily: "monospace" }}>{ccn}</span>
                ))}
              </div>
            </div>
          )}

          {history?.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={dl}>Facility history — other records for the same CCN{ccns.length > 1 ? "s" : ""}</div>
              <div style={{ marginTop: 4 }}>
                {history.slice(0, 8).map(h => (
                  <div key={h.id} style={{ display: "flex", gap: 10, fontSize: 13, color: "#3F4248", padding: "2px 0" }}>
                    <span style={{ color: "#5F636A", minWidth: 84, fontVariantNumeric: "tabular-nums" }}>
                      {h.acquisition_date || h.created_at?.slice(0, 10)}</span>
                    <span>{dealSummary(h)}</span>
                  </div>
                ))}
                {ccns.length === 1 && (
                  <button type="button" onClick={() => onShowFacility(ccns[0])} style={{ ...btnGhost, fontSize: 13, marginTop: 6 }}>
                    Show all {history.length + 1} records for CCN {ccns[0]}
                  </button>
                )}
              </div>
            </div>
          )}

          {deal.source_url && (
            <div>
              <div style={dl}>Source</div>
              {deal.source_type === 'chow' && deal.source_url.includes('y2hd-n93e')
                ? <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnkBlock}>
                    CMS Nursing Home Ownership Data ↗
                  </a>
                : deal.source_type === 'chow'
                ? <a href="https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership"
                    target="_blank" rel="noreferrer" style={lnkBlock}>
                    CMS SNF Change of Ownership Dataset ↗
                  </a>
                : deal.ucc_filing_number
                ? <UccSource deal={deal} searchForm={searchForms?.[deal.ucc_state]} />
                : <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnkBlock}>
                    {deal.source_title || deal.source_url} ↗
                  </a>
              }
            </div>
          )}
        </div>
      )}
    </article>
  );
}

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

function filterDeals(allDeals, { state, dateFrom, dateTo, search, sourceType, ccnStatus, tab, refi, noLiens, entity }) {
  return allDeals.filter(d => {
    if (tab !== undefined && !inTab(d, tab)) return false;
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
        d.acquiring_entity, d.seller_entity, d.lender, d.ucc_debtor,
        ...(d.operator_names || []),
        ...(d.facility_names || []),
        ...(d.ccns || []),
      ].filter(Boolean).join(" ").toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
}

// Filters live in the URL (shareable/bookmarkable) and can be saved as
// named views in this browser, each remembering when it was last opened so
// it can show how many deals are new since then.
const FILTER_PARAMS = { state: "state", dateFrom: "from", dateTo: "to", search: "q", sourceType: "source", ccnStatus: "ccn",
  tab: "tab", refi: "refi", noLiens: "noliens", entity: "entity" };
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
    f.ccnStatus && (f.ccnStatus === "matched" ? "CCN matched" : "No CCN"),
    (f.dateFrom || f.dateTo) && `${f.dateFrom || "…"} – ${f.dateTo || "…"}`,
  ].filter(Boolean).join(" · ");
}

export default function App() {
  const [allDeals, setAllDeals]   = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading]     = useState(true);
  const [expanded, setExpanded]   = useState(new Set());
  const [searchForms, setSearchForms] = useState({});
  const [freshness, setFreshness] = useState({});
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
  const [entity, setEntity]       = useState(initial.entity);
  const [savedViews, setSavedViews] = useState(loadSavedViews);
  const [offset, setOffset]       = useState(0);
  const [parade, setParade]       = useState(false);
  // Open on load when a link already carries one of the tucked-away filters
  const [moreOpen, setMoreOpen]   = useState(!!(initial.sourceType || initial.ccnStatus || initial.dateFrom || initial.dateTo));
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
      })
      .catch(e => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const currentFilters = { state, dateFrom, dateTo, search, sourceType, ccnStatus, tab, refi, noLiens, entity };
  const filterDeps = [state, dateFrom, dateTo, search, sourceType, ccnStatus, tab, refi, noLiens, entity];

  useEffect(() => {
    const params = new URLSearchParams();
    for (const [k, p] of Object.entries(FILTER_PARAMS)) if (currentFilters[k]) params.set(p, currentFilters[k]);
    const qs = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (qs ? `?${qs}` : ""));
  }, filterDeps);

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
    setTab(f.tab || ""); setRefi(f.refi || ""); setNoLiens(f.noLiens || "");
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

  // Summary line: the whole feed minus refinancings (hidden by default)
  const stats = useMemo(() => {
    if (!allDeals.length) return null;
    const shown = allDeals.filter(d => d.deal_type !== "refinancing");
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

  const deals = filtered.slice(offset, offset + LIMIT);
  const total = filtered.length;

  // Clears filters but stays on the current tab
  function clearAll() {
    setState(""); setDateFrom(""); setDateTo("");
    setSearch(""); setSourceType(""); setCcnStatus(""); setRefi(""); setNoLiens(""); setEntity(""); setOffset(0);
  }

  function toggleExpand(id) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const hasFilters = state || dateFrom || dateTo || search || sourceType || ccnStatus || refi || noLiens || entity;
  const moreCount = [sourceType, ccnStatus, dateFrom || dateTo].filter(Boolean).length;

  // Opening a company view replaces the other filters -- it's a profile,
  // not a refinement of whatever list you were looking at.
  function pickEntity(name) {
    clearAll();
    setEntity(name);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showFacility(ccn) {
    clearAll();
    setSearch(ccn);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
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
            trade press.
          </p>
          {stats && (
            <p style={{ fontSize: 13, color: "#5F636A", marginTop: 10 }}>
              {stats.total.toLocaleString()} records · {stats.last90.toLocaleString()} in the last 90 days
              {freshness.pipeline_ran_at && <> · <span title={chowFreshness || undefined}>sources checked {fmtStamp(freshness.pipeline_ran_at)}</span></>}
            </p>
          )}
        </header>

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
              <button type="button" onClick={() => downloadCsv(filtered, currentName.replace(/[^\w-]+/g, "-").replace(/^-|-$/g, ""))}
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
              onShowFacility={showFacility} />
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

        <footer style={{ marginTop: 48, fontSize: 12, color: "#5F636A",
          borderTop: "1px solid #E3E0D8", paddingTop: 20, lineHeight: 1.8 }}>
          Data sourced from CMS ownership and change-of-ownership data, state certificate-of-need
          notices, state UCC-1 filings, SEC EDGAR 8-K filings and trade press.
          {chowFreshness && ` ${chowFreshness}.`}{" "}
          <a href={import.meta.env.BASE_URL + "feed.xml"} style={{ ...lnkBlock, fontSize: 12, color: "#5F636A", verticalAlign: "middle" }}>Subscribe (Atom feed)</a>
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
