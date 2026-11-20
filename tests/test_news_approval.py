"""Every news card waits for a person's approval (user's goal 2026-11-17)."""
import json

from evals import approval as A
from evals import holds as H


def _setup(tmp_path, monkeypatch, judged):
    monkeypatch.setattr(H, "JUDGED", tmp_path / "judged.jsonl")
    monkeypatch.setattr(H, "RELEASED", tmp_path / "released.jsonl")
    monkeypatch.setattr(H, "HOLDS", tmp_path / "holds.json")
    H.JUDGED.write_text("".join(json.dumps(j) + "\n" for j in judged))


def test_passing_news_card_is_held_but_passing_ucc_ships(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [{"id": "n1", "verdict": "right"}, {"id": "u1", "verdict": "right"},
                                   {"id": "n2", "verdict": "wrong", "wrong_fields": ["lender"]}])
    cards = [{"id": "n1", "source_type": "rss"}, {"id": "u1", "source_type": "ucc", "ccns": ["015019"]},
             {"id": "n2", "source_type": "edgar"}]
    holds = H.check(cards, cards, max_cost=0, spend=False)
    assert holds["n1"]["verdict"] == "awaiting_approval"
    assert "u1" not in holds
    assert holds["n2"]["verdict"] == "wrong"


def test_passing_ucc_card_with_no_linked_home_waits_for_approval(tmp_path, monkeypatch):
    # person's decision 2026-11-20: a UCC filing linked to no CMS home (a propco
    # or management company) is approved by a person, like news
    _setup(tmp_path, monkeypatch, [{"id": "u2", "verdict": "right"}, {"id": "p1", "verdict": "right"}])
    cards = [{"id": "u2", "source_type": "ucc", "ccns": []},
             {"id": "p1", "source_type": "ucc", "ccns": [], "individual_filing": True}]
    holds = H.check(cards, cards, max_cost=0, spend=False)
    assert holds["u2"]["verdict"] == "awaiting_approval" and holds["u2"]["approval"]
    assert "p1" not in holds


def test_released_news_card_ships(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [{"id": "n1", "verdict": "right"}])
    H.RELEASED.write_text(json.dumps({"id": "n1"}) + "\n")
    assert H.check([{"id": "n1", "source_type": "rss"}], [], max_cost=0, spend=False) == {}


def test_plan_routes_each_decision(tmp_path, monkeypatch):
    judged = [{"id": "a", "verdict": "right", "gold": {"lender": "X"}},
              {"id": "b", "verdict": "wrong", "wrong_fields": ["facility_count", "deal_type"],
               "gold": {"facility_count": 41, "deal_type": "financing"}},
              {"id": "c", "verdict": "duplicate", "duplicate_of": "zzzz1111"},
              {"id": "d", "verdict": "noise"},
              {"id": "e", "verdict": "right"}]
    _setup(tmp_path, monkeypatch, judged)
    H.save({i: {"verdict": "x", "source_type": "rss"} for i in "abcde"})
    export = tmp_path / "deals.json"
    export.write_text(json.dumps({"deals": [{"id": "a", "lender": "X"}, {"id": "b", "facility_count": 43, "deal_type": "ownership_change"},
                                            {"id": "c"}, {"id": "d"}, {"id": "e", "states": ["PA"]}]}))
    rev = tmp_path / "reviews"
    rev.mkdir()
    for i, r in {"a": {"decision": "agree", "verdict": "right"},
                 "b": {"decision": "agree", "verdict": "wrong"},
                 "c": {"decision": "agree", "verdict": "duplicate"},
                 "d": {"decision": "agree", "verdict": "noise"},
                 "e": {"decision": "fix", "verdict": "minor", "fixes": {"states": ["OH"]}}}.items():
        (rev / f"{i}.json").write_text(json.dumps(r))
    steps = {s["id"]: s for s in A.plan(str(rev), str(export))}
    assert steps["a"]["action"] == "release" and not steps["a"]["columns"]
    assert steps["b"]["columns"] == {"facility_count": 41}
    assert steps["b"]["hand"] == {"deal_type": "financing"} and steps["b"]["action"] == "hand-then-release"
    assert steps["c"]["action"] == "merge" and steps["c"]["into"] == "zzzz1111"
    assert steps["d"]["action"] == "dismiss"
    assert steps["e"]["columns"] == {"states": ["OH"]} and steps["e"]["action"] == "release"


def test_neighbours_find_buyerless_loan_twin_by_lender():
    from evals.judge import neighbours
    card = {"id": "a", "lender": "MONTICELLOAM", "states": ["VA"], "facility_count": 3, "financing_amount_m": None}
    twin = {"id": "b", "lender": "MonticelloAM", "states": ["VA", "IN"], "facility_count": 7, "financing_amount_m": "195.70"}
    other = {"id": "c", "lender": "CIBC", "states": ["VA"], "facility_count": 2}
    assert [n["id"] for n in neighbours(card, [card, twin, other])] == ["b"]


