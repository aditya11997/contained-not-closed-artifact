"""CLAUDE.md rule 1, enforced statically: p4/sched and p4/runbooks (and the modules runbooks
use, p4/contract.py and p4/journal.py) never touch hidden truth or sink internals.

AST walk with an import *allowlist* per package, plus a blacklist of truth names, sink
internals and introspection. The self-test at the bottom plants each violation kind and
checks the walker reports it, so this test cannot pass vacuously.
"""
import ast
import sys
from pathlib import Path

import pytest

P4 = Path(__file__).resolve().parents[2] / "p4"

SCHED_ALLOWED = ("numpy", "p4.sched")
RUNBOOK_ALLOWED = SCHED_ALLOWED + ("p4.runbooks", "p4.contract", "p4.journal", "p4.sim.protocol")
BANNED_MODULES = ("p4.sim.oracle", "p4.sim.processor", "p4.sim.scenario", "p4.sim.faults",
                  "p4.harness", "checker", "importlib", "inspect", "gc", "sys", "ctypes", "builtins")
TRUTH_NAMES = {"D_actual", "y_suspect", "_truth", "PaymentTruth", "Scenario", "make_scenario",
               "SimProcessor"}
# Attributes that expose the sink, the fault realization or the hidden scenario.
BANNED_ATTRS = {"trace", "in_outage", "cfg", "scenario", "export_log", "log", "payments"}
BANNED_CALLS = {"__import__", "eval", "exec", "globals", "vars", "getattr", "setattr"}

SCOPES = {
    "sched": (sorted((P4 / "sched").glob("*.py")), SCHED_ALLOWED),
    "runbooks": (sorted((P4 / "runbooks").glob("*.py")) + [P4 / "contract.py", P4 / "journal.py"],
                 RUNBOOK_ALLOWED),
}


