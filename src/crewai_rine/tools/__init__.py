"""Tool classes for crewai-rine, split by domain.

Re-exports the ``BaseTool`` subclasses from ``messaging``, ``discovery``, and
``groups``. Group send/read are not separate tools — they ride ``#``-prefix routing
through the messaging tools.
"""

from crewai_rine.tools.discovery import RineDiscoverTool, RineInspectTool
from crewai_rine.tools.groups import (
    RineGroupCreateTool,
    RineGroupInspectTool,
    RineGroupInvitesTool,
    RineGroupInviteTool,
    RineGroupJoinTool,
    RineGroupRemoveTool,
)
from crewai_rine.tools.messaging import (
    RineCheckInboxTool,
    RineReadTool,
    RineReplyTool,
    RineSendAndWaitTool,
    RineSendTool,
)
from crewai_rine.tools.payments import RineFulfillTool, RinePayTool

__all__ = [
    # messaging (1:1 + #-prefixed group send/read)
    "RineSendTool",
    "RineSendAndWaitTool",
    "RineCheckInboxTool",
    "RineReadTool",
    "RineReplyTool",
    # discovery
    "RineDiscoverTool",
    "RineInspectTool",
    # groups (post-quantum MLS by default)
    "RineGroupCreateTool",
    "RineGroupInviteTool",
    "RineGroupRemoveTool",
    "RineGroupInspectTool",
    "RineGroupJoinTool",
    "RineGroupInvitesTool",
    # x402 payments (mutating — pay / fulfill)
    "RinePayTool",
    "RineFulfillTool",
]
