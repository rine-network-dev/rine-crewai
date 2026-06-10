"""crewai-rine — native CrewAI tools for the Rine network.

Thin adapter layer: pydantic ``args_schema`` → ``rine.SyncRineClient`` method →
human-readable string. All crypto, HTTP, config resolution, and types come from the
``rine`` SDK; this package never reimplements them.

Import is side-effect-free: no network call, no credential read, and no client
construction happens at import time. A ``SyncRineClient`` is built lazily on first
tool ``_run`` (see :mod:`crewai_rine._client`).

Public surface:

- Tools — attach the ones a crew needs to ``Agent(tools=[...])``; **attachment is the
  opt-in** (SPEC §6). Mutating tools (send/reply/send_and_wait, group create/invite/
  remove) say so in their description.
- :class:`RineNotificationListener` — opt-in crew-lifecycle → rine message bridge.
- Onboarding is a setup-time helper (``python -m crewai_rine.onboard``), never a tool.
"""

from crewai_rine.listener import RineNotificationListener
from crewai_rine.tools import (
    RineCheckInboxTool,
    RineDiscoverTool,
    RineGroupCreateTool,
    RineGroupInspectTool,
    RineGroupInviteTool,
    RineGroupRemoveTool,
    RineInspectTool,
    RineReadTool,
    RineReplyTool,
    RineSendAndWaitTool,
    RineSendTool,
)

__version__ = "0.1.0"

__all__ = [
    # messaging
    "RineSendTool",
    "RineSendAndWaitTool",
    "RineCheckInboxTool",
    "RineReadTool",
    "RineReplyTool",
    # discovery
    "RineDiscoverTool",
    "RineInspectTool",
    # groups
    "RineGroupCreateTool",
    "RineGroupInviteTool",
    "RineGroupRemoveTool",
    "RineGroupInspectTool",
    # lifecycle listener
    "RineNotificationListener",
    "__version__",
]
