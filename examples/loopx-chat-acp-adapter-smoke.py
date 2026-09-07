#!/usr/bin/env python3
"""Offline ACP v1 stdio contract smoke for LoopX Chat."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import stat
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.chat_acp import ACPStdioAdapter  # noqa: E402
from loopx.chat_agent import CodexChatAgentError  # noqa: E402
from loopx.chat_endpoints import AgentEndpointRegistry  # noqa: E402
from loopx.chat_runtime import (  # noqa: E402
    ChatRuntimeController,
    adapter_supports_image_attachments,
    adapter_supports_steering,
)
from loopx.chat_store import ChatSessionStore  # noqa: E402
from loopx.kiro_cli_goal_mode import KIRO_CLI_CHAT_AGENT_ID  # noqa: E402


FAKE_ACP = r'''#!/usr/bin/env python3
import json
import sys
import time
from pathlib import Path

image_report_path = sys.argv[1] if len(sys.argv) > 1 else "/dev/null"
session_id = "acp:fixture/session"
active_prompt_id = None
session_cwd = ""
for line in sys.stdin:
    request = json.loads(line)
    assert request.get("jsonrpc") == "2.0", request
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        assert request["params"]["protocolVersion"] == 1
        assert request["params"]["clientCapabilities"]["terminal"] is False
        result = {
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": True,
                "sessionCapabilities": {"close": {}},
                "promptCapabilities": {"image": True},
            },
        }
    elif method == "session/new":
        assert request["params"]["mcpServers"] == []
        session_cwd = request["params"]["cwd"]
        result = {"sessionId": session_id}
    elif method == "session/load":
        assert request["params"]["sessionId"] == session_id
        session_cwd = request["params"]["cwd"]
        result = {}
    elif method == "session/prompt":
        active_prompt_id = request_id
        blocks = request["params"]["prompt"]
        prompt = blocks[0]["text"]
        image_blocks = [item for item in blocks[1:] if item.get("type") == "image"]
        if image_blocks:
            # An Agent that advertised promptCapabilities.image must receive
            # split payload/mime fields, not the client's data URL.
            for block in image_blocks:
                assert set(block) == {"type", "data", "mimeType"}, block
                assert not block["data"].startswith("data:"), block
            Path(image_report_path).write_text(
                json.dumps(image_blocks), encoding="utf-8"
            )
        if "wait for cancel" in prompt:
            continue
        if "activity renew" in prompt:
            for _ in range(4):
                time.sleep(0.06)
                print(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"sessionId": session_id, "update": {
                        "sessionUpdate": "plan",
                        "entries": [],
                    }},
                }), flush=True)
        print(json.dumps({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session_id, "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "private thought"},
            }},
        }), flush=True)
        print(json.dumps({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session_id, "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "tool-1",
                "title": f"读取 {session_cwd}/private-status.json",
                "kind": "read",
                "status": "in_progress",
            }},
        }), flush=True)
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": 900,
            "method": "session/request_permission",
            "params": {
                "sessionId": session_id,
                "toolCall": {"toolCallId": "tool-2", "status": "pending"},
                "options": [],
            },
        }), flush=True)
        permission = json.loads(sys.stdin.readline())
        assert permission["id"] == 900
        assert permission["result"]["outcome"]["outcome"] == "cancelled"
        envelope = '<loopx-review-json>' + json.dumps({
            "schema_version": "loopx_chat_agent_response_v0",
            "message": "ACP 回答。",
            "proposals": [],
            "gate": None,
        }, ensure_ascii=False) + '</loopx-review-json>'
        response = "ACP 回答。\n" + envelope
        marker_split = response.index("<loopx-review-json>") + 5
        for chunk in (response[:marker_split], response[marker_split:]):
            print(json.dumps({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": session_id, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": chunk},
                }},
            }, ensure_ascii=False), flush=True)
        result = {"stopReason": "end_turn"}
    elif method == "session/cancel":
        if active_prompt_id is not None:
            print(json.dumps({"jsonrpc": "2.0", "id": active_prompt_id, "result": {"stopReason": "cancelled"}}), flush=True)
            active_prompt_id = None
        continue
    elif method == "session/close":
        result = {}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
'''


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-chat-acp-") as raw_tmp:
        root = Path(raw_tmp)
        fake = root / "fake-acp"
        fake.write_text(FAKE_ACP, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        adapter = ACPStdioAdapter.start(command=(str(fake),), work_dir=root)
        assert adapter.upstream_thread_id == "acp:fixture/session"
        assert adapter.capabilities()["resume"] is True
        # ACP v1 has no mid-turn steering call; the runtime gate must read the
        # declaration rather than the adapter class, or a host is offered a lane
        # it refuses at runtime.
        assert adapter.capabilities()["steering"] is False
        assert adapter_supports_steering(adapter) is False
        # This Agent advertised promptCapabilities.image, so images are allowed.
        assert adapter.capabilities()["images"] is True
        assert adapter_supports_image_attachments(adapter) is True
        events: list[tuple[str, dict[str, object]]] = []
        response = adapter.start_turn("检查状态", lambda kind, payload: events.append((kind, payload)))
        assert response["message"] == "ACP 回答。", response
        visible = "".join(
            str(payload.get("text") or "")
            for kind, payload in events
            if kind == "answer.delta"
        )
        assert visible.strip() == "ACP 回答。", events
        assert "<loopx-review-json>" not in visible, visible
        assert any(kind == "agent.phase" and "读取 [project]" in str(payload.get("label")) for kind, payload in events)
        assert str(root) not in str(events), events
        assert not any("private thought" in str(payload) for _, payload in events), events
        adapter.close_session()

        # An advertised image capability must reach the Agent as an ACP image
        # block with split payload and mime type. The fixture asserts the shape
        # on receipt and reports what it saw.
        image_report = root / "image-blocks.json"
        with_images = ACPStdioAdapter.start(
            command=(str(fake), str(image_report)), work_dir=root
        )
        image_response = with_images.start_turn_with_attachments(
            "看这张图",
            lambda *_: None,
            [
                {
                    "data_url": "data:image/png;base64,aGVsbG8=",
                    "id": "att-1",
                    "mime_type": "image/png",
                    "name": "shot.png",
                    "size": 5,
                }
            ],
        )
        assert image_response["message"] == "ACP 回答。", image_response
        delivered = json.loads(image_report.read_text(encoding="utf-8"))
        assert delivered == [
            {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}
        ], delivered
        with_images.close_session()

        # An Agent that never advertised image support must be refused before a
        # prompt is sent, not after the protocol rejects the block.
        silent = root / "fake-acp-no-images"
        silent.write_text(
            FAKE_ACP.replace('"promptCapabilities": {"image": True},', ""),
            encoding="utf-8",
        )
        silent.chmod(silent.stat().st_mode | stat.S_IXUSR)
        no_images = ACPStdioAdapter.start(command=(str(silent),), work_dir=root)
        assert no_images.capabilities()["images"] is False
        assert adapter_supports_image_attachments(no_images) is False
        try:
            no_images.start_turn_with_attachments(
                "看这张图",
                lambda *_: None,
                [
                    {
                        "data_url": "data:image/png;base64,aGVsbG8=",
                        "id": "att-1",
                        "mime_type": "image/png",
                        "name": "shot.png",
                        "size": 5,
                    }
                ],
            )
        except CodexChatAgentError as exc:
            assert exc.error_code == "provider_unavailable", exc.error_code
        else:  # pragma: no cover - guarded by the assertion above
            raise AssertionError("an unadvertised image capability was accepted")
        no_images.close_session()

        renewed = ACPStdioAdapter.start(
            command=(str(fake),),
            work_dir=root,
            idle_timeout_sec=0.15,
            hard_timeout_sec=2,
        )
        renewed_response = renewed.start_turn("activity renew", lambda *_: None)
        assert renewed_response["message"] == "ACP 回答。", renewed_response
        renewed.close_session()

        resumed = ACPStdioAdapter.start(
            command=(str(fake),),
            work_dir=root,
            resume_thread_id="acp:fixture/session",
        )
        events = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(
                resumed.start_turn,
                "wait for cancel",
                lambda kind, payload: events.append((kind, payload)),
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(kind == "turn.started" for kind, _ in events):
                time.sleep(0.01)
            resumed.interrupt_turn()
            running.result(timeout=2)
        resumed.close_session()

        store = ChatSessionStore(root / "runtime")
        endpoint_registry = AgentEndpointRegistry(store.root)
        endpoint_registry.upsert(
            {
                "agent_id": "fixture-acp",
                "display_name": "Fixture ACP",
                "command": [str(fake)],
                "location": "local",
            }
        )
        first = ChatRuntimeController(
            store=store,
            codex_bin="missing-codex-for-fixture",
            endpoint_registry=endpoint_registry,
        )
        session, was_resumed = first.open_session(
            goal_id="fixture-goal",
            agent_id="fixture-acp",
            work_dir=root,
            objective="Exercise the ACP route.",
            mode="resume_latest",
        )
        assert was_resumed is False
        assert session["upstream_thread_id"] == "acp:fixture/session"
        turn, created = first.submit_turn(
            session_id=str(session["session_id"]),
            client_turn_id="fixture-turn",
            message="检查状态",
            work_dir=root,
            objective="Exercise the ACP route.",
        )
        assert created is True
        completed = first.wait_for_turn(
            session_id=str(session["session_id"]),
            turn_id=str(turn["turn_id"]),
            timeout_sec=3,
        )
        assert completed["status"] == "completed", completed
        first.close()
        second = ChatRuntimeController(
            store=store,
            codex_bin="missing-codex-for-fixture",
            endpoint_registry=endpoint_registry,
        )
        restored, was_resumed = second.open_session(
            goal_id="fixture-goal",
            agent_id="fixture-acp",
            work_dir=root,
            objective="Exercise the ACP route.",
            mode="resume_latest",
        )
        assert was_resumed is True
        assert restored["session_id"] == session["session_id"]
        second.close()

        # Kiro CLI is a built-in ACP agent, not an owner-registered endpoint:
        # the capability row must advertise it and `open_session` must reach the
        # same ACP adapter. Pointing kiro_cli_bin at the fixture keeps this
        # offline while still proving the built-in route, because a row that
        # renders in the dashboard but dead-ends at session open is decorative.
        kiro_store = ChatSessionStore(root / "kiro-runtime")
        kiro = ChatRuntimeController(
            store=kiro_store,
            codex_bin="missing-codex-for-fixture",
            kiro_cli_bin=str(fake),
        )
        row = next(
            item
            for item in kiro.capabilities()
            if item["agent_id"] == KIRO_CLI_CHAT_AGENT_ID
        )
        assert row["source"] == "builtin", row
        assert row["adapter_kind"] == "acp", row
        assert row["display_name"] == "Kiro CLI", row
        assert row["available"] is True, row
        assert row["trust_scope"] == "read_only", row
        kiro_session, kiro_resumed = kiro.open_session(
            goal_id="fixture-goal",
            agent_id=KIRO_CLI_CHAT_AGENT_ID,
            work_dir=root,
            objective="Exercise the built-in Kiro CLI ACP route.",
            mode="resume_latest",
        )
        assert kiro_resumed is False
        assert kiro_session["upstream_thread_id"] == "acp:fixture/session"
        kiro_turn, kiro_created = kiro.submit_turn(
            session_id=str(kiro_session["session_id"]),
            client_turn_id="kiro-turn",
            message="检查状态",
            work_dir=root,
            objective="Exercise the built-in Kiro CLI ACP route.",
        )
        assert kiro_created is True
        kiro_completed = kiro.wait_for_turn(
            session_id=str(kiro_session["session_id"]),
            turn_id=str(kiro_turn["turn_id"]),
            timeout_sec=3,
        )
        assert kiro_completed["status"] == "completed", kiro_completed
        kiro.close()

        # A built-in id must not be claimable by an owner-local endpoint, or the
        # registry row would silently shadow the built-in adapter.
        try:
            endpoint_registry.upsert(
                {
                    "agent_id": KIRO_CLI_CHAT_AGENT_ID,
                    "display_name": "Shadow Kiro",
                    "command": [str(fake)],
                }
            )
        except ValueError as exc:
            assert "reserved" in str(exc), exc
        else:  # pragma: no cover - guarded by the assertion above
            raise AssertionError("a reserved built-in agent id was accepted")

        # An uninstalled host renders as needing configuration instead of
        # failing at session open.
        unavailable = ChatRuntimeController(
            store=ChatSessionStore(root / "kiro-missing"),
            codex_bin="missing-codex-for-fixture",
            kiro_cli_bin="loopx-missing-kiro-cli-for-fixture",
        )
        missing_row = next(
            item
            for item in unavailable.capabilities()
            if item["agent_id"] == KIRO_CLI_CHAT_AGENT_ID
        )
        assert missing_row["available"] is False, missing_row
        unavailable.close()

    print("loopx-chat-acp-adapter-smoke: ok")


if __name__ == "__main__":
    main()
