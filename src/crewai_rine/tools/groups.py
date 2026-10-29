"""Group tools.

Six ``BaseTool``s over ``client.groups``: create, invite, remove, inspect, join,
invites. Group **send/read are NOT here** — they ride ``#``-prefix routing through
the messaging tools. New groups are post-quantum MLS by default (the SDK founds
their MLS state on create) and this crew reads and posts them like any other;
``enable_mls`` on create is how it asks for a sender-key group instead, which is
the only kind an open-enrollment group can be. ``RineGroupInspectTool`` reports a
group's encryption so an operator can see which suite a group runs on.

``visibility`` carries no default and never will. Every surface used to invent
one and they disagreed, so the same call produced a group listed at
dir.rine.network from one and an unlisted one from another — and the listing also
flips the member-joined signal, so the two differed in who gets told about a join
as well as in who can find the group. It is required in the schema, so the model
is told at the point of choice, and the refusal when it is omitted anyway is the
SDK's own sentence rather than a second copy of it.

Admission is plural: ``members`` on create and ``agents_to_invite`` on invite each
take a batch and report one outcome per requested agent.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError
from rine import (
    GROUP_READERS_NOTICE,
    VISIBILITY_REQUIRED_MESSAGE,
    VOTE_DURATION_MAX_HOURS,
    VOTE_DURATION_MIN_HOURS,
    VOTE_DURATION_RULE,
    GroupRead,
    JoinRequestRead,
)

# 🔴 Private module, deliberately. ``label_own_agents_sync`` is what makes a
# group row spell this org's seats as handles, and the SDK exports it nowhere
# (`rine.__all__` carries ``own_agents_clause``, which READS the map, and
# nothing that BUILDS it). ``groups.list``/``groups.get`` call it internally;
# a caller rendering a resolve's own row has no public way to. Escalated as an
# open follow-up — the fix belongs in `rine-sdk`, either as an export or by
# labelling inside ``resolve_group``, and it lands identically on all three
# Python connectors.
from rine._agent_handles import label_own_agents_sync
from rine.format import describe_group_encryption

from crewai_rine._client import BaseRineTool, _format_error
from crewai_rine._format import UNFOUNDED_REPAIR, render_group
from crewai_rine.tools._admission import (
    INVITE_TARGET_REQUIRED,
    render_admission,
    render_invite_one,
    render_mls,
)
from crewai_rine.tools._schemas import GROUP_JOIN_REFERENCE, GROUP_REFERENCE


class RineGroupCreateInput(BaseModel):
    """Arguments for ``rine_group_create``."""

    name: str = Field(description="Group name (1-63 chars, DNS-safe).")
    enrollment: str = Field(
        default="closed",
        description="Enrollment policy: open, closed, majority, or unanimity.",
    )
    visibility: str = Field(
        description=(
            "Required, no default. `public` lists the group at dir.rine.network for "
            "anyone to find, and turns the member-joined signal off. `private` does "
            "not list it, and members are signalled when someone joins."
        )
    )
    description: str | None = Field(
        default=None,
        description=(
            "Optional durable house rules, readable by every member and every later "
            "arrival. NOT end-to-end encrypted — unlike a message body it is a plain "
            "column on the group row, readable by the rine server and by any agent the "
            "group is visible to. Put the rules here; send the secret."
        ),
    )
    # Both bounds and the sentence come from the SDK's ``vote_words``, the one
    # place that holds them: this copy called the window the TTL of a
    # nomination, and the server stamps it on a self-filed join request too.
    vote_duration_hours: int | None = Field(
        default=None,
        ge=VOTE_DURATION_MIN_HOURS,
        le=VOTE_DURATION_MAX_HOURS,
        description=VOTE_DURATION_RULE,
    )
    members: list[str] | None = Field(
        default=None,
        description=(
            "Agents to invite as the group is created: handles or UUIDs, reported one "
            "outcome per entry. A roster INVITES, it never seats — the new group has "
            "one member until each invitee joins — and an unaccepted invitation holds "
            "a ratchet-tree seat against the group's seat ceiling until it expires. A "
            "founding roster mints real invitations under every enrollment policy, "
            "including majority and unanimity where a later invite instead nominates: "
            "at founding your crew is the only member, so the vote would be a formality "
            "it casts against itself."
        ),
    )
    enable_mls: bool | None = Field(
        default=None,
        description=(
            "Whether the new group runs post-quantum MLS. Omit it for the default: the "
            "group is an MLS group and the SDK founds its state as part of this call. "
            "Set it false for a sender-key broadcast group instead — the value reaches "
            "the server, so the group is recorded as a sender-key group rather than as "
            "an MLS group that was never founded. An open-enrollment group is sender-key "
            "whatever this says; the server refuses MLS there."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _require_visibility(cls, data: Any) -> Any:
        """State the visibility refusal in rine's words, not pydantic's.

        This framework validates ``args_schema`` BEFORE the handler runs, so the
        SDK's own refusal can never be reached through the public entry point —
        the model would see ``1 validation error ... visibility Field required``,
        which is the dump this refusal exists to replace. ``mode="before"`` runs
        ahead of field validation, so the field stays REQUIRED in the JSON schema
        the model reads AND the refusal is still rine's sentence. Same shape as the
        server's own ``PydanticCustomError`` in ``group_schemas.py``.
        """
        if isinstance(data, dict) and not data.get("visibility"):
            raise PydanticCustomError("visibility_required", VISIBILITY_REQUIRED_MESSAGE)
        return data


class RineGroupCreateTool(BaseRineTool):
    """Create a new end-to-end-encrypted coordination group."""

    name: str = "rine_group_create"
    description: str = (
        "Create a new encrypted coordination group. Performs a real, irreversible "
        "network action. Your crew owns and administers the group. New groups are "
        "post-quantum MLS by default (open-enrollment groups run on sender keys); "
        "your crew reads and posts either kind. `visibility` is required and has no "
        "default: `public` lists the group at dir.rine.network and turns the "
        "member-joined signal off, `private` does neither. Name `members` to invite "
        "agents as the group is created — a founding roster mints real invitations "
        "whatever the enrollment policy, because at founding your crew is the only "
        "member and a vote would be a formality it casts against itself. " + GROUP_READERS_NOTICE
    )
    args_schema: type[BaseModel] = RineGroupCreateInput

    def _run(
        self,
        name: str,
        visibility: str | None = None,
        enrollment: str = "closed",
        description: str | None = None,
        vote_duration_hours: int | None = None,
        members: list[str] | None = None,
        enable_mls: bool | None = None,
    ) -> str:
        # `visibility` is required in the schema and passed straight through. The
        # SDK owns the refusal — restating it here would be a second copy
        # of a sentence with no gate keeping the two in step.
        try:
            group = self._get_client().groups.create(
                name,
                description=description,
                enrollment=enrollment,
                visibility=visibility,
                members=members,
                vote_duration_hours=vote_duration_hours,
                enable_mls=enable_mls,
                agent=self.agent,
            )
        except Exception as err:  # noqa: BLE001 — surface as a tool message, never crash
            return _format_error(err)
        head = (
            f"Created group {group.handle} (id {group.id}, enrollment "
            f"{group.enrollment_policy}).\n"
            f"Encryption: {describe_group_encryption(group, UNFOUNDED_REPAIR)}"
        )
        mls = render_mls(group.mls_admission)
        if group.roster is None:
            return f"{head}{mls}"
        return (
            f"{head}\nRoster invited (nobody is a member until they join; an "
            f"invitation holds a seat until it is accepted or expires):\n"
            f"{render_admission(group.roster)}{mls}"
        )


class RineGroupInviteInput(BaseModel):
    """Arguments for ``rine_group_invite``."""

    group: str = Field(description=GROUP_REFERENCE)
    agent_to_invite: str | None = Field(
        default=None,
        description=(
            "One agent to invite: handle or UUID. On a majority- or "
            "unanimity-enrollment group this nominates rather than invites. Name "
            "exactly one of agent_to_invite or agents_to_invite."
        ),
    )
    agents_to_invite: list[str] | None = Field(
        default=None,
        description=(
            "Several agents to invite in one call: handles or UUIDs, reported one "
            "outcome per entry in the order given. A batch drops what it cannot "
            "admit — a revoked agent, one already a member — instead of refusing "
            "everyone. On a majority- or unanimity-enrollment group each outcome is "
            "`nominated`, not `invited`. Name exactly one of agent_to_invite or "
            "agents_to_invite."
        ),
    )
    message: str | None = Field(default=None, description="Optional invitation message.")


class RineGroupInviteTool(BaseRineTool):
    """Invite one agent, or a batch, into a group your crew administers."""

    name: str = "rine_group_invite"
    description: str = (
        "Invite one agent (agent_to_invite) or several at once (agents_to_invite) "
        "into a group your crew administers. On a closed group this mints an "
        "invitation the agent spends by joining. On a majority- or "
        "unanimity-enrollment group it NOMINATES instead: it files a join request "
        "the group's electorate decides, your crew's own invite counts as one "
        "approval, and nobody is seated until the vote carries. A batch reports one "
        "outcome per agent and drops what it cannot admit rather than refusing "
        "everyone. Performs a real, irreversible network action."
    )
    args_schema: type[BaseModel] = RineGroupInviteInput

    def _run(
        self,
        group: str,
        agent_to_invite: str | None = None,
        agents_to_invite: list[str] | None = None,
        message: str | None = None,
    ) -> str:
        batch = agents_to_invite or []
        if (agent_to_invite is None) == (not batch):
            return _format_error(ValueError(INVITE_TARGET_REQUIRED))
        try:
            if agent_to_invite is not None:
                result = self._get_client().groups.invite(
                    group, agent_to_invite, message=message, agent=self.agent
                )
                return render_invite_one(group, agent_to_invite, result)
            report = self._get_client().groups.invite_many(
                group, batch, message=message, agent=self.agent
            )
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        return f"Invited into {group}:\n{render_admission(report)}{render_mls(report.mls)}"


class RineGroupRemoveInput(BaseModel):
    """Arguments for ``rine_group_remove``."""

    group: str = Field(description=GROUP_REFERENCE)
    agent_id: str = Field(description="UUID of the member to remove.")


class RineGroupRemoveTool(BaseRineTool):
    """Remove a member from a group your crew administers, or leave it."""

    name: str = "rine_group_remove"
    description: str = (
        "Remove a member from a group your crew administers, or leave a group by "
        "naming your own agent. On an MLS group this posts a real Remove commit "
        "that takes the member's ratchet-tree leaf with it, so it is an "
        "O(members) operation that can fail; an open group has no cryptographic "
        "eviction — the server stops delivering to them, and what bounds their "
        "reach into later traffic is each remaining member rotating their sender "
        "key on the next send. A kick takes nothing back: messages already "
        "delivered still open for everyone, the removed member included. A leave "
        "is different — it retires this host's key material for the group, so the "
        "group's messages stop opening here, including ones that arrived before "
        "the leave and were never read. Performs a real, irreversible network "
        "action."
    )
    args_schema: type[BaseModel] = RineGroupRemoveInput

    def _run(self, group: str, agent_id: str) -> str:
        # The sentence is the SDK's, not this surface's. The true one differs
        # between the two group types AND between the two verbs — a leave says
        # what local key material is now gone, a kick names the epoch a Remove
        # commit landed at — and the one string this tool used to print for all
        # four cases was false for every open group.
        try:
            result = self._get_client().groups.remove_member(group, agent_id, agent=self.agent)
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        return result.message


class RineGroupInspectInput(BaseModel):
    """Arguments for ``rine_group_inspect``."""

    group: str = Field(description=GROUP_REFERENCE)


class RineGroupInspectTool(BaseRineTool):
    """Show a group's details + its encryption mode."""

    name: str = "rine_group_inspect"
    description: str = (
        "Show a group's details and encryption mode: post-quantum MLS, an MLS group "
        "still initialising, or sender keys. Your crew reads and posts every mode."
    )
    args_schema: type[BaseModel] = RineGroupInspectInput

    def _run(self, group: str) -> str:
        # The reference walks the SDK's group ladder — the one contract
        # that owns resolution AND its refusal. The local matcher this tool used
        # to carry answered an ambiguous bare name by taking whichever row ``GET
        # /groups`` returned first, and that listing has no ``ORDER BY``, so two
        # identical calls could inspect two different groups. The ladder refuses
        # instead, naming every candidate; and its miss arm carries the org's
        # roster and ``rine_discover_groups`` rather than this tool's roster-less
        # sentence, which ``_not_found_message`` used to answer with
        # ``rine_discover`` — the AGENT directory, which cannot answer a group
        # question at all.
        #
        # The ladder has already read the row, so the render spends
        # ``target.row`` instead of resolving twice for two columns. Every rung
        # of the MEMBER ladder answers with the row (``rine._recipient_ladder.
        # group_ladder``, pinned rung by rung in the SDK's
        # ``tests/contract/test_recipient_parity.py``); the join ladder is the
        # only one that answers id-only, and this verb never walks it.
        client = self._get_client()
        # 🔴 The validation is INSIDE the try, with the resolve: a target that
        # carries no row is the JOIN ladder's shape, which this verb never walks,
        # so reaching it means a broken SDK contract — and outside the try that
        # arrives as a pydantic ``ValidationError`` escaping the CrewAI tool
        # boundary instead of this tool's own refusal. ``hermes-rine`` answers the
        # same impossible state the same way, in the same block.
        try:
            target = client.resolve_group(group, agent=self.agent)
            # 🔴 The row a resolve hands back has never been through the SDK's own
            # seat labeller — ``groups.list``/``groups.get`` call it,
            # ``resolve_group`` does not — and ``own_agents_clause`` reads the map
            # it attaches. Spending ``target.row`` raw would print ``your_agents``
            # as raw UUIDs where this tool has always printed handles, which is a
            # behaviour change nobody asked for. The SDK's labeller is called
            # instead of re-reading the group: it costs the same memoised
            # ``GET /agents`` the old ``groups.list`` path paid, issues nothing
            # when the row names no seat, and never raises.
            row = GroupRead.model_validate(target.row)
            label_own_agents_sync(client, [row])
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        return render_group(row)


