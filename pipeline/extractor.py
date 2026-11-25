"""
Claude-powered deal extractor.
Takes raw article text and returns a list of structured deal dicts.
One article can contain multiple deals (e.g. Dealbook roundups).
"""

import json
import logging
import re
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import anthropic

from pipeline.grounding import ground

from config import get_config
from pipeline.run_health import health

logger = logging.getLogger(__name__)
from pipeline.field_rules import FIELD_RULES

config = get_config()

client = anthropic.Anthropic(api_key=config.anthropic_api_key)

MIN_TEXT_WORDS = 100        # articles under this are likely paywalled/truncated
MIN_TEXT_CHARS = 150        # hard minimum

EXTRACTION_PROMPT = """You are a health policy research assistant extracting nursing home acquisition and financing deals from news articles.

The article's publication date is given just before the article, at the end.

Extract ALL distinct deals mentioned in the article. A single article may describe multiple separate transactions.

For each deal, return a JSON object. Return an array even if there is only one deal. If there are no
qualifying deals (see Scope and Staleness rules below), return an empty array [].

Each deal object must have these fields (use null if not mentioned):
{{
  "acquiring_entity": "name of buyer/acquirer",
  "seller_entity": "name of seller",
  "operator_names": ["list of operator or management company names"],
  "facility_names": ["list of specific facility names"],
  "states": ["2-letter state codes where facilities are located"],
  "facility_count": <integer number of facilities>,
  "deal_value_m": <deal price in millions as a number, e.g. 82.4>,
  "acquisition_date": "YYYY-MM-DD if mentioned, else null",
  "financing_amount_m": <financing/loan amount in millions if mentioned>,
  "lender": "name of lender or financing entity if mentioned",
  "rationale": "1-2 sentence summary of this specific deal, shown on the deal's card",
  "quotes": {{"<field>": "the article's sentence that states this field's value for THIS deal"}}
}}

Quotes: for every one of acquiring_entity, seller_entity, lender, facility_count, deal_value_m,
financing_amount_m, acquisition_date and states that you fill, give in "quotes" the sentence of the
article you read it from, copied word for word (join two pieces of one sentence with "..."). The
sentence must itself state that value for this deal: a company's running total, a portfolio-wide
figure or another deal's number doesn't count. A value no sentence states is null. Fields the site
checks this way are blanked when the quote is missing or isn't in the article.

Scope — only extract deals where the acquired asset (for a loan: the homes the loan is on) is one of: a nursing home, skilled nursing
facility (SNF), assisted living facility, memory care community, continuing care retirement
community (CCRC), a hospice provider or hospice agency, an inpatient rehabilitation hospital or
facility (IRF), or a senior/post-acute housing portfolio containing such facilities.
Do NOT extract a deal whose acquired asset is:
- healthcare technology/software (EHR, practice management, care-coordination platforms)
- a hospital (other than an inpatient rehabilitation hospital), physician practice, or medical
  group with no SNF/AL/memory-care/hospice component
- a home-health-only or personal-care-only provider (no hospice, SNF, AL or memory care involved)
- outpatient physical/occupational therapy clinics
- anything outside healthcare entirely (real estate, retail, fitness, etc. mentioned only in
  passing or by a shared address/developer)
- a PACE program (Program of All-Inclusive Care for the Elderly) or adult day center
Only extract deals whose facilities are in the United States. Skip a deal whose facilities are all
in another country (UK care homes, German or Canadian portfolios, etc.), even if the buyer is a US
company.
If unsure whether the asset qualifies, exclude it rather than guess.

Staleness — only extract a deal if the article is reporting it as current news (an announcement,
a closing, a filing). Do NOT extract a deal that the article mentions only as past background — e.g.
an executive recounting "we acquired X earlier this year / last year / in [past month]" during an
interview, retrospective, or profile piece that isn't primarily about that transaction.

This exclusion is UNCONDITIONAL and applies even when the deal is described in specific, enthusiastic,
or detailed terms — an executive fondly recapping a months-old deal in a podcast/interview is still
NOT news, no matter how much color they give. Concretely: subtract the deal's stated or implied date
from the article's publication date. If the gap is more than ~60 days, you MUST exclude the deal — do not
extract it "just in case," do not include it with a low confidence, leave it out of the array entirely.
Example: an August 2026 podcast interview where the COO says "we completed a large acquisition in
December 2025, adding 14 campuses" — this is old news being recapped, not new news. Exclude it, even
though the acquirer, target, and facility count are all clearly stated.
Two exceptions where past-tense phrasing does NOT mean stale:
1. An article explicitly structured as a news roundup/dealbook of recent transactions
   (e.g. "Skilled Nursing Dealbook: ...") reporting deals from the last ~1-2 weeks — those are
   current news even without an exact date, and should be extracted normally.
2. A company's own routine financial disclosure — an earnings release, earnings call transcript,
   10-Q, or 8-K exhibit — reporting investment activity it completed "during the quarter" or
   "subsequent to quarter end." That is the normal, current way REITs and operators report deals;
   don't treat "closed on," "completed," or "acquired" language in this context as backstory just
   because it's phrased in the past tense. The staleness rule targets a DIFFERENT pattern: a deal
   invoked as background color in an interview/profile/feature article about some other current
   topic, months or years after the fact (the December-2025-Kingston-deal-recapped-in-an-August-
   2026-podcast example above). A same-quarter or "subsequent to quarter end" earnings disclosure
   is not that pattern — extract it normally.

Outside those two exceptions, don't let a missing exact date become an excuse to include a stale
deal: phrases like "earlier this year," "last year," "previously acquired," "since acquiring,"
"following its acquisition of," or a deal named only as backstory for why a company is now doing
something else, are ALL signals of staleness on their own, with or without a resolvable date —
exclude those deals too.

Rules:
- Split broker/lender announcements from acquisition deals (they are separate deals)
- A loan on qualifying homes is a deal even when nothing is bought: a refinancing, recapitalisation,
  HUD or bridge-to-HUD loan, or a new loan. A lender or broker release listing several closed loans
  is one deal per loan, refinancings included, each with its own amount, homes and states
- One announcement covering acquisitions in several states is one deal per state (e.g. "7 facilities in
  Colorado, 9 in Florida and 4 in Washington" is three deals), each with its own facility_count and
  facility_names. A single portfolio sale whose state breakdown the article doesn't give stays one deal
{field_rules}
- Use a party's full legal name when the article gives it. Some buyers are entities named after a
  street address ("101 W State Street Holdings LLC"); keep the LLC/Holdings part so the name doesn't
  read as an address
- Roundups and broker/lender announcements often keep the parties anonymous ("a regional operator",
  "a national owner"). Still fill every field the text does give (lender, financing_amount_m,
  deal_value_m, states, facility_count), and make the rationale say what happened with its numbers:
  the kind of deal (sale, lease, refinancing, new loan), the lender or broker, the amount, beds, and
  how the parties are described, e.g. "CIBC closed a $53M cash-out refinancing for a 9-facility,
  930-bed Pennsylvania SNF portfolio owned by a regional operator."
- The rationale feeds the site's labels, so say plainly: whether it's a sale, a lease, or control
  passing without a sale (a parent becoming the active parent, a member substitution, a new
  manager); whether a loan finances an acquisition or refinances; whether the deal is pending or
  closed; and, if the buyer isn't named, how the article describes them ("a national REIT") or that
  it calls the buyer undisclosed
- Include parent company names (e.g. "Welltower" even if the actual seller was "an affiliate of Welltower")
- States should be 2-letter codes only: ["VA", "CO", "AL"]
- deal_value_m and financing_amount_m must be numbers, not strings
- When the text is only a preview or teaser (a paywalled site), it starts with the headline: extract
  only the deal the headline and teaser state, with their numbers
- Return ONLY the JSON array, no markdown, no explanation

Article:
{article_text}""".replace("{field_rules}", FIELD_RULES)


