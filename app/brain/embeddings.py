"""
Embeddings Module - Text to Vector Embeddings

Provides text embedding functionality for semantic search.
Uses sentence-transformers for local embeddings (lightweight, no API calls).
"""

import os
import json
import hashlib
from typing import List, Optional
import numpy as np


class EmbeddingProvider:
    """Base class for embedding providers"""

    def embed_text(self, text: str) -> List[float]:
        """Convert text to embedding vector"""
        raise NotImplementedError

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Convert multiple texts to embeddings"""
        return [self.embed_text(t) for t in texts]

    def similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        a = np.array(vec1)
        b = np.array(vec2)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


class SentenceTransformerEmbedding(EmbeddingProvider):
    """Sentence-transformers based embeddings (local, no API calls)"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize sentence-transformers model.

        Default model: all-MiniLM-L6-v2
        - Fast and lightweight (80MB)
        - 384 dimensions
        - Good quality for semantic search
        """
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
            self.dimension = 384
            print(f"[BRAIN] Loaded embedding model: {model_name}")
        except ImportError:
            print("[BRAIN] sentence-transformers not installed, falling back to simple embeddings")
            raise

    def embed_text(self, text: str) -> List[float]:
        """Embed single text"""
        if not text.strip():
            return [0.0] * self.dimension
        embedding = self.model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts (more efficient than one-by-one)"""
        if not texts:
            return []
        embeddings = self.model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return embeddings.tolist()


class SimpleEmbedding(EmbeddingProvider):
    """
    Fallback simple embedding using TF-IDF-like approach.
    Used when sentence-transformers is not available.
    """

    def __init__(self):
        self.dimension = 128
        self.vocab = {}
        print("[BRAIN] Using simple TF-IDF embeddings (fallback)")

    def _tokenize(self, text: str) -> List[str]:
        """Simple tokenization"""
        import re
        text = text.lower()
        tokens = re.findall(r'\w+', text)
        return tokens

    def embed_text(self, text: str) -> List[float]:
        """Create simple embedding based on token hashing"""
        if not text.strip():
            return [0.0] * self.dimension

        tokens = self._tokenize(text)
        vector = np.zeros(self.dimension)

        for token in tokens:
            # Hash token to dimension index
            hash_val = int(hashlib.md5(token.encode()).hexdigest(), 16)
            idx = hash_val % self.dimension
            vector[idx] += 1.0

        # Normalize
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm

        return vector.tolist()


class EmbeddingCache:
    """Cache embeddings to avoid recomputation"""

    def __init__(self, cache_file: str = ".brain_embedding_cache.json"):
        self.cache_file = cache_file
        self.cache = {}
        self._load_cache()

    def _load_cache(self):
        """Load cache from disk"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
                print(f"[BRAIN] Loaded {len(self.cache)} cached embeddings")
            except Exception as e:
                print(f"[BRAIN] Failed to load embedding cache: {e}")
                self.cache = {}

    def _save_cache(self):
        """Save cache to disk"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f)
        except Exception as e:
            print(f"[BRAIN] Failed to save embedding cache: {e}")

    def get(self, text: str) -> Optional[List[float]]:
        """Get cached embedding"""
        text_hash = hashlib.md5(text.encode()).hexdigest()
        return self.cache.get(text_hash)

    def set(self, text: str, embedding: List[float]):
        """Cache embedding"""
        text_hash = hashlib.md5(text.encode()).hexdigest()
        self.cache[text_hash] = embedding

        # Save every 10 embeddings
        if len(self.cache) % 10 == 0:
            self._save_cache()


# Global embedding provider instance
_embedding_provider: Optional[EmbeddingProvider] = None
_embedding_cache: Optional[EmbeddingCache] = None


def get_embedding_provider() -> EmbeddingProvider:
    """Get or initialize the global embedding provider"""
    global _embedding_provider, _embedding_cache

    if _embedding_provider is None:
        # Try sentence-transformers first
        try:
            _embedding_provider = SentenceTransformerEmbedding()
        except (ImportError, Exception) as e:
            print(f"[BRAIN] Could not load sentence-transformers: {e}")
            _embedding_provider = SimpleEmbedding()

        _embedding_cache = EmbeddingCache()

    return _embedding_provider


def embed_text(text: str, use_cache: bool = True) -> List[float]:
    """
    Embed a single text into vector space.

    Args:
        text: Text to embed
        use_cache: Whether to use embedding cache

    Returns:
        List of floats representing the embedding vector
    """
    if not text or not text.strip():
        provider = get_embedding_provider()
        return [0.0] * provider.dimension

    if use_cache and _embedding_cache:
        cached = _embedding_cache.get(text)
        if cached is not None:
            return cached

    provider = get_embedding_provider()
    embedding = provider.embed_text(text)

    if use_cache and _embedding_cache:
        _embedding_cache.set(text, embedding)

    return embedding


def embed_batch(texts: List[str], use_cache: bool = True) -> List[List[float]]:
    """
    Embed multiple texts into vector space.
    More efficient than calling embed_text multiple times.

    Args:
        texts: List of texts to embed
        use_cache: Whether to use embedding cache

    Returns:
        List of embedding vectors
    """
    if not texts:
        return []

    provider = get_embedding_provider()

    if not use_cache:
        return provider.embed_batch(texts)

    # Check cache first
    results = []
    texts_to_embed = []
    indices_to_embed = []

    for i, text in enumerate(texts):
        if not text or not text.strip():
            results.append([0.0] * provider.dimension)
            continue

        cached = _embedding_cache.get(text) if _embedding_cache else None
        if cached is not None:
            results.append(cached)
        else:
            results.append(None)
            texts_to_embed.append(text)
            indices_to_embed.append(i)

    # Embed uncached texts
    if texts_to_embed:
        new_embeddings = provider.embed_batch(texts_to_embed)
        for idx, embedding in zip(indices_to_embed, new_embeddings):
            results[idx] = embedding
            if _embedding_cache:
                _embedding_cache.set(texts[idx], embedding)

    return results


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors"""
    provider = get_embedding_provider()
    return provider.similarity(vec1, vec2)