class RineGroupJoinInput(BaseModel):
    """Arguments for ``rine_group_join``."""

    group: str = Field(description=GROUP_JOIN_REFERENCE)
    message: str | None = Field(default=None, description="Optional message for join request.")


class RineGroupJoinTool(BaseRineTool):
    """Join a group your crew was invited to (or request to join)."""

    name: str = "rine_group_join"
    description: str = (
        "Join a group. Open/invited groups join immediately; approval-gated groups "
        "create a pending request that members vote on. Called on a nomination a "
        "member filed for your crew, it records your crew's consent to that request "
        "and answers the row — it does not join the group, because the electorate "
        "still has to carry the vote. Performs a real, irreversible network action. "
        + GROUP_READERS_NOTICE
    )
    args_schema: type[BaseModel] = RineGroupJoinInput

    def _run(self, group: str, message: str | None = None) -> str:
        try:
            result = self._get_client().groups.join(group, message=message, agent=self.agent)
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        if result.status == "joined":
            return f"Joined {group} (member id {result.member.id})."
        # `invited_by` is what tells the two pending answers apart: an application
        # this crew made, and a nomination a member made for it. Both wait on the
        # same vote, but only the second one was somebody else's idea, and only
        # the second one needed this call to record consent.
        if result.request.invited_by is not None:
            return (
                f"Consent recorded for the nomination into {group} (request id "
                f"{result.request.id}). A member nominated your crew; the group's "
                f"electorate still has to carry the vote."
            )
        return f"Join request pending for {group} (request id {result.request.id})."


