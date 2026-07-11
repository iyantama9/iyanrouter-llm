import asyncio
from database import execute as db_execute

async def insert_key():
    api_key = 'sk-f6fc5625c8ccb301881bd2196bfd13972cbdb685488cccbf80a303bb836ca355'
    provider = 'cv'
    
    await db_execute(
        "INSERT INTO api_keys (key, provider, is_active) VALUES ($1, $2, $3)",
        api_key, provider, True
    )
    print(f"Successfully inserted Cavoti key: {api_key[:10]}...")

if __name__ == "__main__":
    asyncio.run(insert_key())
