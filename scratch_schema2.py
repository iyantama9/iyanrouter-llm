import asyncio
from database import fetchrow

async def check_schema():
    res = await fetchrow("SELECT * FROM api_keys LIMIT 1;")
    print("Columns:", res.keys() if res else "Table is empty or missing")

if __name__ == "__main__":
    asyncio.run(check_schema())
