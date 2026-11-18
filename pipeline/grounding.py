"""
Keep only what the source says. Claude's extraction can fill a field from
its own knowledge instead of the text: the Cliveden notice (Philadelphia,
2025-12) came back with three more homes of the same operator that the
notice never names, a MarketBeat index page of 328 characters with a
"PACS acquires Eduro" deal, a Sabra presentation saying only that the homes
were leased to Communicare with Communicare as buyer (audit 2026-10-21).

ground() runs on every extracted deal before it is stored:
  - a facility name the text doesn't contain is dropped
  - a buyer or seller the text doesn't contain is blanked
  - a price or loan amount that isn't a figure in the text (or the sum of
    two, an opco and a propco price) is blanked
  - a closing date neither written in the text nor within 45 days of the
    article's date ("closed last week") is blanked
  - a deal left with no buyer, seller or facility from the text is dropped
Everything removed is logged, and listed under "ungrounded" on the deal.
"""

import logging
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Words that say nothing about which company or home it is
_GENERIC = set("""
LLC INC CORP CORPORATION CO COMPANY LP LLP LTD THE OF AND AT FOR IN A AN
HOLDINGS HOLDING GROUP OPCO PROPCO OPERATIONS OPERATING OPERATOR PROPERTIES
""".split())


def _words(s: str) -> list[str]:
    return [w for w in re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper()).split() if w not in _GENERIC]


# ...nor which home: every story about a sale has these
_HOME_WORDS = set("""
NURSING REHABILITATION REHAB CENTER CENTRE HOME HOMES HEALTH HEALTHCARE CARE
LIVING SENIOR SKILLED FACILITY MANOR VILLAGE COMMUNITY
""".split())


def name_in_text(name: str, text_words: set) -> bool:
    """Most of a name's distinctive words appear in the text. A described
    party ("an Indiana-based private owner") counts when its words do.
    Words every nursing home name has don't count: "Maplewood Nursing and
    Rehabilitation Center" is not in a story naming only Cliveden's."""
    words = [w for w in _words(name) if len(w) >= 3]
    words = [w for w in words if w not in _HOME_WORDS] or words
    if not words:
        return True
    found = sum(1 for w in words if w in text_words or (len(w) > 4 and w.rstrip("S") in text_words))
    return found >= max(1, round(len(words) * 0.75))


# "$ _______111,688.89___" on Alabama's notice form
_MONEY = re.compile(r"\$[\s_]*([\d,]+(?:\.\d+)?)\s*(billion|million|mm|m|bn|b)?\b", re.I)
# ...or with the $ after it ("___468,957.14______$"): any amount in cents
_CENTS = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+\.\d{2})(?![\d])")


