"""
Recorded owner of a facility's parcel, from public assessor records. Read-only
GETs and one search-form POST; no logins, captchas or bot checks.

  colorado         State of Colorado public parcel composite (ArcGIS query API),
                   every county. Its terms allow use with this attribution and
                   forbid resale.
  south_kingstown  South Kingstown, RI assessor (Vision Government Solutions).
                   One address search, then the parcel page. Street searches
                   list private homeowners too, so only parcels whose house
                   number equals the facility's are kept.

Every record carries the URL it came from, so verify.py can ground a link on it.
Other states and towns return no records: a missing source is not evidence.
"""
from __future__ import annotations

import html
import http.cookiejar
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable, Optional

CO_LAYER = "https://gis.colorado.gov/Public/rest/services/Address_and_Parcel/Colorado_Public_Parcels/MapServer/0"
CO_ATTRIBUTION = ("State of Colorado Public parcels were accessed on {day} from "
                  "https://gis.colorado.gov/public/rest/services/Address_and_Parcel")
VISION_SK = "https://gis.vgsi.com/southkingstownri/"
# CMS lists South Kingstown homes under its villages
SOUTH_KINGSTOWN = {"SOUTH KINGSTOWN", "WEST KINGSTON", "WAKEFIELD", "PEACE DALE", "KINGSTON"}
MAX_RECORDS = 5
VISION_PAUSE_S = 2

_UA = "Mozilla/5.0 (research; nursing-home-live-feed)"


def _street_number(address: str) -> tuple[Optional[str], Optional[str]]:
    """('2121', 'MESA') from '2121 Mesa Dr': the number and the first street word."""
    m = re.match(r"\s*(\d+[A-Z]?)\s+([A-Z0-9]+)", (address or "").upper())
    return (m.group(1), m.group(2)) if m else (None, None)


def _get(url: str, data: Optional[bytes] = None, opener=None) -> str:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": _UA})
    with (opener or urllib.request.build_opener()).open(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def colorado(address: str, city: str, fetch: Callable = _get) -> dict:
    number, word = _street_number(address)
    if not number:
        return {"records": [], "note": "no street number in the facility address"}
    params = {"where": f"situsAdd LIKE '{number} {word.replace(chr(39), '')}%'",
              "outFields": "parcel_id,account,situsAdd,sitAddCty,owner,owner2,saleDate,countyName",
              "returnGeometry": "false", "f": "json"}
    url = CO_LAYER + "/query?" + urllib.parse.urlencode(params)
    rows = [f["attributes"] for f in json.loads(fetch(url)).get("features", [])]
    records = [{
        "owner_name": r.get("owner"), "owner2": r.get("owner2"),
        "parcel_id": r.get("parcel_id"), "account": r.get("account"),
        "situs_address": r.get("situsAdd"), "situs_city": r.get("sitAddCty"),
        "county": r.get("countyName"), "last_sale_date": r.get("saleDate"),
        "city_matches": (r.get("sitAddCty") or "").upper() == (city or "").upper(),
        "source_url": url,
    } for r in rows if (r.get("situsAdd") or "").upper().split()[:1] == [number]]
    records.sort(key=lambda r: not r["city_matches"])
    return {"source": "Colorado public parcel composite", "records": records[:MAX_RECORDS],
            "attribution": CO_ATTRIBUTION.format(day=date.today().isoformat())}


def _span(page: str, name: str) -> Optional[str]:
    m = re.search(rf'id="MainContent_{name}"[^>]*>(.*?)</span>', page, re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split()) or None if m else None


def south_kingstown(address: str, city: str, fetch: Callable = _get, pause: float = VISION_PAUSE_S) -> dict:
    number, word = _street_number(address)
    if not number:
        return {"records": [], "note": "no street number in the facility address"}
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    form = fetch(VISION_SK + "Search.aspx", None, opener)
    fields = {}
    for tag in re.findall(r"<input[^>]*>", form):
        n, v = re.search(r'name="([^"]*)"', tag), re.search(r'value="([^"]*)"', tag)
        if n:
            fields[n.group(1)] = html.unescape(v.group(1)) if v else ""
    fields.pop("ctl00$ImageButton1", None)
    q = f"{number} {word}"
    fields.update({"ctl00$MainContent$ddlSearchSource": "0", "ctl00$MainContent$txtSearchAddress": q,
                   "ctl00$MainContent$txtSearch": q, "ctl00$MainContent$btnSubmit": "Search"})
    time.sleep(pause)
    results = fetch(VISION_SK + "Search.aspx", urllib.parse.urlencode(fields).encode(), opener)
    pids = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", results, re.S):
        link = re.search(r'href="Parcel\.aspx\?[Pp]id=(\d+)"', row)
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if link and cells and cells[0].split()[:1] == [number] and link.group(1) not in pids:
            pids.append(link.group(1))
    records = []
    for pid in pids[:MAX_RECORDS]:
        url = f"{VISION_SK}Parcel.aspx?pid={pid}"
        time.sleep(pause)
        page = fetch(url, None, opener)
        records.append({
            "owner_name": _span(page, "lblOwner") or _span(page, "lblGenOwner"),
            "owner2": _span(page, "lblCoOwner"), "parcel_id": pid, "account": _span(page, "lblAcctNum"),
            "situs_address": _span(page, "lblLocation"), "situs_city": "SOUTH KINGSTOWN",
            "last_sale_date": _span(page, "lblSaleDate"), "book_page": _span(page, "lblBp"),
            "use": _span(page, "lblUseCodeDescription"), "city_matches": True, "source_url": url,
        })
    return {"source": "South Kingstown RI assessor (Vision Government Solutions)", "records": records}


def lookup(address: str, city: str, state: str, fetch: Callable = _get, pause: float = VISION_PAUSE_S) -> dict:
    state, city = (state or "").upper(), (city or "").upper()
    out = None
    if state == "CO":
        out = colorado(address, city, fetch)
    elif state == "RI" and city in SOUTH_KINGSTOWN:
        out = south_kingstown(address, city, fetch, pause)
    if out is not None:
        if not out["records"] and "note" not in out:
            out["note"] = "this source lists no parcel at the facility's house number; that says nothing either way"
        return out
    return {"records": [], "note": f"no public property-record source wired for {city.title()}, {state}"}
