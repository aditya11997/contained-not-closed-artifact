"""Make the frozen legacy pilot (research_code/p4lab) importable for the legacy tests."""
import sys
from pathlib import Path

RESEARCH_CODE = Path(__file__).resolve().parents[2] / "research_code"

if str(RESEARCH_CODE) not in sys.path:
    sys.path.insert(0, str(RESEARCH_CODE))
