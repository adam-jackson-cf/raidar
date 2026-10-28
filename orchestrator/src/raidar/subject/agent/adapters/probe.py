"""The shell probe a native agent runs as its agent user before a turn.

It reads what the agent process would run with (user, ``PATH``, ``TMPDIR``) after the same shell
start-up as the command, and the ``SKILL.md`` files under the roots the harness would read.
"""

from __future__ import annotations

SKILL_PREFIX = "skill="
FACTS = ("user", "path", "tmpdir")


def probe_command(startup: str, skill_roots: tuple[str, ...]) -> str:
    """The probe: ``startup`` (the command's shell start-up), the facts, then each root's skills."""
    roots = " ".join(skill_roots)
    return "; ".join(
        (
            startup,
            'echo "user=$(id -un)"',
            'echo "path=$PATH"',
            'echo "tmpdir=${TMPDIR-}"',
            f"for root in {roots}; do "
            f'[ -d "$root" ] && find "$root" -name SKILL.md -type f -exec printf '
            f"'{SKILL_PREFIX}%s\\n' {{}} \\; ; done; true",
        )
    )


def parse_probe(stdout: str) -> tuple[dict[str, str | None], list[str]]:
    """``(agent process facts, host SKILL.md paths)`` from the probe's output."""
    facts: dict[str, str | None] = dict.fromkeys(FACTS)
    skills: list[str] = []
    for line in stdout.splitlines():
        if line.startswith(SKILL_PREFIX):
            skills.append(line.removeprefix(SKILL_PREFIX))
            continue
        key, sep, value = line.partition("=")
        if sep and key in facts:
            facts[key] = value or None
    return facts, sorted(set(skills))
