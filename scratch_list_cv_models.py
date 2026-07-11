import asyncio
import os
import httpx
from dotenv import load_dotenv
import json

load_dotenv()

async def list_cavoti_models():
    api_key = 'sk-f6fc5625c8ccb301881bd2196bfd13972cbdb685488cccbf80a303bb836ca355'
    base_url = 'https://cavoti.com/v1'
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"Sending request to {base_url}/models...")
            response = await client.get(
                f"{base_url}/models",
                headers=headers
            )
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                models = response.json()
                print(f"Total models returned: {len(models.get('data', []))}")
                model_ids = [m['id'] for m in models.get('data', [])]
                print(f"First 20 models: {model_ids[:20]}")
            else:
                print(f"Response Body: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(list_cavoti_models())
