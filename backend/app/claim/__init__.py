"""
Claim pipeline: every document of one claim, uploaded together, decided once.

    ingest (queue, one page per pass) -> plan -> [kyc || claims -> policy] -> fraud -> decide

See graph.py for the wiring and WORKFLOW.md for the rules.
"""
