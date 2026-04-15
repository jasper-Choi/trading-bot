from abc import ABC, abstractmethod
from typing import Any

class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def run(self) -> dict:
        """
        Returns:
            {
                "score": float (0.0 to 1.0),
                "reason": str,
                "raw": Any
            }
        """
        pass

    def safe_run(self) -> dict:
        try:
            return self.run()
        except Exception as e:
            print(f"[{self.name}] ERROR: {e}")
            return {"score": 0.5, "reason": f"error: {str(e)}", "raw": {}}