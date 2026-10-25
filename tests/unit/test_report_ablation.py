"""late_cancel ablation matches each late_cancel = false cell to the late_cancel = true cell with
the same other factors, in the secondary run itself (j = 6 block, G1) or else in the primary."""
import pandas as pd

from analysis.report import late_cancel_ablation
from analysis.stats import EVIDENCE_BOUNDED

BASE = {"fault": "high", "slack": "tight", "k": "1", "n": 32, "score": "calibrated", "c": 0.5}


def rows(cell_id, jitter, late_cancel, loss):
    return [{"cell_id": cell_id, **BASE, "jitter": jitter, "late_cancel": late_cancel, "seed": s,
             "policy": p, "loss": loss + s} for s in (1, 2, 3) for p in EVIDENCE_BOUNDED]


def test_reference_found_in_secondary_and_primary():
    secondary = pd.DataFrame(rows("j6on", "uniform6", True, 100.0) + rows("j6off", "uniform6", False, 130.0)
                             + rows("j3off", "uniform3", False, 50.0))
    primary = pd.DataFrame(rows("j3on", "uniform3", True, 40.0))
    abl = late_cancel_ablation(secondary, primary).set_index(["jitter", "policy"])["mean_diff"]
    assert len(abl) == 6
    assert all(abl.loc[("uniform6", p)] == 30.0 for p in EVIDENCE_BOUNDED)
    assert all(abl.loc[("uniform3", p)] == 10.0 for p in EVIDENCE_BOUNDED)