def figures_m(text: str) -> list[float]:
    """Dollar figures in the text, in millions. A bare figure is read both
    as dollars and as thousands, the unit of SEC filings' tables ("$14,500,
    excluding closing costs": $14.5 million)."""
    out = []
    for num, unit in _MONEY.findall(text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").lower()
        if unit in ("billion", "bn", "b"):
            out.append(v * 1000)
        elif unit in ("million", "mm", "m"):
            out.append(v)
        else:
            out += [v / 1e6, v / 1e3]
    out += [float(x.replace(",", "")) / 1e6 for x in _CENTS.findall(text)]
    return out


def value_in_text(value_m: float, text: str) -> bool:
    figs = figures_m(text)
    close = lambda a, b: abs(a - b) <= max(0.01, 0.01 * b)
    return any(close(f, value_m) for f in figs) or any(
        close(a + b, value_m) for i, a in enumerate(figs) for b in figs[i + 1:])


_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def _as_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def date_in_text(d: date, text: str, published=None) -> bool:
    """A closing date is supported when the text writes it ("June 30",
    "Sept. 1, 2025", "6/30/26", "June 2026", or a month alone for a
    first-of-month date), or when it is within 45 days of the article's own
    date. Gold set 2026-11-16: a $10.5M Connecticut sale "in 2025" stored as
    2025-10-21, and Saber's June closing of Ciena homes stored as April 2."""
    pub = _as_date(published)
    if pub and abs((d - pub).days) <= 45:
        return True
    t = text.lower()
    mon = _MONTHS[d.month - 1]
    names = rf"(?:{mon}|{mon[:3]}\.?|{mon[:4]}\.?)"
    return bool(
        re.search(rf"(?<![a-z]){names}\s+0?{d.day}\b", t)
        or re.search(rf"\b0?{d.month}/0?{d.day}/(?:20)?{d.year % 100:02d}\b", t)
        or re.search(rf"\b{d.year}-{d.month:02d}-{d.day:02d}\b", t)
        or re.search(rf"(?<![a-z]){mon}\b[^.]{{0,40}}\b{d.year}\b", t)
        or (d.day == 1 and re.search(rf"(?<![a-z]){mon}\b", t)))


def ground(deal: dict, text: str, published=None) -> dict | None:
    """The deal with fields its source text doesn't support removed, or
    None when nothing identifying is left. published: the article's date,
    for closing dates given relative to it."""
    text_words = set(_words(text))
    removed, named = [], bool(deal.get("acquiring_entity") or deal.get("seller_entity") or deal.get("facility_names"))
    facs = deal.get("facility_names") or []
    kept = [f for f in facs if name_in_text(f, text_words)]
    removed += [f"facility {f!r}" for f in facs if f not in kept]
    deal["facility_names"] = kept
    for field in ("acquiring_entity", "seller_entity", "lender"):
        if deal.get(field) and not name_in_text(deal[field], text_words):
            removed.append(f"{field} {deal[field]!r}")
            deal[field] = None
    for field in ("deal_value_m", "financing_amount_m"):
        if deal.get(field) and not value_in_text(float(deal[field]), text):
            removed.append(f"{field} {deal[field]}")
            deal[field] = None
    d = _as_date(deal.get("acquisition_date"))
    if d and not date_in_text(d, text, published):
        removed.append(f"acquisition_date {deal['acquisition_date']}")
        deal["acquisition_date"] = None
    if removed:
        deal["ungrounded"] = removed
        logger.info(f"Grounding: not in the source, removed: {'; '.join(removed)}")
    # A story that never named anyone ("a 32-unit community in Utah") stays
    if named and not (deal.get("acquiring_entity") or deal.get("seller_entity") or deal["facility_names"]):
        logger.info("Grounding: dropped a deal with no buyer, seller or facility in its source")
        return None
    # After the drop above: a quote check only blanks fields, never loses a
    # deal (LTC's 8-K says "we acquired", 2026-11-17: 3 deals vanished)
    return ground_quotes(deal, text, published)


# --- Quote per field (user's goal 2026-11-17: news as close to 100% as possible) ---
# The extractor gives, for each field it fills, the sentence it read it from.
# A field stays only when that sentence is in the article word for word and
# itself holds the value; a value with no quote, a quote the article doesn't
# have, or a quote that doesn't say it is blanked. That ties a number to the
# sentence about this deal: a portfolio total ("nine communities in South
# Florida to date") has to be quoted as such to be used.

QUOTED_FIELDS = ("acquiring_entity", "seller_entity", "lender", "facility_count", "deal_value_m",
                 "financing_amount_m", "acquisition_date", "states")
_NUM_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
              "fifteen sixteen seventeen eighteen nineteen twenty").split()
_STATE_NAMES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "DC": "district of columbia", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts",
    "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming"}


def _squash(s: str) -> str:
    """Lower case, curly quotes and dashes made plain, whitespace collapsed:
    quotes and text are compared after this."""
    s = (s or "").lower().translate(str.maketrans("‘’“”–— ", "''\"\"-- "))
    # stored article text can have a space before punctuation ("Clearview Land LLC , which")
    return re.sub(r" ([,.;:!?)])", r"\1", " ".join(s.split()))


def quote_in_text(quote: str, text_squashed: str) -> bool:
    """The quote is in the article. An ellipsis joins pieces that must each be there."""
    parts = [p.strip(" .") for p in re.split(r"\.\.\.|…", _squash(quote))]
    parts = [p for p in parts if len(p) >= 8]
    return bool(parts) and all(p in text_squashed for p in parts)


