"""Free copy of a paywalled story (pipeline/free_copy.py, 2026-11-20). No API calls."""
from pipeline import free_copy as F

PIQUA_TITLE = "Piqua nursing home property sells for $2.6M following property portfolio acquisition - Dayton Business Journal"
PIQUA = ("Preview this article 1 min\nThe buyer is linked to an operator who holds a 12% ownership stake.\n"
         "- Local Pilates studio expands footprint Local Pilates studio expands footprint\n")
COSTAR = ("A joint venture between BH Group and Gold Standard of Care has acquired AdviniaCare Naples ... for $10 million.\n"
          "This news story is available exclusively to CoStar subscribers.")
COPY = ("PIQUA, Ohio - Lionstone Healthcare bought the Piqua Manor nursing home property for $2.6 million. " * 20)


def test_preview_detection():
    assert F.is_preview(PIQUA) and F.is_preview(COSTAR)
    assert not F.is_preview("Ensign Group acquired operations of 20 facilities. " * 30)
    assert not F.is_preview(F.combine(PIQUA, {"url": "https://x.com/a", "text": COPY}))   # already has a copy
    assert not F.is_preview(PIQUA, "ucc://OH/123")


def test_anchors_and_match():
    a = F.anchors(PIQUA_TITLE, PIQUA)
    assert "piqua" in a and "$2.6" in a and "pilates" not in a and "dayton" not in a
    assert F.matches(a, COPY)
    assert not F.matches(a, "Piqua news: a different home sold for $4.1 million. " * 20)   # price differs
    assert not F.matches(F.anchors("Welltower closes sixth South Florida home", ""), "Sabra buys an Ohio home " * 30)


def _fake(monkeypatch, pick, page):
    monkeypatch.setattr(F, "_ask", lambda *a, **k: (pick, None))
    return lambda url: (page, "Release")


def test_find_accepts_matching_free_page(monkeypatch):
    fetch = _fake(monkeypatch, {"url": "https://www.prnewswire.com/lionstone", "why": "Piqua, $2.6M"}, COPY)
    got = F.find("https://www.bizjournals.com/dayton/x.html", PIQUA_TITLE, PIQUA, fetch=fetch)
    assert got["url"].endswith("lionstone")
    text = F.combine(PIQUA, got)
    assert text.startswith("Preview this article") and F.MARKER in text and "Lionstone" in text


def test_find_rejects_archives_paywalls_and_other_deals(monkeypatch):
    for bad in ["https://archive.ph/abc12", "https://www.bizjournals.com/cincinnati/y.html", "https://web.archive.org/web/1/x"]:
        fetch = _fake(monkeypatch, {"url": bad}, COPY)
        assert F.find("https://www.bizjournals.com/dayton/x.html", PIQUA_TITLE, PIQUA, fetch=fetch) is None
    fetch = _fake(monkeypatch, {"url": "https://local.com/other"}, "Sidney nursing home sold for $9 million. " * 30)
    assert F.find("https://www.bizjournals.com/dayton/x.html", PIQUA_TITLE, PIQUA, fetch=fetch) is None


def test_augment_off_for_full_articles_and_filings(monkeypatch):
    monkeypatch.setattr(F, "find", lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched")))
    art = {"url": "https://x.com/a", "source_type": "rss"}
    full = "Ensign Group acquired operations of 20 facilities. " * 30
    assert F.augment(art, full) == full
    assert F.augment({"url": "https://x.com/a", "source_type": "con"}, PIQUA) == PIQUA
