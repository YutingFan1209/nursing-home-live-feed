"""New cards the judge flags are held out of the export (evals/holds.py)."""
import json

import evals.holds as H


def test_hold_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "JUDGED", tmp_path / "j.jsonl")
    monkeypatch.setattr(H, "HOLDS", tmp_path / "h.json")
    monkeypatch.setattr(H, "RELEASED", tmp_path / "r.jsonl")
    H.JUDGED.write_text("\n".join(json.dumps(r) for r in [
        {"id": "right1", "verdict": "right"}, {"id": "wrong1", "verdict": "wrong"},
        {"id": "minor1", "verdict": "minor"}, {"id": "dup1", "verdict": "duplicate"},
        {"id": "freed", "verdict": "wrong"}]) + "\n")
    H.RELEASED.write_text(json.dumps({"id": "freed"}) + "\n")
    export = {"deals": [{"id": i} for i in ["right1", "wrong1", "minor1", "dup1", "freed", "nojudge", "live"]]
              + [{"id": "member", "grouped_into": "right1"}]}
    cards = H.new_cards(export, live_ids={"live"})
    assert [c["id"] for c in cards] == ["right1", "wrong1", "minor1", "dup1", "freed", "nojudge"]
    holds = H.check(cards, export["deals"], max_cost=0, spend=False)    # never calls the API
    assert set(holds) == {"wrong1", "dup1", "nojudge"}                   # unchecked is held, not shipped
    assert holds["nojudge"]["verdict"] == "unchecked"
    H.save(holds)
    assert H.held_ids() == {"wrong1", "dup1", "nojudge"}
