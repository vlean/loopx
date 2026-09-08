from __future__ import annotations

from pathlib import Path
from typing import Any

MANAGED_MARKER_PREFIX = "<!-- loopx-managed-slash-command:v1"
LEGACY_UPGRADABLE_SIGNATURES = (
    "loopx goal-mode setup (NOT Claude Code's built-in /goal)",
    "The output is loopx control-plane SETUP",
    "goalmode_cmd.py",
)
EXISTING_LOOPX_CAPABILITY_SKILL_SIGNATURES = (
    "# LoopX PR Review",
    "Run `loopx pr-review` first",
)


def managed_marker(*, command: str, body_family: str) -> str:
    """Stamp the LoopX ownership marker for a generated command file.

    ``body_family`` names the *body shape*, not the install surface: several
    hosts share one generated body, so `~/.kiro/skills` and `CLAUDE_HOME/skills`
    both carry `claude-skills`. Only the marker prefix is ever parsed — upgrade
    and uninstall test for `MANAGED_MARKER_PREFIX` presence — so this value is
    informational and must not be read back as a host identity.
    """

    return f"{MANAGED_MARKER_PREFIX} command={command} surface={body_family} -->"


def front_matter(*, fields: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        escaped = value.replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines)


def skill_body(
    *,
    command: str,
    title: str,
    description: str,
    argument_hint: str,
    instructions: list[str],
    body_family: str,
    front_matter_name: str | None = None,
) -> str:
    """Render one managed command file body.

    ``body_family`` selects the shared body shape and its prose label; it is not
    the install surface. ``install_skill_facade`` deliberately renders every
    directory-layout skill host from the same `claude-skills` body so the files
    stay byte-comparable across hosts.
    """
    fields = {"description": description, "argument-hint": argument_hint}
    if front_matter_name:
        fields = {"name": front_matter_name, **fields}
    surface_label = (
        "slash command"
        if body_family == "claude-skills"
        else "DSH workflow skill"
        if body_family == "dsh-skills"
        else "explicit LoopX command skill"
    )
    return "\n\n".join(
        [
            front_matter(fields=fields),
            managed_marker(command=command, body_family=body_family),
            f"# {title}",
            f"Treat this as the LoopX `{command}` {surface_label}.",
            "\n".join(instructions),
            "Keep public/private boundaries intact and do not perform external writes unless the active LoopX state or owner explicitly authorizes them.",
        ]
    ) + "\n"


def _is_legacy_upgradable_loopx_file(existing: str) -> bool:
    return any(signature in existing for signature in LEGACY_UPGRADABLE_SIGNATURES)


def _is_existing_loopx_capability_skill(existing: str) -> bool:
    return any(
        signature in existing
        for signature in EXISTING_LOOPX_CAPABILITY_SKILL_SIGNATURES
    )


def target_status(path: Path, content: str, *, execute: bool) -> str:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if MANAGED_MARKER_PREFIX not in existing:
            if _is_legacy_upgradable_loopx_file(existing):
                if execute:
                    path.write_text(content, encoding="utf-8")
                return "upgraded_legacy_managed"
            if path.name == "SKILL.md" and _is_existing_loopx_capability_skill(existing):
                return "preserved_existing_loopx_skill"
            return "skipped_user_file"
        if existing == content:
            return "unchanged"
        if execute:
            path.write_text(content, encoding="utf-8")
        return "updated"
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return "created" if execute else "would_create"


def retire_managed_file(path: Path, *, execute: bool) -> str | None:
    if not path.exists():
        return None
    if MANAGED_MARKER_PREFIX not in path.read_text(encoding="utf-8"):
        return "skipped_user_file"
    if execute:
        path.unlink()
    return "retired_managed_file" if execute else "would_retire_managed_file"


def retire_status(path: Path, *, execute: bool) -> str:
    return retire_managed_file(path, execute=execute) or "absent"


def install_skill_facade(
    *,
    specs: list[dict[str, Any]],
    installed: list[dict[str, Any]],
    skills_dir: Path,
    surface: str,
    host_surfaces: list[str],
    mechanism: str,
    execute: bool,
    uninstall: bool,
    invoke_prefix: str = "",
    flat: bool = False,
) -> None:
    """Write managed command facades in directory or flat host layouts."""
    for spec in specs:
        path = (
            skills_dir / f"{spec['name']}.md"
            if flat
            else skills_dir / str(spec["name"]) / "SKILL.md"
        )
        if uninstall:
            installed.append(
                {
                    "surface": surface,
                    "host_surfaces": list(host_surfaces),
                    "mechanism": mechanism,
                    "command": spec["command"],
                    "path": str(path),
                    "status": retire_status(path, execute=execute),
                    "invoke_as": [],
                }
            )
            continue
        content = skill_body(
            command=str(spec["command"]),
            title=f"LoopX {spec['command']}",
            description=str(spec["description"]),
            argument_hint=str(spec["argument_hint"]),
            instructions=list(spec["instructions"]),
            body_family="claude-skills",
            front_matter_name=str(spec["name"]),
        )
        installed.append(
            {
                "surface": surface,
                "host_surfaces": list(host_surfaces),
                "mechanism": mechanism,
                "command": spec["command"],
                "path": str(path),
                "status": target_status(path, content, execute=execute),
                "invoke_as": [f"{invoke_prefix}{spec['name']}"],
            }
        )
