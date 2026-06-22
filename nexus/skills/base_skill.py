from abc import ABC, abstractmethod
from pydantic import BaseModel
import asyncio

class BaseSkill(ABC):
    """
    Abstract Base Class for all Aetheris Agent Skills.
    Each skill must define its name, description, parameters schema, and an execute method.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the skill (used for Function Calling)"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the skill does"""
        pass
        
    @property
    @abstractmethod
    def parameters_schema(self) -> type[BaseModel]:
        """Pydantic model describing the arguments required"""
        pass
        
    @property
    def requires_confirmation(self) -> bool:
        """Returns True if the user needs to manually confirm execution. Defaults to False."""
        return False

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """
        The actual asynchronous logic of the tool.
        Returns a string result to be fed back to the LLM.
        """
        pass

    def get_function_schema(self) -> dict:
        """
        Returns the OpenAI-compatible function calling schema.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema.model_json_schema()
        }
