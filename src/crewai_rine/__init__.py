"""crewai-rine — native CrewAI tools for the Rine network.

Thin adapter layer: pydantic ``args_schema`` → ``rine.SyncRineClient`` method →
human-readable string. All crypto, HTTP, config resolution, and types come from the
``rine`` SDK; this package never reimplements them.

Import is side-effect-free: no network call, no credential read, and no client
construction happens at import time. A ``SyncRineClient`` is built lazily on first
tool ``_run`` (see :mod:`crewai_rine._client`).

Public surface:

- Tools — attach the ones a crew needs to ``Agent(tools=[...])``; **attachment is the
  opt-in**. Mutating tools (send/reply/send_and_wait, group create/invite/
  remove/vote/leave) say so in their description.
- :class:`RineNotificationListener` — opt-in crew-lifecycle → rine message bridge.
- Onboarding is a setup-time helper (``python -m crewai_rine.onboard``), never a tool.

``__version__`` is the single source of truth, read from the installed package metadata
(the version literal lives only in ``pyproject.toml``).
"""

from importlib.metadata import PackageNotFoundError, version

from crewai_rine.listener import RineNotificationListener
from crewai_rine.tools import (
    RineDiscoverGroupsTool,
    RineDiscoverTool,
    RineFulfillTool,
    RineGroupCreateTool,
    RineGroupInspectTool,
    RineGroupInvitesTool,
    RineGroupInviteTool,
    RineGroupJoinTool,
    RineGroupLeaveTool,
    RineGroupReclaimTool,
    RineGroupRemoveTool,
    RineGroupRequestsTool,
    RineGroupRosterTool,
    RineGroupsTool,
    RineGroupSyncTool,
    RineGroupVoteTool,
    RineInboxTool,
    RineInspectTool,
    RinePayTool,
    RineReadTool,
    RineReplyTool,
    RineSendAndWaitTool,
    RineSendTool,
    RineThreadTool,
    RineWhoamiTool,
)

try:
    __version__ = version("crewai-rine")
except PackageNotFoundError:  # editable/uninstalled runs (e.g. running from source tree)
    # 🔴 A SENTINEL, never the real number. A hardcoded literal here read "0.4.0"
    # while pyproject said 0.5.0, and nothing in this package gates the two against
    # each other — crewai gets pytest alone, with no version-consistency test of the
    # kind rine-sdk carries. A value that cannot go stale is the fix; the number now
    # lives in exactly one place.
    __version__ = "0.0.0"

__all__ = [
    # messaging
    "RineSendTool",
    "RineSendAndWaitTool",
    "RineInboxTool",
    "RineReadTool",
    "RineReplyTool",
    "RineThreadTool",
    # discovery
    "RineDiscoverTool",
    "RineInspectTool",
    "RineDiscoverGroupsTool",
    "RineWhoamiTool",
    # groups
    "RineGroupsTool",
    "RineGroupCreateTool",
    "RineGroupInviteTool",
    "RineGroupRemoveTool",
    "RineGroupInspectTool",
    "RineGroupRosterTool",
    "RineGroupJoinTool",
    "RineGroupInvitesTool",
    "RineGroupRequestsTool",
    "RineGroupVoteTool",
    "RineGroupLeaveTool",
    "RineGroupSyncTool",
    "RineGroupReclaimTool",
    # x402 payments (mutating — pay / fulfill)
    "RinePayTool",
    "RineFulfillTool",
    # lifecycle listener
    "RineNotificationListener",
    "__version__",
]
