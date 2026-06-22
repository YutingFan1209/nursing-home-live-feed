"""
ucc/cgi_form.py

Maine's UCC search runs on a Perl CGI backend (.pl URLs), not ASP.NET —
no __VIEWSTATE/__EVENTVALIDATION tokens to round-trip. This is the
simpler counterpart to aspnet_form.py: just extract default field values
from a page and POST/GET them back with overrides.

The one thing CGI wizards like this commonly DO require is session
continuity via cookies (not a hidden viewstate field) — the search flow
looks like a multi-step wizard (search_cc_begin.pl implies there's a
"begin" step followed by further steps), so a single `requests.Session()`
must be reused across the whole flow to carry the session cookie forward.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup


def get_form_fields(session: requests.Session, url: str) -> tuple[dict, str]:
    """GET a page and return (default field values, form action URL).
    The action URL matters here since CGI wizards often POST to a
    different .pl script than the one you GET'd (e.g. begin -> step2)."""
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    form = soup.find("form")
    action = form.get("action") if form else url
    # Relative action URLs need resolving against the page URL
    if action and not action.startswith("http"):
        from urllib.parse import urljoin
        action = urljoin(url, action)

    fields = {}
    if form:
        for tag in form.find_all(["input", "select", "textarea"]):
            name = tag.get("name")
            if not name:
                continue
            if tag.name == "select":
                selected = tag.find("option", selected=True)
                fields[name] = selected.get("value", "") if selected else ""
            else:
                fields[name] = tag.get("value", "")

    return fields, (action or url)


def submit_form(
    session: requests.Session, action_url: str, fields: dict, method: str = "POST"
) -> requests.Response:
    """Submit the form. Maine's wizard may use GET for some steps and
    POST for others — confirm with discover_form_fields.py which method
    the real <form> tag specifies before assuming POST."""
    if method.upper() == "GET":
        resp = session.get(action_url, params=fields, timeout=15)
    else:
        resp = session.post(action_url, data=fields, timeout=15)
    resp.raise_for_status()
    return resp