# The cached part of the prompt: everything before the article
assert EXTRACTION_PROMPT.endswith("Article:\n{article_text}")
STATIC_PROMPT = EXTRACTION_PROMPT[: -len("Article:\n{article_text}")].format().rstrip() + "\n"


_LINK_LINE = re.compile(r"(?m)^- (.{10,200}?) \1[ \t]*\n?")   # "- X X": a related-story link
_PREVIEW = re.compile(r"^\s*(preview this article|subscribe to read|sign in to read)", re.I)


def article_input(article_text: str, title: str | None = None) -> str:
    """The text Claude reads (and grounding checks): the headline first, then
    the body without lists of links to other stories. Paywalled sites (Business
    Journals) give a teaser plus related-story links; the 2026-11-12 Welltower
    preview linked three other Welltower deals and its card took their total."""
    body = _LINK_LINE.sub("", article_text or "").strip()
    preview = bool(_PREVIEW.match(article_text or ""))
    # The headline only for a teaser: on a full article it pulled Claude toward
    # the headline's story or, for an SEC filing (whose title is our own
    # "[EDGAR 8-K] ..." label), toward one portfolio-wide deal (LTC, 11-17)
    head = []
    if title and title.strip() and not title.startswith("[") and (preview or len(body.split()) < MIN_TEXT_WORDS):
        head.append(f"Headline: {title.strip()}")
    if preview and "--- Second source: a free copy" in body:
        head.append("(Only a preview of this article was available; a free copy of the same story from another site follows it.)")
    elif preview:
        head.append("(Only a preview of this article was available.)")
    return "\n".join(head + ([""] if head else []) + [body])


