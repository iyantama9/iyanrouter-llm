"""
Decisions Module - Track decisions and extract facts from conversations

Automatically identifies and tracks:
- Decisions made during conversations
- Facts learned about users/projects
- Important context and outcomes
"""

import re
from typing import List, Dict, Any, Optional
from app.brain.storage import BrainStorage


class DecisionTracker:
    """Track decisions and facts from conversations"""

    # Decision keywords/patterns
    DECISION_PATTERNS = [
        r"(?i)I (?:decided|chose|selected|picked) (?:to )?(.+)",
        r"(?i)(?:I'll|I will|Let's) (?:use|go with|implement|choose) (.+)",
        r"(?i)(?:We|I) (?:should|must|need to) (.+)",
        r"(?i)Decision: (.+)",
        r"(?i)(?:My|The) decision is (?:to )?(.+)",
    ]

    # Fact patterns (user preferences, information)
    FACT_PATTERNS = [
        r"(?i)I (?:prefer|like|love|want|need) (.+)",
        r"(?i)My (.+) is (.+)",
        r"(?i)I (?:am|work as|do) (.+)",
        r"(?i)I'm using (.+)",
        r"(?i)The project (?:uses|is using|has) (.+)",
    ]

    @staticmethod
    async def extract_and_save_decisions(
        content: str,
        api_key_hash: str,
        session_id: int,
        role: str = "user"
    ):
        """
        Extract decisions from message content and save them.

        Args:
            content: Message content
            api_key_hash: User's API key hash
            session_id: Session ID
            role: Message role (decisions usually from user or assistant)
        """
        decisions = DecisionTracker.extract_decisions(content)

        for decision in decisions:
            await BrainStorage.save_decision(
                api_key_hash=api_key_hash,
                session_id=session_id,
                title=decision["title"],
                description=decision.get("description"),
                context=decision.get("context"),
                decision_type=decision.get("type")
            )

    @staticmethod
    def extract_decisions(content: str) -> List[Dict[str, Any]]:
        """
        Extract decisions from text using pattern matching.

        Args:
            content: Text to analyze

        Returns:
            List of extracted decisions
        """
        decisions = []

        for pattern in DecisionTracker.DECISION_PATTERNS:
            matches = re.findall(pattern, content)
            for match in matches:
                decision_text = match.strip()
                if len(decision_text) > 10:  # Filter out too short matches
                    decisions.append({
                        "title": decision_text[:200],  # Truncate long titles
                        "description": None,
                        "context": content[:500],  # Save context
                        "type": "explicit"
                    })

        return decisions

    @staticmethod
    async def extract_and_save_facts(
        content: str,
        api_key_hash: str,
        session_id: int,
        role: str = "user"
    ):
        """
        Extract facts from message content and save them.

        Args:
            content: Message content
            api_key_hash: User's API key hash
            session_id: Session ID
            role: Message role (facts usually from user messages)
        """
        facts = DecisionTracker.extract_facts(content)

        for fact in facts:
            await BrainStorage.save_fact(
                api_key_hash=api_key_hash,
                session_id=session_id,
                fact=fact["fact"],
                category=fact.get("category"),
                source=f"conversation_{session_id}",
                confidence=fact.get("confidence", 0.8)
            )

    @staticmethod
    def extract_facts(content: str) -> List[Dict[str, Any]]:
        """
        Extract facts from text using pattern matching.

        Args:
            content: Text to analyze

        Returns:
            List of extracted facts
        """
        facts = []

        for pattern in DecisionTracker.FACT_PATTERNS:
            matches = re.findall(pattern, content)
            for match in matches:
                if isinstance(match, tuple):
                    fact_text = " ".join(match).strip()
                else:
                    fact_text = match.strip()

                if len(fact_text) > 5:  # Filter out too short matches
                    # Determine category based on content
                    category = DecisionTracker._categorize_fact(fact_text)

                    facts.append({
                        "fact": fact_text[:500],  # Truncate long facts
                        "category": category,
                        "confidence": 0.8  # Pattern-based extraction has medium confidence
                    })

        return facts

    @staticmethod
    def _categorize_fact(fact_text: str) -> str:
        """Categorize a fact based on its content"""
        text_lower = fact_text.lower()

        if any(word in text_lower for word in ["prefer", "like", "love", "want", "need"]):
            return "preference"
        elif any(word in text_lower for word in ["work", "job", "developer", "engineer"]):
            return "profile"
        elif any(word in text_lower for word in ["project", "app", "application", "system"]):
            return "project"
        elif any(word in text_lower for word in ["using", "use", "technology", "framework", "library"]):
            return "technology"
        else:
            return "general"

    @staticmethod
    async def analyze_conversation(
        content: str,
        api_key_hash: str,
        session_id: int,
        role: str
    ):
        """
        Analyze a conversation message and extract decisions and facts.

        Args:
            content: Message content
            api_key_hash: User's API key hash
            session_id: Session ID
            role: Message role
        """
        # Extract and save decisions
        await DecisionTracker.extract_and_save_decisions(
            content=content,
            api_key_hash=api_key_hash,
            session_id=session_id,
            role=role
        )

        # Extract and save facts (primarily from user messages)
        if role == "user":
            await DecisionTracker.extract_and_save_facts(
                content=content,
                api_key_hash=api_key_hash,
                session_id=session_id,
                role=role
            )

    @staticmethod
    async def get_user_profile(api_key_hash: str) -> Dict[str, Any]:
        """
        Build a user profile from extracted facts.

        Args:
            api_key_hash: User's API key hash

        Returns:
            User profile dictionary
        """
        # Get all facts
        all_facts = await BrainStorage.get_facts(
            api_key_hash=api_key_hash,
            limit=500
        )

        # Group by category
        profile = {
            "preferences": [],
            "profile": [],
            "projects": [],
            "technologies": [],
            "general": []
        }

        for fact in all_facts:
            category = fact.get("category", "general")
            if category in profile:
                profile[category].append(fact)

        # Get recent decisions
        decisions = await BrainStorage.get_decisions(
            api_key_hash=api_key_hash,
            limit=50
        )

        profile["recent_decisions"] = decisions[:10]

        return profile
