#!/usr/bin/env python3
"""
scripts/discover_form_fields.py

Run this LOCALLY (not in this sandbox — state .gov sites aren't reachable
from here) against each state's live UCC search page. It prints every
form input's name/id/type so you can fill in ky.py / nj.py's FIELD_MAP
with real values instead of guesses.

Usage:
    python discover_form_fields.py https://web.sos.ky.gov/ftucc/search.aspx
    python discover_form_fields.py https://www.njportal.com/ucc/search/noncertifiedsearch.aspx

Requires: pip install requests beautifulsoup4
"""

import sys
import requests
from bs4 import BeautifulSoup


def main(url: str):
    session = requests.Session()
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    print(f"\n=== Form fields found on {url} ===\n")

    forms = soup.find_all("form")
    print(f"Found {len(forms)} <form> element(s) on the page.\n")

    for tag in soup.find_all(["input", "select", "textarea", "button"]):
        name = tag.get("name", "")
        tag_id = tag.get("id", "")
        tag_type = tag.get("type", tag.name)
        value = tag.get("value", "")
        # Truncate viewstate-style huge values for readability
        display_value = (value[:40] + "...") if len(value) > 40 else value
        print(f"  type={tag_type:12s} name={name!r:50s} id={tag_id!r:40s} value={display_value!r}")

    print("\nLook for the visible inputs (not __VIEWSTATE/__EVENTVALIDATION) —")
    print("those are your organization-name field, search button, etc.")
    print("Cookies received (session state, if any):", session.cookies.get_dict())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <url>")
        sys.exit(1)
    main(sys.argv[1])