def extract_deals(article_text: str, article_url: str = "", published_at=None, title: str | None = None) -> list[dict]:
    """
    Extract structured deal data from article text using Claude.
    Returns list of deal dicts. Returns empty list if text is too short
    (likely paywalled) or if extraction fails after retries.

    title: the article's headline, put first in the text (article_input).

    published_at (datetime/date/str, optional): the article's publish date,
    passed to Claude so it can tell fresh news apart from a stale deal
    mentioned as past background (see EXTRACTION_PROMPT's Staleness rule).
    Falls back to "unknown" when not available.
    """
    prep = prepare(article_text, article_url, title)
    if prep is None:
        return []
    text, truncated = prep
    published_date_str = str(published_at)[:10] if published_at else "unknown"

    try:
        deals = _call_claude_with_retry(text, article_url, published_date_str)
    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON for {article_url}: {e}")
        health.failed("Claude extraction", f"{article_url}: invalid JSON")
        return []
    except Exception as e:
        logger.error(f"Extraction failed after retries for {article_url}: {e}")
        health.failed("Claude extraction", f"{article_url}: {e}")
        return []

    second = None
    if config.second_read and deals:
        try:
            second = _call_claude_with_retry(text, article_url, published_date_str, prefix=SECOND_READ)
        except Exception as e:
            logger.warning(f"Second read failed for {article_url}: {e}")
            second = False
    return finish(deals, text, published_at, article_url, truncated, second)


def prepare(article_text: str, article_url: str = "", title: str | None = None):
    """The text Claude reads and whether it looks truncated, or None when
    there's too little to extract from. Shared by extract_deals and batch runs."""
    if not article_text:
        logger.warning(f"Empty article text: {article_url}")
        return None
    article_text = article_input(article_text, title)
    word_count = len(article_text.split())
    if len(article_text.strip()) < MIN_TEXT_CHARS:
        logger.warning(f"Article too short ({len(article_text.strip())} chars), skipping: {article_url}")
        return None
    # Under MIN_TEXT_WORDS: still extract (partial text often has enough
    # signal) but mark the result low confidence
    truncated = word_count < MIN_TEXT_WORDS
    if truncated:
        logger.warning(f"Article likely paywalled/truncated ({word_count} words < {MIN_TEXT_WORDS}), "
                       f"flagging: {article_url}")
    return article_text[:config.article_max_chars], truncated


def finish(deals: list, text: str, published_at, article_url: str, truncated: bool, second=None) -> list[dict]:
    """Claude's raw deals -> normalized, grounded deals. second: the second
    read's raw deals (None: not run, False: it failed)."""
    normalized = _clean_read(deals, text, published_at, article_url, truncated)
    if second is not None and normalized:
        # The second read is never stored as a deal; it only flags fields the reads disagree on
        second = _clean_read(second, text, published_at, article_url, truncated) if second is not False else None
        for n in normalized:
            n["second_read"] = compare_reads(n, second) if second is not None else {"failed": True}
    logger.info(
        f"Extracted {len(normalized)} deal(s) from {article_url}"
        + (" [truncated]" if truncated else "")
    )
    return normalized


def _clean_read(deals: list, text: str, published_at, article_url: str, truncated: bool) -> list[dict]:
    normalized = []
    for deal in deals:
        n = _normalize_deal(deal)
        if not n:
            continue
        # Only what the article says (pipeline/grounding.py)
        n = ground(n, text, published_at)
        if not n:
            continue
        if _looks_stale(n.get("rationale")):
            logger.info(
                f"Dropping stale-sounding deal ({n.get('acquiring_entity')!r}) from {article_url}: "
                f"rationale flagged by staleness backstop — {n.get('rationale')!r}"
            )
            continue
        if truncated:
            n["confidence"] = "low"   # downgrade confidence on truncated text
        normalized.append(n)
    return normalized


