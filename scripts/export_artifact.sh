#!/usr/bin/env bash
# Export a clean public snapshot of the committed tree (no git history) for the artifact repo.
#   scripts/export_artifact.sh <out_dir> [ref]
# Keeps code, configs, tests, results, paper sources and the research record (docs/). Drops
# internal working files: agent instruction files, the superseded handoff README, work-package
# prompts, schedules and plans. Replaces README.md with artifact/README.md and adds LICENSE.
# The result is a fresh git repo with one commit; push it to the public artifact repository.
set -euo pipefail
out="${1:?usage: scripts/export_artifact.sh <out_dir> [ref]}"
ref="${2:-HEAD}"
[ -e "$out" ] && { echo "export_artifact: $out exists; choose a new directory" >&2; exit 1; }
mkdir -p "$out"
git archive --format=tar "$ref" | tar -x -C "$out"
cd "$out"
# Code comments cite "CLAUDE.md rule N"; keep those rules as docs/RESEARCH_RULES.md.
{ echo "# Research rules"; echo
  echo "Code comments cite these rules as \"CLAUDE.md rule N\" (the file's name in the working repository)."; echo
  sed -n '/^## Non-negotiable research rules/,/^## Engineering conventions/p' CLAUDE.md | sed '$d'; } > docs/RESEARCH_RULES.md
rm -rf AGENTS.md CLAUDE.md CODEX_START_HERE.md START_HERE_CLAUDE_CODE.md VERIFICATION.txt \
       docs/work_packages docs/CLOUD_DEPLOYMENT_REVIEW.md docs/MILESTONE_01.md docs/REVAMP_PLAN.md \
       docs/PROJECT_DECISIONS.md docs/PREREG_DRAFT.md
mv artifact/README.md README.md
mv artifact/LICENSE LICENSE
rmdir artifact
# Never ship a key (the adapter reads keys only from the environment; this is a final guard).
if grep -rIl -E 'sk_live_[A-Za-z0-9]{10,}|sk_test_[A-Za-z0-9]{10,}|rk_(live|test)_[A-Za-z0-9]{10,}' . ; then
  echo "export_artifact: possible API key found; aborting" >&2; exit 1
fi
git init -q -b main
git add -A
git -c user.name="Aditya Krishnan" -c user.email="aditya11997@gmail.com" \
    commit -q -m "Research artifact for 'Contained Is Not Closed' (snapshot of $(cd "$OLDPWD" && git rev-parse --short "$ref"))"
echo "export_artifact: $(git ls-files | wc -l) files, $(du -sh . | cut -f1) in $out"
