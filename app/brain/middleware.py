"""
Middleware Module - Automatic Brain Context Injection

This middleware automatically:
1. Builds context from brain memory before each request
2. Injects relevant context into the conversation
3. Saves responses with embeddings after completion
"""

import json
import time
import logging
import hashlib
from typing import Optional, Dict, Any
from app.brain.memory import MemoryManager
from app.brain.decisions import DecisionTracker
from app.brain.storage import BrainStorage

logger = logging.getLogger("brain")

# In-memory failure counters. Not persisted (a restart resets them) — they exist
# so a broad try/except doesn't let the brain silently go dark with nothing but
# stdout to show for it. Surfaced via GET /brain/health.
_stats = {
    "context_build_ok": 0,
    "context_build_errors": 0,
    "save_ok": 0,
    "save_errors": 0,
    "last_error": None,
    "last_error_at": None,
}


def get_brain_stats() -> Dict[str, Any]:
    """Snapshot of brain success/failure counters since process start."""
    return dict(_stats)


def _record_error(kind: str, exc: Exception):
    _stats[f"{kind}_errors"] += 1
    _stats["last_error"] = f"{kind}: {type(exc).__name__}: {exc}"
    _stats["last_error_at"] = time.time()
    logger.exception("[BRAIN] %s failed", kind)


class BrainMiddleware:
    """Brain middleware for automatic context management"""

    @staticmethod
    async def build_brain_context(
        api_key_hash: str,
        user_message: str,
        session_id: Optional[int] = None,
        enable_brain: bool = True
    ) -> Optional[str]:
        """
        Build brain context for injection into the request.

        Args:
            api_key_hash: User's API key hash
            user_message: Current user message
            session_id: Current session ID
            enable_brain: Whether brain is enabled

        Returns:
            Formatted context string for injection, or None
        """
        if not enable_brain:
            return None

        try:
            # Build context from memory
            context = await MemoryManager.build_context(
                api_key_hash=api_key_hash,
                current_message=user_message,
                session_id=session_id,
                max_relevant=3,
                include_facts=True,
                include_decisions=True
            )

            # Format for injection
            formatted = MemoryManager.format_context_for_injection(context)
            _stats["context_build_ok"] += 1

            if formatted:
                return f"\n\n<brain_context>\n{formatted}\n</brain_context>"

            return None
        except Exception as e:
            _record_error("context_build", e)
            return None

    @staticmethod
    async def inject_brain_context(
        payload: Dict[str, Any],
        brain_context: str
    ) -> Dict[str, Any]:
        """
        Inject brain context into the request payload.

        Args:
            payload: Request payload (Anthropic format)
            brain_context: Brain context string

        Returns:
            Modified payload with context injected
        """
        if not brain_context:
            return payload

        system = payload.get("system")
        if isinstance(system, list):
            system.append({"type": "text", "text": brain_context})
        elif isinstance(system, str):
            payload["system"] = system + brain_context
        else:
            payload["system"] = brain_context.strip()

        return payload

    @staticmethod
    async def save_conversation_to_brain(
        session_id: int,
        api_key_hash: str,
        message_id: int,
        role: str,
        content: str,
        model: str = None
    ):
        """
        Save conversation to brain with embeddings and extract decisions/facts.

        Args:
            session_id: Session ID
            api_key_hash: User's API key hash
            message_id: Message ID
            role: Message role
            content: Message content
            model: Model used
        """
        try:
            # Save with embedding (async background task)
            await MemoryManager.save_message(
                session_id=session_id,
                api_key_hash=api_key_hash,
                message_id=message_id,
                role=role,
                content=content,
                model=model,
                compute_embedding=True
            )

            # Extract decisions and facts
            text_content = MemoryManager._extract_text_from_content(content)
            await DecisionTracker.analyze_conversation(
                content=text_content,
                api_key_hash=api_key_hash,
                session_id=session_id,
                role=role
            )
            _stats["save_ok"] += 1
        except Exception as e:
            _record_error("save", e)

    @staticmethod
    def get_api_key_hash(api_key: str) -> str:
        """
        Get hash of API key for brain storage.

        Args:
            api_key: API key

        Returns:
            SHA256 hash of the key
        """
        return hashlib.sha256(api_key.encode()).hexdigest()
