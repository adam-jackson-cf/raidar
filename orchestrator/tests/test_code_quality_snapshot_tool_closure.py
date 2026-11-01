"""The snapshot tool's symbol rows, import tracing and call graph over small odd sources."""

from __future__ import annotations

from pathlib import Path

from raidar.scoring.code_quality.snapshot_tool import graph, imports, symbols
from tests.code_quality_support import materialise


def _rows(tmp_path: Path, source: str) -> list[dict[str, object]]:
    return symbols.get_symbols(materialise(tmp_path, {"m.py": source}) / "m.py")


def _names(rows: list[dict[str, object]]) -> list[tuple[object, object]]:
    return [(row["name"], row["type"]) for row in rows]


def test_only_plain_aliases_and_simple_assignment_targets_are_module_symbols(
    tmp_path: Path,
) -> None:
    rows = _rows(
        tmp_path,
        "type Gen[T] = list[T]\ntype Plain = int\na.b = 1\na, b = 1, 2\nz: int = 3\n"
        "@decorate\ndef decorated():\n    return 1\n",
    )

    assert _names(rows) == [("Plain", "type_alias"), ("z", "variable"), ("decorated", "function")]


def test_blank_and_nameless_sources_have_no_symbols(tmp_path: Path) -> None:
    assert _rows(tmp_path / "blank", "  \n\n") == []
    assert _rows(tmp_path / "nameless", "def (:\n    pass\nclass :\n    pass\n") == []


def test_function_rows_count_decisions_and_source_lines_only(tmp_path: Path) -> None:
    (row,) = _rows(
        tmp_path,
        "def f(a: int, b, *rest, **kw):\n"
        "    # a comment\n"
        "\n"
        "    if a and b:\n"
        "        return 'x'\n"
        "    return 1.5\n",
    )

    assert (row["type"], row["start"], row["end"]) == ("function", 1, 6)
    # 1 + `if` + `and`; the comment and the blank line are not source lines.
    assert (row["complexity"], row["sloc"]) == (3, 4)


def test_lineage_hashes_ignore_identifier_names_but_not_literals_or_signatures(
    tmp_path: Path,
) -> None:
    rows = _rows(
        tmp_path,
        "def one(a):\n    b = a\n    return b + 1\n\n"
        "def two(x):\n    y = x\n    return y + 1\n\n"
        "def three(a):\n    b = a\n    return b + 2\n\n"
        "def four(a: int) -> int:\n    b = a\n    return b + 1\n",
    )
    one, two, three, four = rows

    assert one["body_hash"] == two["body_hash"]
    assert one["body_hash"] != three["body_hash"]
    assert one["structure_hash"] == three["structure_hash"]
    assert one["signature_hash"] == two["signature_hash"]
    assert one["signature_hash"] != four["signature_hash"]


def test_methods_name_their_class_and_class_attributes_are_not_symbols(tmp_path: Path) -> None:
    rows = _rows(tmp_path, "class C:\n    y = 1\n    def m(self):\n        return self\n")

    assert _names(rows) == [("C", "class"), ("m", "method")]
    assert rows[1]["parent_class"] == "C"


_SNAPSHOT = {
    "app.py": (
        "from helper import work\n"
        "import b_mod, c_mod\n"
        "\n"
        "class Base:\n"
        "    def helper(self):\n"
        "        return 1\n"
        "\n"
        "class Child(Base):\n"
        "    def go(self):\n"
        "        self.helper()\n"
        "        self.absent()\n"
        "        handlers[0]()\n"
        "\n"
        "def main():\n"
        "    work()\n"
        "    shared()\n"
    ),
    "helper.py": "def work():\n    return 1\n",
    "b_mod.py": (
        "class Other:\n    def absent(self):\n        return 1\n\ndef shared():\n    return 1\n"
    ),
    "c_mod.py": "def shared():\n    return 2\n",
}


def test_calls_resolve_through_hierarchy_and_imports_and_never_guess_between_files(
    tmp_path: Path,
) -> None:
    snapshot = materialise(tmp_path, _SNAPSHOT)

    traversal = graph.dependency_graph_traversal(
        graph.build_dependency_graph(snapshot, Path("app.py"))
    )

    edges = {(edge["source"], edge["target"]) for edge in traversal["edges"]}
    assert edges == {
        ("app.py::Child.go", "app.py::Base.helper"),
        ("app.py::main", "helper.py::work"),
    }
    identities = {node["identity"] for node in traversal["nodes"]}
    assert {"b_mod.py::shared", "c_mod.py::shared", "b_mod.py::Other.absent"} <= identities


def test_a_missing_entrypoint_has_no_graph_and_no_files(tmp_path: Path) -> None:
    snapshot = materialise(tmp_path, {"app.py": "x = 1\n"})

    assert graph.build_dependency_graph(snapshot, Path("gone.py")).number_of_nodes() == 0
    assert imports.trace_source_files(Path("gone.py"), snapshot) == set()


def test_tracing_follows_local_imports_and_skips_what_it_cannot_read(tmp_path: Path) -> None:
    snapshot = materialise(
        tmp_path,
        {
            "__init__.py": "",
            "app.py": "import __init__\nimport broken\nfrom pkg.sub import thing\nimport os\n",
            "broken.py": b"import never\xff\n",
            "never.py": "",
            "pkg/__init__.py": "",
            "pkg/sub.py": "from .missing import y\nfrom . import sibling\n",
            "pkg/sibling.py": "",
        },
    )

    traced = imports.trace_source_files(Path("app.py"), snapshot)

    assert traced == {
        Path(name)
        for name in (
            "__init__.py",
            "app.py",
            "broken.py",
            "pkg/__init__.py",
            "pkg/sub.py",
            "pkg/sibling.py",
        )
    }


def test_a_non_python_entrypoint_is_traced_alone(tmp_path: Path) -> None:
    snapshot = materialise(tmp_path, {"notes.txt": "import helper\n", "helper.py": ""})

    assert imports.trace_source_files(Path("notes.txt"), snapshot) == {Path("notes.txt")}


def test_an_import_without_a_module_resolves_to_nothing(tmp_path: Path) -> None:
    snapshot = materialise(tmp_path, {"app.py": "from import *\n", "x.py": ""})

    assert imports.trace_source_files(Path("app.py"), snapshot) == {Path("app.py")}
