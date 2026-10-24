"""Production-only verbosity (V) and erosion (E) evidence producer.

Ported from ``slop-code-bench`` ``src/slop_code/metrics/scoring/production_quality.py``
at ``25069ba``. Only production ``.py`` files of the assessor-owned inventory
count:

- V = 1 - |Ruff-flagged lines ∪ clone lines| / source LOC, with the pinned
  Ruff 0.8.6 SIM allowlist and the evaluator interpreter's token/AST clone
  detector (``snapshot_tool/evaluator.py``);
- E = 1 - high-complexity mass / total mass, where a function or method's
  mass is ``cc * sqrt(sloc)`` and high complexity is ``cc > 10``.

The fork's measurement lifecycle (Docker executor, saved run layout, locked
evaluator environments) is replaced by the pack tool environment
(``measurement.ToolEnvironment``). Validation rules, error codes, ordering
and Decimal arithmetic are the fork's.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Any, Literal, TypeGuard

from raidar.scoring.code_quality.contract import (
    EXPECTED_INTERPRETER,
    EXPECTED_RUFF_VERSION,
    PARSER_TOKENIZER_PROBE,
    RUFF_ARGUMENTS,
    RUFF_RULES,
)
from raidar.scoring.code_quality.measurement import (
    MeasurementError,
    ProcessResult,
    ToolEnvironment,
)
from raidar.scoring.code_quality.models import (
    EligibleSymbolEvidence,
    ExcludedSymbolEvidence,
    FileRole,
    ProductionQualityRawEvidence,
    QualityCloneGroupEvidence,
    QualityDecimalContextEvidence,
    QualityFileEvidence,
    QualityInterpreterEvidence,
    QualityRuffDiagnosticEvidence,
    QualityRuffEvidence,
    SymbolExclusionReason,
)

HIGH_COMPLEXITY = Decimal(10)
_HEX = frozenset("0123456789abcdef")
_RUFF_OUTSIDE = "score_evidence_invalid: Ruff path is outside production inventory"
_SYMBOL_INVALID = "production_symbol_evidence_invalid"


class ProductionQualityError(ValueError):
    """Evidence cannot be used to compute a production-quality component."""


@dataclass(frozen=True)
class ProductionQualityResult:
    """Components with their complete typed raw evidence."""

    verbosity: Decimal
    erosion: Decimal
    evidence: ProductionQualityRawEvidence


def _is_string_mapping(row: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(row, Mapping) and all(isinstance(key, str) for key in row)


def _value(row: object, *names: str) -> object:
    mapping: Mapping[str, object] | None = None
    if isinstance(row, Mapping):
        if not _is_string_mapping(row):
            raise ProductionQualityError(
                "score_evidence_invalid: evidence mapping keys must be strings"
            )
        mapping = row
    for name in names:
        if mapping is not None and name in mapping:
            return mapping[name]
        if hasattr(row, name):
            return getattr(row, name)
    return None


def _identity(file: object) -> str:
    path = _value(file, "path", "file_path")
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ProductionQualityError("score_evidence_invalid: invalid lexical file identity")
    try:
        path.encode("utf-8", "strict")
    except UnicodeError as error:
        raise ProductionQualityError(
            "score_evidence_invalid: invalid lexical file identity"
        ) from error
    return path


def production_files(inventory: Iterable[object]) -> tuple[object, ...]:
    """Production ``.py`` inventory rows in UTF-8 path order (duplicates rejected)."""
    selected = [
        item
        for item in inventory
        if _value(item, "role") in (FileRole.PRODUCTION, FileRole.PRODUCTION.value)
    ]
    selected = [item for item in selected if _identity(item).endswith(".py")]
    paths = [_identity(item) for item in selected]
    if len(paths) != len(set(paths)):
        raise ProductionQualityError("score_evidence_invalid: duplicate inventory identity")
    return tuple(sorted(selected, key=lambda item: _identity(item).encode("utf-8")))


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in _HEX for char in value)


def _expected_interpreter(interpreter: Mapping[str, object]) -> bool:
    version = interpreter.get("version")
    return (
        interpreter.get("implementation") == EXPECTED_INTERPRETER["implementation"]
        and interpreter.get("cache_tag") == EXPECTED_INTERPRETER["cache_tag"]
        and isinstance(version, str)
        and version.startswith(f"{EXPECTED_INTERPRETER['version']}.")
    )


def _validate_measurement_environment(payload: Mapping[str, object]) -> None:
    """Reject evidence that was not measured under the frozen evaluator contract."""
    interpreter = payload.get("interpreter")
    if (
        not _is_string_mapping(interpreter)
        or not _expected_interpreter(interpreter)
        or payload.get("parser_tokenizer_probe") != PARSER_TOKENIZER_PROBE.hex()
        or not _is_sha256(payload.get("parser_tokenizer_schema_id"))
    ):
        raise ProductionQualityError("measurement_environment_mismatch")


def _verified_payload(tools: ToolEnvironment, sources: Mapping[str, bytes]) -> dict[str, Any]:
    """Token and AST semantics of the evaluator interpreter, never this host's."""
    try:
        completed = tools.evaluate(sources)
    except MeasurementError as error:
        raise ProductionQualityError(str(error)) from error
    if completed.returncode:
        raise ProductionQualityError(
            "score_evidence_invalid: evaluator parser failed: "
            f"{completed.stderr or completed.stdout}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ProductionQualityError(
            "score_evidence_invalid: evaluator emitted malformed evidence"
        ) from error
    if not isinstance(payload, dict):
        raise ProductionQualityError("score_evidence_invalid: evaluator emitted malformed evidence")
    return payload


