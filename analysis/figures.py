"""Paper figures F3-F6 as vector PDFs (WP4).

One fixed colour and marker per policy across every figure (colour follows the policy, never
its rank). Colours are the dataviz reference categorical palette (light mode, validated as a
set with scripts/validate_palette.js); markers are the secondary encoding for print and
colour-vision deficiency, and series are direct-labelled. PDFs carry no creation date, so
re-running on the same inputs gives the same files.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .stats import wilson
from .tables import POLICY_LABEL

STYLE = {  # policy -> (colour, marker)
    "lm_ack": ("#2a78d6", "o"),
    "lm_reconcile": ("#eb6834", "s"),
    "adaptive": ("#1baf7a", "D"),
    "edf": ("#eda100", "v"),
    "mh": ("#e87ba4", "P"),
    "lm_verify": ("#6250d6", "^"),
    "none": ("#8a8984", "X"),
}
ORDER = ("none", "edf", "mh", "lm_ack", "lm_verify", "lm_reconcile", "adaptive")  # legend order
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
FAULT_ORDER = ("none", "low", "high")
COL1, COL2 = 3.5, 7.16   # IEEE column and page widths, inches

plt.rcParams.update({
    "font.size": 7, "axes.titlesize": 7.5, "axes.labelsize": 7, "xtick.labelsize": 6.5,
    "ytick.labelsize": 6.5, "legend.fontsize": 6.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5, "lines.linewidth": 1.4,
    "lines.markersize": 4.5, "pdf.fonttype": 42, "legend.frameon": False,
})


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)
    return path


def _err(mid, lo, hi) -> np.ndarray:
    """Error-bar lengths, clipped at 0 (float noise when lo == mid == hi)."""
    return np.clip(np.vstack([np.asarray(mid) - np.asarray(lo), np.asarray(hi) - np.asarray(mid)]), 0, None)


def _faults(df) -> list[str]:
    present = set(df["fault"])
    return [f for f in FAULT_ORDER if f in present] + sorted(present - set(FAULT_ORDER))


def _cell_label(r) -> str:
    k = "∞" if str(r["k"]) == "inf" else str(r["k"])
    return f"{r['slack']}, k={k}, n={r['n']}"


def f3_paired_vs_p5(table: pd.DataFrame, path: Path) -> Path:
    """Paired loss difference (policy − P5) by cell: dot + 95% CI, one panel per fault level."""
    faults = _faults(table)
    fig, axes = plt.subplots(1, len(faults), figsize=(COL2, 3.0), sharey=True, squeeze=False)
    policies = list(dict.fromkeys(table["a"]))
    offsets = np.linspace(-0.25, 0.25, len(policies)) if len(policies) > 1 else [0.0]
    for ax, fault in zip(axes[0], faults):
        sub = table[table["fault"] == fault]
        labels = list(dict.fromkeys(_cell_label(r) for _, r in sub.iterrows()))
        ypos = {lab: i for i, lab in enumerate(labels)}
        ax.axvline(0, color=MUTED, linewidth=0.8)
        for off, pol in zip(offsets, policies):
            rows = sub[sub["a"] == pol]
            y = np.array([ypos[_cell_label(r)] for _, r in rows.iterrows()]) + off
            colour, marker = STYLE[pol]
            ax.errorbar(rows["mean_diff"], y, xerr=_err(rows["mean_diff"], rows["ci_lo"], rows["ci_hi"]),
                        fmt=marker, color=colour, ecolor=colour, elinewidth=1.0, capsize=0,
                        markeredgecolor="white", markeredgewidth=0.5, label=f"{POLICY_LABEL[pol]} − P5")
        ax.set_yticks(range(len(labels)), labels)
        ax.invert_yaxis()
        ax.set_title(f"fault = {fault}")
        ax.set_xlabel("paired Δ loss vs P5 (95% CI)")
        ax.grid(axis="y", visible=False)
    axes[0][0].legend(loc="upper center", bbox_to_anchor=(0.5 * len(faults), 1.18), ncol=len(policies),
                      handletextpad=0.3, columnspacing=1.2)
    return _save(fig, path)


def _by_fault(fc: pd.DataFrame) -> pd.DataFrame:
    agg = fc.assign(loss_sum=fc["mean_loss"] * fc["runs"]).groupby(["fault", "policy"], sort=False).agg(
        fcs=("false_closures", "sum"), payments=("payments", "sum"), runs=("runs", "sum"),
        loss_sum=("loss_sum", "sum")).reset_index()
    agg["rate"] = agg["fcs"] / agg["payments"]
    agg["mean_loss"] = agg["loss_sum"] / agg["runs"]
    ci = [wilson(int(a), int(b)) for a, b in zip(agg["fcs"], agg["payments"])]
    agg["lo"], agg["hi"] = [c[0] for c in ci], [c[1] for c in ci]
    return agg


def f4_false_closure(fc: pd.DataFrame, path: Path) -> Path:
    """False closures per payment vs fault level (Wilson 95% CI); P4-P6 sit at exactly zero."""
    agg = _by_fault(fc)
    faults = _faults(agg)
    x = {f: i for i, f in enumerate(faults)}
    fig, ax = plt.subplots(figsize=(COL1, 2.3))
    policies = [p for p in ORDER if p in set(agg["policy"]) and p != "none"]
    offsets = np.linspace(-0.18, 0.18, len(policies))
    for off, pol in zip(offsets, policies):
        rows = agg[agg["policy"] == pol]
        xs = np.array([x[f] for f in rows["fault"]]) + off
        colour, marker = STYLE[pol]
        ax.errorbar(xs, 100 * rows["rate"], yerr=100 * _err(rows["rate"], rows["lo"], rows["hi"]),
                    fmt=marker + "-", color=colour, elinewidth=0.9, capsize=0, linewidth=1.0,
                    markeredgecolor="white", markeredgewidth=0.5, label=POLICY_LABEL[pol])
    zero = agg[agg["policy"].isin(("lm_verify", "lm_reconcile", "adaptive"))]
    if len(zero) and int(zero["fcs"].sum()) == 0:
        ax.annotate(f"P4–P6: 0 false closures in {int(zero['runs'].sum()):,} runs", xy=(0.02, 0.92),
                    xycoords="axes fraction", color=MUTED, fontsize=6.5)
    ax.set_xticks(range(len(faults)), faults)
    ax.set_xlabel("transport fault level")
    ax.set_ylabel("false closures per payment (%)")
    ax.set_ylim(bottom=-0.05 * max(0.01, 100 * agg["hi"].max()))
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), handletextpad=0.3)
    ax.grid(axis="x", visible=False)
    return _save(fig, path)


def f5_frontier(fc: pd.DataFrame, path: Path) -> Path:
    """Cost of assurance: false-closure rate vs mean loss per policy, one panel per fault level."""
    agg = _by_fault(fc)
    faults = _faults(agg)
    fig, axes = plt.subplots(1, len(faults), figsize=(COL2, 2.4), sharey=True, squeeze=False)
    for ax, fault in zip(axes[0], faults):
        rows = agg[agg["fault"] == fault].set_index("policy").loc[
            [p for p in ORDER if p in set(agg.loc[agg["fault"] == fault, "policy"])]].reset_index()
        yspan = max(1.0, float(agg["mean_loss"].max() - agg["mean_loss"].min()))
        right = max(0.1, 100 * float(rows["rate"].max()))
        groups: list[list] = []  # nearby points share one label, e.g. "P4,P5,P6"
        for r in rows.itertuples(index=False):
            colour, marker = STYLE[r.policy]
            ax.plot(100 * r.rate, r.mean_loss, marker, color=colour, markersize=5.5,
                    markeredgecolor="white", markeredgewidth=0.6)
            near = [g for g in groups if abs(100 * (g[0].rate - r.rate)) < 0.06 * right
                    and abs(g[0].mean_loss - r.mean_loss) < 0.05 * yspan]
            if near:
                near[0].append(r)
            else:
                groups.append([r])
        for members in groups:
            r = members[0]
            ax.annotate(",".join(POLICY_LABEL[m.policy].split()[0] for m in members),
                        (100 * r.rate, r.mean_loss), xytext=(5, 2), textcoords="offset points",
                        fontsize=6, color=INK)
        ax.set_xlim(-0.06 * right, 1.3 * right)
        ax.set_title(f"fault = {fault}")
        ax.set_xlabel("false closures per payment (%)")
    axes[0][0].set_ylabel("mean loss per run")
    return _save(fig, path)


def f6_control_scaling(neg: pd.DataFrame | None, timing: pd.DataFrame | None, path: Path) -> Path:
    """(a) negative control: largest |loss difference| among P3-P6 per cell (expected 0);
    (b) scaling: median wall time per episode vs n, log-log."""
    fig, axes = plt.subplots(1, 2, figsize=(COL2, 2.3))
    ax = axes[0]
    if neg is not None and len(neg):
        labels = [f"{r['slack']}, n={r['n']}" for _, r in neg.iterrows()]
        ax.plot(neg["max_abs_diff"], range(len(neg)), STYLE["lm_ack"][1], color=STYLE["lm_ack"][0])
        ax.set_yticks(range(len(neg)), labels)
        ax.invert_yaxis()
        ax.axvline(0, color=MUTED, linewidth=0.8)
        ax.set_xlim(-0.5, max(1.0, float(neg["max_abs_diff"].max()) * 1.2))
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
        eq, tot = int(neg["seeds_all_equal"].sum()), int(neg["seeds"].sum())
        ax.annotate(f"P3 = P4 = P5 = P6 on {eq:,}/{tot:,} seeds", xy=(0.02, -0.32), xycoords="axes fraction",
                    color=MUTED, fontsize=6.5)
    else:
        ax.text(0.5, 0.5, "no negative-control run", ha="center", va="center", transform=ax.transAxes, color=MUTED)
    ax.set_title("(a) negative control (k = ∞, no faults/jitter)")
    ax.set_xlabel("max |Δ loss| among P3–P6 per seed")
    ax = axes[1]
    if timing is not None and len(timing):
        med = timing.groupby(["policy", "n"])["wall_ms"].median().reset_index()
        for pol in [p for p in ORDER if p in set(med["policy"])]:
            rows = med[med["policy"] == pol].sort_values("n")
            colour, marker = STYLE[pol]
            ax.plot(rows["n"], rows["wall_ms"], marker + "-", color=colour, label=POLICY_LABEL[pol],
                    markeredgecolor="white", markeredgewidth=0.5)
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_xticks(sorted(set(med["n"])), [str(n) for n in sorted(set(med["n"]))])
        ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), handletextpad=0.3)
    else:
        ax.text(0.5, 0.5, "no scaling run", ha="center", va="center", transform=ax.transAxes, color=MUTED)
    ax.set_title("(b) scaling")
    ax.set_xlabel("payments n")
    ax.set_ylabel("median wall time per episode (ms)")
    fig.tight_layout(w_pad=2.5)
    return _save(fig, path)
