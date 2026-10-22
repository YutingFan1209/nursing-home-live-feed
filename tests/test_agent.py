"""
Offline tests for agent/: a scripted fake model and stub tools, so no DB or
API key is needed. All names below are synthetic.

Run from the project root:  venv/bin/python3 -m pytest tests/test_agent.py
(The SQL in agent/tools.py is NOT exercised here -- use `python -m agent.run --selftest`.)
"""
from types import SimpleNamespace as NS

from agent.loop import resolve_filing
from agent.trace import Trace
from agent.verify import verify_verdict

FILING = {"state": "NY", "filing_number": "T-1", "filing_date": "2025-06-01",
          "debtor_name": "Zephyrdale Operating LLC", "secured_party": "Example Capital Funding"}

FACILITY = {"ccn": "335999", "provider_name": "Zephyrdale Center for Rehabilitation", "provider_state": "NY"}


def tool_use(id, name, inp):
    return NS(type="tool_use", id=id, name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


def resp(*content, stop="tool_use"):
    return NS(content=list(content), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=20))


class FakeClient:
    """Plays back scripted responses; records what the loop sent."""
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        # snapshot: the loop keeps appending to the same list after this call
        self.calls.append({**kw, "messages": list(kw["messages"])})
        return self.script.pop(0)


class StubTools:
    schemas = [{"name": "search_cms_facilities", "description": "", "input_schema": {"type": "object"}},
               {"name": "get_facility_ownership", "description": "", "input_schema": {"type": "object"}}]

    def __init__(self, signal="acquisition_financing", fail=()):
        self.signal, self.fail, self.calls = signal, set(fail), []

    def call(self, name, args):
        self.calls.append((name, args))
        if name in self.fail:
            raise RuntimeError("boom")
        if name == "search_cms_facilities":
            return {"matches": [FACILITY]}
        if name == "get_facility_ownership":
            return {"facility": FACILITY, "timing": {"cms_signal": self.signal}}
        raise KeyError(name)


def verdict_input(**over):
    v = {"category": "acquisition_financing", "facility_name": "Zephyrdale Center for Rehabilitation",
         "ccn": "335999", "owner_entity": "Zephyrdale Operating LLC", "link_basis": "direct", "confidence": "high",
         "needs_human_review": False, "reasoning": "Owner started near the filing.",
         "evidence": [{"claim": "facility matches", "step_id": "s2"}, {"claim": "owner started near filing", "step_id": "s3"}]}
    v.update(over)
    return v


def happy_script(**over):
    return [
        resp(text("Searching by the distinctive core."), tool_use("a", "search_cms_facilities", {"name": "Zephyrdale", "state": "NY"})),
        resp(tool_use("b", "get_facility_ownership", {"ccn": "335999", "filing_date": "2025-06-01"})),
        resp(tool_use("c", "submit_verdict", verdict_input(**over))),
    ]


def test_happy_path_is_verified_and_traced():
    tools, client = StubTools(), FakeClient(happy_script())
    verdict, trace = resolve_filing(FILING, tools, client=client, model="m", effort="low")
    assert verdict["category"] == "acquisition_financing"
    assert verdict["verification"]["passed"] is True
    assert [c[0] for c in tools.calls] == ["search_cms_facilities", "get_facility_ownership"]
    # the opening thought is s1, so the two tool steps are s2 and s3
    assert [s.id for s in trace.tool_steps()] == ["s2", "s3"]
    assert trace.usage["model_calls"] == 3
    # the model is handed the step id of each result so it can cite it
    last_user = client.calls[-1]["messages"][-1]["content"][0]["content"]
    assert '"step_id"' in last_user


def test_evidence_must_cite_real_steps():
    # The thought text takes step s1, so the tool steps are s2 and s3
    verdict, trace = resolve_filing(FILING, StubTools(), client=FakeClient(happy_script()), model="m", effort="low")
    ids = {s.id for s in trace.tool_steps()}
    bad = verify_verdict(verdict_input(evidence=[{"claim": "x", "step_id": "s99"}]), trace)
    assert bad["evidence"] == [] and bad["needs_human_review"] is True
    assert bad["confidence"] != "high"
    assert ids  # sanity


def test_bare_string_evidence_is_dropped_not_a_crash():
    # seen on a real run (FL 202503912301): evidence came back as plain strings
    verdict, trace = resolve_filing(FILING, StubTools(), client=FakeClient(happy_script()), model="m", effort="low")
    bad = verify_verdict(verdict_input(evidence=["CMS lists the operator", "lender is HUD"]), trace)
    assert bad["evidence"] == [] and bad["needs_human_review"] is True
    assert bad["confidence"] != "high"


def test_invented_ccn_and_facility_are_removed():
    script = happy_script(ccn="111111", facility_name="Totally Different Manor")
    verdict, _ = resolve_filing(FILING, StubTools(), client=FakeClient(script), model="m", effort="low")
    assert verdict["ccn"] is None and verdict["facility_name"] is None
    assert any("111111" in u for u in verdict["ungrounded"])
    assert verdict["confidence"] == "low" and verdict["needs_human_review"] is True


