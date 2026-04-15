from .base_agent import BaseAgent

class MacroAgent(BaseAgent):
    def __init__(self):
        super().__init__("MacroAgent")

    def run(self) -> dict:
        return {
            "score": 0.5,
            "reason": "macro data not yet integrated",
            "raw": {},
        }