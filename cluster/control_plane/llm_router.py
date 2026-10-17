"""Cost-tiered LLM routing for the control plane.

Routes LLM calls to the cheapest appropriate provider:
1. Gemini (prepaid) for conductor reasoning
2. Claude ($20 plan) for gate verdicts
3. MiniMax (BYOK) for second judge
4. Grok (emergency fallback only, tracked to ~$0)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from .budget_tracker import BudgetTracker


class LLMRole(Enum):
    """Roles that need LLM calls, in order of priority."""

    CONDUCTOR = "conductor"
    GATE = "gate"
    SECOND_JUDGE = "second_judge"
    FALLBACK = "fallback"


@dataclass
class LLMResponse:
    """Response from an LLM call with usage metadata."""

    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""

    provider: str
    model: str

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        """Generate a completion for the given prompt."""
        ...

    @abstractmethod
    def complete_with_usage(self, prompt: str, max_tokens: int = 500) -> LLMResponse:
        """Generate a completion and return usage metadata."""
        ...


class GeminiClient(BaseLLMClient):
    """Client for Google Gemini API (prepaid, cheapest for conductor)."""

    provider = "gemini"
    model = "gemini-2.0-flash"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or self._load_key()

    def _load_key(self) -> str:
        key_file = Path("~/.config/storypool/gemini.key").expanduser()
        if key_file.is_file():
            return key_file.read_text().strip()
        return os.environ.get("GEMINI_API_KEY", "")

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        response = self.complete_with_usage(prompt, max_tokens)
        return response.text

    def complete_with_usage(self, prompt: str, max_tokens: int = 500) -> LLMResponse:
        if not self.api_key:
            raise ValueError("Gemini API key not configured")
        return LLMResponse(
            text="[Gemini client not implemented — stub response]",
            provider=self.provider,
            model=self.model,
            tokens_in=len(prompt) // 4,
            tokens_out=max_tokens // 2,
            cost_usd=0.0001,
        )


class ClaudeClient(BaseLLMClient):
    """Client for Anthropic Claude API (gate verdicts)."""

    provider = "claude"
    model = "claude-3-haiku-20240307"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or self._load_key()

    def _load_key(self) -> str:
        key_file = Path("~/.config/storypool/claude.key").expanduser()
        if key_file.is_file():
            return key_file.read_text().strip()
        return os.environ.get("ANTHROPIC_API_KEY", "")

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        response = self.complete_with_usage(prompt, max_tokens)
        return response.text

    def complete_with_usage(self, prompt: str, max_tokens: int = 500) -> LLMResponse:
        if not self.api_key:
            raise ValueError("Claude API key not configured")
        return LLMResponse(
            text="[Claude client not implemented — stub response]",
            provider=self.provider,
            model=self.model,
            tokens_in=len(prompt) // 4,
            tokens_out=max_tokens // 2,
            cost_usd=0.001,
        )


class MiniMaxClient(BaseLLMClient):
    """Client for MiniMax API (BYOK, cheap second judge)."""

    provider = "minimax"
    model = "abab6.5s-chat"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or self._load_key()

    def _load_key(self) -> str:
        key_file = Path("~/.config/storypool/minimax.key").expanduser()
        if key_file.is_file():
            return key_file.read_text().strip()
        return os.environ.get("MINIMAX_API_KEY", "")

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        response = self.complete_with_usage(prompt, max_tokens)
        return response.text

    def complete_with_usage(self, prompt: str, max_tokens: int = 500) -> LLMResponse:
        if not self.api_key:
            raise ValueError("MiniMax API key not configured")
        return LLMResponse(
            text="[MiniMax client not implemented — stub response]",
            provider=self.provider,
            model=self.model,
            tokens_in=len(prompt) // 4,
            tokens_out=max_tokens // 2,
            cost_usd=0.0005,
        )


class GrokClient(BaseLLMClient):
    """Client for Grok API (emergency fallback only)."""

    provider = "grok"
    model = "grok-2"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or self._load_key()

    def _load_key(self) -> str:
        key_file = Path("~/.config/storypool/grok.key").expanduser()
        if key_file.is_file():
            return key_file.read_text().strip()
        return os.environ.get("GROK_API_KEY", "")

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        response = self.complete_with_usage(prompt, max_tokens)
        return response.text

    def complete_with_usage(self, prompt: str, max_tokens: int = 500) -> LLMResponse:
        if not self.api_key:
            raise ValueError("Grok API key not configured")
        return LLMResponse(
            text="[Grok client not implemented — stub response]",
            provider=self.provider,
            model=self.model,
            tokens_in=len(prompt) // 4,
            tokens_out=max_tokens // 2,
            cost_usd=0.01,
        )


ROLE_PROVIDERS: dict[LLMRole, list[str]] = {
    LLMRole.CONDUCTOR: ["gemini", "claude", "grok"],
    LLMRole.GATE: ["claude", "gemini", "grok"],
    LLMRole.SECOND_JUDGE: ["minimax", "gemini", "grok"],
    LLMRole.FALLBACK: ["grok"],
}


class LLMRouter:
    """Routes LLM calls to the cheapest available provider for each role.

    Checks budget caps before routing. Falls back to the next provider
    if the preferred one is over budget or unavailable.
    """

    def __init__(
        self,
        budget_tracker: Optional[BudgetTracker] = None,
        gemini_client: Optional[GeminiClient] = None,
        claude_client: Optional[ClaudeClient] = None,
        minimax_client: Optional[MiniMaxClient] = None,
        grok_client: Optional[GrokClient] = None,
    ):
        self.budget = budget_tracker or BudgetTracker()
        self.clients: dict[str, BaseLLMClient] = {}

        if gemini_client:
            self.clients["gemini"] = gemini_client
        if claude_client:
            self.clients["claude"] = claude_client
        if minimax_client:
            self.clients["minimax"] = minimax_client
        if grok_client:
            self.clients["grok"] = grok_client

    def get_client(self, role: LLMRole) -> Optional[BaseLLMClient]:
        """Get the best available client for the given role.

        Returns the first provider in the role's preference list that:
        1. Has a configured client
        2. Is within budget

        Returns None if no provider is available.
        """
        providers = ROLE_PROVIDERS.get(role, [])

        for provider in providers:
            if provider not in self.clients:
                continue
            allowed, reason = self.budget.can_use(provider)
            if not allowed:
                continue
            return self.clients[provider]

        return None

    def complete(
        self,
        role: LLMRole,
        prompt: str,
        max_tokens: int = 500,
    ) -> Optional[LLMResponse]:
        """Route a completion request to the best available provider.

        Records usage after a successful call.
        Returns None if no provider is available.
        """
        client = self.get_client(role)
        if client is None:
            return None

        response = client.complete_with_usage(prompt, max_tokens)

        self.budget.record(
            provider=response.provider,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_usd=response.cost_usd,
        )

        return response

    def conductor_judgment(self, context: dict) -> Optional[str]:
        """Route a conductor judgment call (priority ambiguity).

        Uses Gemini by default (prepaid, cheapest).
        """
        prompt = f"""You are the StoryPool conductor deciding what work runs next.

Context:
{context}

Based on the queue depths, machine health, and pending work, what should run next?
Be concise — one sentence with the action and why.
"""
        response = self.complete(LLMRole.CONDUCTOR, prompt, max_tokens=100)
        return response.text if response else None
