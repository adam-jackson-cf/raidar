"""Runtime pack CLI commands (agent image build/audit, lazy tool provisioning)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict

import click

from raidar.packs import available_runtimes, load_runtime_pack
from raidar.packs.python.image import (
    ImageAuditError,
    ImageNotBuiltError,
    audit_image,
    build_image,
    image_lock_hash,
    image_tag,
    inspect_image,
)
from raidar.packs.python.models import ToolExecution, load_tool_lock
from raidar.packs.python.provisioner import (
    EvalSandboxToolError,
    ToolProvisioner,
    ToolProvisioningError,
)

RUNTIME_CHOICE = click.Choice(list(available_runtimes()))


def register(main: click.Group) -> None:
    main.add_command(pack)


def _echo_json(payload: object) -> None:
    click.echo(json.dumps(payload, indent=2, default=str))


def _docker_failure(error: subprocess.CalledProcessError) -> click.ClickException:
    verb = " ".join(str(arg) for arg in error.cmd[1:3])
    detail = (error.stderr or "").strip()[-2000:]
    suffix = f": {detail}" if detail else " (see the docker output above)"
    return click.ClickException(f"docker {verb} failed with exit code {error.returncode}{suffix}")


@click.group()
def pack() -> None:
    """Runtime pack images and measurement tools."""


@pack.command("info")
@click.argument("runtime", type=RUNTIME_CHOICE)
def pack_info(runtime: str) -> None:
    """Show the pack image tag, lock hashes and tool classes."""
    spec, lock = load_runtime_pack(runtime), load_tool_lock()
    provisioner = ToolProvisioner(lock)
    record = inspect_image(image_tag(spec))
    _echo_json(
        {
            "runtime": runtime,
            "image_tag": image_tag(spec),
            "image_lock_hash": image_lock_hash(spec),
            "image_present": record is not None,
            "image_id": record.image_id if record else None,
            "tool_lock_hash": lock.lock_hash(),
            "tools": {name: tool.execution.value for name, tool in lock.tools.items()},
            "provisioned": {
                name: provisioner.is_provisioned(name)
                for name, tool in lock.tools.items()
                if tool.execution is ToolExecution.HOST_PARSE
            },
        }
    )


@pack.command("build")
@click.argument("runtime", type=RUNTIME_CHOICE)
@click.option("--force", is_flag=True, help="Rebuild even if the lock-hash tag exists.")
def pack_build(runtime: str, force: bool) -> None:
    """Build the agent image once per lock hash and assert it has no scorer tools."""
    try:
        result = build_image(load_runtime_pack(runtime), load_tool_lock(), force=force)
    except ImageAuditError as error:
        _echo_json(asdict(error.audit) | {"ok": False})
        raise click.ClickException(f"pack image {error.audit.tag} failed its audit") from error
    except subprocess.CalledProcessError as error:
        raise _docker_failure(error) from error
    _echo_json(asdict(result))


@pack.command("audit")
@click.argument("runtime", type=RUNTIME_CHOICE)
def pack_audit(runtime: str) -> None:
    """Assert the built agent image has the pinned agent CLIs and no scorer tools or browsers."""
    spec = load_runtime_pack(runtime)
    try:
        audit = audit_image(image_tag(spec), spec, load_tool_lock())
    except ImageNotBuiltError as error:
        raise click.ClickException(str(error)) from error
    except subprocess.CalledProcessError as error:
        raise _docker_failure(error) from error
    _echo_json(asdict(audit) | {"ok": audit.ok})
    if not audit.ok:
        raise click.ClickException(f"pack image {audit.tag} failed its audit")


@pack.command("provision")
@click.argument("runtime", type=RUNTIME_CHOICE)
@click.argument("tool")
def pack_provision(runtime: str, tool: str) -> None:
    """Provision one host-parse measurement tool into the lock-keyed host cache."""
    del runtime
    try:
        provisioned = ToolProvisioner(load_tool_lock()).ensure(tool)
    except (KeyError, EvalSandboxToolError, ToolProvisioningError) as error:
        raise click.ClickException(str(error)) from error
    _echo_json(
        {
            "tool": provisioned.name,
            "lock_hash": provisioned.lock_hash,
            "root": str(provisioned.root),
            "first_use": provisioned.first_use.as_record() if provisioned.first_use else None,
        }
    )
