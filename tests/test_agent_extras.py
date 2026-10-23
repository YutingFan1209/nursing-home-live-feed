"""
Offline tests for agent/score.py, agent/audit.py and agent/mcp_server.py.
Synthetic names only. Run:  venv/bin/python3 -m pytest tests/test_agent_extras.py
(Needs the `mcp` package for the server tests; they skip without it.)
"""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from agent.audit import apply_audit, build_audit_prompt, run_audit
from agent.loop import resolve_filing
from agent.score import is_correct, render, score_rows
from agent.trace import Trace

# ── score ─────────────────────────────────────────────────────

def row(ccn, label, conf="high", review="False", verified="True", src="human", **kw):
    return {"state": "NY", "filing_number": kw.pop("fn", "1"), "ccn": ccn, "human_ccn": label,
            "confidence": conf, "needs_review": review, "verified": verified, "label_source": src,
            "tool_calls": "4", "input_tokens": "1000", "output_tokens": "100", **kw}


def test_is_correct_handles_none_and_ccn_suffix():
    assert is_correct("335999", "335999")
    assert is_correct("335999 ", "335999AB")        # compared on the 6-char CCN
    assert is_correct("", "NONE") and is_correct(None, "none")
    assert not is_correct("335999", "NONE") and not is_correct("", "335999")
    assert is_correct("056215", "56215") and is_correct("056215", "56215.0")   # spreadsheet dropped the zero


def test_score_calibration_and_accepted_operating_point():
    rows = [
        row("111111", "111111", "high", fn="a"), row("222222", "222222", "high", fn="b"),
        row("333333", "999999", "high", fn="c"),                       # confident and wrong
        row("444444", "444444", "medium", review="True", fn="d"),
        row("", "555555", "low", review="True", verified="False", fn="e"),
        row("666666", "", "high", fn="f"),                             # unlabeled: ignored
    ]
    r = score_rows(rows)
    assert (r["rows"], r["labeled"]) == (6, 5)
    s = r["by_label_source"]["human"]
    assert s["accuracy"] == 0.6 and s["coverage"] == 0.8 and s["precision"] == 0.75
    assert s["by_confidence"]["high"] == {"n": 3, "accuracy": 0.667}
    assert s["by_confidence"]["low"] == {"n": 0, "accuracy": None}   # the abstention (row e) isn't an answer
    assert s["abstained"] == {"n": 1, "share": 0.2}
    assert s["accepted"] == {"n": 3, "share": 0.6, "accuracy": 0.667}   # a, b, c passed with no review
    assert [c["filing_number"] for c in s["confident_wrong"]] == ["c"]
    assert s["avg_tool_calls"] == 4.0 and s["avg_input_tokens"] == 1000.0


def test_label_sources_are_kept_separate_and_flagged_in_the_report():
    rows = [row("111111", "111111", src="pipeline_match"), row("222222", "222222", src="human")]
    r = score_rows(rows)
    assert set(r["by_label_source"]) == {"human", "pipeline_match"}
    assert "easy cases" in render(r)


def test_nothing_labeled_says_what_to_do():
    assert "Fill in human_ccn" in render(score_rows([row("111111", "")]))


# ── audit ─────────────────────────────────────────────────────

FILING = {"state": "NY", "filing_number": "T-1", "filing_date": "2025-06-01",
          "debtor_name": "Zephyrdale Operating LLC", "secured_party": "Example Capital Funding"}


def make_trace():
    t = Trace(filing=FILING, model="m")
    t.add_tool("s1", "search_cms_facilities", {"name": "Zephyrdale"}, {"matches": [{"ccn": "335999"}]}, None, 5)
    t.add_tool("s2", "get_facility_ownership", {"ccn": "335999"}, {"timing": {"cms_signal": "acquisition_financing"}}, None, 5)
    return t


def make_verdict(**over):
    v = {"category": "acquisition_financing", "confidence": "high", "needs_human_review": False,
         "evidence": [{"claim": "facility is CCN 335999", "step_id": "s1"},
                      {"claim": "owner changed near the filing", "step_id": "s2"}],
         "verification": {"passed": True, "issues": []}}
    v.update(over)
    return v


