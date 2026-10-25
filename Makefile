PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: test test-legacy audit lint hooks smoke golden results results-dev paper

test:
	$(PYTHON) -m pytest -q

test-legacy:
	$(PYTHON) -m pytest -q tests/legacy

audit:
	@for f in scripts/audit_*.py; do echo "== $$f"; $(PYTHON) $$f || exit 1; done

lint:
	@if command -v ruff >/dev/null 2>&1; then ruff check .; \
	elif $(PYTHON) -m ruff --version >/dev/null 2>&1; then $(PYTHON) -m ruff check .; \
	else echo "lint: ruff not installed, skipping ruff"; fi
	$(PYTHON) scripts/check_secrets.py

# Installs a local (unversioned) git pre-commit hook that runs the secret scan.
hooks:
	printf '#!/bin/sh\nexec $(PYTHON) scripts/check_secrets.py\n' > .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

# WP4. Smoke: configs/smoke.toml on 20 dev seeds (throwaway output, not evidence).
smoke:
	rm -rf results/smoke
	$(PYTHON) -m experiments.run --config configs/smoke.toml --partition dev --out results/smoke --no-keep-runs
	cmp results/smoke/trials.csv tests/golden/golden_trials.csv

# Regenerate the golden file deliberately; log why in docs/CHANGELOG_RESEARCH.md.
golden:
	rm -rf results/smoke
	$(PYTHON) -m experiments.run --config configs/smoke.toml --partition dev --out results/smoke --no-keep-runs
	cp results/smoke/trials.csv tests/golden/golden_trials.csv

# Every paper table, macro (paper/generated/numbers.tex) and figure from results/holdout/*, and the
# pre-registered verdicts (verdicts.md). C1: make results CONFORMANCE=results/conformance/stripe-<time>
# After a deviation: PRE_FIX=<the superseded report's verdicts.json> reports it alongside.
results:
	@test -d results/holdout || { echo "results: no results/holdout (holdout runs need the prereg-v1 tag)"; exit 1; }
	$(PYTHON) -m analysis.report --results results/holdout --out paper/generated --figs paper/figs $(if $(CONFORMANCE),--conformance $(CONFORMANCE)) $(if $(PRE_FIX),--pre-fix-verdicts $(PRE_FIX))

# The same pipeline on a dev run directory (or a directory of runs): make results-dev RUN=results/dev/primary
results-dev:
	@test -n "$(RUN)" || { echo "usage: make results-dev RUN=results/dev/<run or dir of runs>"; exit 1; }
	$(PYTHON) -m analysis.report --results $(RUN) --out $(RUN)/analysis

# Every generated paper input: `make results` (with the official C1 run and one-column contrast
# tables), the post-hoc macros (posthoc.tex, from the committed CSVs), the case-study macros
# (case_study.tex), the holdout hash check (repro.tex; fails if a file is missing), and then the
# check that every macro of numbers.tex on $(MACRO_REF) is unchanged.
C1_RUN ?= results/conformance/stripe-20261024T024751Z
MACRO_REF ?= main
paper:
	@test -d results/holdout || { echo "paper: no results/holdout (holdout runs need the prereg-v1 tag)"; exit 1; }
	$(PYTHON) -m analysis.report --results results/holdout --out paper/generated --figs paper/figs --conformance $(C1_RUN) --narrow
	$(PYTHON) -m analysis.posthoc.macros --posthoc paper/generated/posthoc --contrasts paper/generated/contrasts_primary.csv --out paper/generated/posthoc.tex
	$(PYTHON) -m agent.summarize --tex-only paper/generated/case_study.tex
	$(PYTHON) scripts/verify_archive.py --tex paper/generated/repro.tex
	$(PYTHON) -m analysis.design_macros --out paper/generated/design.tex
	$(PYTHON) scripts/check_old_macros.py --ref $(MACRO_REF) --file paper/generated/numbers.tex