def test_neighbours_count_undated_same_shape():
    from evals.judge import neighbours
    card = {"id": "a", "buyer": "LTC Properties", "states": ["AL"], "facility_count": 1, "created_at": "2026-10-16"}
    twin = {"id": "b", "buyer": "LTC Properties", "states": ["AL"], "facility_count": 1, "acquisition_date": "2026-07-01"}
    assert [n["id"] for n in neighbours(card, [card, twin])] == ["b"]


ARTICLE = ("Welltower has acquired three more skilled nursing homes in Broward County, Florida for $143 million. "
           "That brings its South Florida total to nine communities. "
           "Cambridge Realty Capital arranged the loan; a regional bank provided it.")


def _deal(**kw):
    return {"acquiring_entity": None, "seller_entity": None, "lender": None, "facility_count": None,
            "deal_value_m": None, "financing_amount_m": None, "acquisition_date": None, "states": [],
            "facility_names": [], **kw}


def test_quote_must_hold_the_value_for_this_deal():
    from pipeline.grounding import ground_quotes
    d = ground_quotes(_deal(acquiring_entity="Welltower", facility_count=9, deal_value_m=143, states=["FL"],
                            quotes={"acquiring_entity": "Welltower has acquired three more skilled nursing homes",
                                    "facility_count": "Welltower has acquired three more skilled nursing homes",
                                    "deal_value_m": "in Broward County, Florida for $143 million",
                                    "states": "Broward County, Florida"}), ARTICLE)
    assert d["acquiring_entity"] == "Welltower" and d["deal_value_m"] == 143 and d["states"] == ["FL"]
    assert d["facility_count"] is None           # quote says three, not nine
    assert any("facility_count" in u for u in d["ungrounded"])


def test_missing_quote_flags_invented_quote_blanks():
    from pipeline.grounding import ground_quotes
    d = ground_quotes(_deal(deal_value_m=1000, lender="Cambridge Realty Capital",
                            quotes={"lender": "Cambridge Realty Capital provided the loan"}), ARTICLE)
    assert d["deal_value_m"] == 1000 and d["unquoted"] == ["deal_value_m"]   # no quote: flagged (user 11-17)
    assert d["lender"] is None                   # quote not in the article: blanked


def test_states_trimmed_to_the_quote():
    from pipeline.grounding import ground_quotes
    d = ground_quotes(_deal(states=["FL", "SC", "TX"], quotes={"states": "three more skilled nursing homes in Broward County, Florida"}),
                      ARTICLE)
    assert d["states"] == ["FL"]


def test_deal_without_quotes_key_is_not_checked():
    from pipeline.grounding import ground_quotes
    d = ground_quotes(_deal(deal_value_m=143), ARTICLE)    # CHOW / older records
    assert d["deal_value_m"] == 143


def test_compare_reads_flags_disagreements():
    from pipeline.extractor import compare_reads
    first = _deal(acquiring_entity="Welltower", facility_count=9, deal_value_m=143.0, states=["FL"])
    second = [_deal(acquiring_entity="Welltower Inc.", facility_count=3, deal_value_m=143, states=["FL"]),
              _deal(acquiring_entity="Sabra", facility_count=2)]
    r = compare_reads(first, second)
    assert r["matched"] and r["disagree"] == {"facility_count": 3}
    assert compare_reads(_deal(seller_entity="Genesis"), second) == {"matched": False, "disagree": {}}


def test_quote_check_accepts_short_names_back_references_and_sums():
    from pipeline.grounding import name_in_quote, count_in_quote, quote_in_text, _squash
    assert name_in_quote("VIUM Capital", "VIUM provided a $15 million bridge loan in July")
    assert name_in_quote("CIBC", "The bank also closed a $53 million cash-out refinancing")
    assert name_in_quote("LTC Properties", "During the quarter we acquired two communities")
    assert not name_in_quote("Sabra Health Care REIT", "Omega bought three homes in Ohio")
    assert count_in_quote(35, "acquisition of 32 skilled nursing facilities and three independent living facilities")
    assert not count_in_quote(120, "a 120-bed skilled nursing facility")    # beds aren't homes
    assert quote_in_text("Clearview Land LLC, which appears tied", _squash("the seller was Clearview Land LLC , which appears tied"))


def test_quote_check_never_drops_a_deal():
    from pipeline.grounding import ground
    d = ground(_deal(acquiring_entity="Welltower", quotes={"acquiring_entity": "a sentence that is not there"}), ARTICLE)
    assert d is not None and d["acquiring_entity"] is None


def test_unquoted_field_flagged_not_blanked_in_flag_mode():
    from pipeline.grounding import ground_quotes
    d = ground_quotes(_deal(facility_count=3, quotes={}), ARTICLE, missing="flag")
    assert d["facility_count"] == 3 and d["unquoted"] == ["facility_count"]
