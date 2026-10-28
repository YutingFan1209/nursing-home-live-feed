"""
Offline tests for deal mode (agent/deals.py): scripted fake model, stub tools.
All names below are synthetic.
"""
from agent.deals import deal_record, resolve_deal, verify_deal_verdict
from agent.tests.test_agent import FakeClient, resp, tool_use
from agent.trace import Trace

DEAL = deal_record({
    "deal_id": 7, "source": "rss", "title": "Quillmark Health buys Zephyrdale Center in Ohio",
    "acquiring_entity": "Quillmark Health", "seller_entity": "Larchmere Holdings LLC",
    "facility_names": ["Zephyrdale Center"], "states": ["OH"], "facility_count": 2,
    "acquisition_date": "2026-09-01", "summary": "Quillmark acquired two skilled nursing homes.",
})

HOMES = {
    "365001": {"ccn": "365001", "provider_name": "Zephyrdale Center for Rehabilitation", "provider_state": "OH",
               "legal_business_name": "Zephyrdale Operating LLC"},
    "365002": {"ccn": "365002", "provider_name": "Brookhollow Manor", "provider_state": "OH",
               "legal_business_name": "Brookhollow Opco LLC"},
    "365003": {"ccn": "365003", "provider_name": "Sablewood Care", "provider_state": "OH",
               "legal_business_name": "Sablewood LLC"},
    "155004": {"ccn": "155004", "provider_name": "Zephyrdale Gardens", "provider_state": "IN",
               "legal_business_name": "Zephyrdale Gardens LLC"},
}


class StubTools:
    schemas = [{"name": n, "description": "", "input_schema": {"type": "object"}}
               for n in ("search_cms_facilities", "search_chow_records")]

    def call(self, name, args):
        if name == "search_cms_facilities":
            return {"matches": [h for h in HOMES.values() if args["name"].lower() in h["provider_name"].lower()]}
        if name == "search_chow_records":
            return {"matches": [{"ccn": "365002", "buyer": "Quillmark Health LLC", "seller": "Larchmere Holdings LLC",
                                 "effective_date": "09/01/2026", "state": "OH"}]}
        raise KeyError(name)


def fac(ccn, basis, step):
    return {"ccn": ccn, "facility_name": "whatever the model typed", "link_basis": basis,
            "evidence": [{"claim": "found", "step_id": step}]}


def run(facilities, outcome="linked", confidence="high"):
    script = [
        resp(tool_use("a", "search_cms_facilities", {"name": "Zephyrdale", "state": "OH"})),
        resp(tool_use("b", "search_chow_records", {"name": "Quillmark", "state": "OH"}),
             tool_use("c", "search_cms_facilities", {"name": "Sablewood"})),
        resp(tool_use("d", "submit_verdict", {"outcome": outcome, "facilities": facilities, "confidence": confidence,
                                              "needs_human_review": False, "reasoning": "r"})),
    ]
    return resolve_deal(DEAL, StubTools(), client=FakeClient(script), model="m", effort="low")


def test_named_home_and_chow_buyer_both_pass_with_cms_names():
    v, trace = run([fac("365001", "named_in_deal", "s1"), fac("365002", "party_is_owner", "s2")])
    assert v["verification"]["passed"] is True, v["verification"]
    assert [(f["ccn"], f["link_basis"]) for f in v["facilities"]] == [("365001", "named_in_deal"), ("365002", "party_is_owner")]
    assert v["facilities"][0]["facility_name"] == "Zephyrdale Center for Rehabilitation"
    assert v["confidence"] == "high"


def test_invented_ccn_is_removed():
    v, _ = run([fac("365001", "named_in_deal", "s1"), fac("369999", "named_in_deal", "s1")])
    assert [f["ccn"] for f in v["facilities"]] == ["365001"]
    assert v["needs_human_review"] and not v["verification"]["passed"]


def test_home_in_another_state_is_removed():
    v, _ = run([fac("155004", "named_in_deal", "s1")])
    # the stub's OH search never returns it; force it into a tool result to test the state rule alone
    assert v["facilities"] == [] and v["outcome"] == "unresolved"
    t = Trace(filing=DEAL, model="m")
    t.add_tool("s1", "search_cms_facilities", {}, {"matches": [HOMES["155004"]]}, None, 1)
    v = verify_deal_verdict({"outcome": "linked", "facilities": [fac("155004", "named_in_deal", "s1")],
                             "confidence": "high", "needs_human_review": False, "reasoning": "r"}, t)
    assert v["facilities"] == [] and any("not the deal's states" in i for i in v["verification"]["issues"])


def test_a_claimed_tie_the_results_dont_show_is_name_similarity_for_a_person():
    v, _ = run([fac("365003", "party_is_owner", "s3")])
    f = v["facilities"][0]
    assert f["link_basis"] == "name_similarity"
    assert v["confidence"] == "medium" and v["needs_human_review"] is True


def test_more_homes_than_the_deal_has_goes_to_a_person():
    v, _ = run([fac("365001", "named_in_deal", "s1"), fac("365002", "party_is_owner", "s2"),
                fac("365003", "name_similarity", "s3")])
    assert any("the deal says 2" in i for i in v["verification"]["issues"])


def test_evidence_citing_a_missing_step_is_dropped():
    v, _ = run([fac("365001", "named_in_deal", "s9")])
    assert v["facilities"][0]["evidence"] == [] and v["needs_human_review"]


def test_portfolio_not_named_is_an_answer_without_facilities():
    v, _ = run([], outcome="portfolio_not_named", confidence="medium")
    assert v["outcome"] == "portfolio_not_named" and v["verification"]["passed"] is True


def test_no_verdict_is_unresolved_and_trace_saves_under_the_deal_id(tmp_path):
    script = [resp(tool_use("a", "search_cms_facilities", {"name": "Zephyrdale"}))] * 3
    v, trace = resolve_deal(DEAL, StubTools(), client=FakeClient(script), model="m", effort="low", max_steps=3)
    assert v["outcome"] == "unresolved" and v["confidence"] == "low"
    assert trace.save(tmp_path).name == "OH_deal7.json"


def test_town_words_in_the_text_dont_make_another_home_named():
    deal = deal_record({"deal_id": 8, "title": "[NJ CON] Real estate transfer: Atrium Post Acute Care of Wayneview",
                        "facility_names": ["Atrium Post Acute Care of Wayneview"], "states": ["NJ"],
                        "summary": "Facility: Atrium Post Acute Care of Wayneview, Wayne, NJ"})
    t = Trace(filing=deal, model="m")
    t.add_tool("s1", "search_cms_facilities", {}, {"matches": [
        {"ccn": "315335", "provider_name": "ATRIUM POST ACUTE CARE OF WAYNE", "provider_state": "NJ"}]}, None, 1)
    v = verify_deal_verdict({"outcome": "linked", "facilities": [fac("315335", "named_in_deal", "s1")],
                             "confidence": "medium", "needs_human_review": False, "reasoning": "r"}, t)
    assert v["facilities"][0]["link_basis"] == "name_similarity" and v["needs_human_review"] is True
