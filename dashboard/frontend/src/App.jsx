import { useState, useEffect, useMemo } from "react";

const FACILITY_BASE = import.meta.env.VITE_FACILITY_BASE_URL || "https://www.medicare.gov/care-compare/details/nursing-home";
console.log('VITE_FACILITY_BASE_URL:', import.meta.env.VITE_FACILITY_BASE_URL)
const DATA_URL = import.meta.env.BASE_URL + "deals.json";

// Calendar-date comparison, not elapsed hours — a deal from yesterday
// afternoon shouldn't still read "Today" this morning just because it's
// under 24h old.
const localDateStr = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

const fmtDate = (v) => {
  if (!v) return null;
  const d = new Date(v);
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
const isNew = (c) => c && (Date.now() - new Date(c).getTime()) < 48 * 60 * 60 * 1000;

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
const BRAND_CASE = { JPMORGAN: "JPMorgan", MIDCAP: "MidCap", HARBORCHASE: "HarborChase", LENDINGTREE: "LendingTree" };
const KEEP_LOWER = new Set(["of", "and", "at", "the", "for", "in", "on", "by", "to", "as", "a", "an", "de", "du"]);

function smartCase(text) {
  if (!text || typeof text !== "string") return text;
  const letters = text.replace(/[^A-Za-z]/g, "");
  if (!letters || (/[a-z]/.test(letters) && /[A-Z]/.test(letters))) return text;  // already mixed case
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
  chow:  { label: "Federal Record", dot: "#16a34a", tip: "CMS Provider Enrollment — verified federal ownership data" },
  edgar: { label: "SEC Filing",     dot: "#2563eb", tip: "SEC EDGAR 8-K — publicly traded company filing" },
  rss:   { label: "News",           dot: "#d97706", tip: "Trade press or news article" },
  ucc:   { label: "UCC Filing",     dot: "#7c3aed", tip: "State UCC-1 financing statement — early signal, not yet confirmed by CMS" },
};

// deal_type is derived in scripts/export_deals.py -- UCC from the filing
// type and lender classifier, news from the extracted amounts.
const DEAL_TYPE = {
  ownership_change: { label: "Ownership change", tip: "A change of owner or operator" },
  financing:        { label: "Financing", tip: "A loan or financing statement on the facility or its operator — often, but not always, tied to a sale" },
  non_deal_lien:   { label: "Non-deal lien", tip: "Lien from an equipment vendor, registered agent or small-business lender — rarely deal-related" },
  amendment:        { label: "UCC-3 amendment", tip: "An amendment (assignment, continuation or termination) to an earlier UCC-1 filing" },
};

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
  ["lender",   d => [d.lender?.startsWith("Not available") ? null : d.lender]],
  ["operator", d => d.operator_names || []],
];
const normName = (n) => (n || "").trim().toLowerCase();
const dealRoles = (d, entity) => {
  const key = normName(entity);
  return ENTITY_ROLES.filter(([, get]) => get(d).some(n => n && normName(n) === key)).map(([role]) => role);
};

function EntityLink({ name, onPick, style }) {
  if (!name || !onPick) return <strong style={style}>{name}</strong>;
  return (
    <strong style={{ cursor: "pointer", textDecoration: "underline dotted", textUnderlineOffset: 3, ...style }}
      title={`All deals involving ${name}`}
      onClick={e => { e.stopPropagation(); onPick(name); }}>{name}</strong>
  );
}

const dealSummary = (d) =>
  d.source_type === "ucc"
    ? `${d.deal_type === "amendment" ? "UCC-3 amendment" : "UCC-1 filed"}${d.lender && !d.lender.startsWith("Not available") ? ` — ${d.lender}` : ""}`
    : d.source_type === "chow"
    ? `CMS change of ownership${d.acquiring_entity ? ` → ${d.acquiring_entity}` : ""}`
    : `${SOURCE[d.source_type]?.label || "News"}: ${[d.acquiring_entity, d.seller_entity].filter(Boolean).join(" ← ") || d.source_title || "deal reported"}`;

