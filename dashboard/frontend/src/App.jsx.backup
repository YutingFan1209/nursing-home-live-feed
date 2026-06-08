import { useState, useEffect, useCallback } from "react";

const API = import.meta.env.VITE_API_URL ?? "";
const FACILITY_BASE = import.meta.env.VITE_FACILITY_BASE_URL ?? "";

const fmtDate = (v) => {
  if (!v) return null;
  const d = new Date(v);
  const now = new Date();
  const diff = Math.floor((now - d) / (1000 * 60 * 60 * 24));
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return `${diff} days ago`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

const fmtM = (v) => v != null ? `$${Number(v).toFixed(1)}M` : null;
const isNew = (c) => c && (Date.now() - new Date(c).getTime()) < 48 * 60 * 60 * 1000;

const SOURCE = {
  chow:  { label: "Federal Record", dot: "#16a34a", tip: "CMS Provider Enrollment — verified federal ownership data" },
  edgar: { label: "SEC Filing",     dot: "#2563eb", tip: "SEC EDGAR 8-K — publicly traded company filing" },
  rss:   { label: "News",           dot: "#d97706", tip: "Trade press or news article" },
};

const US_STATES = [
  "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
  "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
  "VA","WA","WV","WI","WY","DC"
];

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

function DealCard({ deal, expanded, onToggle }) {
  const src = SOURCE[deal.source_type] || SOURCE.rss;
  const date = fmtDate(deal.acquisition_date) || fmtDate(deal.created_at);
  const ccns = deal.ccns?.filter(Boolean) || [];
  const fresh = isNew(deal.created_at);

  const headline = deal.acquiring_entity && deal.seller_entity
    ? <><strong>{deal.acquiring_entity}</strong><span style={{color:"#6b7280"}}> acquired from </span><strong>{deal.seller_entity}</strong></>
    : deal.acquiring_entity
    ? <><strong>{deal.acquiring_entity}</strong><span style={{color:"#6b7280"}}> — new ownership</span></>
    : deal.seller_entity
    ? <><strong>{deal.seller_entity}</strong><span style={{color:"#6b7280"}}> changes ownership</span></>
    : deal.facility_names?.length > 0
    ? <><strong>{deal.facility_names[0]}</strong><span style={{color:"#6b7280"}}> — ownership change</span></>
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
          {deal.lender && (
            <span style={{ fontSize: 12, color: "#9ca3af" }}>Financed by {deal.lender}</span>
          )}
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
            {deal.operator_names?.length > 0 && (
              <div><div style={dl}>Operator(s)</div><div style={dv}>{deal.operator_names.join(", ")}</div></div>
            )}
            {deal.facility_names?.length > 0 && (
              <div><div style={dl}>Facilities</div><div style={dv}>{deal.facility_names.join(", ")}</div></div>
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

          <div>
            <div style={dl}>Source</div>
            {deal.source_type === 'chow'
              ? <a href="https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership"
                  target="_blank" rel="noreferrer" style={lnk}>
                  CMS SNF Change of Ownership Dataset ↗
                </a>
              : <a href={deal.source_url} target="_blank" rel="noreferrer" style={lnk}>
                  {deal.source_title || deal.source_url} ↗
                </a>
            }
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [deals, setDeals]         = useState([]);
  const [total, setTotal]         = useState(0);
  const [stats, setStats]         = useState(null);
  const [loading, setLoading]     = useState(true);
  const [expanded, setExpanded]   = useState(new Set());
  const [lastUpdated, setLastUpdated] = useState(null);
  const [state, setState]         = useState("");
  const [dateFrom, setDateFrom]   = useState("");
  const [dateTo, setDateTo]       = useState("");
  const [search, setSearch]       = useState("");
  const [sourceType, setSourceType] = useState("");
  const [offset, setOffset]       = useState(0);
  const LIMIT = 20;

  const fetchStats = useCallback(async () => {
    try { setStats(await (await fetch(`${API}/api/stats`)).json()); } catch {}
  }, []);

  const fetchDeals = useCallback(async () => {
    setLoading(true);
    try {
      const p = new URLSearchParams({ limit: LIMIT, offset });
      if (state)      p.set("state", state);
      if (dateFrom)   p.set("date_from", dateFrom);
      if (dateTo)     p.set("date_to", dateTo);
      if (search)     p.set("operator", search);
      if (sourceType) p.set("deal_type", sourceType);
      const data = await (await fetch(`${API}/api/feed?${p}`)).json();
      setDeals(data.deals || []);
      setTotal(data.total || 0);
      setLastUpdated(new Date());
    } finally { setLoading(false); }
  }, [state, dateFrom, dateTo, search, sourceType, offset]);

  useEffect(() => { fetchStats(); }, []);
  useEffect(() => { fetchDeals(); }, [fetchDeals]);
  useEffect(() => {
    const t = setInterval(() => { fetchStats(); fetchDeals(); }, 5 * 60 * 1000);
    return () => clearInterval(t);
  }, [fetchStats, fetchDeals]);

  function toggleExpand(id) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearAll() {
    setState(""); setDateFrom(""); setDateTo("");
    setSearch(""); setSourceType(""); setOffset(0);
  }

  const hasFilters = state || dateFrom || dateTo || search || sourceType;
  const exportUrl = `${API}/api/feed/export/csv?${new URLSearchParams(
    Object.fromEntries(Object.entries({state, date_from: dateFrom, date_to: dateTo}).filter(([,v])=>v))
  )}`;

  const chowFreshness = "Last updated: Jan 2026 (quarterly)";
  const edgarFreshness = lastUpdated ? `EDGAR checked ${lastUpdated.toLocaleTimeString()}` : "";

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
          {lastUpdated && (
            <span style={{ fontSize: 12, color: "#6b7280" }}>
              · Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
        </div>
        <a href={exportUrl} style={{ fontSize: 13, color: "#9ca3af",
          border: "1px solid #2d3358", borderRadius: 6, padding: "6px 14px",
          textDecoration: "none" }}>↓ Export CSV</a>
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
                <div style={{ fontSize: 12, color: "#16a34a" }}>{chowFreshness}</div>
                {edgarFreshness && <div style={{ fontSize: 12, color: "#16a34a" }}>{edgarFreshness}</div>}
              </div>
            </div>
          )}
        </div>

        <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 8,
          padding: "14px 16px", marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
            <input value={search} onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && (setOffset(0), fetchDeals())}
              placeholder="Search operator or acquirer..."
              style={{ ...selStyle, flex: 1, minWidth: 200 }} />
            <select value={sourceType} onChange={e => setSourceType(e.target.value)} style={selStyle}>
              <option value="">All sources</option>
              <option value="chow">Federal Record</option>
              <option value="edgar">SEC Filing</option>
              <option value="rss">News</option>
            </select>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select value={state} onChange={e => setState(e.target.value)} style={selStyle}>
              <option value="">All states</option>
              {US_STATES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={selStyle} />
            <span style={{ color: "#9ca3af", fontSize: 13 }}>to</span>
            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={selStyle} />
            <button onClick={() => { setOffset(0); fetchDeals(); }} style={btnPrimary}>Apply</button>
            {hasFilters && <button onClick={clearAll} style={btnGhost}>Clear all</button>}
            <span style={{ fontSize: 12, color: "#9ca3af", marginLeft: "auto" }}>
              {total.toLocaleString()} record{total !== 1 ? "s" : ""}
            </span>
          </div>
        </div>

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
              onToggle={toggleExpand} />
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
          Data sourced from CMS Provider Enrollment – SNF Change of Ownership dataset
          and SEC EDGAR 8-K filings. Updated automatically.
          Federal Provider Numbers (CCNs) link to facility profiles.
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
