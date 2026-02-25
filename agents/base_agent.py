"""Base agent class with Groq LLM integration and shared utilities."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()

AGENT_ACTIVITY_LOG: list[dict] = []

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
FALLBACK_MODEL = "llama-3.3-70b-versatile"


class BaseAgent:
    """Base class for all Sentinel-ES agents with Groq LLM integration."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list | None = None,
        model: str | None = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.model = model or DEFAULT_MODEL
        self.memory: dict = {}
        self._client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

    async def run(self, user_message: str, context: dict | None = None) -> str:
        """Run a single LLM call with optional context injected into the prompt.

        Args:
            user_message: The user/task message to send to the LLM.
            context: Optional key-value context to prepend to the user message.

        Returns:
            The LLM response text.
        """
        context = context or {}
        full_message = user_message
        if context:
            context_str = "\n".join(f"[{k}]: {v}" for k, v in context.items())
            full_message = f"Context:\n{context_str}\n\n{user_message}"

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": full_message},
        ]

        start = time.time()
        try:
            response = await self._call_llm(messages)
        except Exception as e:
            self.log_activity(f"{self.name}:run", f"LLM error: {e}")
            raise
        elapsed_ms = int((time.time() - start) * 1000)

        self.log_activity(
            f"{self.name}:run",
            f"Completed in {elapsed_ms}ms (model={self.model})",
        )
        return response

    async def run_with_memory(self, messages: list[dict]) -> tuple[list[dict], str]:
        """Run LLM with full conversation history, appending the new response.

        Args:
            messages: Full conversation history as list of role/content dicts.

        Returns:
            Tuple of (updated_messages_list, response_text).
        """
        if not messages or messages[0].get("role") != "system":
            messages = [{"role": "system", "content": self.system_prompt}] + messages

        response = await self._call_llm(messages)
        messages.append({"role": "assistant", "content": response})
        return messages, response

    async def _call_llm(self, messages: list[dict]) -> str:
        """Call Groq LLM with automatic fallback to secondary model."""
        try:
            completion = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=2048,
            )
            return completion.choices[0].message.content
        except Exception as primary_err:
            if self.model != FALLBACK_MODEL:
                try:
                    completion = await self._client.chat.completions.create(
                        model=FALLBACK_MODEL,
                        messages=messages,
                        temperature=0.3,
                        max_tokens=2048,
                    )
                    return completion.choices[0].message.content
                except Exception:
                    pass
            raise primary_err

    def log_activity(self, action: str, result: str):
        """Log agent activity to the global activity log (used for Kibana dashboard)."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": self.name,
            "action": action,
            "result": result,
        }
        AGENT_ACTIVITY_LOG.append(entry)