def _module_of(path: Path) -> str:
    rel = path.relative_to(P4.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve(node: ast.ImportFrom, module: str, is_pkg: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = module.split(".") if is_pkg else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _under(name: str, prefixes) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def violations(source: str, module: str, allowed, is_pkg: bool = False) -> list[str]:
    out = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        modules, symbols = [], []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [_resolve(node, module, is_pkg)]
            for a in node.names:
                if a.name in TRUTH_NAMES:
                    out.append(f"line {node.lineno}: imports truth name {a.name}")
                symbols.append(f"{modules[0]}.{a.name}")  # catches `from p4.sim import processor`
        for name in modules + symbols:
            if _under(name, BANNED_MODULES):
                out.append(f"line {node.lineno}: imports banned module {name}")
        for name in modules:
            top = name.split(".")[0]
            if not (_under(name, allowed) or top in sys.stdlib_module_names):
                out.append(f"line {node.lineno}: import {name} not in allowlist")
        if isinstance(node, ast.Attribute):
            if node.attr in TRUTH_NAMES or node.attr in BANNED_ATTRS:
                out.append(f"line {node.lineno}: attribute .{node.attr}")
            is_self = isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")
            if node.attr.startswith("_") and not node.attr.startswith("__") and not is_self:
                out.append(f"line {node.lineno}: private attribute .{node.attr} on another object")
        if isinstance(node, ast.Name) and node.id in TRUTH_NAMES | BANNED_CALLS:
            out.append(f"line {node.lineno}: name {node.id}")
        if isinstance(node, ast.keyword) and node.arg in TRUTH_NAMES:
            out.append(f"line {node.lineno}: keyword {node.arg}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in TRUTH_NAMES | BANNED_ATTRS:
            out.append(f"line {node.lineno}: string {node.value!r}")
    return out


def _cases():
    for scope, (files, allowed) in SCOPES.items():
        for f in files:
            yield pytest.param(f, allowed, id=f"{scope}/{f.name}")


@pytest.mark.parametrize("path,allowed", list(_cases()))
def test_no_oracle_access(path, allowed):
    found = violations(path.read_text(), _module_of(path), allowed, path.name == "__init__.py")
    assert found == [], f"{path}: {found}"


def test_scopes_are_not_empty():
    assert len(SCOPES["sched"][0]) >= 6 and len(SCOPES["runbooks"][0]) >= 8


PLANTED = [
    "from p4.sim import oracle",
    "import p4.sim.oracle",
    "from ..sim.processor import SimProcessor",
    "from p4.sim.scenario import PaymentTruth",
    "import checker.check",
    "x = o.D_actual",
    "x = o.y_suspect",
    "x = self._truth",
    "x = transport._sink",
    "x = transport.trace",
    "x = transport.in_outage(3)",
    "x = getattr(o, 'D_actual')",
    "__import__('p4.sim.processor')",
    "import importlib",
    "import pandas",
]


@pytest.mark.parametrize("src", PLANTED)
def test_walker_catches_planted_violation(src):
    assert violations(src, "p4.runbooks.evil", RUNBOOK_ALLOWED), src


def test_walker_accepts_allowed_imports():
    ok = "from ..sim.protocol import CANCELLED\nfrom ..sched import plan_lm\nimport math\nself._x = 1"
    assert violations(ok, "p4.runbooks.fine", RUNBOOK_ALLOWED) == []
    assert violations("from ..sim.protocol import CANCELLED", "p4.sched.fine", SCHED_ALLOWED)


# -- checker/ (WP3): independent of the simulator; implementable from p4.schema alone ---------
REPO = P4.parent
CHECKER_FILES = sorted((REPO / "checker").glob("*.py"))
CHECKER_P4_ALLOWED = ("p4.schema",)
DYNAMIC_IMPORT = ("importlib", "runpy", "ctypes", "builtins", "pkgutil", "zipimport")


def checker_violations(source: str, module: str, is_pkg: bool = False) -> list[str]:
    """Every import is stdlib, checker.*, or p4.schema; no dynamic import or eval."""
    out = []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(node, module, is_pkg)
            names = [f"{base}.{a.name}" for a in node.names] if base == "p4" else [base]
        for name in names:
            top = name.split(".")[0]
            if top == "p4" and not _under(name, CHECKER_P4_ALLOWED):
                out.append(f"line {node.lineno}: imports {name} (only p4.schema is allowed)")
            elif top not in ("p4", "checker") and top not in sys.stdlib_module_names:
                out.append(f"line {node.lineno}: imports non-stdlib {name}")
            if _under(name, DYNAMIC_IMPORT):
                out.append(f"line {node.lineno}: imports {name}")
        if isinstance(node, ast.Name) and node.id in ("__import__", "eval", "exec"):
            out.append(f"line {node.lineno}: name {node.id}")
    return out


@pytest.mark.parametrize("path", CHECKER_FILES, ids=lambda p: f"checker/{p.name}")
def test_checker_imports_only_p4_schema(path):
    module = _module_of_repo(path)
    assert checker_violations(path.read_text(), module, path.name == "__init__.py") == []


def _module_of_repo(path: Path) -> str:
    parts = list(path.relative_to(REPO).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def test_checker_scope_is_not_empty():
    assert {p.name for p in CHECKER_FILES} >= {"__init__.py", "check.py"}


CHECKER_PLANTED = [
    "from p4.sim import processor",
    "from p4.sim.oracle import score",
    "import p4.harness",
    "from p4 import contract",
    "from p4 import schema, contract",
    "from p4 import *",
    "from p4.runbooks.base import cancel_key",
    "import numpy",
    "import importlib",
    "__import__('p4.sim.processor')",
]


@pytest.mark.parametrize("src", CHECKER_PLANTED)
def test_checker_walker_catches_planted_import(src):
    assert checker_violations(src, "checker.evil"), src


def test_checker_walker_accepts_allowed_imports():
    ok = "from p4 import schema as S\nfrom p4.schema import LogLine\nfrom .conform import conform\nimport json"
    assert checker_violations(ok, "checker.fine") == []


def test_importing_the_checker_loads_no_other_p4_module():
    import subprocess
    code = ("import sys, checker.check; "
            "print(','.join(sorted(m for m in sys.modules if m == 'p4' or m.startswith('p4.'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "p4,p4.schema"


def test_schema_module_is_only_typeddicts_and_constants():
    tree = ast.parse((P4 / "schema.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module in ("typing", "__future__"), node.module
        elif isinstance(node, ast.ClassDef):
            assert [ast.unparse(b) for b in node.bases] == ["TypedDict"], node.name
            for item in node.body:
                assert isinstance(item, ast.AnnAssign) or (
                    isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)), node.name
        else:
            assert isinstance(node, (ast.Assign, ast.AnnAssign)) or (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)), ast.dump(node)[:80]
    assert not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Call))
                   for n in ast.walk(tree))
