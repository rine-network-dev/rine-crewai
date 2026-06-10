"""Tool classes for crewai-rine, split by domain (SPEC §5).

Re-exports the ``BaseTool`` subclasses from ``messaging``, ``discovery``, and
``groups``. Group send/read are not separate tools — they ride ``#``-prefix routing
through the messaging tools (SPEC §6.9).
"""

from crewai_rine.tools.discovery import RineDiscoverTool, RineInspectTool
from crewai_rine.tools.groups import (
    RineGroupCreateTool,
    RineGroupInspectTool,
    RineGroupInviteTool,
    RineGroupRemoveTool,
)
from crewai_rine.tools.messaging import (
    RineCheckInboxTool,
    RineReadTool,
    RineReplyTool,
    RineSendAndWaitTool,
    RineSendTool,
)

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
    # groups (sender-key)
    "RineGroupCreateTool",
    "RineGroupInviteTool",
    "RineGroupRemoveTool",
    "RineGroupInspectTool",
]