SECOND_READ = """Read this article as a fact-checker would. Another reader already extracted its deals;
yours is the independent check, so be strict: a number or name goes in a field only when a sentence
states it for that one transaction. Totals across several deals, a company's running count, and
figures for a different deal stay out. A firm that arranged, brokered or announced a loan is not its
lender. When in doubt, null.

"""
COMPARED = ("acquiring_entity", "seller_entity", "lender", "facility_count", "states",
            "deal_value_m", "financing_amount_m", "acquisition_date")


def _same(a, b) -> bool:
    from pipeline.grounding import _words
    if isinstance(a, list) or isinstance(b, list):
        return sorted(a or []) == sorted(b or [])
    if a in (None, "") or b in (None, ""):
        return a in (None, "") and b in (None, "")
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            return abs(float(a) - float(b)) <= 0.01 * max(abs(float(a)), abs(float(b)), 1)
        except (TypeError, ValueError):
            return False
    wa, wb = set(_words(str(a))), set(_words(str(b)))
    return str(a).strip().lower() == str(b).strip().lower() or (bool(wa) and (wa <= wb or wb <= wa))


def compare_reads(deal: dict, second: list[dict]) -> dict:
    """The second read's deal that is this one (most fields alike), and the
    fields where the two disagree: {"matched": bool, "disagree": {field: second's value}}.
    No match means the fact-checker didn't find this deal at all."""
    best, best_score = None, 0
    for s in second:
        score = sum(_same(deal.get(f), s.get(f)) for f in COMPARED if deal.get(f) or s.get(f))
        named = any(deal.get(f) and _same(deal.get(f), s.get(f))
                    for f in ("acquiring_entity", "seller_entity", "lender"))
        if named and score > best_score:
            best, best_score = s, score
    if best is None:
        return {"matched": False, "disagree": {}}
    return {"matched": True,
            "disagree": {f: best.get(f) for f in COMPARED if not _same(deal.get(f), best.get(f))}}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
    reraise=True,
)
def _call_claude_with_retry(article_text: str, article_url: str, published_date_str: str = "unknown",
                            prefix: str = "") -> list[dict]:
    """Call Claude API with exponential backoff retry on rate limits.
    prefix: the second read's fact-checker framing."""
    response = client.messages.create(**request_params(article_text, published_date_str, prefix))
    return parse_response(response, article_url)


def request_params(article_text: str, published_date_str: str = "unknown", prefix: str = "") -> dict:
    """One extraction request, for messages.create or a Message Batches request."""
    return dict(
        model=config.claude_model,
        max_tokens=config.claude_max_tokens,
        output_config={"effort": config.claude_effort},
        messages=[{
            "role": "user",
            # Instructions first and cached (identical on every call, ~2.7k
            # tokens); then what changes per article. Cache reads bill at
            # 0.05x input on Sonnet 5.5 (user asked to turn caching on, 2026-11-18)
            "content": [
                {"type": "text", "text": STATIC_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": prefix + f"This article was published on: {published_date_str}\n\n"
                                         f"Article:\n{article_text}"},
            ],
        }],
    )