def _run(tools: ToolEnvironment, argv: Sequence[str], cwd: Path | None = None) -> ProcessResult:
    try:
        return tools.run(argv, cwd=cwd)
    except MeasurementError as error:
        if "timed out" in str(error):
            raise ProductionQualityError(str(error)) from error
        raise ProductionQualityError(
            "measurement_environment_mismatch: Ruff unavailable"
        ) from error


def _lexical_ruff_path(filename: str, root: Path, paths: Sequence[str]) -> str:
    reported = Path(filename)
    try:
        lexical = (
            reported.relative_to(root).as_posix()
            if reported.is_absolute()
            else (reported.as_posix())
        )
    except ValueError as error:
        raise ProductionQualityError(_RUFF_OUTSIDE) from error
    if lexical not in paths:
        raise ProductionQualityError(_RUFF_OUTSIDE)
    try:
        (root / lexical).resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ProductionQualityError(_RUFF_OUTSIDE) from error
    return lexical


def _ruff_range(location: Mapping[str, Any], end: Mapping[str, Any]) -> tuple[tuple[int, int], ...]:
    try:
        start = (int(location["row"]), int(location["column"]))
        finish = (int(end["row"]), int(end["column"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionQualityError("score_evidence_invalid: malformed Ruff range") from error
    if start < (1, 1) or finish <= start:
        raise ProductionQualityError("score_evidence_invalid: invalid Ruff range")
    return start, finish


def _normalized_diagnostic(
    item: Mapping[str, Any], root: Path, paths: Sequence[str]
) -> dict[str, Any] | None:
    code = item.get("code")
    location = item.get("location")
    end = item.get("end_location")
    filename = item.get("filename")
    if (
        not isinstance(location, Mapping)
        or not isinstance(end, Mapping)
        or not isinstance(filename, str)
    ):
        raise ProductionQualityError("score_evidence_invalid: unexpected Ruff diagnostic")
    lexical = _lexical_ruff_path(filename, root, paths)
    start, finish = _ruff_range(location, end)
    if code is None:
        message = item.get("message")
        if not isinstance(message, str) or not message.startswith("SyntaxError:"):
            raise ProductionQualityError("score_evidence_invalid: unexpected Ruff diagnostic")
        return None
    if code not in RUFF_RULES:
        raise ProductionQualityError("score_evidence_invalid: unexpected Ruff diagnostic")
    return {"code": code, "path": lexical, "start": start, "end": finish}


def _ruff(
    tools: ToolEnvironment, root: Path, paths: Sequence[str]
) -> tuple[str, tuple[dict[str, Any], ...], tuple[str, ...]]:
    version_result = _run(tools, ["-m", "ruff", "--version"])
    version = version_result.stdout.strip()
    if version_result.returncode != 0 or version != f"ruff {EXPECTED_RUFF_VERSION}":
        raise ProductionQualityError("measurement_environment_mismatch: Ruff version mismatch")
    invocation = (str(tools.python), *RUFF_ARGUMENTS, *paths)
    done = _run(tools, list(invocation[1:]), cwd=root)
    if done.returncode not in (0, 1):
        raise ProductionQualityError(
            "measurement_environment_mismatch: canonical Ruff rule unavailable"
        )
    try:
        diagnostics = json.loads(done.stdout)
    except json.JSONDecodeError as error:
        raise ProductionQualityError(
            "score_evidence_invalid: Ruff emitted malformed JSON"
        ) from error
    if not isinstance(diagnostics, list):
        raise ProductionQualityError("score_evidence_invalid: Ruff diagnostics are not a list")
    normalized = [
        row
        for item in diagnostics
        if (row := _normalized_diagnostic(item, root, paths)) is not None
    ]
    ordered = sorted(
        normalized,
        key=lambda item: (item["path"].encode(), item["start"], item["end"], item["code"]),
    )
    return version, tuple(ordered), invocation


def _read_sources(root: Path, paths: Sequence[str]) -> dict[str, bytes]:
    sources = {}
    for path in paths:
        target = (root / path).resolve()
        try:
            target.relative_to(root)
            sources[path] = target.read_bytes()
        except (OSError, ValueError) as error:
            raise ProductionQualityError(
                "score_evidence_invalid: production source unreadable"
            ) from error
    return sources


def _check_file_rows(file_rows: Iterable[object], paths: Sequence[str]) -> None:
    joined: dict[str, object] = {}
    for row in file_rows:
        path = _value(row, "path", "file_path")
        if isinstance(path, str):
            if path in joined:
                raise ProductionQualityError("score_evidence_invalid: duplicate file row")
            joined[path] = row
    if set(joined) & set(paths) != set(paths):
        raise ProductionQualityError("score_evidence_invalid: missing production file row")
    if any(_value(joined[path], "success") is not True for path in paths):
        raise ProductionQualityError("score_evidence_invalid: unsuccessful file row")


def _flagged_lines(
    diagnostics: Sequence[Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
    source_by_path: Mapping[str, set[int]],
) -> tuple[set[tuple[str, int]], set[tuple[str, int]]]:
    verbosity: set[tuple[str, int]] = set()
    clone_lines: set[tuple[str, int]] = set()
    for diagnostic in diagnostics:
        eligible = source_by_path.get(diagnostic["path"])
        if eligible is None:
            raise ProductionQualityError(_RUFF_OUTSIDE)
        # A range ending at column 1 does not cover its end row.
        stop = diagnostic["end"][0] + (diagnostic["end"][1] > 1)
        verbosity.update(
            (diagnostic["path"], line)
            for line in range(diagnostic["start"][0], stop)
            if line in eligible
        )
    for group in groups:
        for path, start, end in group["occurrences"]:
            clone_lines.update(
                (path, line) for line in range(start, end + 1) if line in source_by_path[path]
            )
    return verbosity, clone_lines


def _int_field(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass
class _SymbolLedger:
    """Eligible function/method mass for E, and every exclusion, in the fork's rules."""

    paths: tuple[str, ...]
    symbols: list[EligibleSymbolEvidence] = field(default_factory=list)
    excluded: list[ExcludedSymbolEvidence] = field(default_factory=list)
    identities: set[tuple[str, str, str, int, int]] = field(default_factory=set)
    total: Decimal = Decimal(0)
    erosion_mass: Decimal = Decimal(0)

    def add(self, row: object) -> None:
        kind = _value(row, "kind", "type")
        path = _value(row, "path", "file_path")
        path_text = path if isinstance(path, str) else ""
        if kind not in {"function", "method"}:
            self.excluded.append(
                ExcludedSymbolEvidence(
                    reason=SymbolExclusionReason.KIND_INELIGIBLE,
                    kind=kind if isinstance(kind, str) else "",
                    path=path_text,
                )
            )
            return
        eligible_kind: Literal["function", "method"] = (
            "function" if kind == "function" else "method"
        )
        if not isinstance(path, str) or path not in self.paths:
            self.excluded.append(
                ExcludedSymbolEvidence(
                    reason=SymbolExclusionReason.PATH_NOT_PRODUCTION,
                    kind=eligible_kind,
                    path=path_text,
                )
            )
            return
        self._measure(row, path, eligible_kind)

    def _identity(
        self, row: object, path: str, kind: Literal["function", "method"]
    ) -> tuple[str, str, Literal["function", "method"], int, int]:
        qualified = _value(row, "qualified_name", "name")
        start = _value(row, "start_line", "start")
        end = _value(row, "end_line", "end")
        if not isinstance(qualified, str) or not qualified:
            raise ProductionQualityError(_SYMBOL_INVALID)
        if not (_int_field(start) and _int_field(end)):
            raise ProductionQualityError(_SYMBOL_INVALID)
        identity = (path, qualified, kind, start, end)
        if start <= 0 or end <= 0 or end < start or identity in self.identities:
            raise ProductionQualityError(_SYMBOL_INVALID)
        self.identities.add(identity)
        return identity

    def _measure(self, row: object, path: str, kind: Literal["function", "method"]) -> None:
        identity = self._identity(row, path, kind)
        raw_cc = _value(row, "cc", "complexity")
        raw_loc = _value(row, "sloc", "SLOC")
        if isinstance(raw_cc, bool) or not _int_field(raw_loc) or raw_loc <= 0:
            raise ProductionQualityError(_SYMBOL_INVALID)
        try:
            cc = Decimal(str(raw_cc))
        except Exception as error:  # any unparseable complexity invalidates the symbol
            raise ProductionQualityError(_SYMBOL_INVALID) from error
        if not cc.is_finite() or cc < 0 or str(cc) != str(raw_cc):
            raise ProductionQualityError(_SYMBOL_INVALID)
        mass = cc * Decimal(raw_loc).sqrt()
        self.total += mass
        high = cc > HIGH_COMPLEXITY
        if high:
            self.erosion_mass += mass
        self.symbols.append(
            EligibleSymbolEvidence(
                identity=identity, cc=cc, sloc=raw_loc, mass=mass, high_complexity=high
            )
        )

    def ordered(
        self,
    ) -> tuple[tuple[EligibleSymbolEvidence, ...], tuple[ExcludedSymbolEvidence, ...]]:
        return (
            tuple(sorted(self.symbols, key=lambda symbol: symbol.identity)),
            tuple(
                sorted(
                    self.excluded,
                    key=lambda symbol: (
                        symbol.reason.value,
                        symbol.path.encode("utf-8"),
                        symbol.kind.encode("utf-8"),
                    ),
                )
            ),
        )


def _erosion(
    symbol_rows: Iterable[object], paths: tuple[str, ...]
) -> tuple[_SymbolLedger, Decimal]:
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        ledger = _SymbolLedger(paths)
        for row in symbol_rows:
            ledger.add(row)
        erosion = (
            Decimal(1) if ledger.total == 0 else Decimal(1) - ledger.erosion_mass / ledger.total
        )
    return ledger, erosion


def _verbosity(
    payload: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]
) -> tuple[Decimal, set[tuple[str, int]], set[tuple[str, int]], int, int]:
    source_by_path = {item["path"]: set(item["source_lines"]) for item in payload["files"]}
    denominator = sum(len(lines) for lines in source_by_path.values())
    flagged, clone_lines = _flagged_lines(diagnostics, payload["clone_groups"], source_by_path)
    numerator = len(flagged | clone_lines)
    if numerator > denominator:
        raise ProductionQualityError(
            "score_evidence_invalid: verbosity numerator exceeds source LOC"
        )
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        component = Decimal(1) - Decimal(numerator) / Decimal(denominator)
    return component, flagged, clone_lines, numerator, denominator


def _source_loc(payload: Mapping[str, Any]) -> int:
    return sum(len(set(item["source_lines"])) for item in payload["files"])


def _evidence(
    checkpoint_id: str,
    payload: Mapping[str, Any],
    ruff: tuple[str, tuple[dict[str, Any], ...], tuple[str, ...]],
    verbosity: tuple[Decimal, set[tuple[str, int]], set[tuple[str, int]], int, int],
    ledger: _SymbolLedger,
    erosion: Decimal,
) -> ProductionQualityRawEvidence:
    version, diagnostics, invocation = ruff
    component, flagged, clone_lines, numerator, denominator = verbosity
    symbols, excluded = ledger.ordered()
    return ProductionQualityRawEvidence(
        checkpoint_id=checkpoint_id,
        interpreter=QualityInterpreterEvidence.model_validate(payload["interpreter"]),
        parser_tokenizer_schema_id=payload["parser_tokenizer_schema_id"],
        parser_tokenizer_probe=payload["parser_tokenizer_probe"],
        files=tuple(QualityFileEvidence.model_validate(item) for item in payload["files"]),
        ruff=QualityRuffEvidence(
            version=version,
            invocation=invocation,
            rules=RUFF_RULES,
            diagnostics=tuple(
                QualityRuffDiagnosticEvidence.model_validate(item) for item in diagnostics
            ),
        ),
        clones=tuple(
            QualityCloneGroupEvidence.model_validate(group) for group in payload["clone_groups"]
        ),
        verbosity_lines=tuple(sorted(flagged)),
        clone_lines=tuple(sorted(clone_lines)),
        union_numerator=numerator,
        source_loc_denominator=denominator,
        eligible_symbols=symbols,
        excluded_symbols=excluded,
        erosion_numerator=ledger.erosion_mass,
        erosion_denominator=ledger.total,
        decimal_context=QualityDecimalContextEvidence(precision=50, rounding="ROUND_HALF_EVEN"),
        verbosity=component,
        erosion=erosion,
    )


def produce_production_quality(
    checkpoint_id: str,
    root: Path,
    inventory: Iterable[object],
    file_rows: Iterable[object],
    symbol_rows: Iterable[object],
    tools: ToolEnvironment,
) -> ProductionQualityResult:
    """Produce V/E from the assessor inventory and the snapshot's measured rows."""
    if not checkpoint_id:
        raise ProductionQualityError("score_evidence_invalid: checkpoint identity missing")
    root = root.resolve()
    paths = tuple(_identity(item) for item in production_files(inventory))
    _check_file_rows(file_rows, paths)
    payload = _verified_payload(tools, _read_sources(root, paths))
    _validate_measurement_environment(payload)
    if not _source_loc(payload):
        raise ProductionQualityError("production_source_loc_zero")
    ruff = _ruff(tools, root, paths)
    verbosity = _verbosity(payload, ruff[1])
    ledger, erosion = _erosion(symbol_rows, paths)
    if not (Decimal(0) <= verbosity[0] <= Decimal(1) and Decimal(0) <= erosion <= Decimal(1)):
        raise ProductionQualityError("score_evidence_invalid: component out of range")
    evidence = _evidence(checkpoint_id, payload, ruff, verbosity, ledger, erosion)
    return ProductionQualityResult(verbosity[0], erosion, evidence)