def audit_in(*supported, follows=True):
    return {"results": [{"n": i, "supported": s, "reason": "r"} for i, s in enumerate(supported, 1)],
            "category_follows": follows, "category_reason": "r"}


def test_audit_prompt_pairs_each_claim_with_its_cited_output_and_no_reasoning():
    v = make_verdict(reasoning="SECRET CHAIN OF THOUGHT")
    body = json.loads(build_audit_prompt(v, make_trace()))
    assert body["evidence"][1]["cited_step"] == "s2" and "acquisition_financing" in body["evidence"][1]["cited_output"]
    assert "SECRET" not in json.dumps(body)


def test_clean_audit_changes_nothing():
    out = apply_audit(make_verdict(), audit_in(True, True))
    assert out["confidence"] == "high" and out["needs_human_review"] is False
    assert out["audit"]["valid"] and out["verification"]["passed"] is True


def test_unsupported_claim_lowers_confidence_and_forces_review():
    out = apply_audit(make_verdict(), audit_in(True, False))
    assert out["confidence"] == "medium" and out["needs_human_review"] is True
    assert any("not supported" in i for i in out["verification"]["issues"])
    assert out["audit"]["supported"][1]["supported"] is False


def test_category_not_following_goes_to_low_and_review():
    out = apply_audit(make_verdict(), audit_in(True, True, follows=False))
    assert out["confidence"] == "low" and out["needs_human_review"] is True


@pytest.mark.parametrize("bad", [audit_in(True), audit_in(True, True, True), {"results": [{"n": 1, "supported": True, "reason": ""}, {"n": 1, "supported": True, "reason": ""}], "category_follows": True, "category_reason": ""}])
def test_incomplete_or_duplicated_audit_is_invalid_and_sent_to_review(bad):
    out = apply_audit(make_verdict(), bad)
    assert out["audit"]["valid"] is False and out["needs_human_review"] is True and out["confidence"] == "medium"


def test_missing_audit_is_a_reason_for_review():
    out = apply_audit(make_verdict(), None)
    assert out["audit"] == {"valid": False} and out["needs_human_review"] is True


class OneShotClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        return self.script.pop(0)


def _resp(*content):
    return NS(content=list(content), stop_reason="tool_use", usage=NS(input_tokens=10, output_tokens=5))


def _tu(id, name, inp):
    return NS(type="tool_use", id=id, name=name, input=inp)


class Tools:
    schemas = [{"name": "search_cms_facilities", "description": "", "input_schema": {"type": "object"}},
               {"name": "get_facility_ownership", "description": "", "input_schema": {"type": "object"}}]
    fac = {"ccn": "335999", "provider_name": "Zephyrdale Center", "provider_state": "NY"}

    def call(self, name, args):
        return {"matches": [self.fac]} if name == "search_cms_facilities" else {"facility": self.fac, "timing": {"cms_signal": "acquisition_financing"}}


def _verdict_call():
    return _tu("v", "submit_verdict", {
        "category": "acquisition_financing", "facility_name": "Zephyrdale Center", "ccn": "335999",
        "owner_entity": None, "link_basis": "direct", "confidence": "high", "needs_human_review": False, "reasoning": "r",
        "evidence": [{"claim": "match", "step_id": "s1"}, {"claim": "timing", "step_id": "s2"}]})


def _run(audit_response, audit=True):
    client = OneShotClient([_resp(_tu("a", "search_cms_facilities", {"name": "Z"})),
                            _resp(_tu("b", "get_facility_ownership", {"ccn": "335999", "filing_date": "2025-06-01"})),
                            _resp(_verdict_call()), audit_response])
    return client, resolve_filing(FILING, Tools(), client=client, model="m", effort="low", audit=audit)


def test_loop_runs_audit_in_a_fresh_context_with_no_tools_but_the_audit_tool():
    client, (verdict, trace) = _run(_resp(_tu("x", "submit_audit", audit_in(True, True))))
    call = client.calls[-1]
    assert [t["name"] for t in call["tools"]] == ["submit_audit"]
    assert len(call["messages"]) == 1                       # no history from the agent loop
    assert verdict["audit"]["valid"] and verdict["confidence"] == "high"
    assert trace.usage["model_calls"] == 4


