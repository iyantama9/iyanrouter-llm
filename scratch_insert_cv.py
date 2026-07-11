import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')

def insert_cv_key(api_key):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        provider = 'cv'
        
        cur.execute(
            "INSERT INTO api_keys (key, provider, is_active) VALUES (%s, %s, %s)",
            (api_key, provider, True)
        )
        conn.commit()
        print(f"Successfully inserted Cavoti key: {api_key[:10]}...")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    insert_cv_key('sk-f6fc5625c8ccb301881bd2196bfd13972cbdb685488cccbf80a303bb836ca355')