const CSV_COLUMNS = [
  ["date", d => d.acquisition_date || d.created_at?.slice(0, 10)],
  ["source", d => SOURCE[d.source_type]?.label || d.source_type],
  ["deal_type", d => DEAL_TYPE[d.deal_type]?.label || d.deal_type],
  ["stage", d => d.stage],
  ["buyer", d => d.acquiring_entity],
  ["seller", d => d.seller_entity],
  ["borrower", d => d.ucc_debtor],
  ["lender", d => d.lender],
  ["lender_category", d => d.lender_category],
  ["states", d => (d.states || []).join(" ")],
  ["facility_count", d => d.facility_count],
  ["facilities", d => hasUnresolvedFacilities(d) ? "" : (d.facility_names || []).join("; ")],
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

function Tooltip({ text, children }) {
  const [show, setShow] = useState(false);
  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center" }}
      onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
      {children}
      {show && (
        <span style={{ position: "absolute", bottom: "100%", left: 0, marginBottom: 6,
          background: "#1f2937", color: "#fff", fontSize: 11, padding: "5px 9px",
          borderRadius: 5, whiteSpace: "nowrap", zIndex: 10, pointerEvents: "none",
          boxShadow: "0 2px 8px rgba(0,0,0,0.2)" }}>
          {text}
        </span>
      )}
    </span>
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
        <button type="submit" style={{ ...lnk, background: "none", border: "none", padding: 0, cursor: "pointer" }}>
          {label} ↗
        </button>
      </form>
    );
  }

  const copy = () => navigator.clipboard?.writeText(deal.ucc_filing_number)
    .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });

  return (
    <span>
      <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnk}>
        {deal.source_title || deal.source_url} ↗
      </a>
      {deal.ucc_state === "PA" && (
        <button onClick={copy} style={{ marginLeft: 8, fontSize: 11, color: "#374151", background: "#f3f4f6",
          border: "1px solid #e5e7eb", borderRadius: 4, padding: "2px 7px", cursor: "pointer" }}>
          {copied ? "Copied" : "Copy filing #"}
        </button>
      )}
    </span>
  );
}

