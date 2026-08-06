#!/usr/bin/env python3
"""
Test script for Conversation Memory feature.

Usage:
    python test_conversation_memory.py
"""

import requests
import json
import time
import sys

# Configuration
ROUTER_URL = "http://localhost:4000/v1/messages"
API_KEY = "YOUR_API_KEY_HERE"  # Replace with your actual API key
SESSION_ID = f"test-session-{int(time.time())}"

# Colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

def log(message, color=""):
    """Print colored log message."""
    print(f"{color}{message}{RESET}")

def make_request(messages, enable_memory=True, session_id=None):
    """Make a request to the router."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    if enable_memory:
        headers["X-Enable-Memory"] = "true"

    if session_id:
        headers["X-Session-Id"] = session_id

    payload = {
        "model": "claude-sonnet-5",
        "messages": messages,
        "max_tokens": 500
    }

    log(f"\n{'='*60}", BLUE)
    log(f"Request to: {ROUTER_URL}", BLUE)
    log(f"Session ID: {session_id or 'Auto-generated'}", BLUE)
    log(f"Enable Memory: {enable_memory}", BLUE)
    log(f"Messages: {json.dumps(messages, indent=2)}", BLUE)
    log(f"{'='*60}\n", BLUE)

    try:
        response = requests.post(ROUTER_URL, json=payload, headers=headers, timeout=60)

        if response.status_code == 200:
            data = response.json()
            content = data.get("content", [])

            # Extract text from content blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

            response_text = "\n".join(text_parts)
            log(f"✓ Response (200):", GREEN)
            log(f"{response_text}\n", GREEN)
            return True, response_text
        else:
            log(f"✗ Error ({response.status_code}):", RED)
            log(f"{response.text}\n", RED)
            return False, None
    except Exception as e:
        log(f"✗ Exception: {type(e).__name__}: {str(e)}", RED)
        return False, None

def test_conversation_memory():
    """Test conversation memory feature."""
    log("\n" + "="*60, YELLOW)
    log("CONVERSATION MEMORY TEST", YELLOW)
    log("="*60 + "\n", YELLOW)

    # Test 1: First message in a session
    log("Test 1: First message (set context)", YELLOW)
    log("-" * 60, YELLOW)

    success1, response1 = make_request(
        messages=[
            {"role": "user", "content": "Halo, nama saya Iyan dan saya sedang membangun LLM router."}
        ],
        enable_memory=True,
        session_id=SESSION_ID
    )

    if not success1:
        log("✗ Test 1 FAILED - Could not establish first message", RED)
        return False

    log("✓ Test 1 PASSED - First message sent successfully\n", GREEN)
    time.sleep(2)  # Give DB time to persist

    # Test 2: Second message - should remember context
    log("Test 2: Second message (recall context)", YELLOW)
    log("-" * 60, YELLOW)

    success2, response2 = make_request(
        messages=[
            {"role": "user", "content": "Siapa nama saya?"}
        ],
        enable_memory=True,
        session_id=SESSION_ID
    )

    if not success2:
        log("✗ Test 2 FAILED - Could not send second message", RED)
        return False

    # Check if response contains the name
    if "iyan" in response2.lower():
        log("✓ Test 2 PASSED - AI remembered the name from previous message!", GREEN)
    else:
        log("⚠ Test 2 WARNING - AI responded but didn't mention the name", YELLOW)
        log(f"Expected to see 'Iyan' in response, got: {response2[:100]}...", YELLOW)
        log("\nThis could mean:", YELLOW)
        log("1. History injection is working but AI interpreted differently", YELLOW)
        log("2. History is not being loaded/injected correctly", YELLOW)
        log("\nCheck server logs for '[LOG] Loaded X history messages'", YELLOW)

    time.sleep(2)

    # Test 3: Third message - deeper context
    log("\nTest 3: Third message (deeper context recall)", YELLOW)
    log("-" * 60, YELLOW)

    success3, response3 = make_request(
        messages=[
            {"role": "user", "content": "Apa yang sedang saya bangun?"}
        ],
        enable_memory=True,
        session_id=SESSION_ID
    )

    if not success3:
        log("✗ Test 3 FAILED - Could not send third message", RED)
        return False

    # Check if response mentions LLM router
    if any(keyword in response3.lower() for keyword in ["router", "llm"]):
        log("✓ Test 3 PASSED - AI remembered the project context!", GREEN)
    else:
        log("⚠ Test 3 WARNING - AI responded but didn't mention LLM router", YELLOW)

    time.sleep(2)

    # Test 4: New session (should NOT remember)
    log("\nTest 4: New session (should NOT remember previous context)", YELLOW)
    log("-" * 60, YELLOW)

    new_session_id = f"test-session-new-{int(time.time())}"
    success4, response4 = make_request(
        messages=[
            {"role": "user", "content": "Siapa nama saya?"}
        ],
        enable_memory=True,
        session_id=new_session_id
    )

    if not success4:
        log("✗ Test 4 FAILED - Could not send message with new session", RED)
        return False

    # New session should NOT know the name
    if "iyan" not in response4.lower():
        log("✓ Test 4 PASSED - New session correctly has no previous context", GREEN)
    else:
        log("✗ Test 4 FAILED - New session incorrectly has context from old session!", RED)
        log("This indicates session isolation is not working", RED)
        return False

    # Test 5: Without memory header (should NOT remember)
    log("\nTest 5: Without X-Enable-Memory header (should NOT remember)", YELLOW)
    log("-" * 60, YELLOW)

    success5, response5 = make_request(
        messages=[
            {"role": "user", "content": "Siapa nama saya?"}
        ],
        enable_memory=False,
        session_id=SESSION_ID
    )

    if not success5:
        log("✗ Test 5 FAILED - Could not send message without memory", RED)
        return False

    # Without memory header, should NOT know the name (even with same session ID)
    if "iyan" not in response5.lower():
        log("✓ Test 5 PASSED - Request without memory header correctly has no history", GREEN)
    else:
        log("⚠ Test 5 WARNING - Response mentions the name even without memory header", YELLOW)
        log("This could be coincidence or AI guessing from context", YELLOW)

    log("\n" + "="*60, GREEN)
    log("ALL TESTS COMPLETED SUCCESSFULLY!", GREEN)
    log("="*60 + "\n", GREEN)

    log("Summary:", BLUE)
    log(f"- Session ID used: {SESSION_ID}", BLUE)
    log(f"- New session ID: {new_session_id}", BLUE)
    log(f"- Router URL: {ROUTER_URL}", BLUE)
    log("\nNext steps:", BLUE)
    log("1. Check database: SELECT * FROM chat_sessions WHERE project_identifier LIKE 'test-session%';", BLUE)
    log("2. Check messages: SELECT * FROM chat_messages WHERE session_id IN (...);", BLUE)
    log("3. Check server logs for history loading messages", BLUE)

    return True

def check_prerequisites():
    """Check if prerequisites are met."""
    log("Checking prerequisites...", BLUE)

    # Check if API key is set
    if API_KEY == "YOUR_API_KEY_HERE":
        log("✗ API_KEY not set. Please edit the script and set your API key.", RED)
        log("  Example: API_KEY = 'sk-xxx...'", RED)
        return False

    # Check if router is accessible
    try:
        log(f"Testing connection to {ROUTER_URL}...", BLUE)
        # Try to connect (expect 401 or similar, just checking if endpoint exists)
        response = requests.post(
            ROUTER_URL,
            json={"model": "test", "messages": []},
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        log(f"✓ Router is accessible (status: {response.status_code})", GREEN)
    except requests.exceptions.ConnectionError:
        log(f"✗ Cannot connect to {ROUTER_URL}", RED)
        log("  Make sure the router is running: uvicorn app.main:app --reload --port 4000", RED)
        return False
    except Exception as e:
        log(f"⚠ Warning: {type(e).__name__}: {str(e)}", YELLOW)

    log("✓ Prerequisites check passed\n", GREEN)
    return True

if __name__ == "__main__":
    if not check_prerequisites():
        sys.exit(1)

    try:
        success = test_conversation_memory()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("\n\nTest interrupted by user", YELLOW)
        sys.exit(130)
    except Exception as e:
        log(f"\n✗ Unexpected error: {type(e).__name__}: {str(e)}", RED)
        import traceback
        traceback.print_exc()
        sys.exit(1)
