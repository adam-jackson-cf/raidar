#!/usr/bin/env python3
"""Write one passing and one violating fixture per applicable rule, plus specs.json.

    make_fixtures.py OUT_DIR LOCK_FILE SPECS_FILE [--pack 3|4]

``--pack 3`` writes python-guardrails@3's set (``specs-3.json``): every @1
canary, the tests check's canaries (``TEST.*``), and a commit canary whose
clean workspace has a test (the @3 gate refuses a change with none).
``--pack 4`` writes python-guardrails@4's (``specs-4.json``): @3's plus the
scope canaries (``SCOPE.*``: an extensionless python script, and the target of
a ``.py`` symlink, are checked).

LOCK_FILE is a ``uv.lock`` for the canary's demo project (``name = "demo"``, no
dependencies), produced by the uv that the gate will run, so the lockfile
parity canary compares like with like.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path("fixtures")


def fn_args(n: int, kw: bool = False) -> str:
    names = ", ".join(f"a{i}: int" for i in range(n))
    sep = "*, " if kw else ""
    return f"def f({sep}{names}) -> int:\n    return a0\n"


def branches(n: int) -> str:
    body = "".join(f"    if x == {i}:\n        y += {i}\n" for i in range(n))
    return f"def f(x: int) -> int:\n    y = 0\n{body}    return y\n"


def complexity(n: int) -> str:
    # cc = 1 + number of ifs; ifs on separate branches without else stays one return
    body = "".join(f"    if x > {i}:\n        y += 1\n" for i in range(n - 1))
    return f"def f(x: int) -> int:\n    y = 0\n{body}    return y\n"


def returns(n: int) -> str:
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(n - 1))
    return f"def f(x: int) -> int:\n{body}    return -1\n"


def locals_(n: int) -> str:
    body = "".join(f"    v{i} = x + {i}\n" for i in range(n))
    total = " + ".join(f"v{i}" for i in range(n))
    return f"def f(x: int) -> int:\n{body}    return {total}\n"


def statements(n: int) -> str:
    body = "".join("    x += 1\n" for _ in range(n - 1))
    return f"def f(x: int) -> int:\n{body}    return x\n"


def public_methods(n: int) -> str:
    methods = "".join(f"    def m{i}(self) -> int:\n        return {i}\n\n" for i in range(n))
    return f"class C:\n{methods}"


def class_lines(n: int) -> str:
    body = "".join(f"    a{i}: int = {i}\n" for i in range(n - 1))
    return f"class C:\n{body}"


def init_args(n: int) -> str:
    names = ", ".join(f"a{i}: int" for i in range(n))
    return f"class C:\n    def __init__(self, *, {names}) -> None:\n        self.a0 = a0\n"


def attributes(n: int) -> str:
    body = "".join(f"        self.a{i} = {i}\n" for i in range(n))
    return f"class C:\n    def __init__(self) -> None:\n{body}"


def loops(depth: int) -> str:
    lines, indent = [], "    "
    for i in range(depth):
        lines.append(f"{indent}for i{i} in range(2):")
        indent += "    "
    lines.append(f"{indent}total += 1")
    return "def f() -> int:\n    total = 0\n" + "\n".join(lines) + "\n    return total\n"


def fanout(n: int, pad: int = 0) -> str:
    extras = "".join(f"# canary-extra: pkg{i}.py=X = {i}\\n\n" for i in range(n))
    imports = "".join(f"import pkg{i}\n" for i in range(n))
    total = " + ".join(f"pkg{i}.X" for i in range(n)) or "0"
    filler = "".join(f"V{i} = {i}\n" for i in range(pad))
    return f"{extras}{imports}\n{filler}\n\ndef f() -> int:\n    return {total}\n"


CANARY_PATH = "# canary-path: {}\n"
SPECS: dict[str, dict[str, object]] = {}


def add(
    rule: str, codes: str, passing: str | bytes, violating: str | bytes, mode: str = "rule"
) -> None:
    folder = OUT / rule
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in (("pass.py", passing), ("violate.py", violating)):
        data = body if isinstance(body, bytes) else body.encode()
        (folder / name).write_bytes(data)
    SPECS[rule] = {"codes": codes, "mode": mode}


def main(argv: list[str]) -> None:
    global OUT
    OUT = Path(argv[0])
    lock = Path(argv[1]).read_text().replace("\n", "\\n")
    ok = "def ok() -> int:\n    return 1\n"
    # --- Ruff selectors -------------------------------------------------------
    add("RUFF.SELECT.A", "A001", "values = [1]\n", "list = [1]\n")
    add(
        "RUFF.SELECT.B",
        "B009",
        "import os\n\nsep = os.sep\n",
        'import os\n\nsep = getattr(os, "sep")\n',
    )
    add("RUFF.SELECT.C4", "C408", "d: dict[str, int] = {}\n", "d: dict[str, int] = dict()\n")
    add("RUFF.SELECT.C901", "C901", complexity(10), complexity(11))
    add(
        "RUFF.SELECT.D",
        "D",
        'def f() -> int:\n    """Return one."""\n    return 1\n',
        'def f() -> int:\n    """ Return one"""\n    return 1\n',
    )
    add("RUFF.SELECT.E", "E711", "x = None\nok = x is None\n", "x = None\nok = x == None\n")
    add("RUFF.SELECT.F", "F401", "import os\n\nprint(os.sep)\n", "import os\n")
    add(
        "RUFF.SELECT.I",
        "I001",
        "import os\nimport sys\n\nprint(os, sys)\n",
        "import sys\nimport os\n\nprint(os, sys)\n",
    )
    add("RUFF.SELECT.N", "N802", ok, "def DoThing() -> int:\n    return 1\n")
    add("RUFF.SELECT.PGH", "PGH003", "x: int = 1\n", "x: int = 1  # type: ignore\n")
    add("RUFF.SELECT.PLR0904", "PLR0904", public_methods(20), public_methods(21))
    add("RUFF.SELECT.PLR0911", "PLR0911", returns(6), returns(7))
    add("RUFF.SELECT.PLR0912", "PLR0912", branches(12), branches(13))
    add("RUFF.SELECT.PLR0913", "PLR0913", fn_args(5, kw=True), fn_args(6, kw=True))
    add("RUFF.SELECT.PLR0914", "PLR0914", locals_(14), locals_(16))
    add("RUFF.SELECT.PLR0915", "PLR0915", statements(51), statements(52))
    add("RUFF.SELECT.PLR0917", "PLR0917", fn_args(5), fn_args(6))
    add(
        "RUFF.SELECT.PT",
        "PT018",
        CANARY_PATH.format("tests/unit/test_x.py")
        + "def test_a() -> None:\n    assert 1\n    assert 2\n",
        CANARY_PATH.format("tests/unit/test_x.py") + "def test_a() -> None:\n    assert 1 and 2\n",
    )
    add(
        "RUFF.SELECT.SIM",
        "SIM108",
        "def f(c: bool) -> int:\n    return 1 if c else 2\n",
        "def f(c: bool) -> int:\n    if c:\n        x = 1\n    else:\n        x = 2\n    return x\n",
    )
    add(
        "RUFF.SELECT.UP",
        "UP035",
        "from collections.abc import Sequence\n\nS = Sequence[int]\n",
        "from typing import Sequence\n\nS = Sequence[int]\n",
    )
    add("RUFF.SELECT.W", "W291", ok, "def ok() -> int:\n    return 1 \n")
    add("RUFF.SELECT.E", "E711", "x = None\nok = x is None\n", "x = None\nok = x == None\n")
    # --- Ruff settings and limits (same rule, at the configured threshold) ------
    add("RUFF.LIMIT.max-args", "PLR0913", fn_args(5, kw=True), fn_args(6, kw=True))
    add("RUFF.LIMIT.max-branches", "PLR0912", branches(12), branches(13))
    add("RUFF.LIMIT.max-locals", "PLR0914", locals_(14), locals_(16))
    add("RUFF.LIMIT.max-positional-args", "PLR0917", fn_args(5), fn_args(6))
    add("RUFF.LIMIT.max-public-methods", "PLR0904", public_methods(20), public_methods(21))
    add("RUFF.LIMIT.max-returns", "PLR0911", returns(6), returns(7))
    add("RUFF.LIMIT.max-statements", "PLR0915", statements(51), statements(52))
    add("RUFF.SETTING.mccabe.max-complexity", "C901", complexity(10), complexity(11))
    add(
        "RUFF.SETTING.target-version",
        "UP040",
        "type Pair = tuple[int, int]\n",
        "from typing import TypeAlias\n\nPair: TypeAlias = tuple[int, int]\n",
    )
    long_line = "x = [" + ", ".join(["1111111111"] * 8) + "]\n"  # 99 chars > 88
    short_line = "x = [" + ", ".join(["1111111111"] * 6) + "]\n"  # 77 chars <= 88
    add("RUFF.SETTING.line-length", "FORMAT", short_line, long_line)
    add(
        "RUFF.PYDOCSTYLE.convention",
        "D",
        'def f() -> int:\n    """Return one.\n\n    Always one.\n    """\n    return 1\n',
        'def f() -> int:\n    """Return one"""\n    return 1\n',
    )
    add(
        "RUFF.ISORT.combine-as-imports",
        "I001",
        "from os import path as p, sep\n\nprint(p, sep)\n",
        "from os import path as p\nfrom os import sep\n\nprint(p, sep)\n",
    )
    add(
        "RUFF.ISORT.force-single-line",
        "I001",
        "from os import path, sep\n\nprint(path, sep)\n",
        "from os import path\nfrom os import sep\n\nprint(path, sep)\n",
    )
    add(
        "RUFF.ISORT.split-on-trailing-comma",
        "I001",
        "from os import (\n    path,\n    sep,\n)\n\nprint(path, sep)\n",
        "from os import (path, sep,)\n\nprint(path, sep)\n",
    )
    add(
        "RUFF.ISORT.known-first-party",
        "I001",
        "# canary-extra: localmod.py=X = 1\\n\nimport os\n\nimport localmod\n\nprint(os, localmod)\n",
        "# canary-extra: localmod.py=X = 1\\n\nimport localmod\nimport os\n\nprint(os, localmod)\n",
    )
    add("RUFF.FORMAT.quote-style", "FORMAT", 'x = "a"\n', "x = 'a'\n")
    add(
        "RUFF.FORMAT.indent-style",
        "FORMAT",
        "def f() -> int:\n    return 1\n",
        "def f() -> int:\n\treturn 1\n",
    )
    add("RUFF.FORMAT.line-ending", "FORMAT", b"x = 1\r\ny = 2\r\n", b"x = 1\r\ny = 2\nz = 3\r\n")
    add(
        "RUFF.FORMAT.docstring-code-format",
        "FORMAT",
        'def f() -> int:\n    """Return one.\n\n    Examples\n    --------\n    >>> x = 1\n    """\n    return 1\n',
        'def f() -> int:\n    """Return one.\n\n    Examples\n    --------\n    >>> x=1\n    """\n    return 1\n',
    )
    add(
        "RUFF.ROOT.exclude",
        "F401",
        CANARY_PATH.format(".venv/lib/mod.py") + "import os\n",
        CANARY_PATH.format("lib/mod.py") + "import os\n",
    )
    add(
        "RUFF.ROOT.respect-gitignore",
        "F401",
        ok,
        "# canary-extra: .gitignore=violate.py\\n\nimport os\n",
    )
    add("RUFF.ROOT.fix", "F401", ok, "import os\n", mode="unchanged")
    add("RUFF.ROOT.output-format", "F401", ok, "import os\n")
    add("RUFF.ROOT.preview", "PLR0917", fn_args(5), fn_args(6))
    add(
        "RUFF.IGNORES",
        "SUPPRESS,E501,D103,D100",
        "def f() -> str:\n    return 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n",
        "import os  # noqa: F401\n",
    )
    # --- mypy -------------------------------------------------------------------
    untyped = "def f(x):\n    return x\n"
    add("MYPY.strict", "MYPY.no-untyped-def", ok, untyped)
    add("MYPY.SETTING.show_error_codes", "MYPY.no-untyped-def", ok, untyped)
    add(
        "MYPY.warn_unused_ignores",
        "MYPY.unused-ignore",
        ok,
        "x: int = 1  # type: ignore[assignment]\n",
    )
    add(
        "MYPY.SETTING.python_version",
        "MYPY.attr-defined",
        "import typing\n\nOverride = typing.override\n",
        "import warnings\n\nDeprecated = warnings.deprecated\n",
    )
    add(
        "MYPY.SETTING.exclude",
        "MYPY",
        CANARY_PATH.format("build/gen.py") + untyped,
        CANARY_PATH.format("gen/gen.py") + untyped,
    )
    add(
        "MYPY.SETTING.mypy_path",
        "MYPY",
        "# canary-extra: pkg/__init__.py=\n# canary-extra: pkg/core.py=def one() -> int:\\n    return 1\\n\nfrom pkg.core import one\n\nTWO: int = one() + 1\n",
        "# canary-extra: pkg/__init__.py=\n# canary-extra: pkg/core.py=def one() -> int:\\n    return 1\\n\nfrom pkg.core import one\n\nTWO: str = one() + 1\n",
    )
    add(
        "MYPY.follow_imports",
        "MYPY",
        "# canary-extra: tests/unit/test_support.py=def helper(x):\\n    return x\\n\nfrom tests.unit.test_support import helper\n\nVALUE: int = helper(1)\n",
        "# canary-extra: other.py=def helper(x: int) -> int:\\n    return x\\n\nfrom other import helper\n\nVALUE: str = helper(1)\n",
    )
    add(
        "MYPY.module-overrides",
        "MYPY",
        CANARY_PATH.format("tests/unit/test_x.py") + untyped,
        untyped,
    )
    add(
        "MYPY.package-coverage",
        "MYPY",
        CANARY_PATH.format("pkg/sub/mod.py") + ok,
        CANARY_PATH.format("pkg/sub/mod.py") + untyped,
    )
    # --- bespoke ------------------------------------------------------------------
    add(
        "PYQ102",
        "PYQ102",
        CANARY_PATH.format("tests/unit/test_x.py") + "def test_a() -> None:\n    assert True\n",
        CANARY_PATH.format("tests/test_x.py") + "def test_a() -> None:\n    assert True\n",
    )
    add(
        "PYQ103",
        "PYQ103",
        CANARY_PATH.format("store/io.py") + ok,
        CANARY_PATH.format("misc/io.py") + ok,
    )
    add(
        "PYQ104",
        "PYQ104",
        CANARY_PATH.format("formatting.py") + ok,
        CANARY_PATH.format("helpers.py") + ok,
    )
    add(
        "PYQ105",
        "PYQ105",
        CANARY_PATH.format("data_store/io.py") + ok,
        CANARY_PATH.format("DataStore/io.py") + ok,
    )
    add(
        "PYQ107",
        "PYQ107",
        CANARY_PATH.format("data_store.py") + ok,
        CANARY_PATH.format("DataStore.py") + ok,
    )
    add("PYQ200", "PYQ200", class_lines(700), class_lines(701))
    add("PYQ202", "PYQ202", init_args(8), init_args(9))
    add("PYQ203", "PYQ203", attributes(12), attributes(13))
    add("PYQ299", "PYQ299", "x = 'caf\xc3\xa9'\n".encode("latin-1"), b"x = '\xff\xfe'\n")
    add("PYS000", "PYS000", ok, "def broken(:\n    pass\n")
    add("PYS250", "PYS250", loops(2), loops(3))
    add("FANOUT.general", "FANOUT.general", fanout(10), fanout(11))
    add("FANOUT.large-module", "FANOUT.large-module", fanout(3, pad=820), fanout(4, pad=820))
    add(
        "TEST.subprocess-timeouts",
        "TST001",
        CANARY_PATH.format("tests/unit/test_cli.py")
        + "import subprocess\n\n\ndef test_cli() -> None:\n    subprocess.run(['true'], check=True, timeout=10)\n",
        CANARY_PATH.format("tests/unit/test_cli.py")
        + "import subprocess\n\n\ndef test_cli() -> None:\n    subprocess.run(['true'], check=True)\n",
    )
    add(
        "PARITY.lock-version",
        "PARITY.lock-version",
        '# canary-extra: pyproject.toml=[project]\\nname = "demo"\\nversion = "0.1.0"\\nrequires-python = ">=3.12"\\ndependencies = []\\n\n# canary-extra: uv.lock='
        + lock
        + "\n"
        + ok,
        '# canary-extra: pyproject.toml=[project]\\nname = "demo"\\nversion = "0.1.0"\\nrequires-python = ">=3.12"\\ndependencies = []\\n\n'
        + ok,
    )
    add(
        "TEST.failure-propagation",
        "",
        "scenario\nok\n",
        "scenario\nmissing-mypy\n",
        mode="propagation",
    )
    add_config_entries()
    pack = argv[4] if len(argv) > 4 and argv[3] == "--pack" else "1"
    commit_ok = ok if pack == "1" else with_test("mod.py", MODULE, UNIT_TEST)
    add("RUNNER.local-ci-parity", "F401", commit_ok, "import os\n", mode="commit")
    add("TEST.tiers", "F401", ok, "import os\n")
    if pack in ("3", "4"):
        add_tests_check()
    if pack == "4":
        add_scope()
    Path(argv[2]).write_text(json.dumps(SPECS, indent=2, sort_keys=True) + "\n")


def add_config_entries() -> None:
    """Pack config entries that had no canary of their own (both packs ship them)."""
    add(
        "RUFF.LINT.per-file-ignores",
        "F401",
        CANARY_PATH.format("pkg/__init__.py") + "import os\n",
        CANARY_PATH.format("pkg/mod.py") + "import os\n",
    )
    twin = "# canary-extra: b/util.py=VALUE: int = 1\\n\n"
    add(
        "MYPY.explicit_package_bases",
        "MYPY",
        CANARY_PATH.format("a/util.py") + twin + "from b.util import VALUE\n\nTOTAL: int = VALUE\n",
        CANARY_PATH.format("a/util.py") + twin + "from b.util import VALUE\n\nTEXT: str = VALUE\n",
    )
    add(
        "MYPY.disable_error_code",
        "MYPY.import-untyped,MYPY.import-not-found",
        "import yaml\n\nLOADER: object = yaml\n",
        "import canary_module_that_does_not_exist\n\nLOADER: object = canary_module_that_does_not_exist\n",
    )


# ---- python-guardrails@3: the tests check -------------------------------------------------

MODULE = 'def double(x: int) -> int:\n    """Double x."""\n    return 2 * x\n'
UNIT_TEST = (
    "import unittest\\n\\nfrom mod import double\\n\\n\\n"
    "class DoubleTest(unittest.TestCase):\\n"
    "    def test_double(self) -> None:\\n"
    "        self.assertEqual(double(2), {expected})\\n"
)
PYTEST_TEST = (
    "from mod import double\\n\\n\\n"
    "def test_double() -> None:\\n"
    "    assert double(2) == {expected}\\n"
)
WRITING_TEST = (
    "import unittest\\n\\nfrom mod import double\\n\\n\\n"
    "class DoubleTest(unittest.TestCase):\\n"
    "    def test_double(self) -> None:\\n"
    '        with open("written.txt", "w", encoding="utf-8") as out:\\n'
    '            out.write("x")\\n'
    "        self.assertEqual(double(2), {expected})\\n"
)
UNCOVERED = (
    'def triple(x: int) -> int:\n    """Triple x."""\n    return 3 * x\n\n\n'
    'def quad(x: int) -> int:\n    """Quadruple x."""\n    return 4 * x\n'
)
CLI_IMPORTS = "import sys\n\n\n"
CLI = CLI_IMPORTS + (
    'def main() -> int:\n    """Print the sum of two integers."""\n'
    "    print(int(sys.argv[1]) + int(sys.argv[2]))\n    return 0\n\n\n"
    'if __name__ == "__main__":\n    raise SystemExit(main())\n'
)
CLI_UNCOVERED = (
    'def spare(x: int) -> int:\n    """Not run by any test."""\n    if x > 1:\n'
    "        return x\n    return -x\n\n\n"
)
CLI_TEST = (
    "import subprocess\\nimport sys\\nimport unittest\\n\\n\\n"
    "class CliTest(unittest.TestCase):\\n"
    "    def test_cli(self) -> None:\\n"
    '        done = subprocess.run([sys.executable, "cli.py", "2", "3"], '
    "capture_output=True, text=True, check=True, timeout=60)\\n"
    '        self.assertEqual(done.stdout.strip(), "5")\\n'
)


def with_test(
    path: str, body: str, test: str, *, expected: int = 4, test_path: str = "test_mod.py"
) -> str:
    """``body`` at ``path`` with one test file beside it."""
    return (
        CANARY_PATH.format(path)
        + f"# canary-extra: {test_path}={test.format(expected=expected)}\n"
        + body
    )


def add_tests_check() -> None:
    """The tests check's rules and behaviour (python-guardrails@3)."""
    untested = CANARY_PATH.format("mod.py") + MODULE
    add("TEST.missing", "TEST.missing", with_test("mod.py", MODULE, UNIT_TEST), untested)
    add(
        "TEST.no-change-exempt",
        "TEST.missing",
        untested,
        CANARY_PATH.format("mod.py") + "# canary-after-commit: extra.py=X: int = 1\\n\n" + MODULE,
        mode="since-commit",
    )
    add(
        "TEST.failed",
        "TEST.failed",
        with_test("mod.py", MODULE, UNIT_TEST),
        with_test("mod.py", MODULE, UNIT_TEST, expected=5),
    )
    add(
        "TEST.pytest-runner",
        "TEST.failed",
        with_test("mod.py", MODULE, PYTEST_TEST),
        with_test("mod.py", MODULE, PYTEST_TEST, expected=5),
    )
    add(
        "TEST.coverage-minimum",
        "TEST.coverage-minimum",
        with_test("mod.py", MODULE, UNIT_TEST),
        with_test("mod.py", MODULE + "\n\n" + UNCOVERED, UNIT_TEST),
    )
    add(
        "TEST.subprocess-coverage",
        "TEST.coverage-minimum",
        with_test("cli.py", CLI, CLI_TEST, test_path="test_cli.py"),
        with_test(
            "cli.py",
            CLI_IMPORTS + CLI_UNCOVERED + CLI.removeprefix(CLI_IMPORTS),
            CLI_TEST,
            test_path="test_cli.py",
        ),
    )
    add(
        "TEST.isolated-run",
        "TEST.failed",
        with_test("mod.py", MODULE, WRITING_TEST),
        with_test("mod.py", MODULE, WRITING_TEST, expected=5),
        mode="workspace-clean",
    )


# ---- python-guardrails@4: the gate's scope ----------------------------------------------

SHEBANG = "#!/usr/bin/env python3\n"


def add_scope() -> None:
    """Python files without a ``.py`` name are checked (python-guardrails@4)."""
    script = SHEBANG + CANARY_PATH.format("tool")
    add("SCOPE.shebang-script", "F401", script + "VALUE: int = 1\n", script + "import os\n")
    linked = CANARY_PATH.format("tool") + "# canary-symlink: tool.py=tool\n"
    add("SCOPE.py-symlink", "F401", linked + "VALUE: int = 1\n", linked + "import os\n")


if __name__ == "__main__":
    main(sys.argv[1:])
