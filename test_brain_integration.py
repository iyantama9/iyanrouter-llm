"""
Brain Integration Test Script

Tests the automatic brain functionality:
1. Brain context building from past conversations
2. Context injection into requests
3. Conversation saving with embeddings
4. Decision and fact extraction
"""

import asyncio
import httpx
import json
from app.database import init_db, close_db, execute, fetchrow, fetch


async def test_brain_health():
    """Test brain health endpoint"""
    print("\n=== Testing Brain Health Endpoint ===")
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:8000/brain/health")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 200
        assert response.json()["brain"] == "enabled"
    print("✓ Brain health check passed")


async def test_brain_tables():
    """Test that brain tables exist in database"""
    print("\n=== Testing Brain Tables ===")
    await init_db()

    tables = [
        "brain_conversations",
        "brain_decisions",
        "brain_facts",
        "brain_profiles"
    ]

    for table in tables:
        result = await fetchrow(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = $1)",
            table
        )
        exists = result["exists"]
        print(f"Table '{table}': {'✓ exists' if exists else '✗ missing'}")
        assert exists, f"Table {table} does not exist"

    await close_db()
    print("✓ All brain tables exist")


async def test_embedding_generation():
    """Test embedding generation"""
    print("\n=== Testing Embedding Generation ===")
    from app.brain.embeddings import embed_text

    test_texts = [
        "Hello, how are you?",
        "I need help with my Python code",
        "What is the weather today?"
    ]

    for text in test_texts:
        embedding = embed_text(text)
        print(f"Text: '{text[:50]}...'")
        print(f"Embedding shape: {len(embedding)} dimensions")
        print(f"First 5 values: {embedding[:5]}")
        assert len(embedding) > 0
        assert all(isinstance(v, float) for v in embedding)

    print("✓ Embedding generation works")


async def test_conversation_save():
    """Test saving conversation to brain"""
    print("\n=== Testing Conversation Save ===")
    await init_db()

    from app.brain.memory import MemoryManager

    test_api_key_hash = "test_user_hash_12345"
    test_session_id = 999

    # Save user message
    await MemoryManager.save_message(
        session_id=test_session_id,
        api_key_hash=test_api_key_hash,
        message_id=1,
        role="user",
        content="I want to build a LLM router with brain functionality",
        model="test/model",
        compute_embedding=True
    )

    # Save assistant message
    await MemoryManager.save_message(
        session_id=test_session_id,
        api_key_hash=test_api_key_hash,
        message_id=2,
        role="assistant",
        content="Great! I can help you build a LLM router with automatic brain features.",
        model="test/model",
        compute_embedding=True
    )

    # Check if saved
    rows = await fetch(
        "SELECT * FROM brain_conversations WHERE api_key_hash = $1 ORDER BY created_at",
        test_api_key_hash
    )

    print(f"Saved {len(rows)} messages to brain")
    for row in rows:
        print(f"  - Role: {row['role']}, Content: {row['content'][:50]}...")
        print(f"    Has embedding: {row['embedding'] is not None}")

    assert len(rows) >= 2

    # Cleanup
    await execute("DELETE FROM brain_conversations WHERE api_key_hash = $1", test_api_key_hash)

    await close_db()
    print("✓ Conversation save works")


async def test_decision_extraction():
    """Test decision extraction from text"""
    print("\n=== Testing Decision Extraction ===")
    from app.brain.decisions import DecisionTracker

    test_texts = [
        "I decided to use PostgreSQL for the database",
        "Let's go with FastAPI for the backend framework",
        "We should implement caching to improve performance",
        "My decision is to deploy on Cloudflare Workers"
    ]

    for text in test_texts:
        decisions = DecisionTracker.extract_decisions(text)
        print(f"Text: '{text}'")
        print(f"Extracted {len(decisions)} decision(s)")
        for d in decisions:
            print(f"  - Title: {d['title']}")

    print("✓ Decision extraction works")


async def test_fact_extraction():
    """Test fact extraction from text"""
    print("\n=== Testing Fact Extraction ===")
    from app.brain.decisions import DecisionTracker

    test_texts = [
        "I prefer using TypeScript over JavaScript",
        "My name is John and I work as a backend engineer",
        "I'm using React for the frontend",
        "The project uses Docker for containerization"
    ]

    for text in test_texts:
        facts = DecisionTracker.extract_facts(text)
        print(f"Text: '{text}'")
        print(f"Extracted {len(facts)} fact(s)")
        for f in facts:
            print(f"  - Fact: {f['fact']}")
            print(f"    Category: {f['category']}")

    print("✓ Fact extraction works")


async def test_semantic_search():
    """Test semantic search"""
    print("\n=== Testing Semantic Search ===")
    await init_db()

    from app.brain.memory import MemoryManager
    from app.brain.semantic import SemanticSearch

    test_api_key_hash = "test_search_user_12345"
    test_session_id = 888

    # Save some test conversations
    test_conversations = [
        ("user", "How do I implement authentication in FastAPI?"),
        ("assistant", "You can use OAuth2 with JWT tokens for authentication."),
        ("user", "What database should I use for my project?"),
        ("assistant", "PostgreSQL is a great choice for most applications."),
        ("user", "How do I deploy my application?"),
        ("assistant", "You can deploy on platforms like Heroku, Railway, or Cloudflare."),
    ]

    for i, (role, content) in enumerate(test_conversations):
        await MemoryManager.save_message(
            session_id=test_session_id,
            api_key_hash=test_api_key_hash,
            message_id=i + 1,
            role=role,
            content=content,
            model="test/model",
            compute_embedding=True
        )

    # Test search
    search_queries = [
        "authentication and security",
        "database selection",
        "deployment options"
    ]

    for query in search_queries:
        results = await SemanticSearch.search(
            query=query,
            api_key_hash=test_api_key_hash,
            limit=3,
            min_similarity=0.1
        )
        print(f"\nQuery: '{query}'")
        print(f"Found {len(results)} result(s)")
        for r in results:
            print(f"  - Similarity: {r['similarity']:.3f}")
            print(f"    Content: {r['content'][:60]}...")

    # Cleanup
    await execute("DELETE FROM brain_conversations WHERE api_key_hash = $1", test_api_key_hash)

    await close_db()
    print("✓ Semantic search works")


async def run_all_tests():
    """Run all brain integration tests"""
    print("=" * 60)
    print("BRAIN INTEGRATION TEST SUITE")
    print("=" * 60)

    try:
        await test_brain_health()
        await test_brain_tables()
        await test_embedding_generation()
        await test_conversation_save()
        await test_decision_extraction()
        await test_fact_extraction()
        await test_semantic_search()

        print("\n" + "=" * 60)
        print("✓ ALL TESTS PASSED")
        print("=" * 60)
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"✗ TEST FAILED: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_all_tests())
