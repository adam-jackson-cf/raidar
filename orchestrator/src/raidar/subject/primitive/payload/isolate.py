"""Drop to the agent's user and deny the network for one process and its descendants.

Without ``CAP_SYS_ADMIN`` a network namespace is unavailable, so the child gets a seccomp-bpf
filter instead: ``socket`` is refused (``EPERM``), ``socketpair`` is allowed for ``AF_UNIX``
only (local wake-up pipes), and ``io_uring_*``, ``pidfd_getfd``, ``ptrace`` and
``process_vm_*`` (every other way to obtain or use a foreign socket) are refused. A foreign
syscall ABI (32-bit compat, x32) is refused or killed so the numbers cannot be dodged. The
filter survives ``fork`` and ``exec``. Each step is verified; any failure raises
``IsolationError`` and the caller must not exec.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import socket
import struct

PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2

_BPF_LD_ABS = 0x20  # BPF_LD | BPF_W | BPF_ABS
_BPF_JEQ = 0x15  # BPF_JMP | BPF_JEQ | BPF_K
_BPF_JGE = 0x35  # BPF_JMP | BPF_JGE | BPF_K
_BPF_RET = 0x06  # BPF_RET | BPF_K
_RET_ALLOW = 0x7FFF0000
_RET_KILL_PROCESS = 0x80000000
_RET_EPERM = 0x00050000 | errno.EPERM
_X32_BIT = 0x40000000
_AF_UNIX = 1
_OFF_NR, _OFF_ARCH, _OFF_ARG0 = 0, 4, 16

_AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
_SOCKETPAIR = {"x86_64": 53, "aarch64": 199}
_DENIED = {
    "x86_64": (41, 425, 426, 427, 438, 101, 310, 311),
    "aarch64": (198, 425, 426, 427, 438, 117, 270, 271),
}
_MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
_IO_URING_SETUP = 425


class IsolationError(RuntimeError):
    """Isolation could not be installed or verified; the child must not run."""


def _architecture() -> str:
    machine = platform.machine().lower()
    arch = _MACHINES.get(machine)
    if arch is None or platform.system() != "Linux":
        raise IsolationError(f"no seccomp filter for {platform.system()} {machine}")
    return arch


def _insn(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("HBBI", code, jt, jf, k)


def build_filter(arch: str) -> bytes:
    """The classic-BPF program for ``arch`` (``x86_64`` or ``aarch64``)."""
    head = [
        _insn(_BPF_LD_ABS, 0, 0, _OFF_ARCH),
        _insn(_BPF_JEQ, 1, 0, _AUDIT_ARCH[arch]),
        _insn(_BPF_RET, 0, 0, _RET_KILL_PROCESS),
        _insn(_BPF_LD_ABS, 0, 0, _OFF_NR),
    ]
    denied = list(_DENIED[arch])
    count = len(denied) + 1 + (1 if arch == "x86_64" else 0)
    end = len(head) + count  # the plain ALLOW; DENY is end + 1 and the socketpair test end + 2
    checks: list[bytes] = []
    for number in denied:
        checks.append(_insn(_BPF_JEQ, end - (len(head) + len(checks)), 0, number))
    checks.append(_insn(_BPF_JEQ, end + 1 - (len(head) + len(checks)), 0, _SOCKETPAIR[arch]))
    if arch == "x86_64":
        checks.append(_insn(_BPF_JGE, end - (len(head) + len(checks)), 0, _X32_BIT))
    tail = [
        _insn(_BPF_RET, 0, 0, _RET_ALLOW),
        _insn(_BPF_RET, 0, 0, _RET_EPERM),
        _insn(_BPF_LD_ABS, 0, 0, _OFF_ARG0),
        _insn(_BPF_JEQ, 1, 0, _AF_UNIX),
        _insn(_BPF_RET, 0, 0, _RET_EPERM),
        _insn(_BPF_RET, 0, 0, _RET_ALLOW),
    ]
    return b"".join([*head, *checks, *tail])


class _Program(ctypes.Structure):
    _fields_ = (("length", ctypes.c_ushort), ("filters", ctypes.c_void_p))


def _prctl(libc: ctypes.CDLL, *arguments: int) -> None:
    padded = [*arguments, 0, 0, 0, 0][:5]
    if libc.prctl(*(ctypes.c_ulong(value) for value in padded)) != 0:
        raise IsolationError(f"prctl{arguments[:1]} failed: {os.strerror(ctypes.get_errno())}")


def install_seccomp() -> None:
    """Set ``no_new_privs`` and install the filter, then prove ``socket`` is refused."""
    arch = _architecture()
    libc = ctypes.CDLL(None, use_errno=True)
    program = build_filter(arch)
    buffer = ctypes.create_string_buffer(program, len(program))
    descriptor = _Program(len(program) // 8, ctypes.addressof(buffer))
    _prctl(libc, PR_SET_NO_NEW_PRIVS, 1)
    _prctl(libc, PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.addressof(descriptor))
    _prove_denied(libc)


def _prove_denied(libc: ctypes.CDLL) -> None:
    try:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
    except OSError as error:
        if error.errno != errno.EPERM:
            raise IsolationError(f"socket() failed with {error!r}, not EPERM") from error
    else:
        raise IsolationError("socket() was not refused")
    ctypes.set_errno(0)
    refused = libc.syscall(ctypes.c_long(_IO_URING_SETUP), 0, 0) == -1
    if not refused or ctypes.get_errno() != errno.EPERM:
        raise IsolationError("io_uring_setup was not refused")
    unix, other = socket.socketpair(socket.AF_UNIX)  # local wake-up pipes still work
    unix.close()
    other.close()


def drop_privileges(uid: int, gid: int) -> None:
    """Clear supplementary groups and become ``uid``/``gid`` irreversibly (capabilities go too)."""
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    if (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) != (uid, uid, gid, gid):
        raise IsolationError("ids were not dropped")
    try:
        os.setuid(0)
    except PermissionError:
        return
    raise IsolationError("root was regained after the drop")
