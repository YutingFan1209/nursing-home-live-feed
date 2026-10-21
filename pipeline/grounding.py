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
  - a price that isn't a figure in the text (or the sum of two, an opco and
    a propco price) is blanked
  - a deal left with no buyer, seller or facility from the text is dropped
Everything removed is logged, and listed under "ungrounded" on the deal.
"""

import logging
import re

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


def ground(deal: dict, text: str) -> dict | None:
    """The deal with fields its source text doesn't support removed, or
    None when nothing identifying is left."""
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
    if deal.get("deal_value_m") and not value_in_text(float(deal["deal_value_m"]), text):
        removed.append(f"deal_value_m {deal['deal_value_m']}")
        deal["deal_value_m"] = None
    if removed:
        deal["ungrounded"] = removed
        logger.info(f"Grounding: not in the source, removed: {'; '.join(removed)}")
    # A story that never named anyone ("a 32-unit community in Utah") stays
    if named and not (deal.get("acquiring_entity") or deal.get("seller_entity") or deal["facility_names"]):
        logger.info("Grounding: dropped a deal with no buyer, seller or facility in its source")
        return None
    return deal
