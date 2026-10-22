"""
agent/ -- UCC filing resolver (agentic workshop demo).

Separate from the production pipeline: nothing in main.py imports this.
Given one UCC-1 filing that the rule-based pipeline could not tie to a
facility, a Claude tool-use loop decides which lookups to run, and a
verification step (verify.py) checks the verdict against what the tools
actually returned before it is accepted.

    python -m agent.run --selftest
    python -m agent.run --state PA --filing-number <number>
    python -m agent.run --sample 5 --state PA

See agent/README.md.
"""