# Tokens used by extraction calls in this process (cost analysis 2026-11-18:
# nothing recorded them, so extraction spend could only be estimated)
USAGE = {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
PRICES = {"claude-sonnet-5-5": {"input": 2.00, "cache_read": 0.10, "cache_write": 2.50, "output": 10.00},
          "claude-haiku-5-5": {"input": 0.10, "cache_read": 0.01, "cache_write": 0.125, "output": 0.50},
          "claude-opus-5-5": {"input": 4.00, "cache_read": 0.20, "cache_write": 5.00, "output": 20.00}}


def record_usage(usage, label: str = "", batch: bool = False) -> None:
    u = {"input": usage.input_tokens or 0, "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
         "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0, "output": usage.output_tokens or 0}
    USAGE["calls"] += 1
    for k, v in u.items():
        USAGE[k] += v
    USAGE["batch"] = USAGE.get("batch", 0) + batch
    logger.info(f"Extraction usage {label}: in {u['input']} cache_read {u['cache_read']} "
                f"cache_write {u['cache_write']} out {u['output']}" + (" (batch)" if batch else ""))


def usage_cost(usage: dict = USAGE, model: str | None = None) -> float:
    """$ for the recorded tokens (batch calls at half price)."""
    p = PRICES.get(model or config.claude_model, PRICES["claude-sonnet-5-5"])
    full = sum(usage[k] * p[k] for k in p) / 1e6
    share = (usage.get("batch", 0) / usage["calls"]) if usage["calls"] else 0
    return full * (1 - share / 2)


def usage_line(usage: dict = USAGE) -> str:
    return (f"{usage['calls']} extraction calls: {usage['input']:,} input, {usage['cache_read']:,} cache-read, "
            f"{usage['cache_write']:,} cache-write, {usage['output']:,} output tokens = ${usage_cost(usage):.2f}"
            " (measured)")


def parse_response(response, article_url: str = "", batch: bool = False) -> list[dict]:
    """The deals in one extraction reply (a messages.create or batch result message)."""
    record_usage(response.usage, article_url, batch)
    if response.stop_reason == "refusal":
        logger.warning(f"Extraction refused for {article_url}")
        return []

    # The reply can start with thinking blocks; the JSON is in the text block
    raw = "".join(b.text for b in response.content if b.type == "text").strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        deals = json.loads(raw)
    except json.JSONDecodeError:
        # Sometimes a sentence or two of reasoning comes before the array
        # (5 of 33 articles on 2026-10-22): read the JSON that follows it
        # (a bracket inside the prose, like "[sic]", just fails to parse and is skipped)
        deals = _first_json(raw)
        if deals is None:
            raise
    if not isinstance(deals, list):
        deals = [deals]
    return deals


def _first_json(text: str):
    """The first JSON array or object in `text`, or None."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                return decoder.raw_decode(text[i:])[0]
            except json.JSONDecodeError:
                continue
    return None


def _normalize_deal(raw: dict) -> Optional[dict]:
    """Clean and validate a raw deal dict from Claude."""
    # Must have at least some identifying info to be useful
    has_entity = any([
        raw.get("acquiring_entity"),
        raw.get("seller_entity"),
        raw.get("operator_names"),
        raw.get("facility_names"),
    ])
    has_location = bool(raw.get("states"))

    if not (has_entity or has_location):
        return None

    return {
        "acquiring_entity": _clean_str(raw.get("acquiring_entity")),
        "seller_entity":    _clean_str(raw.get("seller_entity")),
        "operator_names":   _clean_list(raw.get("operator_names")),
        "facility_names":   _clean_list(raw.get("facility_names")),
        "states":           _clean_states(raw.get("states")),
        "facility_count":   _clean_int(raw.get("facility_count")),
        "deal_value_m":     _clean_float(raw.get("deal_value_m")),
        "acquisition_date": _clean_date(raw.get("acquisition_date")),
        "financing_amount_m": _clean_float(raw.get("financing_amount_m")),
        "lender":           _clean_str(raw.get("lender")),
        "rationale":        _clean_str(raw.get("rationale")),
        "quotes":           _clean_quotes(raw.get("quotes")),
        "extraction_model": config.claude_model,
    }


def _clean_quotes(val) -> dict:
    """Field -> sentence, strings only. A reply without quotes gets {}, not a
    pass: grounding then blanks every quoted field (2026-11-17)."""
    if not isinstance(val, dict):
        return {}
    return {str(k): v.strip() for k, v in val.items() if isinstance(v, str) and v.strip()}


# Regex backstop for staleness: the prompt asks Claude to exclude deals it
# recognizes as past background (see EXTRACTION_PROMPT), but that instruction
# isn't followed 100% reliably — Claude sometimes writes a rationale that
# plainly says the deal is old ("earlier this year", "earlier in 2026") yet
# still returns it. Catch those phrasings here as a deterministic second pass
# rather than trusting prompt compliance alone.
_STALE_PHRASE_RE = re.compile(
    r"\b("
    r"earlier (this|that) year"
    r"|earlier in \d{4}"
    r"|last year"
    r"|previously acquired"
    r"|since (its|their|the) acquisition of"
    r"|since acquiring"
    r"|following (its|their) acquisition of"
    r"|completed (the |its |their )?acquisition (of|in) .{0,40}\b(19|20)\d{2}\b"
    r")\b",
    re.IGNORECASE,
)


def _looks_stale(rationale: Optional[str]) -> bool:
    if not rationale:
        return False
    return bool(_STALE_PHRASE_RE.search(rationale))


def _clean_str(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("null", "none", "unknown", "unnamed", "undisclosed") else None


def _clean_list(val) -> list[str]:
    if not val:
        return []
    if isinstance(val, str):
        return [val.strip()] if val.strip() else []
    return [str(v).strip() for v in val if v and str(v).strip()]


def _clean_states(val) -> list[str]:
    states = _clean_list(val)
    # Ensure 2-letter uppercase codes only
    return [s.upper()[:2] for s in states if len(s.strip()) >= 2]


def _clean_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _clean_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _clean_date(val) -> Optional[str]:
    if not val:
        return None
    s = str(val).strip()
    # Basic YYYY-MM-DD validation
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None
