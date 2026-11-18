"""
Shared by the paid eval commands (evals.judge, evals.score extract):

  read_ids(path)    --only / --ids: a file of card ids, one per line (or a
                    JSON list); 8-character prefixes are fine. While tuning a
                    prompt, re-run just the cards a change can affect and do
                    one full run at the end (cost analysis 2026-11-18: three
                    full 150-card judge runs 10-15 minutes apart).

  run_batch(...)    --batch: the same requests through the Message Batches
                    API at half price. Most batches finish within the hour
                    (24 h at most), so it's for measurements nobody waits on,
                    never the deploy-time check. The batch id is saved next to
                    the output file, so an interrupted wait picks the same
                    batch back up instead of paying for a new one.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def read_ids(path: str) -> list[str]:
    text = Path(path).read_text().strip()
    if text.startswith("["):
        return [str(x).strip() for x in json.loads(text) if str(x).strip()]
    return [line.split()[0] for line in text.splitlines() if line.strip() and not line.startswith("#")]


def pick(items: list[dict], ids: list[str], key=lambda x: x["id"]) -> list[dict]:
    """The items whose id is, or starts with, one of ids. Unmatched ids are reported."""
    out = [x for x in items if any(key(x).startswith(i) for i in ids)]
    missing = [i for i in ids if not any(key(x).startswith(i) for x in items)]
    if missing:
        print(f"{len(missing)} id(s) not found: {', '.join(m[:8] for m in missing)}", file=sys.stderr)
    return out


def run_batch(client, requests: dict[str, dict], state: Path, poll: int = 60) -> dict[str, object]:
    """requests: custom_id -> Messages API params (no stream/fallbacks).
    Returns custom_id -> the result Message; errored requests are reported and left out."""
    from anthropic.types.messages.batch_create_params import Request

    batch_id = state.read_text().strip() if state.exists() else None
    if batch_id:
        print(f"Resuming batch {batch_id}", file=sys.stderr)
    else:
        b = client.messages.batches.create(
            requests=[Request(custom_id=cid, params=p) for cid, p in requests.items()])
        batch_id = b.id
        state.write_text(batch_id)
        print(f"Batch {batch_id}: {len(requests)} requests submitted (half price; usually under an hour)",
              file=sys.stderr)
    while True:
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        c = b.request_counts
        print(f"  {time.strftime('%H:%M')} processing {c.processing}, done {c.succeeded}, errored {c.errored}",
              file=sys.stderr)
        time.sleep(poll)
    out, errors = {}, 0
    for r in client.messages.batches.results(batch_id):
        if r.result.type == "succeeded":
            out[r.custom_id] = r.result.message
        else:
            errors += 1
            print(f"  {r.custom_id[:12]} {r.result.type}: {getattr(r.result, 'error', '')}", file=sys.stderr)
    print(f"Batch {batch_id} ended: {len(out)} succeeded, {errors} not", file=sys.stderr)
    state.unlink(missing_ok=True)
    return out
