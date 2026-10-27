"""
Offline tests for agent/simulator: the world is reproducible and its known
answers are graded as intended. No DB or API key needed.

Run from the project root:  venv/bin/python3 -m pytest agent/tests/test_simulator.py
"""
from agent.simulator.score import grade
from agent.simulator.world import CASE_KINDS, build_world


def test_same_seed_same_world():
    a, b = build_world(3), build_world(3)
    assert [c.filing for c in a.cases] == [c.filing for c in b.cases]
    assert [h.ccn for h in a.homes] == [h.ccn for h in b.homes]


def test_every_kind_appears_and_ccns_are_unique():
    w = build_world(7)
    assert {c.kind for c in w.cases} == set(CASE_KINDS)
    assert len({h.ccn for h in w.homes}) == len(w.homes)


def test_cases_are_tied_to_the_world():
    w = build_world(7)
    ccns = {h.ccn for h in w.homes} | {None}
    for c in w.cases:
        assert c.ccn_ok <= ccns
        assert c.filing in w.filings


def test_grade():
    w = build_world(7)
    acq = next(c for c in w.cases if c.kind == "acq_exact")
    twin = next(c for c in w.cases if c.kind == "twin_homes")
    (ccn,) = acq.ccn_ok
    assert grade({"category": "acquisition_financing", "ccn": ccn}, acq) == "right"
    assert grade({"category": "refinancing", "ccn": ccn}, acq) == "confident_wrong"
    assert grade({"category": "refinancing", "ccn": ccn, "needs_human_review": True}, acq) == "flagged_wrong"
    assert grade({"category": "unresolved"}, acq) == "abstain"
    # twins: "can't tell" is the right answer, and only with a person asked to look
    assert grade({"category": "unresolved", "needs_human_review": True}, twin) == "right"
    assert grade({"category": "unresolved"}, twin) == "abstain"
