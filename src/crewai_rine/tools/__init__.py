"""Tool classes for crewai-rine, split by domain.

Re-exports the ``BaseTool`` subclasses from ``messaging``, ``discovery``,
``groups``, ``groups_list``, ``groups_admin`` and ``groups_reclaim``. Group
send/read are not separate tools — they ride ``#``-prefix routing through the
messaging tools.
"""

from crewai_rine.tools.discovery import RineDiscoverTool, RineInspectTool, RineWhoamiTool
from crewai_rine.tools.groups import (
    RineGroupCreateTool,
    RineGroupInspectTool,
    RineGroupInvitesTool,
    RineGroupInviteTool,
    RineGroupJoinTool,
    RineGroupRemoveTool,
)
from crewai_rine.tools.groups_admin import (
    RineGroupLeaveTool,
    RineGroupRequestsTool,
    RineGroupSyncTool,
    RineGroupVoteTool,
)
from crewai_rine.tools.groups_list import (
    RineDiscoverGroupsTool,
    RineGroupRosterTool,
    RineGroupsTool,
)
from crewai_rine.tools.groups_reclaim import RineGroupReclaimTool
from crewai_rine.tools.messaging import (
    RineInboxTool,
    RineReadTool,
    RineReplyTool,
    RineSendAndWaitTool,
    RineSendTool,
    RineThreadTool,
)
from crewai_rine.tools.payments import RineFulfillTool, RinePayTool

__all__ = [
    # messaging (1:1 + #-prefixed group send/read)
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
    # groups (post-quantum MLS by default)
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
]
