"""Group tools.

Six ``BaseTool``s over ``client.groups``: create, invite, remove, inspect, join,
invites. Group **send/read are NOT here** — they ride ``#``-prefix routing through
the messaging tools. ``mls_enabled`` is never exposed: new groups are post-quantum
MLS by default (the SDK founds their MLS state on create), and this crew reads and
posts them like any other. ``RineGroupInspectTool`` reports a group's encryption
so an operator can see which suite a group runs on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from rine import GroupRead, JoinRequestRead, NotFoundError, is_uuid, normalize_handle

from crewai_rine._client import BaseRineTool, _format_error
from crewai_rine._format import group_is_mls, render_group


class RineGroupCreateInput(BaseModel):
    """Arguments for ``rine_group_create``."""

    name: str = Field(description="Group name (1-63 chars, DNS-safe).")
    enrollment: str = Field(
        default="closed",
        description="Enrollment policy: open, closed, majority, or unanimity.",
    )
    visibility: str = Field(default="private", description="Visibility: public or private.")
    description: str | None = Field(default=None, description="Optional group description.")


class RineGroupCreateTool(BaseRineTool):
    """Create a new end-to-end-encrypted coordination group."""

    name: str = "rine_group_create"
    description: str = (
        "Create a new encrypted coordination group. Performs a real, irreversible "
        "network action. Your crew owns and administers the group. New groups are "
        "post-quantum MLS by default (open-enrollment groups run on sender keys); "
        "your crew reads and posts either kind."
    )
    args_schema: type[BaseModel] = RineGroupCreateInput

    def _run(
        self,
        name: str,
        enrollment: str = "closed",
        visibility: str = "private",
        description: str | None = None,
    ) -> str:
        try:
            group = self._get_client().groups.create(
                name,
                description=description,
                enrollment=enrollment,
                visibility=visibility,
                agent=self.agent,
            )
        except Exception as err:  # noqa: BLE001 — surface as a tool message, never crash
            return _format_error(err)
        encryption = "post-quantum MLS" if group_is_mls(group) else "sender-key"
        return (
            f"Created group {group.handle} (id {group.id}, {encryption} E2EE, "
            f"enrollment {group.enrollment_policy})."
        )


class RineGroupInviteInput(BaseModel):
    """Arguments for ``rine_group_invite``."""

    group: str = Field(description="Group handle (#group@org) or UUID.")
    agent_to_invite: str = Field(description="Handle or UUID of the agent to invite.")
    message: str | None = Field(default=None, description="Optional invitation message.")


class RineGroupInviteTool(BaseRineTool):
    """Invite an agent into a group your crew administers."""

    name: str = "rine_group_invite"
    description: str = (
        "Invite an agent into a group your crew administers. Performs a real, "
        "irreversible network action."
    )
    args_schema: type[BaseModel] = RineGroupInviteInput

    def _run(self, group: str, agent_to_invite: str, message: str | None = None) -> str:
        try:
            result = self._get_client().groups.invite(
                group, agent_to_invite, message=message, agent=self.agent
            )
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        return f"Invited {agent_to_invite} to {group} (status {result.status})."


class RineGroupRemoveInput(BaseModel):
    """Arguments for ``rine_group_remove``."""

    group: str = Field(description="Group handle (#group@org) or UUID.")
    agent_id: str = Field(description="UUID of the member to remove.")


class RineGroupRemoveTool(BaseRineTool):
    """Remove a member from a group your crew administers."""

    name: str = "rine_group_remove"
    description: str = (
        "Remove a member from a group your crew administers. Rotates the group's "
        "keys (forward secrecy). Performs a real, irreversible network action."
    )
    args_schema: type[BaseModel] = RineGroupRemoveInput

    def _run(self, group: str, agent_id: str) -> str:
        try:
            self._get_client().groups.remove_member(group, agent_id, agent=self.agent)
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        return f"Removed {agent_id} from {group}; keys rotated."


class RineGroupInspectInput(BaseModel):
    """Arguments for ``rine_group_inspect``."""

    group: str = Field(description="Group handle (#group@org) or UUID.")


class RineGroupInspectTool(BaseRineTool):
    """Show a group's details + its encryption (post-quantum MLS or sender-key)."""

    name: str = "rine_group_inspect"
    description: str = (
        "Show a group's details and encryption status — whether it runs on "
        "post-quantum MLS or sender keys. Your crew reads and posts either kind."
    )
    args_schema: type[BaseModel] = RineGroupInspectInput

    def _run(self, group: str) -> str:
        try:
            groups = self._get_client().groups.list(agent=self.agent)
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        match = self._find_group(groups, group)
        if match is None:
            return _format_error(
                NotFoundError(f"No group '{group}' among the groups this agent belongs to.")
            )
        return render_group(match)

    @staticmethod
    def _find_group(groups: list[GroupRead], target: str) -> GroupRead | None:
        """Match a group by UUID, full handle, or bare local name.

        A bare name (no ``@``, not a UUID) matches on the handle's local part —
        ``g.handle.split("@")[0].lstrip("#") == target.lstrip("#")`` — for parity
        with ``invite``/``remove``, which the SDK lets accept a bare group name (M3).
        """
        if is_uuid(target):
            return next((g for g in groups if str(g.id) == target), None)
        if "@" not in target:
            want = target.lstrip("#")
            return next((g for g in groups if g.handle.split("@")[0].lstrip("#") == want), None)
        wanted = normalize_handle(target if target.startswith("#") else f"#{target}")
        return next((g for g in groups if normalize_handle(g.handle) == wanted), None)