def test_category_disagreeing_with_timing_rule_goes_to_review():
    script = happy_script(category="refinancing")      # tool said acquisition_financing
    verdict, _ = resolve_filing(FILING, StubTools(signal="acquisition_financing"), client=FakeClient(script), model="m", effort="low")
    assert verdict["needs_human_review"] is True
    assert any("disagrees" in i for i in verdict["verification"]["issues"])


def test_timing_category_without_checking_timing_is_flagged():
    script = [
        resp(tool_use("a", "search_cms_facilities", {"name": "Zephyrdale"})),
        resp(tool_use("c", "submit_verdict", verdict_input(evidence=[{"claim": "match", "step_id": "s1"}]))),
    ]
    verdict, _ = resolve_filing(FILING, StubTools(), client=FakeClient(script), model="m", effort="low")
    assert verdict["confidence"] == "medium"
    assert any("without checking" in i for i in verdict["verification"]["issues"])


def test_null_timing_signal_caps_confidence():
    verdict, _ = resolve_filing(FILING, StubTools(signal=None), client=FakeClient(happy_script()), model="m", effort="low")
    assert verdict["confidence"] == "medium"


def test_tool_error_is_returned_to_model_not_raised():
    script = [
        resp(tool_use("a", "search_cms_facilities", {"name": "Zephyrdale"})),
        resp(tool_use("c", "submit_verdict", verdict_input(category="unresolved", confidence="low",
                                                          facility_name=None, ccn=None, owner_entity=None, evidence=[]))),
    ]
    client = FakeClient(script)
    verdict, trace = resolve_filing(FILING, StubTools(fail={"search_cms_facilities"}), client=client, model="m", effort="low")
    assert trace.tool_steps()[0].error.startswith("RuntimeError")
    sent = client.calls[1]["messages"][-1]["content"][0]
    assert sent.get("is_error") is True
    assert verdict["category"] == "unresolved"


def test_parallel_tool_calls_return_all_results_in_one_message():
    script = [
        resp(tool_use("a", "search_cms_facilities", {"name": "Zephyrdale"}),
             tool_use("b", "search_cms_facilities", {"name": "Zephyr"})),
        resp(tool_use("c", "submit_verdict", verdict_input(category="unresolved", confidence="low", evidence=[]))),
    ]
    client = FakeClient(script)
    resolve_filing(FILING, StubTools(), client=client, model="m", effort="low")
    results = client.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_runs_out_of_steps_becomes_unresolved_for_a_person():
    script = [resp(tool_use(str(i), "search_cms_facilities", {"name": "Z"})) for i in range(3)]
    verdict, trace = resolve_filing(FILING, StubTools(), client=FakeClient(script), model="m", effort="low", max_steps=3)
    assert verdict["category"] == "unresolved" and verdict["needs_human_review"] is True


def test_model_that_stops_without_verdict_is_nudged_once_then_unresolved():
    script = [resp(text("I think it's a refinancing."), stop="end_turn"), resp(text("Still thinking."), stop="end_turn")]
    client = FakeClient(script)
    verdict, trace = resolve_filing(FILING, StubTools(), client=client, model="m", effort="low")
    assert verdict["category"] == "unresolved"
    assert len(client.calls) == 2   # one nudge, then give up


def test_trace_saves_and_renders(tmp_path):
    verdict, trace = resolve_filing(FILING, StubTools(), client=FakeClient(happy_script()), model="m", effort="low")
    path = trace.save(tmp_path)
    assert path.exists() and path.name == "NY_T-1.json"
    out = trace.render_text()
    assert "VERDICT" in out and "search_cms_facilities" in out


def test_verdict_keeps_what_the_model_proposed_next_to_what_survived():
    script = happy_script(ccn="111111", facility_name="Totally Different Manor")
    verdict, _ = resolve_filing(FILING, StubTools(), client=FakeClient(script), model="m", effort="low")
    assert verdict["proposed"]["ccn"] == "111111" and verdict["ccn"] is None
    assert verdict["proposed"]["confidence"] == "high" and verdict["confidence"] == "low"
    assert verdict["proposed"]["needs_human_review"] is False and verdict["needs_human_review"] is True


def test_unresolved_is_always_low_confidence_and_for_a_person():
    script = [resp(tool_use("c", "submit_verdict", verdict_input(
        category="unresolved", confidence="medium", needs_human_review=False,
        facility_name=None, ccn=None, owner_entity=None, evidence=[])))]
    verdict, _ = resolve_filing(FILING, StubTools(), client=FakeClient(script), model="m", effort="low")
    assert verdict["confidence"] == "low" and verdict["needs_human_review"] is True
    assert verdict["proposed"]["confidence"] == "medium"      # what the model said is still on record