class RineGroupInvitesInput(BaseModel):
    """Arguments for ``rine_group_invites`` (none required)."""


class RineGroupInvitesTool(BaseRineTool):
    """List this agent's pending group invites."""

    name: str = "rine_group_invites"
    description: str = (
        "List the open offers addressed to your crew across all groups — the "
        "authoritative way to discover one when a notification did not arrive. Two "
        "kinds, told apart by status: an `invited` row is an invitation your crew "
        "spends with rine_group_join, and a `pending` row is a nomination a member "
        "filed on its behalf, which rine_group_join consents to and the group's "
        "electorate decides. This is the only place a nomination is visible to the "
        "agent it names."
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
    """Render one open offer as a single readable line.

    ``status`` is the whole discriminator: ``invited`` is a voucher to spend,
    ``pending`` is a nomination to consent to. The clause spells that out because
    a bare token under the head "pending invite(s)" reads as one kind of thing.
    """
    group = inv.group_handle or inv.group_name or str(inv.group_id)
    kind = (
        "a nomination to consent to; the electorate decides it"
        if inv.status == "pending"
        else "an invitation to spend"
    )
    return f"group={group} | request_id={inv.id} | status={inv.status} ({kind})"


__all__ = [
    "RineGroupCreateTool",
    "RineGroupInviteTool",
    "RineGroupRemoveTool",
    "RineGroupInspectTool",
    "RineGroupJoinTool",
    "RineGroupInvitesTool",
]
