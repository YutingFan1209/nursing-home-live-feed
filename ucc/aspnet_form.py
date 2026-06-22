"""
ucc/aspnet_form.py

Both Kentucky's and New Jersey's UCC search pages are classic ASP.NET
WebForms (.aspx) — they rely on __VIEWSTATE / __EVENTVALIDATION hidden
fields that must be round-tripped on every POST, not a clean REST API.
This module centralizes that handshake so ky.py / nj.py only need to
supply field names + values, not reimplement viewstate handling.

Usage pattern:
    session = requests.Session()
    fields = get_form_fields(session, SEARCH_URL)
    fields.update({"ctl00$...$txtOrgName": "ABC NURSING HOME LLC"})
    response = post_form(session, SEARCH_URL, fields)

IMPORTANT — before wiring up ky.py / nj.py for real:
State .gov domains aren't reachable from this sandbox's network egress,
so the exact field names below (txtOrgName, btnSearch, etc.) are
placeholders, not verified live values. Run
`scripts/discover_form_fields.py <url>` locally against the real search
page first — it prints every input name/id on the page in ~2 seconds,
then drop the real names into ky.py / nj.py's FIELD_MAP.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup


ASPNET_HIDDEN_FIELDS = [
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
    "__EVENTTARGET",
    "__EVENTARGUMENT",
]


def get_form_fields(session: requests.Session, url: str) -> dict:
    """GET the search page and extract all current hidden + visible
    input fields, so a POST can include the live viewstate token."""
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return get_form_fields_from_soup(BeautifulSoup(resp.text, "html.parser"))


def get_form_fields_from_soup(soup: BeautifulSoup) -> dict:
    """Extract all form field values from an already-parsed BeautifulSoup
    object. Useful when you need to extract step-N fields from a response
    you already have without making an extra GET request."""
    fields = {}
    for tag in soup.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "select":
            selected = tag.find("option", selected=True)
            fields[name] = selected.get("value", "") if selected else ""
        else:
            fields[name] = tag.get("value", "")
    return fields


def post_form(session: requests.Session, url: str, fields: dict) -> requests.Response:
    """POST the form back with updated field values. ASP.NET WebForms
    pages generally require the Referer header to match the page the
    viewstate came from, and expect form-encoded (not JSON) body."""
    headers = {
        "Referer": url,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    resp = session.post(url, data=fields, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp


def list_form_field_names(session: requests.Session, url: str) -> list[str]:
    """Diagnostic helper — returns just the field names (no values),
    useful for figuring out which input corresponds to e.g. the
    organization-name textbox vs. the search button."""
    return list(get_form_fields(session, url).keys())
