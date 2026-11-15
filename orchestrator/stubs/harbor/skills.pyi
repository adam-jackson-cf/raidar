from dataclasses import dataclass
from pathlib import Path

from harbor.utils.git import select_ls_remote_sha as select_ls_remote_sha

__all__ = [
    "SKILL_FILE_NAME",
    "ResolvedRepo",
    "ResolvedSkill",
    "compute_skill_digest",
    "get_git_skill_metadata",
    "resolve_repo_source",
    "resolve_skill_sources",
    "resolve_skills",
    "select_ls_remote_sha",
]

SKILL_FILE_NAME: str

@dataclass(frozen=True)
class ResolvedRepo:
    host: str
    org: str
    name: str
    ref: str | None = ...
    subdir: str | None = ...
    @property
    def clone_url(self) -> str: ...
    @property
    def display_url(self) -> str: ...

@dataclass(frozen=True)
class ResolvedSkill:
    name: str
    source: Path

def resolve_repo_source(value: str) -> ResolvedRepo: ...
def resolve_skills(skills: list[str | Path]) -> list[ResolvedSkill]: ...
def resolve_skill_sources(values: list[str]) -> list[Path]: ...
def compute_skill_digest(skill_dir: Path) -> str: ...
def get_git_skill_metadata(skill_source: Path) -> tuple[str, str] | None: ...
