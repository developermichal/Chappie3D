"""Domain services package for Chappie3D Engineering Workstation:

1. ProjectService: Project management, .chappie SSOT, file ops, turn rationale, and Redis cache.
2. GeneralChatService: Standalone conversational chat, session memory, RAG, and knowledge profiles.
"""

from chappie.services.general_chat_service import GeneralChatService
from chappie.services.project_service import ProjectService

__all__ = [
    "GeneralChatService",
    "ProjectService",
]
