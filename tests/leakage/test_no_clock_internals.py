"""Post-hoc verification (docs/POSTHOC.md PH4): runbooks never read the shared clock's
internals. The event queue also holds the sink's scheduled captures, so `next_time`, `pending`
or the queue itself would reveal capture times (hidden truth, CLAUDE.md rule 1).

The one allowed use is the engine loop in `Runbook.run` (p4/runbooks/base.py), which must call
`self.clock.next_time()` exactly once to advance the clock; it never hands the value to a
policy. A self-test plants each violation kind so this test cannot pass vacuously.
"""
import ast
from pathlib import Path

import pytest

RUNBOOKS = Path(__file__).resolve().parents[2] / "p4" / "runbooks"
CLOCK_INTERNALS = {"next_time", "pending", "_queue", "_seq"}
ENGINE = ("base.py", "Runbook", "run")   # file, class, method of the one allowed call


def _is_engine_call(node: ast.Attribute) -> bool:
    """`self.clock.next_time`"""
    v = node.value
    return (node.attr == "next_time" and isinstance(v, ast.Attribute) and v.attr == "clock"
            and isinstance(v.value, ast.Name) and v.value.id == "self")


def clock_uses(source: str, filename: str) -> tuple[list[str], int]:
    """(violations, number of allowed engine-loop calls) in one module."""
    violations, engine_calls = [], 0

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, ast.ClassDef):
                inner = (child.name, None)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = (scope[0], child.name)
            yield child, inner
            yield from walk(child, inner)

    for node, (cls, fn) in walk(ast.parse(source), (None, None)):
        where = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Attribute) and node.attr in CLOCK_INTERNALS:
            if (filename, cls, fn) == ENGINE and _is_engine_call(node):
                engine_calls += 1
            else:
                violations.append(f"{where}: attribute .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in CLOCK_INTERNALS:
            violations.append(f"{where}: name {node.id}")
        elif isinstance(node, ast.alias) and (node.asname or node.name).split(".")[-1] in CLOCK_INTERNALS:
            violations.append(f"{where}: import {node.name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in CLOCK_INTERNALS:
            violations.append(f"{where}: string {node.value!r}")
    return violations, engine_calls


def test_runbooks_never_read_clock_internals():
    files = sorted(RUNBOOKS.glob("*.py"))
    assert files
    violations, engine_calls = [], 0
    for path in files:
        v, n = clock_uses(path.read_text(encoding="utf-8"), path.name)
        violations += v
        engine_calls += n
    assert violations == []
    assert engine_calls == 1  # the engine loop, and nothing else


PLANTED = {
    "attribute": "class A:\n    def f(self):\n        return self.clock.next_time()\n",
    "pending": "def f(clock):\n    return clock.pending()\n",
    "queue": "def f(clock):\n    return clock._queue[0]\n",
    "name": "from x import y\ndef f():\n    return next_time\n",
    "import": "from p4.sim.clock import pending\n",
    "string": "def f(clock):\n    return '_queue'\n",
    "engine_call_elsewhere_in_base": "class Runbook:\n    def next_action(self):\n        return self.clock.next_time()\n",
    "other_class_run": "class Other:\n    def run(self):\n        return self.clock.next_time()\n",
}


@pytest.mark.parametrize("kind", sorted(PLANTED))
def test_self_test_planted_violations_are_found(kind):
    filename = "base.py" if kind != "attribute" else "planted.py"
    violations, engine_calls = clock_uses(PLANTED[kind], filename)
    assert violations, kind
    assert engine_calls == 0


def test_self_test_engine_loop_is_the_only_exception():
    src = "class Runbook:\n    def run(self):\n        self.clock.run_until(self.clock.next_time())\n"
    assert clock_uses(src, "base.py") == ([], 1)
    assert clock_uses(src, "other.py")[0]  # the same call outside base.py is a violation
