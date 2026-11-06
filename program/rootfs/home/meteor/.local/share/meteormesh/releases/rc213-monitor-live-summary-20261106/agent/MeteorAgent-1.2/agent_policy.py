"""MeteorMesh Agent permission floor: offline specification, NOT installed.

No tool execution, no filesystem or network effects. Production MUST enforce a
server-side tool policy and durable redacted audit, never just an LLM prompt.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class Mode(str, Enum):
    OBSERVE = "observe"
    SHADOW = "shadow"
    ASSISTED = "assisted"


READ_ONLY_TOOLS = frozenset({
    "get_overview", "list_devices", "get_health", "list_nights",
    "list_segments", "get_disk_budget", "get_event", "get_logs_redacted",
    "get_build_info", "explain_failure",
})
APPROVAL_TOOLS = frozenset({
    "retry_transient_job", "enqueue_verified_analysis", "resume_ingest",
    "start_night", "stop_capture", "change_capture_profile",
    "archive_verified_event",
})
FORBIDDEN_TOOLS = frozenset({
    "sudo_shell", "arbitrary_shell", "delete_raw", "purge_raw_files",
    "format_disk", "edit_netplan", "disable_tailscale", "update_firewall",
    "systemctl_enable", "install_packages", "deploy_code", "reboot",
    "change_secrets",
})


@dataclass(frozen=True)
class GateState:
    mm01: bool = False
    session_engine: bool = False
    client_adapter: bool = False
    genuine_e2e: bool = False
    shadow_audit_ready: bool = False
    explicit_operator_approval: bool = False


@dataclass(frozen=True)
class Decision:
    permitted: bool
    reason: str
    # No claim that an operation executed; this is an authorization decision only.
    evidence_status: str = "SUGGESTED"


def authorize(tool_name: str, mode: Mode, gates: GateState,
              *, parameters: Mapping[str, object] | None = None) -> Decision:
    """Deny-by-default policy; explicitly NO mutation support in this prototype.

    Parameters are intentionally not used for side effects. Production requires a
    typed schema for each tool, actor/approval ID, quotas and auditable RPC calls.
    """
    if not isinstance(tool_name, str) or not tool_name:
        return Decision(False, "UNKNOWN_TOOL")
    if tool_name in FORBIDDEN_TOOLS:
        return Decision(False, "CRITICAL_TOOL_NOT_EXPOSED")
    if tool_name in READ_ONLY_TOOLS:
        if mode == Mode.OBSERVE:
            return Decision(True, "READ_ONLY_ALLOWED")
        if mode == Mode.SHADOW and gates.shadow_audit_ready:
            return Decision(True, "SHADOW_READ_ONLY_ALLOWED")
        if mode == Mode.ASSISTED and gates.shadow_audit_ready:
            return Decision(True, "ASSISTED_READ_ONLY_ALLOWED")
        return Decision(False, "MODE_GATES_NOT_MET")
    if tool_name in APPROVAL_TOOLS:
        # No execution interface is intentionally built for this class yet.
        return Decision(False, "MUTATIONS_NOT_IMPLEMENTED")
    return Decision(False, "UNKNOWN_TOOL")
