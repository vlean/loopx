from __future__ import annotations

from loopx.slash_command_files import MANAGED_MARKER_PREFIX, managed_marker, skill_body


def _body(body_family: str) -> str:
    return skill_body(
        command="/loopx",
        title="LoopX /loopx",
        description="d",
        argument_hint="a",
        instructions=["first", "second"],
        body_family=body_family,
        front_matter_name="loopx",
    )


def test_marker_field_carries_the_body_family_not_a_host() -> None:
    """The marker's `surface=` value names the shared body, not the install host.

    Several hosts render the same body, so a reader must not treat this value as
    a host identity. Only the prefix is ever parsed.
    """

    assert managed_marker(command="/loopx", body_family="claude-skills") == (
        f"{MANAGED_MARKER_PREFIX} command=/loopx surface=claude-skills -->"
    )


def test_body_family_selects_the_prose_label() -> None:
    assert "Treat this as the LoopX `/loopx` slash command." in _body("claude-skills")
    assert "Treat this as the LoopX `/loopx` DSH workflow skill." in _body("dsh-skills")
    assert (
        "Treat this as the LoopX `/loopx` explicit LoopX command skill."
        in _body("codex-skills")
    )


def test_every_body_family_is_detectable_as_loopx_managed() -> None:
    for body_family in ("claude-skills", "codex-skills", "dsh-skills"):
        assert MANAGED_MARKER_PREFIX in _body(body_family)
