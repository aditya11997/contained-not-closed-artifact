"""Independent checker (WP3): grades a run from its exported files alone.

It reads events.jsonl, report.json and truth.json and imports nothing from `p4` except
`p4.schema` (tests/leakage). It re-derives the sink's semantics from the log instead of
calling simulator code, so a bug in the sink or a runbook cannot grade itself.
"""
