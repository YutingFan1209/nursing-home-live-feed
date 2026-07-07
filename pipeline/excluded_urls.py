"""
URLs that must never be (re)processed into deals — e.g. listicles/roundup
articles that mention multiple operators and cause the extractor to
hallucinate false-positive deals with company names in facility_names.

Add a URL here and it will be treated as already-seen by every source in
main.py's discover_articles, so it's skipped before extraction ever runs.
"""

EXCLUDED_URLS = {
    "https://skillednursingnews.com/2026/06/top-five-skilled-nursing-providers-to-watch-in-2026/",
}
