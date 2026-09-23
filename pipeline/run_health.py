"""
pipeline/run_health.py

Makes a run's failures change its outcome. Until 2026-09-23 every failure
handler logged a warning and carried on, so runs that lost whole sources
(RSS feeds failing SSL for months, OH searches blocked but logged as
"0 filings") still ended "Pipeline complete" with exit code 0.

Failure points report here, and main.py exits non-zero when any problem
is found, after committing whatever did succeed. Two kinds of problem:

- source_failed: a whole feed, state or pipeline step produced nothing
  because of an error. Always fails the run.
- attempted/failed: per-item work (article fetches, Claude calls, UCC name
  searches). A few failures are normal (paywalls, 403s); the run fails when
  the failure rate or count says something systemic is wrong.

Anything noted with note() is printed in the summary without failing.
"""
import logging
from collections import Counter

logger = logging.getLogger(__name__)

MAX_FAILURE_RATE = 0.25
MAX_FAILURES = 10
EXIT_UNHEALTHY = 2


class RunHealth:
    def __init__(self):
        self.reset()

    def reset(self):
        self.source_failures: list[str] = []
        self.attempts = Counter()
        self.failures = Counter()
        self.examples: dict[str, str] = {}
        self.notes: list[str] = []

    def source_failed(self, source: str, detail) -> None:
        self.source_failures.append(f"{source}: {detail}")

    def attempted(self, kind: str, n: int = 1) -> None:
        self.attempts[kind] += n

    def failed(self, kind: str, detail) -> None:
        self.failures[kind] += 1
        self.examples.setdefault(kind, str(detail)[:200])

    def note(self, message: str) -> None:
        self.notes.append(message)

    def problems(self) -> list[str]:
        problems = list(self.source_failures)
        for kind, n in self.failures.items():
            # attempts can be unknown (0) for kinds where any failure is bad
            total = max(self.attempts[kind], n)
            if n >= MAX_FAILURES or n / total >= MAX_FAILURE_RATE:
                problems.append(f"{kind}: {n} of {total} failed (e.g. {self.examples[kind]})")
        return problems

    def report(self) -> int:
        """Log the summary; return the process exit code."""
        problems = self.problems()
        tolerated = [f"{k}: {n} of {max(self.attempts[k], n)} failed" for k, n in self.failures.items()
                     if not any(p.startswith(f"{k}:") for p in problems)]
        for msg in self.notes + tolerated:
            logger.info(f"RUN HEALTH (ok): {msg}")
        for p in problems:
            logger.error(f"RUN HEALTH: {p}")
        if problems:
            logger.error(f"=== Run finished with {len(problems)} problem(s) — exit {EXIT_UNHEALTHY} ===")
            return EXIT_UNHEALTHY
        logger.info("=== Run healthy ===")
        return 0


health = RunHealth()
