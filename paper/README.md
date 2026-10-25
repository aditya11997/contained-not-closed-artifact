# Paper: *Contained Is Not Closed*

Primary venue AGENT 2027 (ICSE workshop): ≤ 8 pages **including** references, IEEEtran conference 10pt, single-anonymous.
Fallback ICRITO 2027: ≤ 6 pages including references, double-blind.

## Build
Needs a TeX distribution with the IEEEtran class (MacTeX includes it), `latexmk`, and poppler (`brew install poppler`).
`IEEEtran.cls` (V1.8b, 2015/08/26) and `IEEEtran.bst` (V1.14, 2015/08/26) in this directory are vendored, unmodified copies (LPPL) from TeX Live 2026, so every build uses the same IEEE class.

```
make -C paper            # AGENT profile  -> paper/build/main-agent.pdf
make -C paper icrito     # anonymized ICRITO profile -> paper/build/main-icrito.pdf
make -C paper check      # builds AGENT, then checks: page limit (8), fonts, citations, banned words, typed numbers
make -C paper check VENUE=icrito   # same for ICRITO (6 pp, plus identity-leak check)
make -C paper check-all  # both venues
make -C paper lint       # source-only checks (no PDF needed)
make -C paper final      # markers (\MISSING, \CONFIRM, \CITEPENDING, \TODO) become hard errors
```

`check` builds the venue it checks (AGENT unless `VENUE=icrito`), so the page limit never comes from a stale build. The Makefile uses `../.venv/bin/python` when it exists. It fails (exit ≠ 0) on:
- more pages than the venue allows;
- a font that is not embedded or is Type 3;
- a `\cite` key missing from `refs.bib` or from `docs/REFERENCES_VERIFIED.md`;
- a banned word or phrase;
- `\ifanon` / `\ificrito` / `\iffinal` inside a section file;
- identifying strings in the anonymized PDF.

Hand-typed numbers produce a warning, and fail under `--final`.

## Rules
- **Numbers come only from `paper/generated/`** (written by `make paper` at the repo root from `analysis/`). Never edit generated files. A missing number is written `\MISSING{what, which script}` and fixed in the analysis code.
- `macros-generated.tex` is written by `scripts/collect_macros.py`. It fails on conflicting macro definitions, and skips a generated file that is an identical subset of another (today: `stripe.tex` ⊂ `numbers.tex`).
- Identifying information lives only in `meta.tex`. Section files never test the venue.
- A literal with digits that is not a measured number (e.g. notation) goes in `\lit{...}`.
- Figures: `paper/figs/` (from `analysis/figures.py`) plus `fig1_timeline.pdf`, copied from `results/case_study/figures/` at build time.
- Section order: Related Work comes after the evidence (Section VII), with a closest-work paragraph in the Introduction.
