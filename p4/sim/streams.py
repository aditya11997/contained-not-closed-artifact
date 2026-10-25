"""Random-stream identifiers and a process-stable uid hash.

Every generator is `numpy.random.default_rng([seed, STREAM_*, ...])`, so no two components
share draws and adding draws in one component never shifts another.
"""
import hashlib

STREAM_SCENARIO = 1
STREAM_FAULT = 2
STREAM_OUTAGE = 3
STREAM_AGENT = 4   # WP6: capture timing of agent-created payments, keyed by creation index

# Sub-streams of STREAM_SCENARIO: observations stay identical when hidden models change.
SUB_OBSERVED = 0
SUB_JITTER = 1
SUB_LABEL = 2


def stable_hash(text: str) -> int:
    """63-bit hash that is identical across processes (Python's hash() is salted)."""
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") >> 1
