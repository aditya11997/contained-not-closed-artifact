"""Every generated table (paper/generated/*.tex with a table float) compiles in a minimal IEEEtran
conference document with no Overfull \\hbox, uses a math minus for negative numbers, and is ASCII."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "paper" / "generated"
TABLES = sorted(p for p in GENERATED.glob("*.tex") if "\\begin{table" in p.read_text(encoding="utf-8"))
DOC = "\\documentclass[conference]{{IEEEtran}}\n\\usepackage{{booktabs}}\n\\begin{{document}}\n\\input{{{path}}}\n\\end{{document}}\n"


def _latex_missing() -> str | None:
    if not shutil.which("pdflatex"):
        return "pdflatex not installed"
    if not shutil.which("kpsewhich") or subprocess.run(["kpsewhich", "IEEEtran.cls"], capture_output=True,
                                                         text=True, check=False).stdout.strip() == "":
        return "IEEEtran.cls not found"
    return None


def test_there_are_generated_tables():
    assert {"crash_matrix.tex", "contrasts_faulty_tex.tex", "contrasts_primary_narrow.tex"} <= {p.name for p in TABLES}


@pytest.mark.parametrize("table", TABLES, ids=lambda p: p.name)
def test_table_fits_without_overfull_hbox(table, tmp_path):
    reason = _latex_missing()
    if reason:
        pytest.skip(reason)
    (tmp_path / "t.tex").write_text(DOC.format(path=table.as_posix()), encoding="utf-8")
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "t.tex"], cwd=tmp_path,
                   capture_output=True, timeout=120, check=False)
    log = (tmp_path / "t.log").read_text(encoding="latin-1")
    assert (tmp_path / "t.pdf").is_file(), f"{table.name} does not compile:\n" + "\n".join(
        line for line in log.splitlines() if line.startswith("!"))
    overfull = re.findall(r"Overfull \\hbox \([0-9.]+pt too wide\)", log)
    assert not overfull, f"{table.name}: {overfull}"


@pytest.mark.parametrize("table", TABLES, ids=lambda p: p.name)
def test_table_uses_math_minus_and_ascii(table):
    body = [line for line in table.read_text(encoding="utf-8").splitlines() if not line.startswith("%")]
    hyphen_numbers = [line for line in body if re.search(r"(?<![\w$])-\d", line)]
    assert not hyphen_numbers, f"{table.name}: hyphen before a number: {hyphen_numbers[:3]}"
    assert all(line.isascii() for line in body), f"{table.name}: non-ASCII character"
