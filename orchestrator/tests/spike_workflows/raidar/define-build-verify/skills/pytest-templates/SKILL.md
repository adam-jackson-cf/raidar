---
name: "pytest-templates"
description: "USE WHEN writing pytest tests for a Python project."
---
Test templates for a Python project tested with pytest. Pick the template whose
shape matches the aspect, copy it, and fill in the aspect's own situation and
outcome. Each template drives the product through the entry point the criterion
names and asserts the exact value the aspect states. Name each test after its
aspect (`test_<criterion id>_<aspect number>_<slug>`).

Shared helper for a command-line program, a fixture (put it in
`tests/conftest.py` once; every test below takes `run` as a parameter):

```python
import subprocess, sys
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parents[1] / "<entry file>"


@pytest.fixture
def run():
    def run(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
        """Run the program as a user would; return (exit code, stdout, stderr)."""
        done = subprocess.run(
            [sys.executable, str(ENTRY), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=30,
        )
        return done.returncode, done.stdout, done.stderr

    return run
```

`make_input(tmp_path, ...)` below stands for a small helper you write that
builds the input file the situation needs and returns its path.

1. **Exit code and error message.** Assert all three channels.

```python
def test_f1_ac2_1_missing_input(tmp_path, run):
    code, out, err = run("<command>", str(tmp_path / "absent"))
    assert code == 1
    assert err == "error: <message the issue states>\n"
    assert out == ""
```

2. **Literal output format.** Compare the whole output, byte for byte.

```python
def test_f1_ac3_1_line_format(tmp_path, run):
    src = tmp_path / "in.txt"
    src.write_text("<input that yields the value>\n")
    code, out, _ = run("<command>", str(src))
    assert code == 0
    assert out == "<exact expected output>\n"
```

3. **Default value.** Leave the option out; assert the default's effect.

```python
def test_f1_ac4_1_default_applies(tmp_path, run):
    # input with more items than the default allows, so the default is visible
    code, out, _ = run("<command>", str(make_input(tmp_path, items=5)))
    assert out.splitlines() == ["<first>", "<second>", "<third>"]
```

4. **Ordering with a tie.** Start unsorted; include a tie the tie-break decides.

```python
def test_f1_ac5_1_order_and_tie(tmp_path, run):
    code, out, _ = run("<command>", str(make_input(tmp_path, rows=[...])))
    assert out.splitlines() == ["<highest>", "<tie, first by rule>", "<tie, second>"]
```

5. **Boundary.** Test each edge and just outside it, plus a non-number. Every
   accepted case asserts the output its input yields, not only the exit code.

```python
import pytest


@pytest.mark.parametrize(
    "value, expected_out",
    [
        ("<min>", "<output the issue states for the minimum>\n"),
        ("<max>", "<output the issue states for the maximum>\n"),
    ],
)
def test_f1_ac6_1_range_edges_accepted(tmp_path, run, value, expected_out):
    code, out, err = run("<command>", "<--option>", value, str(make_input(tmp_path)))
    assert (code, out, err) == (0, expected_out, "")


@pytest.mark.parametrize("value", ["<min - 1>", "<max + 1>", "abc"])
def test_f1_ac6_2_outside_the_range_rejected(tmp_path, run, value):
    code, out, err = run("<command>", "<--option>", value, str(make_input(tmp_path)))
    assert (code, out, err) == (2, "", "error: <message the issue states>\n")
```

6. **Rejected input with its location.** Put the bad item after good ones.

```python
def test_f1_ac7_1_bad_item_located(tmp_path, run):
    src = tmp_path / "in.txt"
    src.write_text("<header>\n<good>\n<bad value>\n")
    code, out, err = run("<command>", str(src))
    assert (code, out) == (1, "")
    assert err == "error: line 3: <message with the bad value>\n"
```

7. **Empty input.** Input with nothing to process, and input filtered to nothing.

```python
def test_f1_ac8_1_empty(tmp_path, run):
    code, out, _ = run("<command>", str(make_input(tmp_path, rows=[])))
    assert (code, out) == (0, "<empty-case output the issue states>\n")
```

8. **Worked example from the issue.** Replay it exactly, then run one more
   input of your own so a hard-coded answer fails. Assert the exit code too.

```python
def test_f1_ac9_1_issue_example(tmp_path, run):
    src = tmp_path / "in.txt"
    src.write_text("""<the issue's example input, verbatim>""")
    code, out, _ = run("<command>", str(src))
    assert code == 0
    assert out == """<the issue's example output, verbatim>"""


def test_f1_ac9_2_another_input_not_hard_coded(tmp_path, run):
    src = tmp_path / "in.txt"
    src.write_text("""<a different input the rule decides>""")
    code, out, _ = run("<command>", str(src))
    assert code == 0
    assert out == """<the output the rule gives for it>"""
```

9. **Precedence between sources of a setting** (flag, environment, file,
   default). One test per cell: each source alone, the higher one winning, and
   an invalid lower source ignored when a higher one is given.

```python
import os


def test_f1_ac10_2_flag_beats_env(tmp_path, run):
    env = {**os.environ, "<VAR>": "<value a>"}
    code, out, _ = run(
        "<command>", "<--flag>", "<value b>", str(make_input(tmp_path)), env=env
    )
    assert "<effect of value b>" in out and "<effect of value a>" not in out
```

10. **Regression** (`Kind: regression`): write no new test; map to the
    criterion in `ac_tests` the repository's earlier tests that cover the
    behaviour it names (tests that passed before your change), and keep them
    passing unchanged. A regression criterion with no such test mapped stays
    open. Never edit or delete an earlier test to make it pass.

Rules for every template: the input must make the expected outcome differ from
the input and from a do-nothing program; assert the value, never only that the
command succeeded; one situation per test or per parametrised case.