def test_a_name_only_link_is_capped_and_sent_to_a_person():
    # Epworth-style: the facility is real and the timing fits, but the debtor's tie to it is a shared name word
    verdict, _ = resolve_filing(FILING, StubTools(), client=FakeClient(happy_script(link_basis="name_similarity")), model="m", effort="low")
    assert verdict["ccn"] == "335999"                       # still reported, not hidden
    assert verdict["confidence"] == "medium" and verdict["needs_human_review"] is True
    assert any("not shown directly" in i for i in verdict["verification"]["issues"])
    assert verdict["proposed"]["link_basis"] == "name_similarity"


def test_missing_link_basis_is_treated_as_not_direct():
    script = happy_script()
    script[-1].content[0].input.pop("link_basis")
    verdict, _ = resolve_filing(FILING, StubTools(), client=FakeClient(script), model="m", effort="low")
    assert verdict["needs_human_review"] is True


def test_tool_sql_placeholders_are_valid_pyformat():
    """The SQL can't run offline, but a bad %-escape (the usual psycopg2 mistake) can be caught:
    every query must survive Python % formatting with its own params, and keep its trigram operators."""
    from agent.tools import ToolBox

    class Conn:
        def set_session(self, **kw): pass

    seen = []

    class TB(ToolBox):
        def _rows(self, sql, params=None):
            seen.append((sql, params))
            if params is not None:
                sql % (params if isinstance(params, dict) else tuple(f"'{p}'" for p in params))   # raises on a bad escape
            return []

    tb = TB(Conn())
    tb.call("search_cms_facilities", {"name": "North Dade", "state": "FL"})
    tb.call("search_cms_owners", {"owner_name": "Ventura", "state": "FL"})
    tb.call("get_related_ucc_filings", {"debtor_name": "X LLC"})
    tb.call("search_deals", {"name": "X"})
    fac_sql, fac_params = seen[0]
    assert "<%%" in fac_sql and "word_similarity" in fac_sql and fac_params["p"] == "%North Dade%"
    assert (fac_sql % {**fac_params, "n": "x"}).count("<%") == 2


def _pair_trace(debtor, company):
    t = Trace(filing={**FILING, "debtor_name": debtor}, model="m")
    t.add_tool("s1", "search_cms_facilities", {"name": "Zephyrdale"},
               {"matches": [{**FACILITY, "legal_business_name": company}]}, None, 5)
    t.add_tool("s2", "get_facility_ownership", {"ccn": "335999", "filing_date": "2025-06-01"},
               {"facility": FACILITY, "owners": [], "timing": {"cms_signal": None}}, None, 5)
    return t


def test_propco_opco_pair_rule():
    from agent.verify import propco_opco_pair
    assert propco_opco_pair("ZEPHYRDALE SNF REAL ESTATE COMPANY, LLC", "ZEPHYRDALE SNF OPERATING COMPANY, LLC")
    assert propco_opco_pair("Zephyrdale Realty LLC", "Zephyrdale Rehabilitation and Healthcare LLC")
    assert propco_opco_pair("ZEPHYR HC PROPCO RE LLC", "ZEPHYR HC, LLC")
    assert propco_opco_pair("ZEPHYR HILL 5185 REALTY LLC", "ZEPHYR HILL REHABILITATION AND HEALTHCARE LLC")
    assert not propco_opco_pair("Zephyrdale Properties, LLC", "198 Example Avenue LLC")   # only a shared word elsewhere
    assert not propco_opco_pair("Zeph Realty LLC", "Zeph Operating LLC")                   # core too short to trust
    assert not propco_opco_pair("Realty Holdings LLC", "Operating Company LLC")            # nothing left


def test_a_propco_opco_pair_passes_the_link_check_but_stays_medium():
    t = _pair_trace("ZEPHYRDALE SNF REAL ESTATE COMPANY, LLC", "ZEPHYRDALE SNF OPERATING COMPANY, LLC")
    v = verify_verdict(verdict_input(category="financing", link_basis="name_similarity", confidence="high",
                                     evidence=[{"claim": "operator", "step_id": "s1"}]), t)
    assert v["link_basis"] == "propco_opco_pair" and "zephyrdale snf" in v["link_rule"]
    assert v["confidence"] == "medium"
    assert not any("not shown directly" in i for i in v["verification"]["issues"])
    assert v["proposed"]["link_basis"] == "name_similarity"


def test_a_shared_word_that_is_not_a_pair_still_goes_to_a_person():
    t = _pair_trace("ZEPHYRDALE PROPERTIES, LLC", "198 EXAMPLE AVENUE LLC")
    v = verify_verdict(verdict_input(category="financing", link_basis="name_similarity",
                                     evidence=[{"claim": "operator", "step_id": "s1"}]), t)
    assert v["link_basis"] == "name_similarity" and v["needs_human_review"] is True


def test_an_empty_verdict_is_an_abstention_not_a_pass():
    # seen on a real run (OH OH00295739949): submit_verdict arrived with no fields at all
    verdict, trace = resolve_filing(FILING, StubTools(), client=FakeClient(happy_script()), model="m", effort="low")
    v = verify_verdict({}, trace)
    assert v["category"] == "unresolved" and v["confidence"] == "low"
    assert v["needs_human_review"] is True and v["verification"]["passed"] is False
