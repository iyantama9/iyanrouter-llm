import asyncio
import asyncpg
import os

DATABASE_URL = "postgresql://neondb_owner:npg_Ie6o7NJfptTM@ep-plain-lab-aockmdpu-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

async def clear_cavoti_keys():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        deleted = await conn.execute("DELETE FROM api_keys WHERE provider = 'cv'")
        print(f"Deleted Cavoti keys: {deleted}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(clear_cavoti_keys())
