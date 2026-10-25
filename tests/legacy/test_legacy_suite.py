"""Guards CODE_AUDIT E1.

`python research_code/test_lab.py` stops at the misplaced `unittest.main()` and runs only
7 of the 10 tests. Discovery imports the whole module, so all 10 must run and pass.
"""
import io
import unittest
from pathlib import Path

RESEARCH_CODE = Path(__file__).resolve().parents[2] / "research_code"
EXPECTED_TESTS = 10


def test_legacy_unittest_suite_runs_all_10_and_passes():
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(RESEARCH_CODE), pattern="test_lab.py",
                            top_level_dir=str(RESEARCH_CODE))
    assert not loader.errors, loader.errors
    assert suite.countTestCases() == EXPECTED_TESTS

    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    assert result.testsRun == EXPECTED_TESTS, stream.getvalue()
    assert not result.skipped, stream.getvalue()
    assert result.wasSuccessful(), stream.getvalue()
