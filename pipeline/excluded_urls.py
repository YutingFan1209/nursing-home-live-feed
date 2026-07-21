"""
URLs that must never be (re)processed into deals — e.g. listicles/roundup
articles that mention multiple operators and cause the extractor to
hallucinate false-positive deals with company names in facility_names.

Add a URL here and it will be treated as already-seen by every source in
main.py's discover_articles, so it's skipped before extraction ever runs.

EXCLUDED_DOMAINS blocks a whole domain rather than one URL — for sources
that are structurally never going to carry SNF acquisition news (a law
firm's press-release feed, a home health trade outlet, etc.), so Google
Alerts can keep surfacing new URLs from them without re-litigating each one.
"""

EXCLUDED_URLS = {
    "https://skillednursingnews.com/2026/06/top-five-skilled-nursing-providers-to-watch-in-2026/",
    "https://markets.businessinsider.com/news/stocks/yorkville-university-to-acquire-beal-university-canada-strengthening-the-future-of-nursing-education-in-new-brunswick-1036306561",
    "https://www.citybiz.co/article/864543/cfg-finances-more-than-145-million-across-seven-transactions/",
    "https://www.citybiz.co/article/867398/cfg-closes-318-8-million-in-financing-for-skilled-nursing-operator/",
}

EXCLUDED_DOMAINS = {
    "homehealthcarenews.com",   # home health, not skilled nursing
    "multibagg.ai",             # Indian market-news aggregator, not US SNF
    "hklaw.com",                # law firm deal-announcement press releases
}
