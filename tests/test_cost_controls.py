"""--only/--ids subsets, --batch, usage logging, prices (cost analysis 2026-11-18). No API calls."""
import json
from types import SimpleNamespace as NS

from evals import runs


def test_read_ids_lines_prefixes_and_json(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("# tuning set\n6de5a0cd  lender case\n90ee2200\n")
    assert runs.read_ids(str(p)) == ["6de5a0cd", "90ee2200"]
    p.write_text('["abc", "def"]')
    assert runs.read_ids(str(p)) == ["abc", "def"]
    items = [{"id": "6de5a0cd-1111"}, {"id": "90ee2200-2222"}, {"id": "ffff"}]
    assert [x["id"] for x in runs.pick(items, ["6de5a0cd", "90ee2200", "zzzz"])] == ["6de5a0cd-1111", "90ee2200-2222"]


class FakeBatches:
    def __init__(self):
        self.created = []
        self.polls = 0

    def create(self, requests):
        self.created.append(requests)
        return NS(id="msgbatch_1")

    def retrieve(self, bid):
        self.polls += 1
        status = "ended" if self.polls > 1 else "in_progress"
        return NS(processing_status=status, request_counts=NS(processing=1, succeeded=0, errored=0))

    def results(self, bid):
        msg = NS(usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=0),
                 stop_reason="end_turn", content=[NS(type="text", text="[]")])
        return [NS(custom_id="a_r1", result=NS(type="succeeded", message=msg)),
                NS(custom_id="b_r1", result=NS(type="errored", error="overloaded"))]


def test_run_batch_submits_once_and_resumes_by_saved_id(tmp_path):
    client = NS(messages=NS(batches=FakeBatches()))
    state = tmp_path / "x.batch"
    out = runs.run_batch(client, {"a_r1": {"model": "m"}, "b_r1": {"model": "m"}}, state, poll=0)
    assert list(out) == ["a_r1"] and len(client.messages.batches.created) == 1
    assert not state.exists()                          # cleared once the results are in
    state.write_text("msgbatch_1")                     # an interrupted wait
    client.messages.batches.polls = 0
    runs.run_batch(client, {"a_r1": {}}, state, poll=0)
    assert len(client.messages.batches.created) == 1   # picked the batch back up, didn't pay again


def test_extraction_usage_is_recorded_and_priced(monkeypatch):
    from pipeline import extractor as X
    monkeypatch.setattr(X, "USAGE", {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0})
    msg = NS(usage=NS(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=1_000_000,
                      cache_creation_input_tokens=0),
             stop_reason="end_turn", content=[NS(type="text", text='[{"acquiring_entity": "X"}]')])
    assert X.parse_response(msg, "u") == [{"acquiring_entity": "X"}]
    assert X.USAGE["calls"] == 1 and X.USAGE["cache_read"] == 1_000_000
    # Sonnet 5.5: $2 input + $0.10 cache read + $1 output
    assert abs(X.usage_cost(X.USAGE, "claude-sonnet-5-5") - 3.10) < 1e-9
    X.parse_response(msg, "u", batch=True)
    assert abs(X.usage_cost(X.USAGE, "claude-sonnet-5-5") - 3.10 * 1.5) < 1e-9    # second call at half


def test_request_params_cache_the_instructions_not_the_article():
    from pipeline import extractor as X
    p = X.request_params("ARTICLE TEXT", "2026-11-18")
    first, second = p["messages"][0]["content"]
    assert first["cache_control"] == {"type": "ephemeral"} and "ARTICLE TEXT" not in first["text"]
    assert "2026-11-18" not in first["text"] and "2026-11-18" in second["text"] and "cache_control" not in second


def test_prices_are_current():
    from evals import judge, score
    assert (score.PRICE_IN, score.PRICE_OUT) == (2.00, 10.00)          # Sonnet 5.5
    assert (judge.PRICE_IN, judge.PRICE_OUT, judge.PRICE_CACHE_READ) == (4.00, 20.00, 0.20)   # Opus 5.5


def test_async_extraction_warms_the_cache_with_one_call_first(monkeypatch):
    import asyncio
    import main
    from pipeline import extractor as X
    monkeypatch.setattr(X, "USAGE", {**X.USAGE, "calls": 0})
    order, inflight, peak = [], [0], [0]

    def fake(article):
        inflight[0] += 1
        peak[0] = max(peak[0], inflight[0])
        order.append(article["id"])
        if article["id"] != "short":        # a too-short article never reaches Claude
            X.USAGE["calls"] += 1
        inflight[0] -= 1
        return (article, None, [])

    monkeypatch.setattr(main, "_fetch_and_extract", fake)
    arts = [{"id": "short"}, {"id": "a"}, {"id": "b"}, {"id": "c"}]
    out = asyncio.run(main._batch_fetch_extract(arts))
    assert [o[0]["id"] for o in out] == ["short", "a", "b", "c"]     # order kept
    assert order[:2] == ["short", "a"]                               # sequential until a call happened
