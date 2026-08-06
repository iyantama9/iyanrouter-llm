"""
Semantic Search Module - Search conversations by meaning

Provides semantic search capabilities over conversation history.
"""

from typing import List, Dict, Any, Optional
from app.brain.embeddings import embed_text, cosine_similarity
from app.brain.storage import BrainStorage


class SemanticSearch:
    """Semantic search over conversation history"""

    @staticmethod
    async def search(
        query: str,
        api_key_hash: str,
        session_id: Optional[int] = None,
        limit: int = 10,
        min_similarity: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Search conversations by semantic similarity.

        Args:
            query: Search query text
            api_key_hash: User's API key hash
            session_id: Optional session to search within
            limit: Maximum number of results
            min_similarity: Minimum similarity threshold (0-1)

        Returns:
            List of matching conversations with similarity scores
        """
        # Embed the query
        query_embedding = embed_text(query)

        # Search in database
        results = await BrainStorage.search_conversations_by_embedding(
            api_key_hash=api_key_hash,
            query_embedding=query_embedding,
            limit=limit * 2,  # Get more results for filtering
            session_id=session_id
        )

        # Filter by minimum similarity
        filtered = [r for r in results if r.get("similarity", 0) >= min_similarity]

        return filtered[:limit]

    @staticmethod
    async def find_related_conversations(
        message: str,
        api_key_hash: str,
        session_id: Optional[int] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find related past conversations for a given message.
        Used for context injection.

        Args:
            message: Current message text
            api_key_hash: User's API key hash
            session_id: Optional session to search within
            limit: Maximum number of related conversations

        Returns:
            List of related conversations
        """
        return await SemanticSearch.search(
            query=message,
            api_key_hash=api_key_hash,
            session_id=session_id,
            limit=limit,
            min_similarity=0.4  # Higher threshold for context injection
        )

    @staticmethod
    async def search_decisions(
        query: str,
        api_key_hash: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search past decisions by semantic similarity.

        Args:
            query: Search query
            api_key_hash: User's API key hash
            limit: Maximum number of results

        Returns:
            List of matching decisions
        """
        from app.brain.embeddings import embed_text

        # Get all decisions
        decisions = await BrainStorage.get_decisions(
            api_key_hash=api_key_hash,
            limit=100
        )

        if not decisions:
            return []

        # Embed query
        query_embedding = embed_text(query)

        # Calculate similarity for each decision
        results = []
        for decision in decisions:
            # Combine title + description for search
            text = f"{decision.get('title', '')} {decision.get('description', '')}"
            text_embedding = embed_text(text)
            similarity = cosine_similarity(query_embedding, text_embedding)

            results.append({
                **decision,
                "similarity": similarity
            })

        # Sort by similarity
        results.sort(key=lambda x: x["similarity"], reverse=True)

        # Filter by minimum similarity
        filtered = [r for r in results if r["similarity"] >= 0.3]

        return filtered[:limit]

    @staticmethod
    async def search_facts(
        query: str,
        api_key_hash: str,
        category: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search facts by semantic similarity.

        Args:
            query: Search query
            api_key_hash: User's API key hash
            category: Optional category filter
            limit: Maximum number of results

        Returns:
            List of matching facts
        """
        from app.brain.embeddings import embed_text

        # Get all facts
        facts = await BrainStorage.get_facts(
            api_key_hash=api_key_hash,
            category=category,
            limit=200
        )

        if not facts:
            return []

        # Embed query
        query_embedding = embed_text(query)

        # Calculate similarity for each fact
        results = []
        for fact in facts:
            fact_text = fact.get('fact', '')
            fact_embedding = embed_text(fact_text)
            similarity = cosine_similarity(query_embedding, fact_embedding)

            results.append({
                **fact,
                "similarity": similarity
            })

        # Sort by similarity and confidence
        results.sort(key=lambda x: (x["similarity"] * x.get("confidence", 1.0)), reverse=True)

        # Filter by minimum similarity
        filtered = [r for r in results if r["similarity"] >= 0.3]

        return filtered[:limit]
