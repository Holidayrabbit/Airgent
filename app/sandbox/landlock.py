"""Linux Landlock sandbox (unprivileged, kernel >= 5.13).

Pure-Python Landlock via ctypes: no helper binary, no compilation. The
launch prefix re-execs this module (`python -m app.sandbox.landlock`)
which installs a Landlock ruleset and then execs the pi kernel, so the
restriction applies to the kernel process and everything it spawns.

Modes (same tiers as the other providers):
- read-only:      FS writes allowed only under the Airgent data dir + tmp
- workspace-write (default): FS writes allowed under workspace + data dir + tmp
- full:           no Landlock rules installed (provider still selects network,
                  which Landlock does not govern)

Landlock restricts filesystem access only; network confinement is not
covered — combine with AIRGENT_SANDBOX_NETWORK expectations accordingly.
"""

from __future__ import annotations

import os
import sys

from app.core.config import Settings
from app.sandbox.base import SandboxError, SandboxSpec

# Syscall numbers are generic across architectures since Linux 5.13.
_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT = 446

_LANDLOCK_CREATE_RULESET_VERSION = 1

# Filesystem access rights (Landlock ABI v1).
_ACCESS_EXECUTE = 1 << 0
_ACCESS_WRITE_FILE = 1 << 1
_ACCESS_READ_FILE = 1 << 2
_ACCESS_READ_DIR = 1 << 3
_ACCESS_REMOVE_DIR = 1 << 4
_ACCESS_REMOVE_FILE = 1 << 5
_ACCESS_MAKE_CHAR = 1 << 6
_ACCESS_MAKE_DIR = 1 << 7
_ACCESS_MAKE_REG = 1 << 8
_ACCESS_MAKE_SOCK = 1 << 9
_ACCESS_MAKE_FIFO = 1 << 10
_ACCESS_MAKE_BLOCK = 1 << 11
_ACCESS_MAKE_SYM = 1 << 12
_ACCESS_REFER = 1 << 13  # ABI v2
_ACCESS_TRUNCATE = 1 << 14  # ABI v3

_HANDLED = (
    _ACCESS_EXECUTE
    | _ACCESS_WRITE_FILE
    | _ACCESS_READ_FILE
    | _ACCESS_READ_DIR
    | _ACCESS_REMOVE_DIR
    | _ACCESS_REMOVE_FILE
    | _ACCESS_MAKE_CHAR
    | _ACCESS_MAKE_DIR
    | _ACCESS_MAKE_REG
    | _ACCESS_MAKE_SOCK
    | _ACCESS_MAKE_FIFO
    | _ACCESS_MAKE_BLOCK
    | _ACCESS_MAKE_SYM
    | _ACCESS_REFER
    | _ACCESS_TRUNCATE
)

# Rights granted inside allowed write roots: everything except unrestricted
# execute (execution is permitted for the toolchain paths below).
_WRITE_ROOTS_RIGHTS = (
    _ACCESS_READ_FILE
    | _ACCESS_READ_DIR
    | _ACCESS_WRITE_FILE
    | _ACCESS_REMOVE_DIR
    | _ACCESS_REMOVE_FILE
    | _ACCESS_MAKE_DIR
    | _ACCESS_MAKE_REG
    | _ACCESS_MAKE_SOCK
    | _ACCESS_MAKE_FIFO
    | _ACCESS_MAKE_SYM
    | _ACCESS_TRUNCATE
    | _ACCESS_REFER
)


def _landlock_syscall(number: int, *args: int) -> int:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(ctypes.c_long(number), *(ctypes.c_long(a) for a in args))
    if result < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return int(result)


class _RulesetAttr(ctypes.Structure if False else object):  # populated lazily
    pass


def _install_ruleset(allowed_write_roots: list[str]) -> None:
    import ctypes

    class RulesetAttr(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathBeneathAttr(ctypes.Structure):
        _fields_ = [
            ("allowed_access", ctypes.c_uint64),
            ("parent_fd", ctypes.c_int32),
            ("reserved", ctypes.c_uint32),
        ]

    attr = RulesetAttr(_HANDLED)
    fd = _landlock_syscall(
        _SYS_LANDLOCK_CREATE_RULESET,
        ctypes.addressof(attr),
        ctypes.sizeof(RulesetAttr),
        _LANDLOCK_CREATE_RULESET_VERSION,
    )
    for root in allowed_write_roots:
        try:
            parent_fd = os.open(root, os.O_PATH | os.O_CLOEXEC)
        except OSError as exc:
            raise SandboxError(f"landlock: cannot open allowed root {root}: {exc}") from exc
        rule = PathBeneathAttr(_WRITE_ROOTS_RIGHTS, parent_fd, 0)
        try:
            _landlock_syscall(
                _SYS_LANDLOCK_ADD_RULE,
                fd,
                1,  # LANDLOCK_RULE_PATH_BENEATH
                ctypes.addressof(rule),
                0,
            )
        finally:
            os.close(parent_fd)
    _landlock_syscall(_SYS_LANDLOCK_RESTRICT, fd, 0)


def landlock_spec(settings: Settings) -> SandboxSpec:
    if sys.platform != "linux":
        raise SandboxError(
            "The landlock sandbox provider requires Linux (kernel >= 5.13). "
            f"This host is {sys.platform}; use seatbelt (macOS) or docker instead."
        )
    if settings.sandbox_mode not in {"read-only", "workspace-write", "full"}:
        raise SandboxError(
            f"Unknown sandbox mode {settings.sandbox_mode!r}; "
            "expected read-only, workspace-write, or full."
        )
    if settings.sandbox_mode == "full":
        # No rules: Landlock adds nothing beyond what the provider documents.
        return SandboxSpec(
            provider="landlock",
            summary="linux landlock mode=full (no fs restrictions installed)",
        )

    roots = [str(settings.data_dir), "/tmp"]
    if settings.sandbox_mode == "workspace-write":
        roots.insert(0, str(settings.project_root))
    argv = [
        sys.executable,
        "-m",
        "app.sandbox.landlock",
        "--root",
        *roots,
        "--",
    ]
    return SandboxSpec(
        provider="landlock",
        argv_prefix=argv,
        summary=(
            f"linux landlock mode={settings.sandbox_mode} "
            f"workspace={settings.project_root} network-unmanaged"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry: python -m app.sandbox.landlock --root DIR ... -- CMD ARGS"""

    args = list(sys.argv[1:] if argv is None else argv)
    roots: list[str] = []
    if args and args[0] == "--root":
        args = args[1:]
        while args and not args[0].startswith("--"):
            roots.append(args.pop(0))
    if not args or args[0] != "--":
        print("usage: python -m app.sandbox.landlock --root DIR ... -- CMD [ARGS]", file=sys.stderr)
        return 2
    command = args[1:]
    if not command:
        print("landlock: no command given after --", file=sys.stderr)
        return 2
    if sys.platform != "linux":
        print(
            f"landlock: requires Linux (kernel >= 5.13); this host is {sys.platform}",
            file=sys.stderr,
        )
        return 2
    try:
        _install_ruleset(roots)
    except (OSError, SandboxError) as exc:
        print(f"landlock: {exc}", file=sys.stderr)
        return 2
    os.execvpe(command[0], command, os.environ)
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