def _counts(q: str) -> list[int]:
    """Small whole numbers in a sentence, as digits or words (not money, years, beds or percentages)."""
    nums = [int(m) for m in re.findall(r"(?<![\d.,$])(\d{1,3})(?![\d,%]|\.\d|-(?:bed|unit))", q)]
    nums += [i for i, w in enumerate(_NUM_WORDS) if re.search(rf"\b{w}\b", q)]
    return nums


def count_in_quote(n: int, quote: str) -> bool:
    """The count is in the sentence, or is the sum of two or three of its counts
    ("32 skilled nursing facilities and three independent living" = 35)."""
    q = _squash(quote)
    nums = _counts(q)
    if n in nums:
        return True
    pairs = [a + b for i, a in enumerate(nums) for b in nums[i + 1:]]
    triples = [a + b + c for i, a in enumerate(nums) for j, b in enumerate(nums[i + 1:], i + 1) for c in nums[j + 1:]]
    if n in pairs or n in triples:
        return True
    # one home is often "a 120-bed skilled nursing facility"
    return n == 1 and bool(re.search(r"\b(a|an|the|one)\b", q))


def state_in_quote(st: str, quote: str) -> bool:
    q = _squash(quote)
    return bool(re.search(rf"\b{_STATE_NAMES.get(st, '#')}\b", q) or re.search(rf"(?<![a-z]){st.lower()}\b", q))


# A sentence about the deal often names a party briefly ("VIUM" for VIUM
# Capital) or refers back to it ("The bank also closed..."); ground() has
# already found the full name in the article.
_BACK_REF = re.compile(r"\b(it|its|they|we|our|us|the (company|bank|firm|lender|buyer|seller|reit|operator|"
                       r"partnership|trust|fund|group|joint venture|provider|purchaser|acquirer|companies))\b")


def name_in_quote(name: str, quote: str) -> bool:
    qw = set(_words(quote))
    words = [w for w in _words(name) if len(w) >= 3]
    words = [w for w in words if w not in _HOME_WORDS] or words
    return any(w in qw for w in words) or bool(_BACK_REF.search(_squash(quote)))


MISSING_QUOTE = "flag"    # user 2026-11-17: keep a value given without a quote, flag it ("unquoted") for approval; "blank" was 22/64 vs 35/64


def ground_quotes(deal: dict, text: str, published=None, missing: str | None = None) -> dict:
    """Blank every field whose quote is not in the text or doesn't hold the
    value; a field with no quote at all is blanked or flagged (MISSING_QUOTE).
    Only for deals that carry quotes (news extraction)."""
    missing = missing or MISSING_QUOTE
    quotes = deal.get("quotes")
    if not isinstance(quotes, dict):
        return deal   # extracted before quotes were asked for (or not news)
    t = _squash(text)
    removed = []
    for field in QUOTED_FIELDS:
        v = deal.get(field)
        if v in (None, "", []):
            continue
        q = quotes.get(field)
        if not isinstance(q, str) and missing == "flag":
            deal.setdefault("unquoted", []).append(field)
            continue
        if not isinstance(q, str) or not quote_in_text(q, t):
            ok = False
        elif field in ("acquiring_entity", "seller_entity", "lender"):
            ok = name_in_quote(v, q)
        elif field in ("deal_value_m", "financing_amount_m"):
            ok = value_in_text(float(v), q)
        elif field == "facility_count":
            ok = count_in_quote(int(v), q)
        elif field == "acquisition_date":
            d = _as_date(v)
            ok = bool(d) and date_in_text(d, q, published)
        else:  # states: keep the ones the quote names; all of them if it names none
            kept = [s for s in v if state_in_quote(s, q)]
            if kept and len(kept) < len(v):
                removed.append(f"states {sorted(set(v) - set(kept))} (not in quote)")
                deal["states"] = kept
            continue
        if not ok:
            removed.append(f"{field} {v!r} (quote {'missing' if not q else 'does not support it'})")
            deal[field] = None
    if removed:
        deal["ungrounded"] = (deal.get("ungrounded") or []) + removed
        logger.info(f"Quote check: removed {'; '.join(removed)}")
    return deal
