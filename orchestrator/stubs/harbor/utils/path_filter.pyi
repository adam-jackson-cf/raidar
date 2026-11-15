from collections.abc import Sequence

def filter_paths_by_patterns(
    paths: Sequence[str],
    *,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
) -> list[str]: ...
