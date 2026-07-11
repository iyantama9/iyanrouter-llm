import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

async def test_cavoti():
    api_key = 'sk-f6fc5625c8ccb301881bd2196bfd13972cbdb685488cccbf80a303bb836ca355'
    base_url = 'https://cavoti.com/v1'
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ]
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"Sending request to {base_url}/chat/completions...")
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=data
            )
            print(f"Status Code: {response.status_code}")
            print(f"Response Body: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_cavoti())