def test_loop_audit_failure_to_answer_forces_review_not_a_crash():
    _, (verdict, _) = _run(_resp(NS(type="text", text="looks fine")))
    assert verdict["needs_human_review"] is True and verdict["audit"] == {"valid": False}


def test_audit_is_skipped_unless_asked_and_for_unresolved():
    client, (verdict, _) = _run(None, audit=False)
    assert "audit" not in verdict and len(client.calls) == 3


# ── MCP server ────────────────────────────────────────────────

def test_mcp_server_exposes_read_only_tools_and_docs():
    pytest.importorskip("mcp")
    from agent.mcp_server import build_server

    class TB:
        def call(self, name, args):
            return {"echo": name, "args": args}

    server = build_server(TB())
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {"search_cms_facilities", "search_cms_owners", "search_chow_records",
                          "search_cms_by_address", "get_facility_ownership",
                          "get_related_ucc_filings", "search_deals", "classify_lender", "get_ucc_filing",
                          "lookup_property_owner"}
    assert all(t.annotations.read_only_hint for t in tools.values())
    assert tools["get_facility_ownership"].input_schema["required"] == ["ccn"]
    res = asyncio.run(server.call_tool("get_facility_ownership", {"ccn": "335999", "filing_date": "2025-06-01"}))
    assert json.loads(res.content[0].text)["args"] == {"ccn": "335999", "filing_date": "2025-06-01"}
    uris = {str(r.uri) for r in asyncio.run(server.list_resources())}
    assert uris == {"nursing-home://methodology", "nursing-home://how-it-works"}


# ── tools that need no database ───────────────────────────────

def _chow_row(ccn, buyer, seller, state="NY", date="04/08/2024"):
    return {"CCN - BUYER": ccn, "CCN - SELLER": ccn, "EFFECTIVE DATE": date, "CHOW TYPE TEXT": "CHANGE OF OWNERSHIP",
            "ORGANIZATION NAME - BUYER": buyer, "DOING BUSINESS AS NAME - BUYER": "",
            "ORGANIZATION NAME - SELLER": seller, "DOING BUSINESS AS NAME - SELLER": "",
            "ENROLLMENT STATE - BUYER": state, "ENROLLMENT STATE - SELLER": state}


def test_chow_search_finds_a_company_that_sold_its_home():
    from agent.tools import ToolBox
    tb = ToolBox(NS(set_session=lambda **kw: None))
    tb._chow_rows = [_chow_row("335999", "NEW ZEPHYR OPCO LLC", "ZEPHYRDALE MANOR, LLC"),
                     _chow_row("335998", "OTHER BUYER LLC", "OTHER SELLER LLC")]
    got = tb.call("search_chow_records", {"name": "Zephyrdale Manor LLC", "state": "NY"})["matches"]
    assert [(m["ccn"], m["matched_as"]) for m in got] == [("335999", "seller")]
    assert tb.call("search_chow_records", {"name": "Zephyrdale Manor", "state": "PA"})["matches"] == []


# ── property records (saved-response shapes, synthetic names) ──

CO_RESPONSE = json.dumps({"features": [
    {"attributes": {"parcel_id": "100", "account": "R1", "situsAdd": "2121 ZEPHYR DR", "sitAddCty": "ALPINE",
                    "owner": "ZEPHYRDALE PROPCO LLC", "owner2": None, "saleDate": "4/28/2016", "countyName": "Boulder"}},
    {"attributes": {"parcel_id": "101", "account": "R2", "situsAdd": "21210 ZEPHYR DR", "sitAddCty": "ALPINE",
                    "owner": "SOMEONE ELSE", "owner2": None, "saleDate": None, "countyName": "Boulder"}}]})

