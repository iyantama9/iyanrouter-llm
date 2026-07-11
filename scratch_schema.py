import asyncio
from database import fetch

async def check_schema():
    res = await fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'api_keys';")
    print("Columns in api_keys:", [row['column_name'] for row in res])

if __name__ == "__main__":
    asyncio.run(check_schema())
