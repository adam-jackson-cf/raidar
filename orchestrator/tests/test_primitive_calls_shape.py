"""Call-transport shape checks that start no process: record manifests and the seccomp program."""

from __future__ import annotations

import struct

import pytest

from raidar.subject.primitive import calls
from raidar.subject.primitive.payload import isolate


def test_a_malformed_record_manifest_is_a_violation_not_a_raise() -> None:
    for document in ({"path": "x"}, {"path": "x", "entries": 5}, {"entries": [{"path": 1}]}):
        assert calls.record_manifest(document).violation.startswith("malformed manifest")


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_the_seccomp_program_is_well_formed_and_names_the_denied_calls(arch: str) -> None:
    program = isolate.build_filter(arch)
    instructions = list(struct.iter_unpack("HBBI", program))

    for position, (code, jump_true, _, _) in enumerate(instructions):
        if code in (0x15, 0x35):
            assert position + 1 + jump_true < len(instructions)
    assert instructions[-1][0] == 0x06
    numbers = {k for code, _, _, k in instructions if code == 0x15}
    assert numbers >= {41 if arch == "x86_64" else 198, 425, 426, 427, 438}