SK_FORM = '<input name="__VIEWSTATE" value="x"/><input name="ctl00$ImageButton1" value=""/>'
SK_RESULTS = ('<tr><td>55 ZEPHYR WAY</td><td>ZEPHYR SHELL LLC</td><td><a href="Parcel.aspx?pid=7">x</a></td></tr>'
              '<tr><td>557 ZEPHYR WAY</td><td>A PRIVATE PERSON</td><td><a href="Parcel.aspx?pid=8">x</a></td></tr>')
SK_PARCEL = ('<span id="MainContent_lblOwner">ZEPHYR SHELL LLC</span><span id="MainContent_lblLocation">55 ZEPHYR WAY</span>'
             '<span id="MainContent_lblSaleDate">08/06/2026</span><span id="MainContent_lblBp">1939/780</span>')


def test_colorado_lookup_keeps_only_the_facility_house_number_and_its_query_url():
    from agent import property_records as pr
    seen = []
    out = pr.lookup("2121 Zephyr Dr", "Alpine", "CO", fetch=lambda url, *a: seen.append(url) or CO_RESPONSE)
    assert [r["owner_name"] for r in out["records"]] == ["ZEPHYRDALE PROPCO LLC"]
    assert out["records"][0]["source_url"] == seen[0] and "2121+ZEPHYR" in seen[0]
    assert out["records"][0]["city_matches"] and "accessed on" in out["attribution"]


def test_south_kingstown_lookup_drops_neighbours_and_cites_the_parcel_page():
    from agent import property_records as pr
    pages = {"Search.aspx": [SK_FORM, SK_RESULTS]}
    calls = []

    def fetch(url, data=None, opener=None):
        calls.append(url)
        return pages["Search.aspx"].pop(0) if url.endswith("Search.aspx") else SK_PARCEL
    out = pr.lookup("55 Zephyr Way", "Wakefield", "RI", fetch=fetch, pause=0)
    assert [r["source_url"] for r in out["records"]] == [pr.VISION_SK + "Parcel.aspx?pid=7"]
    assert out["records"][0]["owner_name"] == "ZEPHYR SHELL LLC" and out["records"][0]["book_page"] == "1939/780"
    assert not any("pid=8" in c for c in calls)        # the private neighbour's page is never opened
    assert pr.lookup("1 Main St", "Providence", "RI", fetch=fetch)["records"] == []


def _deed_trace(owner, source_url="https://example.test/parcel/1", city_matches=True):
    t = Trace(filing={**FILING, "debtor_name": "Zephyrdale Propco LLC"}, model="m")
    t.add_tool("s1", "search_cms_facilities", {"name": "Zephyrdale"},
               {"matches": [{"ccn": "065999", "provider_name": "Alpine Care", "legal_business_name": "Alpine Opco LLC"}]}, None, 5)
    t.add_tool("s2", "lookup_property_owner", {"ccn": "065999"},
               {"ccn": "065999", "records": [{"ccn": "065999", "owner_name": owner, "parcel_id": "100",
                                              "situs_address": "2121 ZEPHYR DR", "city_matches": city_matches,
                                              "source_url": source_url}]}, None, 5)
    return t


def test_recorded_owner_equal_to_debtor_is_a_grounded_link_capped_at_medium():
    from agent.verify import verify_verdict
    v = {"category": "financing", "ccn": "065999", "facility_name": "Alpine Care", "link_basis": "name_similarity",
         "confidence": "high", "needs_human_review": False, "reasoning": "r",
         "evidence": [{"claim": "debtor owns the parcel", "step_id": "s2"}]}
    out = verify_verdict(v, _deed_trace("ZEPHYRDALE PROPCO, L.L.C."))
    assert out["link_basis"] == "recorded_owner" and "https://example.test/parcel/1" in out["link_rule"]
    assert out["confidence"] == "medium" and out["verification"]["passed"]
    # a different owner, no source URL, or another city's parcel is no link
    for t in (_deed_trace("OTHER LANDLORD LLC"), _deed_trace("ZEPHYRDALE PROPCO LLC", source_url=None),
              _deed_trace("ZEPHYRDALE PROPCO LLC", city_matches=False)):
        assert verify_verdict(v, t)["link_basis"] != "recorded_owner"