function DealCard({ deal, expanded, onToggle, searchForms, onEntity, history, onShowFacility }) {
  const src = SOURCE[deal.source_type] || SOURCE.rss;
  const date = fmtDate(deal.acquisition_date) || fmtDate(deal.created_at);
  const ccns = deal.ccns?.filter(Boolean) || [];
  const fresh = deal.source_type !== 'ucc' && isNew(deal.created_at);

  // A UCC-1 records a financing, not an ownership change -- lead with the
  // facility when enrichment has named it, else the borrower (debtor).
  const uccSubject = deal.source_type === 'ucc'
    ? ((deal.facility_names?.length > 0 && !hasUnresolvedFacilities(deal))
        ? deal.facility_names[0] : deal.operator_names?.[0])
    : null;

  const headline = uccSubject
    ? <><strong>{uccSubject}</strong><span style={{color:"#6b7280"}}> — {
        deal.deal_type === "amendment" ? "UCC-3 amendment"
        : deal.deal_type === "non_deal_lien" ? "likely non-deal lien" : "UCC-1 financing"}</span></>
    : deal.acquiring_entity && deal.seller_entity
    ? <><EntityLink name={deal.acquiring_entity} onPick={onEntity} /><span style={{color:"#6b7280"}}> acquired from </span><EntityLink name={deal.seller_entity} onPick={onEntity} /></>
    : deal.acquiring_entity
    ? <><EntityLink name={deal.acquiring_entity} onPick={onEntity} /><span style={{color:"#6b7280"}}> — new ownership</span></>
    : deal.seller_entity
    ? <><EntityLink name={deal.seller_entity} onPick={onEntity} /><span style={{color:"#6b7280"}}> changes ownership</span></>
    : deal.facility_names?.length > 0 && !hasUnresolvedFacilities(deal)
    ? <><strong>{deal.facility_names[0]}</strong><span style={{color:"#6b7280"}}> — ownership change</span></>
    : deal.facility_count || deal.states?.length
    // anonymized trade-press items ("a Pennsylvania-based regional
    // operator") -- say what is known rather than nothing
    ? <><strong>{[
          deal.facility_count && `${deal.facility_count} ${deal.facility_count === 1 ? "facility" : "facilities"}`,
          deal.states?.length && `${deal.facility_count ? "in " : ""}${deal.states.join(", ")}`,
        ].filter(Boolean).join(" ")}</strong>
        <span style={{color:"#6b7280"}}>{deal.lender ? " — financing" : " — ownership change"}</span></>
    : <span style={{color:"#6b7280"}}>Ownership change recorded</span>;

  return (
    <div style={{
      background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10,
      marginBottom: 8, overflow: "hidden",
      boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
    }}>
      <div onClick={() => onToggle(deal.id)}
        style={{ padding: "16px 20px", cursor: "pointer", borderLeft: `3px solid ${src.dot}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
          <Tooltip text={src.tip}>
            <span style={{ display: "flex", alignItems: "center", gap: 5,
              fontSize: 11, fontWeight: 600, color: src.dot, letterSpacing: "0.03em" }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%",
                background: src.dot, display: "inline-block" }} />
              {src.label}
            </span>
          </Tooltip>
          <span style={{ color: "#e5e7eb" }}>·</span>
          {deal.states?.map(s => (
            <span key={s} style={{ fontSize: 11, fontWeight: 600, color: "#374151",
              background: "#f3f4f6", padding: "1px 7px", borderRadius: 20 }}>{s}</span>
          ))}
          {deal.deal_type && deal.deal_type !== (deal.source_type === "ucc" ? "financing" : "ownership_change") && (
            <span title={DEAL_TYPE[deal.deal_type]?.tip} style={{ fontSize: 11, fontWeight: 600,
              color: deal.deal_type === "non_deal_lien" ? "#9ca3af" : "#b45309",
              background: deal.deal_type === "non_deal_lien" ? "#f3f4f6" : "#fffbeb",
              padding: "1px 7px", borderRadius: 20 }}>{DEAL_TYPE[deal.deal_type]?.label}</span>
          )}
          {fresh && (
            <span style={{ fontSize: 10, fontWeight: 700, color: "#fff",
              background: "#16a34a", padding: "2px 7px", borderRadius: 20,
              letterSpacing: "0.05em" }}>NEW</span>
          )}
          <span style={{ marginLeft: "auto", fontSize: 12, color: "#9ca3af" }}>{date}</span>
        </div>
        <div style={{ fontSize: 15, lineHeight: 1.5, color: "#111827", marginBottom: 8 }}>
          {headline}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          {deal.facility_count && (
            <span style={{ fontSize: 13, color: "#6b7280" }}>
              {deal.facility_count} {deal.facility_count === 1 ? "facility" : "facilities"}
            </span>
          )}
          {fmtM(deal.deal_value_m) && (
            <span style={{ fontSize: 13, fontWeight: 700, color: "#7c3aed" }}>
              {fmtM(deal.deal_value_m)}
            </span>
          )}
          {fmtM(deal.financing_amount_m) && (
            <span style={{ fontSize: 13, fontWeight: 700, color: "#7c3aed" }}
              title="Loan / financing amount, not a sale price">
              {fmtM(deal.financing_amount_m)} financing
            </span>
          )}
          {deal.lender && (
            // main.py stores an explanatory placeholder for NJ, whose free
            // UCC search never returns the secured party
            deal.lender.startsWith("Not available")
              ? <span style={{ fontSize: 12, color: "#9ca3af" }}
                  title="New Jersey's free UCC search doesn't return the secured party (lender)">
                  Lender not disclosed (NJ)
                </span>
              : <span style={{ fontSize: 12, color: "#9ca3af" }}>
                  Financed by <EntityLink name={deal.lender} onPick={onEntity} style={{ fontWeight: 400 }} />
                  {deal.lender_category && <span style={{ color: "#c4b5fd" }}> · {deal.lender_category}</span>}
                </span>
          )}
          {deal.source_type === 'ucc' ? (
            <span style={{ fontSize: 11, fontWeight: 600, color: "#7c3aed",
              background: "#f5f3ff", padding: "2px 7px", borderRadius: 20 }}
              title="Early signal from state UCC-1 filing, not yet confirmed by CMS">
              UCC Signal
            </span>
          ) : deal.ucc_confirmed ? (
            <span style={{ fontSize: 11, fontWeight: 600, color: "#7c3aed",
              background: "#f5f3ff", padding: "2px 7px", borderRadius: 20 }}
              title="Corroborated by a state UCC-1 financing statement">
              UCC confirmed
            </span>
          ) : deal.stage === 'confirmed' ? (
            <span style={{ fontSize: 11, fontWeight: 600, color: "#15803d",
              background: "#f0fdf4", padding: "2px 7px", borderRadius: 20 }}
              title="Verified against CMS ownership records">
              CMS Confirmed
            </span>
          ) : null}
          {deal.also_reported_count > 0 && (
            <span style={{ fontSize: 11, color: "#9ca3af", background: "#f3f4f6",
              padding: "2px 8px", borderRadius: 20 }}>
              +{deal.also_reported_count} other source{deal.also_reported_count > 1 ? "s" : ""}
            </span>
          )}
          <span style={{ marginLeft: "auto", fontSize: 12, color: "#9ca3af" }}>
            {expanded ? "↑ collapse" : "↓ details"}
          </span>
        </div>
      </div>

      {expanded && (
        <div style={{ borderTop: "1px solid #f3f4f6", padding: "16px 20px", background: "#fafafa" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 14, marginBottom: 14 }}>
            {deal.ucc_debtor && (
              <div><div style={dl}>Borrower (debtor)</div><div style={dv}><EntityLink name={deal.ucc_debtor} onPick={onEntity} style={{ fontWeight: 400 }} /></div></div>
            )}
            {deal.source_type === "ucc" && deal.lender && !deal.lender.startsWith("Not available") && (
              <div><div style={dl}>Lender (secured party)</div>
                <div style={dv}>{deal.lender}{deal.lender_category && <div style={{ fontSize: 12, color: "#9ca3af" }}>{deal.lender_category}</div>}</div></div>
            )}
            {deal.operator_names?.length > 0 && (
              <div><div style={dl}>{deal.source_type === "ucc" ? "Linked CMS owner(s)" : "Operator(s)"}</div><div style={dv}>{deal.operator_names.map((n, i) => <span key={n}>{i > 0 && ", "}<EntityLink name={n} onPick={onEntity} style={{ fontWeight: 400 }} /></span>)}</div></div>
            )}
            {deal.facility_names?.length > 0 && (
              <div><div style={dl}>Facilities</div><div style={dv}>{hasUnresolvedFacilities(deal) ? "—" : deal.facility_names.join(", ")}</div></div>
            )}
            {deal.acquisition_date && (
              <div><div style={dl}>Effective Date</div><div style={dv}>{deal.acquisition_date}</div></div>
            )}
            <div>
              <div style={dl}>Data Source</div>
              <Tooltip text={src.tip}>
                <div style={{ ...dv, cursor: "help", textDecoration: "underline dotted", textUnderlineOffset: 3 }}>
                  {src.tip.split(" — ")[0]}
                </div>
              </Tooltip>
            </div>
          </div>

          {ccns.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={dl}>CMS Certification Number(s)</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                {ccns.map(ccn => (
                  FACILITY_BASE
                    ? <a key={ccn} href={`${FACILITY_BASE}/${ccn}`} target="_blank" rel="noreferrer"
                        style={{ fontSize: 12, color: "#2563eb", background: "#eff6ff",
                          padding: "3px 9px", borderRadius: 4, textDecoration: "none",
                          fontFamily: "monospace", border: "1px solid #bfdbfe" }}>{ccn} ↗</a>
                    : <span key={ccn} style={{ fontSize: 12, color: "#374151",
                        background: "#f3f4f6", padding: "3px 9px", borderRadius: 4,
                        fontFamily: "monospace" }}>{ccn}</span>
                ))}
              </div>
            </div>
          )}

          {history?.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={dl}>Facility history — other records for the same CCN{ccns.length > 1 ? "s" : ""}</div>
              <div style={{ marginTop: 4 }}>
                {history.slice(0, 8).map(h => (
                  <div key={h.id} style={{ display: "flex", gap: 10, fontSize: 12, color: "#374151", padding: "2px 0" }}>
                    <span style={{ color: "#9ca3af", minWidth: 78, fontVariantNumeric: "tabular-nums" }}>
                      {h.acquisition_date || h.created_at?.slice(0, 10)}</span>
                    <span style={{ width: 6, height: 6, borderRadius: "50%", marginTop: 5, flexShrink: 0,
                      background: (SOURCE[h.source_type] || SOURCE.rss).dot }} />
                    <span>{dealSummary(h)}</span>
                  </div>
                ))}
                {ccns.length === 1 && (
                  <button onClick={() => onShowFacility(ccns[0])} style={{ ...btnGhost, fontSize: 12, padding: "3px 10px", marginTop: 6 }}>
                    Show all {history.length + 1} records for CCN {ccns[0]}
                  </button>
                )}
              </div>
            </div>
          )}

          {deal.source_url && (
            <div>
              <div style={dl}>Source</div>
              {deal.source_type === 'chow'
                ? <a href="https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership"
                    target="_blank" rel="noreferrer" style={lnk}>
                    CMS SNF Change of Ownership Dataset ↗
                  </a>
                : deal.ucc_filing_number
                ? <UccSource deal={deal} searchForm={searchForms?.[deal.ucc_state]} />
                : <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnk}>
                    {deal.source_title || deal.source_url} ↗
                  </a>
              }
            </div>
          )}
        </div>
      )}
    </div>
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
    <div style={{ background: "#fff", border: "1px solid #c7d2fe", borderRadius: 8, padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: "#111827" }}>{entity}</div>
        <button onClick={onClose} style={{ ...btnGhost, marginLeft: "auto", padding: "3px 10px", fontSize: 12 }}>× Close</button>
      </div>
      <div style={{ fontSize: 13, color: "#6b7280", marginTop: 4 }}>
        {deals.length} record{deals.length !== 1 ? "s" : ""}
        {Object.keys(roleCounts).length > 0 && " as " + Object.entries(roleCounts).map(([r, n]) => `${r} (${n})`).join(", ")}
        {dates.length > 0 && ` · ${dates[0]} – ${dates[dates.length - 1]}`}
        {states.size > 0 && ` · ${[...states].sort().join(", ")}`}
      </div>
      {top.length > 0 && (
        <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={dl}>Most frequent counterparties</span>
          {top.map(([n, c]) => (
            <span key={n} onClick={() => onEntity(n)} title={`All deals involving ${n}`}
              style={{ fontSize: 12, color: "#374151", background: "#f3f4f6", borderRadius: 20,
                padding: "2px 9px", cursor: "pointer" }}>{n} <span style={{ opacity: 0.5 }}>{c}</span></span>
          ))}
        </div>
      )}
    </div>
  );
}

function computeStats(allDeals) {
  const now = new Date();
  const cutoff90 = new Date(now - 90 * 24 * 60 * 60 * 1000);
  const stateCounts = {};
  let last90 = 0;
  for (const d of allDeals) {
    if (d.acquisition_date && new Date(d.acquisition_date) >= cutoff90) last90++;
    for (const s of (d.states || [])) {
      stateCounts[s] = (stateCounts[s] || 0) + 1;
    }
  }
  const topStates = Object.entries(stateCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([state, count]) => ({ state, count }));
  return {
    total: allDeals.length,
    last_90_days: last90,
    states_covered: Object.keys(stateCounts).length,
    top_states: topStates,
  };
}

function filterDeals(allDeals, { state, dateFrom, dateTo, search, sourceType, ccnStatus, dealType, entity }) {
  return allDeals.filter(d => {
    if (state && !(d.states || []).includes(state)) return false;
    const effectiveDate = d.acquisition_date || d.created_at;
    if (dateFrom && effectiveDate && effectiveDate < dateFrom) return false;
    if (dateTo && effectiveDate && effectiveDate > dateTo + "T99") return false;
    if (sourceType && d.source_type !== sourceType) return false;
    if (entity && dealRoles(d, entity).length === 0) return false;
    if (dealType === "no_liens" ? d.deal_type === "non_deal_lien" : dealType && d.deal_type !== dealType) return false;
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
const FILTER_PARAMS = { state: "state", dateFrom: "from", dateTo: "to", search: "q", sourceType: "source", ccnStatus: "ccn", dealType: "type", entity: "entity" };
const SAVED_VIEWS_KEY = "nh-saved-views";

function filtersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return Object.fromEntries(Object.entries(FILTER_PARAMS).map(([k, p]) => [k, params.get(p) || ""]));
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
    f.dealType && (f.dealType === "no_liens" ? "No non-deal liens" : DEAL_TYPE[f.dealType]?.label || f.dealType),
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
  const [dealType, setDealType]   = useState(initial.dealType);
  const [entity, setEntity]       = useState(initial.entity);
  const [savedViews, setSavedViews] = useState(loadSavedViews);
  const [offset, setOffset]       = useState(0);
  const LIMIT = 20;

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

  const currentFilters = { state, dateFrom, dateTo, search, sourceType, ccnStatus, dealType, entity };

  useEffect(() => {
    const params = new URLSearchParams();
    for (const [k, p] of Object.entries(FILTER_PARAMS)) if (currentFilters[k]) params.set(p, currentFilters[k]);
    const qs = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (qs ? `?${qs}` : ""));
  }, [state, dateFrom, dateTo, search, sourceType, ccnStatus, dealType, entity]);

  const updateSavedViews = (views) => { setSavedViews(views); storeSavedViews(views); };
  const currentName = viewName(currentFilters);
  const isSaved = savedViews.some(v => v.name === currentName);

  function saveView() {
    if (!currentName || isSaved) return;
    updateSavedViews([...savedViews, { name: currentName, filters: currentFilters, lastSeen: new Date().toISOString() }]);
  }

  function openView(view) {
    const f = view.filters;
    setState(f.state || ""); setDateFrom(f.dateFrom || ""); setDateTo(f.dateTo || "");
    setSearch(f.search || ""); setSourceType(f.sourceType || ""); setCcnStatus(f.ccnStatus || "");
    setDealType(f.dealType || ""); setEntity(f.entity || ""); setOffset(0);
    updateSavedViews(savedViews.map(v => v.name === view.name ? { ...v, lastSeen: new Date().toISOString() } : v));
  }

  const removeView = (name) => updateSavedViews(savedViews.filter(v => v.name !== name));

  const newCounts = useMemo(() => Object.fromEntries(savedViews.map(v => [
    v.name,
    filterDeals(allDeals, v.filters).filter(d => d.created_at && new Date(d.created_at) > new Date(v.lastSeen)).length,
  ])), [savedViews, allDeals]);

  const filtered = useMemo(
    () => filterDeals(allDeals, currentFilters),
    [allDeals, state, dateFrom, dateTo, search, sourceType, ccnStatus, dealType, entity]
  );

  const stats = useMemo(() => allDeals.length ? computeStats(allDeals) : null, [allDeals]);

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

  function applyFilters() { setOffset(0); }

  function clearAll() {
    setState(""); setDateFrom(""); setDateTo("");
    setSearch(""); setSourceType(""); setCcnStatus(""); setDealType(""); setEntity(""); setOffset(0);
  }

  function toggleExpand(id) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const hasFilters = state || dateFrom || dateTo || search || sourceType || ccnStatus || dealType || entity;

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
  const pipelineFreshness = freshness.pipeline_ran_at && `Sources checked ${fmtStamp(freshness.pipeline_ran_at)}`;
  const chowFreshness = freshness.chow_file_date &&
    `CMS CHOW file: ${new Date(freshness.chow_file_date + "T12:00:00").toLocaleDateString(undefined,
      { month: "short", day: "numeric", year: "numeric" })} (quarterly)`;

  return (
    <div style={{ minHeight: "100vh", background: "#f9fafb",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" }}>

      <div style={{ background: "#1a1f36", borderBottom: "1px solid #2d3358",
        padding: "0 40px", height: 56, display: "flex",
        alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 7, height: 7, borderRadius: "50%",
            background: "#22c55e", boxShadow: "0 0 0 2px rgba(34,197,94,0.3)" }} />
          <span style={{ fontSize: 14, fontWeight: 600, color: "#fff" }}>
            Nursing Home Ownership Feed
          </span>
          {freshness.exported_at && (
            <span style={{ fontSize: 12, color: "#6b7280" }}>
              · Updated {fmtStamp(freshness.exported_at)}
            </span>
          )}
        </div>
      </div>

      <div style={{ maxWidth: 740, margin: "0 auto", padding: "32px 20px" }}>
        <div style={{ marginBottom: 28 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: "#111827",
            letterSpacing: "-0.02em", marginBottom: 6 }}>
            Nursing Home Ownership Changes
          </h1>
          <p style={{ fontSize: 14, color: "#6b7280", lineHeight: 1.6, maxWidth: 520 }}>
            Federal data on skilled nursing facility ownership changes, sourced from
            CMS Provider Enrollment records and SEC filings.
          </p>

          {stats && (
            <div style={{ display: "flex", gap: 12, marginTop: 20, flexWrap: "wrap" }}>
              {[
                ["Total records", stats.total?.toLocaleString()],
                ["Last 90 days", stats.last_90_days?.toLocaleString()],
                ["States covered", stats.states_covered],
              ].map(([label, val]) => (
                <div key={label} style={{ background: "#fff", border: "1px solid #e5e7eb",
                  borderRadius: 8, padding: "12px 16px", minWidth: 110 }}>
                  <div style={{ fontSize: 11, color: "#9ca3af", fontWeight: 600,
                    textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: "#111827" }}>{val}</div>
                </div>
              ))}
              <div style={{ background: "#f0fdf4", border: "1px solid #bbf7d0",
                borderRadius: 8, padding: "12px 16px", display: "flex",
                flexDirection: "column", justifyContent: "center" }}>
                <div style={{ fontSize: 11, color: "#15803d", fontWeight: 600,
                  textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 2 }}>Data freshness</div>
                {pipelineFreshness && <div style={{ fontSize: 12, color: "#16a34a" }}>{pipelineFreshness}</div>}
                {chowFreshness && <div style={{ fontSize: 12, color: "#16a34a" }}>{chowFreshness}</div>}
              </div>
            </div>
          )}
        </div>

        <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 8,
          padding: "14px 16px", marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
            <input value={search} onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applyFilters()}
              placeholder="Search operator, acquirer, borrower, facility or lender..."
              style={{ ...selStyle, flex: 1, minWidth: 200 }} />
            <select value={sourceType} onChange={e => { setSourceType(e.target.value); setOffset(0); }} style={selStyle}>
              <option value="">All sources</option>
              <option value="chow">Federal Record</option>
              <option value="edgar">SEC Filing</option>
              <option value="rss">News</option>
              <option value="ucc">UCC Filing</option>
            </select>
            <select value={dealType} onChange={e => { setDealType(e.target.value); setOffset(0); }} style={selStyle}>
              <option value="">All deal types</option>
              {Object.entries(DEAL_TYPE).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
              <option value="no_liens">All except non-deal liens</option>
            </select>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select value={state} onChange={e => { setState(e.target.value); setOffset(0); }} style={selStyle}>
              <option value="">All states</option>
              {US_STATES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={ccnStatus} onChange={e => { setCcnStatus(e.target.value); setOffset(0); }} style={selStyle}>
              <option value="">All CCN status</option>
              <option value="matched">Matched to CCN</option>
              <option value="unmatched">Not yet matched</option>
            </select>
            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={selStyle} />
            <span style={{ color: "#9ca3af", fontSize: 13 }}>to</span>
            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={selStyle} />
            <button onClick={applyFilters} style={btnPrimary}>Apply</button>
            {hasFilters && <button onClick={clearAll} style={btnGhost}>Clear all</button>}
            {hasFilters && !isSaved && (
              <button onClick={saveView} style={btnGhost} title="Save these filters in this browser">☆ Save view</button>
            )}
            <span style={{ fontSize: 12, color: "#9ca3af", marginLeft: "auto" }}>
              {total.toLocaleString()} record{total !== 1 ? "s" : ""}
            </span>
            {total > 0 && (
              <button onClick={() => downloadCsv(filtered, currentName.replace(/[^\w-]+/g, "-").replace(/^-|-$/g, ""))}
                style={{ ...btnGhost, padding: "5px 10px", fontSize: 12 }} title="Download the filtered records as CSV">
                ↓ CSV
              </button>
            )}
          </div>
        </div>

        {entity && (
          <CompanyPanel entity={entity} deals={filtered} onEntity={pickEntity} onClose={() => { setEntity(""); setOffset(0); }} />
        )}

        {savedViews.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
            <span style={{ fontSize: 11, color: "#9ca3af", fontWeight: 600,
              textTransform: "uppercase", letterSpacing: "0.06em", marginRight: 2 }}>Saved</span>
            {savedViews.map(v => {
              const active = v.name === currentName;
              return (
                <span key={v.name} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12,
                  padding: "3px 6px 3px 10px", borderRadius: 20, cursor: "pointer",
                  background: active ? "#111827" : "#fff", color: active ? "#fff" : "#374151",
                  border: `1px solid ${active ? "#111827" : "#e5e7eb"}` }}
                  onClick={() => openView(v)}>
                  ★ {v.name}
                  {newCounts[v.name] > 0 && (
                    <span style={{ fontSize: 11, fontWeight: 700, color: "#fff", background: "#7c3aed",
                      borderRadius: 10, padding: "0 6px" }}>+{newCounts[v.name]} new</span>
                  )}
                  <span onClick={e => { e.stopPropagation(); removeView(v.name); }} title="Remove"
                    style={{ color: active ? "#9ca3af" : "#9ca3af", padding: "0 2px" }}>×</span>
                </span>
              );
            })}
          </div>
        )}

        {stats?.top_states?.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 16 }}>
            {stats.top_states.map(({ state: s, count }) => (
              <button key={s}
                onClick={() => { setState(p => p === s ? "" : s); setDateFrom(""); setDateTo(""); setOffset(0); }}
                style={{ fontSize: 12, fontWeight: 600,
                  background: state === s ? "#eff6ff" : "#fff",
                  color: state === s ? "#2563eb" : "#6b7280",
                  border: `1px solid ${state === s ? "#bfdbfe" : "#e5e7eb"}`,
                  borderRadius: 20, padding: "4px 12px", cursor: "pointer" }}>
                {s} <span style={{ opacity: 0.6 }}>{count}</span>
              </button>
            ))}
          </div>
        )}

        {loading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} style={{ background: "#fff", border: "1px solid #e5e7eb",
              borderRadius: 10, height: 88, marginBottom: 8, animation: "pulse 1.5s infinite" }} />
          ))
        ) : loadError ? (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <div style={{ fontSize: 14, color: "#ef4444" }}>Failed to load data: {loadError}</div>
          </div>
        ) : deals.length === 0 ? (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>◎</div>
            <div style={{ fontSize: 14, color: "#9ca3af" }}>No ownership changes match these filters</div>
            {hasFilters && <button onClick={clearAll} style={{ ...btnGhost, marginTop: 16 }}>Clear filters</button>}
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
            alignItems: "center", marginTop: 20 }}>
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              style={{ ...btnGhost, opacity: offset === 0 ? 0.4 : 1 }}>← Newer</button>
            <span style={{ fontSize: 13, color: "#9ca3af" }}>
              {offset + 1}–{Math.min(offset + LIMIT, total)} of {total.toLocaleString()}
            </span>
            <button disabled={offset + LIMIT >= total} onClick={() => setOffset(offset + LIMIT)}
              style={{ ...btnGhost, opacity: offset + LIMIT >= total ? 0.4 : 1 }}>Older →</button>
          </div>
        )}

        <div style={{ marginTop: 48, fontSize: 12, color: "#d1d5db",
          borderTop: "1px solid #f3f4f6", paddingTop: 20, lineHeight: 1.8 }}>
          Data sourced from the CMS SNF Change of Ownership dataset, state UCC-1 filings
          (NY, NJ, OH, PA, KY), SEC EDGAR 8-K filings and trade press.
          Federal Provider Numbers (CCNs) link to facility profiles.{" "}
          <a href={import.meta.env.BASE_URL + "feed.xml"} style={{ color: "#9ca3af" }}>Subscribe (Atom feed)</a>
        </div>
      </div>

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { color: #111827; }
        select option { background: #fff; color: #111827; }
        @keyframes pulse { 0%,100%{opacity:0.5} 50%{opacity:1} }
        input::placeholder { color: #9ca3af; }
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-track { background: #f9fafb; }
        ::-webkit-scrollbar-thumb { background: #e5e7eb; border-radius: 3px; }
      `}</style>
    </div>
  );
}

const dl = { fontSize: 10, color: "#9ca3af", textTransform: "uppercase",
  letterSpacing: "0.07em", marginBottom: 3, fontWeight: 600 };
const dv = { fontSize: 13, color: "#374151", lineHeight: 1.5 };
const lnk = { fontSize: 13, color: "#2563eb", textDecoration: "none" };
const selStyle = { background: "#fff", border: "1px solid #e5e7eb", color: "#374151",
  borderRadius: 6, padding: "7px 12px", fontSize: 13, outline: "none" };
const btnPrimary = { background: "#111827", color: "#fff", border: "none",
  borderRadius: 6, padding: "8px 18px", fontSize: 13, fontWeight: 600, cursor: "pointer" };
const btnGhost = { background: "transparent", color: "#6b7280",
  border: "1px solid #e5e7eb", borderRadius: 6, padding: "7px 14px",
  fontSize: 13, cursor: "pointer" };