class RineGroupJoinInput(BaseModel):
    """Arguments for ``rine_group_join``."""

    group: str = Field(description="Group handle (#group@org) or UUID.")
    message: str | None = Field(default=None, description="Optional message for join request.")


class RineGroupJoinTool(BaseRineTool):
    """Join a group your crew was invited to (or request to join)."""

    name: str = "rine_group_join"
    description: str = (
        "Join a group. Open/invited groups join immediately; approval-gated groups "
        "create a pending request that members vote on. Performs a real, "
        "irreversible network action."
    )
    args_schema: type[BaseModel] = RineGroupJoinInput

    def _run(self, group: str, message: str | None = None) -> str:
        try:
            result = self._get_client().groups.join(group, message=message, agent=self.agent)
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        if result.status == "joined":
            return f"Joined {group} (member id {result.member.id})."
        return f"Join request pending for {group} (request id {result.request.id})."


class RineGroupInvitesInput(BaseModel):
    """Arguments for ``rine_group_invites`` (none required)."""


class RineGroupInvitesTool(BaseRineTool):
    """List this agent's pending group invites."""

    name: str = "rine_group_invites"
    description: str = (
        "List your pending invites across all groups — the authoritative way to "
        "discover an invite when a notification did not arrive."
    )
    args_schema: type[BaseModel] = RineGroupInvitesInput

    def _run(self) -> str:
        try:
            invites = self._get_client().groups.list_invites(agent=self.agent)
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        if not invites:
            return "No pending invites."
        lines = [f"{i}. {_render_invite(inv)}" for i, inv in enumerate(invites, start=1)]
        return f"{len(invites)} pending invite(s):\n" + "\n".join(lines)


def _render_invite(inv: JoinRequestRead) -> str:
    """Render one pending invite as a single readable line."""
    group = inv.group_handle or inv.group_name or str(inv.group_id)
    return f"group={group} | request_id={inv.id} | status={inv.status}"


__all__ = [
    "RineGroupCreateTool",
    "RineGroupInviteTool",
    "RineGroupRemoveTool",
    "RineGroupInspectTool",
    "RineGroupJoinTool",
    "RineGroupInvitesTool",
]
