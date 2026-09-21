"""Jev (TypeSafe System One) comparison harness.

Measures two decision points against the rule-based implementations a team
would ship without a decision model:

    routing  (choice)  vs  weighted keyword matching
    gate     (noul)    vs  regex denylist

Run from ``agents/python``::

    TYPESAFE_API_KEY=... python -m evals.jev_comparison.runner

See ``results/`` for output and ``../../shared/jev/README.md`` for the
integration notes and the list of things this does not claim.
"""
