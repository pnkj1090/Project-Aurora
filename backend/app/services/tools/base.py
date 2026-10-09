from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, Optional, List, Union
from pydantic import BaseModel, Field

from app.services.permissions.models import PermissionType


class ToolRiskLevel(str, Enum):
    LOW = "low"            # Read-only operations, queries, searches
    MEDIUM = "medium"      # Modifying user items (notes, tasks)
    HIGH = "high"          # External actions, payments, sending emails, deleting data


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    requires_confirmation: bool = False
    required_permissions: List[Union[PermissionType, str]] = Field(default_factory=list)


class BaseTool(ABC):
    """
    Standardized base class for all future assistant tools
    (Calendar, Reminders, Notes, Weather, Web, etc.).
    """

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Tool metadata and execution contract."""
        pass

    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the tool with validated parameters."""
        pass
