"""
Labels for the public feed: what kind of event a record is, what kind of
facility, whether the buyer is known, how sure we are it happened, and the
headline. Applied when deals.json is written (scripts/export_deals.py), like
pipeline/ucc_category.py, so nothing is stored and the rules can be retuned.

The feed is read by researchers and legislators, so the rules lean toward
saying less: a label is only applied when the source text supports it.

All text rules read the deal's summary (Claude's one-line rationale, written
from the article) and the source title, never the raw article.
"""

import re
from datetime import date, timedelta

from rapidfuzz import fuzz

from pipeline.normalizer import looks_non_us
from pipeline.ucc_category import is_person, norm
from ucc.lender_classifier import LenderCategory, classify_secured_party



def _text(d: dict) -> str:
    return " ".join(filter(None, [d.get("summary"), d.get("source_title")]))


def _day(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


# ── Transaction type ─────────────────────────────────────────────────────
# A lease moves operations without a sale. Only when the transaction itself
# is the lease: MI CON "NEW 36-MTH LEASE", MS CON "CHOW (Lease)", news
# "executed a new lease". "ACQ 99 BED NH BY X [10-YR LEASE]" and "acquiring
# operations through lease arrangements" are purchases and stay sales.
_LEASE_RE = re.compile(
    r"\[MI CON\] [\d-]+ NEW [^:]*LEASE"
    r"|\[MS CON\] CHOW \(Lease\)"
    r"|\b(executed|arranged|signed|entered|entered into) an? (new )?[\w -]{0,30}\blease\b"
    r"|\bnew lease (commenced|began|started|took effect)\b",
    re.I)
# A new operator taking over under the landlord's lease: PACS taking over 32
# Omega-owned Florida homes "on Omega's master lease" is not a purchase
# Saber taking over Ciena's 18 Omega-leased homes: "a lease transition ...
# an operator change, not a sale" (2026-10-21 audit)
_OPS_TRANSFER_RE = re.compile(r"\b(takeover|took over|take over|transition\w*|transfer\w*) (of |the )?operations?\b"
                              r"|\blease transition\b|\boperator (change|transition)\b", re.I)
_LEASE_WORD_RE = re.compile(r"\b(lease|leases|rent)\b", re.I)
_SALE_RE = re.compile(r"\b(acquir\w*|purchas\w*|bought|sold|sale of|ACQ)\b", re.I)

# Control passes without a sale: ArchCare going from passive to active parent
# of Eger. Not "Acquisition of Control", which is Maine's term for any sale.
_CONTROL_RE = re.compile(
    r"\bpassive parent\b|\bactive parent\b|\bno purchase price\b|\bmember substitution\b"
    r"|\bwithout (a|any) (sale|purchase)\b|\b(internal|corporate) reorganization\b"
    # a new manager isn't a new owner (Trustwell "assumed management of")
    r"|\b(assumed|assumes|assuming|took over|takes over) (the )?management\b", re.I)

_ACQ_FIN_RE = re.compile(
    r"\bacquisition financing\b|\bfor the (acquisition|purchase) of\b|\bto (acquire|purchase)\b"
    r"|\bfinanc\w* the (acquisition|purchase)\b|\bbridge-to-HUD acquisition\b", re.I)
_REFI_RE = re.compile(r"\brefinanc\w*|\bcash-out\b|\brefund(ing)?\b", re.I)


# Consumer lenders: a filing from one against a person is a personal loan,
# not nursing home financing (NY individual filings from credit unions,
# Sheffield Financial, Snap-on tool credit, a storage company; the KY trial's
# 306 were all like this). 2026-10-12.
CONSUMER_LENDER_RE = re.compile(
    r"credit union|\bF\.?C\.?U\b|federal credit|sheffield financial|republic finance|heights finance|"
    r"\bdeere\b|kubota|harley|toyota|honda|nissan|ford motor credit|\bally (bank|financial)|santander consumer|"
    r"synchrony|goodleap|sunrun|\bsolar\b|mosaic solar|rent.to.own|self.?storage|extra space storage|"
    r"snap.?on|matco|mac tools|farm credit|agricultural credit|yamaha|polaris|bmw financial|"
    r"1st franklin|world acceptance|onemain|lendmark|mariner finance|regional finance|security finance|"
    r"tower loan|cnh industrial|caterpillar financial|"
    # NY individual run 2026-10-20: solar loans, timeshares, manufactured
    # homes, farm supply, powersports, card banks
    r"cred\w* union|loanpal|capital resorts|timeshare|vacation|resorts? group|21st mortgage|origen financial|"
    r"green ?tree|freedom road|great plains acceptance|merrick bank|groffdale|growmark|"
    r"social security administration|consumer finance|"
    # audit 2026-10-21: older personal and farm-equipment lenders
    r"american general finan|springleaf|citifinancial|household finance|beneficial (finance|mortgage)|"
    r"new holland credit|avco financial|associates financial|ubs bank usa", re.I)

# Audit 2026-10-21 noise: true filings that aren't nursing home deals.
# A person's filing older than NY's two-year search window, from a lender
# that doesn't finance nursing homes (M&T 2007, Banco Popular 2017). Kept:
# nursing home and private credit lenders (Warren Cole's Capital Finance
# filings for Elderwood) and any filing CMS dates put near a sale.
PERSON_FILING_MAX_AGE = timedelta(days=730)
HOME_LENDERS = ("Healthcare RE / HUD lender", "Private credit fund")
# A syndicate's agent or a deal's nominee lends to a business the owner
# guarantees (Bank of America as administrative agent, Carillo and Friedman 2018)
_DEAL_LENDER_RE = re.compile(r"\bAS (ADMINISTRATIVE |COLLATERAL )?AGENT\b|\bNOMINEE\b|\bLENDER,? LLC\b", re.I)
# Banks that lend to NY nursing homes but sort as plain banks: 11 of 30
# hidden records in the fresh sample (2026-10-21) were their loans to owners
_HOME_BANK_RE = re.compile(
    r"\bVNB NEW YORK|VALLEY NATIONAL|PRIVATE ?BANK AND TRUST|HAPOALIM|SIGNATURE BANK|DORAL HEALTHCARE"
    r"|METROPOLITAN NATIONAL BANK|FORTRESS CREDIT", re.I)
# Florida's daily file is matched on words like "Nursing" and "Rehab": a
# filing linked to no home is kept unless it's plainly another business
# (nursing schools, home care, hospice, pharmacy) or another kind of loan
# (merchant cash advances, copiers, a person as lender)
NOT_A_HOME_RE = re.compile(
    r"school|education|home ?care|home health|hospice|pharmacy|nursing services|staffing|spinal|"
    r"pediatric|mobility|allied health|nursing on demand|\bshop\b", re.I)
SMALL_BUSINESS_LENDER_RE = re.compile(
    r"bitty advance|ace funding|dash funding|seamless funding|lcf group|funding metrics|biz capital|"
    r"olympus lending|mazel tov|alternative funding group|funding strategy partners|middesk|copiers|"
    r"targeted education finance", re.I)
# A lender with no business word in its name is a person ("DAMAS MIRIAM");
# is_person() is for debtors and passes "FORBRIGHT BANK, AS AGENT"
_BUSINESS_WORD_RE = re.compile(
    r"BANK\b|\b(BANCORP|N\.?A|LLC|L\.?L\.?C|INC|CORP\w*|CO|COMPANY|LP|L\.?P|LTD|FUND|CAPITAL|TRUST|FINANC\w*|"
    r"CREDIT|LENDING|FUNDING|AGENT|SECRETARY|DEPARTMENT|ASSOCIATION|PARTNERS|GROUP|HOLDINGS?|SERVIC\w*|"
    r"MANAGEMENT|INVESTORS?|MORTGAGE|COALITION|PLAN)\b", re.I)


def is_personal_loan(d: dict) -> bool:
    return bool(d.get("individual_filing") and CONSUMER_LENDER_RE.search(d.get("lender") or ""))


def is_not_a_deal(d: dict, today: date = None) -> bool:
    """A UCC filing that is real but not a nursing home deal (see
    PERSON_FILING_MAX_AGE and NOT_A_HOME_RE)."""
    filed = str(d.get("acquisition_date") or "")[:10]
    if (d.get("individual_filing") and d.get("deal_type") == "financing" and filed
            and filed < ((today or date.today()) - PERSON_FILING_MAX_AGE).isoformat()
            and d.get("lender_category") not in HOME_LENDERS and not _DEAL_LENDER_RE.search(d.get("lender") or "")
            and not _HOME_BANK_RE.search(d.get("lender") or "")):
        return True
    if d.get("ucc_state") == "FL" and not d.get("ccns"):
        lender = d.get("lender") or ""
        return bool(NOT_A_HOME_RE.search(d.get("ucc_debtor") or "") or SMALL_BUSINESS_LENDER_RE.search(lender)
                    or (lender and not _BUSINESS_WORD_RE.search(lender)))
    # Equipment and vendor liens (welding gear, HVAC) the classifier now knows
    return classify_secured_party(d.get("lender") or "").category == LenderCategory.EQUIPMENT_VENDOR


def transaction_type(d: dict) -> str:
    """deal_type with leases, control changes and news financing subtypes
    split out. UCC types come from pipeline/ucc_category.py and pass through,
    except a person's loan from a consumer lender, which is a non-deal lien."""
    dt = d.get("deal_type") or "ownership_change"
    if d.get("source_type") == "ucc":
        return "non_deal_lien" if is_personal_loan(d) or is_not_a_deal(d) else dt
    text = _text(d)
    if dt == "ownership_change":
        if _CONTROL_RE.search(text) and not d.get("deal_value_m"):
            return "control_change"
        if _LEASE_RE.search(text) and not (d.get("source_type") in ("rss", "edgar") and _SALE_RE.search(d.get("summary") or "")):
            return "lease"
        summary = d.get("summary") or ""
        if (d.get("source_type") in ("rss", "edgar") and _OPS_TRANSFER_RE.search(summary)
                and _LEASE_WORD_RE.search(summary) and not _SALE_RE.search(summary)):
            return "lease"
        return dt
    if dt in ("financing", "refinancing", "acquisition_financing"):
        # "for the acquisition of" wins over "refinancing" in the same line
        if _ACQ_FIN_RE.search(d.get("summary") or ""):
            return "acquisition_financing"
        if _REFI_RE.search(d.get("summary") or ""):
            return "refinancing"
        return "financing"
    return dt


# ── Facility type ────────────────────────────────────────────────────────
_SNF_RE = re.compile(
    r"skilled nursing|\bSNFs?\b|nursing (home|facilit|center|centre|cent|and rehab|& rehab)"
    r"|post-acute|long-term care|\bRHCF\b|rehabilitation (center|centre|and nursing|& nursing)"
    r"|health (care|and rehabilitation) center|\bcare center\b|convalescent|nursing\b", re.I)
_SENIOR_RE = re.compile(
    r"assisted living|memory care|independent living|senior living|senior housing|seniors housing"
    r"|\bCCRC\b|continuing care retirement|life plan|retirement communit|supportive living|residential care|\bunits?\b", re.I)
_HOSPITAL_RE = re.compile(r"\bhospital\b|\bmedical center\b", re.I)

FACILITY_TYPES = ("skilled_nursing", "senior_living", "hospital", "non_us", "unknown")


def facility_type(d: dict) -> str:
    """skilled_nursing covers any record with a nursing home in it (a CCRC's
    SNF beds, a hospital campus with an SNF). CMS, UCC and state CON records
    are all nursing-facility sources; news is read from its own words."""
    if d.get("source_type") in ("chow", "ucc", "con"):
        return "skilled_nursing"
    if d.get("ccns"):
        # matched to a CMS-certified nursing home in the record's own state
        return "skilled_nursing"
    if looks_non_us(d):
        return "non_us"
    text = " ".join([_text(d), *(d.get("facility_names") or [])])
    if _SNF_RE.search(text):
        return "skilled_nursing"
    if _SENIOR_RE.search(text):
        return "senior_living"
    if _HOSPITAL_RE.search(text):
        return "hospital"
    return "unknown"


# ── Buyer ────────────────────────────────────────────────────────────────
# "Undisclosed" only when the source says so; "an unnamed buyer" just means
# the article doesn't name one
_UNDISCLOSED_RE = re.compile(
    r"\bundisclosed (buyer|purchaser|acquirer)\b"
    r"|\bbuyer (was|is|were|has) not (been )?(disclosed|named|identified)\b"
    r"|\bdid not (disclose|name|identify) the (buyer|purchaser)\b", re.I)
# "acquired by a national owner and a new operator entering the Ohio market"
_STOP = (r"(?=,|;|\.|\s(?:in|for|at|from|with|whose|who|that|which|as|on|to|after|"
         r"entering|seeking|marking|partnering|expanding|making|continuing|while)\b|$)")
_DESC_AFTER_RE = re.compile(
    r"\b(?:sold to|sale \w+ to|to|acquired by|purchased by|bought by|(?i:the buyer was|buyer was))\s+((?:an?|the)\s+[^,;.]{2,110}?)" + _STOP)
# "A private real estate investment group, using a New York-based operator, acquired"
_DESC_SUBJECT_RE = re.compile(
    r"(?:^|[;.] )((?:An?|The|a)\s+[^,;.]{2,80}?)(?:,[^,;.]{0,80},)?\s+(?:acquired|purchased|bought|is acquiring|is entering|has acquired|submitted the winning bid)\b")
_NOT_A_DESC_RE = re.compile(r"^(an?|the)\s+(undisclosed|buyer|purchaser|acquirer|portfolio|facility|price|sale|deal|transaction|\d)", re.I)
_UNNAMED_RE = re.compile(r"^(an?|the)\s+(unnamed|unidentified)\s+", re.I)


def _clean_description(desc: str) -> str | None:
    """'an unnamed Utah-based buyer' -> 'a Utah-based buyer'; nothing left
    but 'a buyer' -> None."""
    m = _UNNAMED_RE.match(desc)
    if m:
        rest = desc[m.end():]
        desc = ("an " if rest[:1].lower() in "aeio" else "a ") + rest  # "a Utah-based"
    if _NOT_A_DESC_RE.match(desc):
        return None
    return desc[0].lower() + desc[1:]
# Extraction sometimes puts a description in the name field: "regional
# owner/operator", "Ohio-based provider", seller "public"
# Lower-case first word, "X-based ...", or a lone adjective like "public".
# Case-sensitive on purpose: "National Healthcare Properties" is a company.
_DESC_AS_NAME_RE = re.compile(
    r"^[a-z]|^[A-Z][a-z]+-based [a-z]|^(Regional|National|Local|Public|Private|Nonprofit)( [a-z][\w/-]*)*$")


def _looks_like_description(name: str | None) -> bool:
    return bool(name) and bool(_DESC_AS_NAME_RE.match(name.strip()))


def buyer_fields(d: dict) -> dict:
    """buyer: a name; buyer_description: how the source describes an unnamed
    buyer ("a national owner"); buyer_disclosure: 'named', 'described',
    'undisclosed' (the source says so, or a state filing has no buyer
    field), 'not_named' (the source just doesn't say), or 'cms' (the
    source doesn't name them; the name is CMS's new owner of record)."""
    buyer, seller = d.get("acquiring_entity"), d.get("seller_entity")
    desc = None
    if _looks_like_description(buyer):
        desc, buyer = buyer, None
        desc = desc if re.match(r"(?i)an?\b|the\b", desc) else ("an " if desc[0].lower() in "aeiou" else "a ") + desc
    if _looks_like_description(seller):
        seller = None
    if buyer:
        return {"buyer": buyer, "seller": seller, "buyer_description": None, "buyer_disclosure": "named"}
    # The source doesn't name the buyer but CMS now lists the new owner
    event, sale = d.get("cms_event"), d.get("cms_sale")
    if event and event.get("owner") and d.get("source_type") != "chow":
        return {"buyer": event["owner"], "seller": seller, "buyer_description": desc, "buyer_disclosure": "cms"}
    if sale and d.get("source_type") != "chow":
        return {"buyer": sale["buyer"], "seller": seller, "buyer_description": desc, "buyer_disclosure": "cms"}
    summary = d.get("summary") or ""
    if _UNDISCLOSED_RE.search(summary):
        return {"buyer": None, "seller": seller, "buyer_description": None, "buyer_disclosure": "undisclosed"}
    if not desc:
        m = _DESC_AFTER_RE.search(summary) or _DESC_SUBJECT_RE.search(summary)
        if m:
            desc = _clean_description(m.group(1).strip())
    if desc:
        return {"buyer": None, "seller": seller, "buyer_description": desc, "buyer_disclosure": "described"}
    if d.get("source_type") in ("con", "chow"):
        return {"buyer": None, "seller": seller, "buyer_description": None, "buyer_disclosure": "undisclosed"}
    return {"buyer": None, "seller": seller, "buyer_description": None, "buyer_disclosure": "not_named"}


# ── Status ───────────────────────────────────────────────────────────────
_PENDING_RE = re.compile(
    r"\b(plans? to|agreed to|agrees to|will (acquire|sell|purchase)|to acquire|to sell|is acquiring|"
    r"are acquiring|is selling|is purchasing|expected to (close|be completed)|entered into an agreement|"
    r"signed a (definitive |non-binding )?(agreement|MOU|LOI)|under contract|pending|upon .{0,30}approval|"
    r"is transitioning|projected to close|set to close|"
    # a sale awaiting a court or regulator: "final court approval expected October 14"
    r"(court |final |regulatory )?approval (is |was )?expected|expected .{0,40}approval|awaiting .{0,40}approval|"
    r"subject to .{0,40}approval|asked to (give final approval|approve|confirm))\b", re.I)
_DONE_RE = re.compile(r"\b(completed|closed (on )?the|closed on|closed (its|their)|has acquired|finalized|took over|closed sale|"
                      r"(sale|deal|transaction) (that )?closed)\b", re.I)


def cms_event(d: dict, cms) -> dict | None:
    """The CMS ownership change confirming an ownership deal: a new direct
    owner or a CHOW at one of its facilities within 6 months of the deal
    date (pipeline/cms_confirm.py). A CMS match on the facility alone isn't
    confirmation: Selectis' GA homes still list Selectis as owner, so that
    sale isn't confirmed yet. CMS records confirm themselves."""
    if d.get("source_type") == "chow":
        return {"date": d.get("acquisition_date") or (d.get("created_at") or "")[:10], "owner": d.get("acquiring_entity"),
                "ccn": (d.get("ccns") or [None])[0], "source": "CMS change-of-ownership record", "facility": None}
    if d.get("deal_type") != "ownership_change" or not cms:
        return None
    when = _day(d.get("acquisition_date")) or _day(d.get("published_at")) or _day(d.get("created_at"))
    ev = cms.new_owner(d.get("ccns"), when)
    if not ev or not ev.get("owner"):
        return ev
    # Who CMS names has to fit (2026-10-21): Monroe County, the seller of
    # Oak Hill, "confirmed" its own sale; a Brookdale Galleria owner change
    # four months before Brookdale's purchase, to an unrelated company, did too
    if same_party(ev["owner"], d.get("seller_entity")):
        return None
    buyer_side = [d.get("acquiring_entity")] + (d.get("operator_names") or [])
    if (date.fromisoformat(ev["date"]) < when - EARLY_EVENT
            and not any(same_party(ev["owner"], b) for b in buyer_side)):
        return None
    return ev


SALE_TYPES = ("ownership_change", "acquisition_financing")


def cms_sale(d: dict, cms) -> dict | None:
    """The sale in CMS's change-of-ownership file that this record is
    about: same facility, within 6 months of the deal date, with a buyer
    that fits (2026-10-22: sources often name neither party, but the CHOW
    file names both). CMS records and UCC filings that aren't acquisition
    financing have none."""
    if d.get("source_type") == "chow" or d.get("deal_type") not in SALE_TYPES or not cms:
        return None
    when = _day(d.get("acquisition_date")) or _day(d.get("published_at")) or _day(d.get("created_at"))
    sale = cms.sale(d.get("ccns"), when)
    if not sale:
        return None
    if same_party(sale["buyer"], d.get("seller_entity")) or same_party(sale["seller"], d.get("acquiring_entity")):
        return None   # the other way round: a different sale
    if d.get("source_type") == "ucc":
        # the filing's date and CMS timing already say it's this sale, unless
        # the borrower is the seller (Hilborn Care, sold to Macao Holdings)
        return None if same_party(sale["seller"], d.get("ucc_debtor")) else sale
    buyer_side = [d.get("acquiring_entity")] + (d.get("operator_names") or [])
    fits = any(same_party(sale["buyer"], b) for b in buyer_side)
    # A named buyer has to be CMS's buyer; an unnamed one can't be checked,
    # so only a sale on or after the deal date (or just before) counts
    if d.get("acquiring_entity") and not fits:
        return None
    if not fits and date.fromisoformat(sale["date"]) < when - EARLY_EVENT:
        return None
    return sale


# "from an Indiana-based private owner exiting the nursing home business",
# "The seller was a large owner/operator". A bare "by" isn't one: "run by
# an Ensign affiliate" names the operator, not the seller.
_SELLER_DESC_RE = re.compile(
    r"\b(?:[Tt]he seller (?:was|is)|sold by|(?:sale|purchase|acquisition|buying|bought|acquired|purchased|acquires|buys)"
    r"\b[^.;]{0,120}?\bfrom) ((?:an?|the) [^.,;]{3,70}?)(?=[.,;]| exiting| that| who| which| described| to | for | in )")
_SELLER_UNNAMED_RE = re.compile(r"^(an?|the) (unnamed|undisclosed|unidentified) seller$", re.I)


def seller_fields(d: dict, out: dict) -> dict:
    """The seller from the source, else CMS's (seller_disclosure 'cms'),
    else how the source describes an unnamed one ('described', with
    seller_description) or 'undisclosed'."""
    sale = d.get("cms_sale")
    if out.get("seller"):
        return {"seller_disclosure": "named", "seller_description": None}
    if sale and sale.get("seller") and not same_party(sale["seller"], out.get("buyer")):
        return {"seller": sale["seller"], "seller_disclosure": "cms", "seller_description": None}
    # a sale only: "acquisition financing from an unnamed lending partner" is the lender
    m = (_SELLER_DESC_RE.search(d.get("summary") or "")
         if d.get("source_type") in ("rss", "edgar") and d.get("deal_type") == "ownership_change" else None)
    if m and _SELLER_UNNAMED_RE.match(m.group(1)):
        return {"seller_disclosure": "undisclosed", "seller_description": None}
    if m and not re.match(r"(?i)(an?|the) (unnamed|undisclosed) (buyer|purchaser)", m.group(1)):
        desc = re.sub(r"^an? unnamed ", "", m.group(1))
        desc = desc if desc.lower().startswith(("a ", "an ", "the ")) else ("an " if desc[0].lower() in "aeiou" else "a ") + desc
        desc = re.sub(r"(?: in an? [\w -]+ (?:transaction|deal|sale))$", "", desc)
        return {"seller_disclosure": "described", "seller_description": desc}
    return {"seller_disclosure": None, "seller_description": None}


# A state filing older than this may predate a licensee change CMS has since recorded
LICENSEE_FRESH = timedelta(days=274)


def current_licensee(d: dict, cms, today: date) -> str | None:
    """For a state filing made before closing that names no seller: the
    home's licensee in CMS today (2026-10-22). Not called the seller: in a
    share transfer the licensee stays (Cliveden, Oklahoma stock transfers).
    None once CMS shows a new owner, when it is the buyer, or for an old filing."""
    if d.get("source_type") != "con" or d.get("deal_type") != "ownership_change" or d.get("seller") or not cms:
        return None
    if d.get("cms_event") or d.get("cms_sale"):
        return None
    when = _day(d.get("acquisition_date")) or _day(d.get("published_at")) or _day(d.get("created_at"))
    if not when or today - when > LICENSEE_FRESH:
        return None
    name = cms.current_licensee(d.get("ccns"))
    return None if not name or same_party(name, d.get("buyer")) else name


# A CMS owner change this much before the deal date confirms it only when
# CMS names the buyer (news often runs weeks after a closing)
EARLY_EVENT = timedelta(days=60)


def same_party(a: str | None, b: str | None) -> bool:
    """The same company or person, allowing for suffixes, word order and an
    added place ("Monroe County, Illinois"). Two names sharing one word
    ("ABC Healthcare", "XYZ Healthcare") are not."""
    a, b = norm(a or ""), norm(b or "")
    if not (a and b):
        return False
    short, long_ = sorted((set(a.split()), set(b.split())), key=len)
    return fuzz.token_sort_ratio(a, b) >= 85 or (len(short) >= 2 and short <= long_)


STALE_AFTER = timedelta(days=183)


def status(d: dict, event: dict | None, today: date | None = None) -> dict:
    """confirmed / expected / unconfirmed (expected, but over 6 months on) /
    reported, or on_file for a UCC filing, with a line for the Details panel."""
    today = today or date.today()
    src, when = d.get("source_type"), _day(d.get("acquisition_date"))
    if src == "chow":
        return {"status": "confirmed", "status_detail": "Confirmed: CMS change-of-ownership record"}
    # The filing is on record; that is not a confirmed sale (2026-10-21:
    # "Confirmed" reached readers through the CSV download and deals.json)
    if src == "ucc":
        return {"status": "on_file", "status_detail": "On file: UCC-1 financing statement on file with the state "
                "(a record of the loan, not of a sale)"}
    if event and d.get("deal_type") == "ownership_change":
        where = f" of {event['facility']}" if event.get("facility") else " at this facility"
        who = (f"{event['owner']} as owner" if event.get("owner")
               else f"{event['owner_count']} new owners" if event.get("owner_count", 0) > 1 else "a new owner")
        return {"status": "confirmed", "status_detail":
                f"Confirmed: {event['source']} shows {who}{where} from {event['date']}"}
    future = when and when > today
    pending_words = _PENDING_RE.search(d.get("summary") or "") and not _DONE_RE.search(d.get("summary") or "")
    if src == "con" or future or pending_words:
        what = "State filing made before closing" if src == "con" else "Reported as pending"
        # Still pending long after the filing or report: don't keep calling
        # it expected (ArchCare at Eger, filed December 2025)
        # no deal date: measure from the source's publication date, not the
        # day this tracker happened to find it
        since = when or _day(d.get("published_at")) or _day(d.get("created_at"))
        if since and today - since > STALE_AFTER:
            if d.get("deal_type") != "ownership_change":
                # a lease or control change doesn't show up as a new CMS owner
                check = "this tracker hasn't seen a record of its completion"
            elif d.get("ccns"):
                check = "CMS shows no new owner at this facility since"
            else:
                check = "it isn't matched to a CMS facility, so completion can't be checked"
            return {"status": "unconfirmed", "status_detail":
                    f"Unconfirmed: {what.lower()} on {since.isoformat()}, over 6 months ago; {check}. "
                    "It may not have closed, or the records may not have caught up."}
        tail = (f"; expected to close {when.isoformat()}" if future
                else "; completion not yet confirmed by CMS")
        return {"status": "expected", "status_detail": f"Expected: {what}{tail}"}
    return {"status": "reported", "status_detail": "Reported: news or company filing, not yet confirmed by CMS"}


# ── Headline ─────────────────────────────────────────────────────────────
# A headline is a list of [text, style] segments; style '' (plain), 'muted',
# or 'undisclosed' (gray italics). The frontend only renders them.

def _target(d: dict, facilities: list[str]) -> str | None:
    if facilities:
        # One facility listed under its old and new names ("Serenity Estates of
        # Freeport", "Stephenson County Nursing Center") is still one
        more = len(facilities) - 1 if d.get("facility_count") != 1 else 0
        return facilities[0] + (f" and {more} more" if more else "")
    n = d.get("facility_count")
    if not n:
        return None
    count = "a facility" if n == 1 else f"{n} facilities"
    return f"{count} in {', '.join(d['states'])}" if d.get("states") else count


def _in_states(d):
    return f" in {', '.join(d['states'])}" if d.get("states") else ""


_ROLE_RE = re.compile(r"\bto (active parent|sole member|active member|sole parent|co-operator|operator|parent)\b"
                      r"|\b(?:assumed|assumes|took over|takes over) (management)\b", re.I)
_DBA_RE = re.compile(r"\bd/b/a ([A-Z][\w&'.-]*(?: [A-Z][\w&'.-]*)*)")


def headline(d: dict, facilities: list[str], show_individual_names: bool = True) -> list[list[str]]:
    dt, b = d["deal_type"], d
    target = _target(d, facilities)
    # Unconfirmed is still pending as far as anyone has recorded: same wording
    expected = d.get("status") in ("expected", "unconfirmed")

    if d.get("source_type") == "ucc":
        if d.get("individual_filing"):
            subject = d.get("ucc_debtor") if show_individual_names else "Individual owner"
        else:
            subject = (facilities[:1] or d.get("operator_names") or [d.get("ucc_debtor") or "Borrower"])[0]
        kind = {"amendment": "UCC-3 amendment",
                "non_deal_lien": "likely personal loan" if is_personal_loan(d) else "likely non-deal lien",
                "acquisition_financing": "acquisition financing", "refinancing": "refinancing",
                "operations_transfer": "likely operations transfer"}.get(dt, "UCC-1 financing")
        return [[subject, ""], [f" — {kind}", "muted"]]

    if dt == "control_change":
        entity = b.get("buyer") or (d.get("operator_names") or [None])[0]
        dba = _DBA_RE.search(d.get("summary") or "")
        entity = dba.group(1) if dba else entity
        role = _ROLE_RE.search(d.get("summary") or "")
        facility = facilities[0] if facilities else target or "facility"
        if entity and role:
            what = "manager" if role.group(2) else role.group(1).lower()
            return [[entity, ""], [f" becomes {what} of ", "muted"], [facility, ""]]
        return [["Control change at ", "muted"], [facility, ""], *([[f" ({entity})", "muted"]] if entity else [])]

    if dt == "lease":
        place = target or f"facility{_in_states(d)}"
        if b.get("buyer"):
            return [[b["buyer"], ""], [" to lease " if expected else " leases ", "muted"], [place, ""]]
        return [["New lease" if not expected else "Pending lease", "muted"], [": ", "muted"], [place, ""]]

    if dt in ("financing", "acquisition_financing", "refinancing"):
        lender = d.get("lender") if d.get("lender") and not d["lender"].startswith("Not available") else None
        subject = target or " / ".join(filter(None, [b.get("buyer"), b.get("seller")]))
        verb = {"acquisition_financing": "finances acquisition of", "refinancing": "refinances"}.get(dt, "finances")
        if lender and subject:
            return [[lender, ""], [f" {verb} ", "muted"], [subject, ""]]
        noun = {"acquisition_financing": "acquisition financing", "refinancing": "refinancing"}.get(dt, "financing")
        if lender:
            return [[lender, ""], [f" — {noun}{_in_states(d)}", "muted"]]
        return [[subject, ""], [f" — {noun}", "muted"]] if subject else [[f"Facility {noun}{_in_states(d)}", "muted"]]

    # Ownership change
    buyer, seller = b.get("buyer"), b.get("seller")
    if seller == buyer or b.get("seller_disclosure") == "cms":
        seller = None   # a seller from CMS shows on the card, marked as such
    place = target or f"facility{_in_states(d)}"
    if buyer and b.get("buyer_disclosure") == "cms":
        # Name from CMS, not the source: say so, and don't call it a sale
        # (a stock transfer can give a facility new owners of record too)
        return [[place, ""], [" — new owner per CMS: ", "muted"], [buyer, ""]]
    if buyer:
        verb = " to acquire " if expected else " acquires "
        if target:
            out = [[buyer, ""], [verb, "muted"], [target, ""]]
            return out + ([[" from ", "muted"], [seller, ""]] if seller else [])
        if seller:
            return [[seller, ""], [" to sell to " if expected else " sells to ", "muted"], [buyer, ""]]
        return [[buyer, ""], [f" — new owner{_in_states(d)}", "muted"]]
    if seller:
        out = [[seller, ""], [" to sell " if expected else " sells ", "muted"], [target or "facility", ""]]
    else:
        out = [["Pending sale of " if expected else "Sale of ", "muted"], [place, ""]]
    disclosure = b.get("buyer_disclosure")
    if disclosure == "described":
        return out + [[" to ", "muted"], [b["buyer_description"], ""]]
    if disclosure == "undisclosed":
        return out + [[" · ", "muted"], ["Buyer not disclosed", "undisclosed"]]
    return out


# ── Everything, for one exported deal ────────────────────────────────────

def label(d: dict, cms, facilities: list[str], today: date | None = None,
          show_individual_names: bool = True) -> dict:
    """Fields added to an exported deal. `cms` is a pipeline.cms_confirm.CmsIndex
    (None skips CMS confirmation); `facilities` is the facility list the card
    shows (individual filings use their CMS owner matches)."""
    out = {"deal_type": transaction_type(d), "facility_type": facility_type(d)}
    out["cms_event"] = cms_event({**d, **out}, cms)
    out["cms_sale"] = cms_sale({**d, **out}, cms)
    merged = {**d, **out}
    if out["deal_type"] in ("ownership_change", "lease", "control_change"):
        out.update(buyer_fields(merged))
    else:
        sale = out["cms_sale"]
        buyer = d.get("acquiring_entity") or (sale or {}).get("buyer")
        out.update({"buyer": buyer, "seller": d.get("seller_entity"), "buyer_description": None,
                    "buyer_disclosure": None if not buyer else "named" if d.get("acquiring_entity") else "cms"})
    if out["deal_type"] == "control_change":
        # No sale: no price, no buyer
        out.update({"buyer": None, "buyer_disclosure": None, "buyer_description": None})
    out.update(seller_fields({**d, **out}, out) if out["deal_type"] in SALE_TYPES
               else {"seller_disclosure": "named" if out.get("seller") else None, "seller_description": None})
    out["current_licensee"] = current_licensee({**d, **out}, cms, today or date.today())
    out["cms_confirmed_on"] = out["cms_event"]["date"] if out["cms_event"] else None
    merged = {**d, **out}
    out.update(status(merged, out["cms_event"], today))
    merged = {**d, **out}
    out["headline"] = headline(merged, facilities, show_individual_names)
    return out


def is_individual_filing(d: dict) -> bool:
    from pipeline.individual_owners import STATES
    return d.get("source_type") == "ucc" and d.get("ucc_state") in STATES and is_person(d.get("ucc_debtor"))
