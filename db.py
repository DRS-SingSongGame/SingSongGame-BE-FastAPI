from dotenv import load_dotenv
load_dotenv()
import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DATABASE_URL = (f"mysql+asyncmy://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

engine = create_async_engine(DATABASE_URL, pool_size=10, max_overflow=20, echo=False)

async def fetch_random_keywords(limit: int) -> list[dict]:
    """
    keyword 테이블에서 `limit` 개를 중복 없이 무작위로 뽑아
    Socket.IO 로직에서 바로 쓸 수 있는 딕셔너리 형태로 반환한다.
    """
    sql = text(
        """
        SELECT keyword_type, keyword_name, keyword_alias
        FROM keyword
        ORDER BY RAND()
        LIMIT :limit
        """
    )

    async with engine.connect() as conn:
        result = await conn.execute(sql, {"limit": limit})
        rows = result.mappings().all()

    keywords = []
    for row in rows:
        # '레드벨벳|redvelvet' → ['레드벨벳', 'redvelvet']
        alias_list = (
            [a.strip() for a in row["keyword_alias"].split("|")]
            if row["keyword_alias"]
            else []
        )
        keywords.append(
            {
                "type": row["keyword_type"],
                "name": row["keyword_name"],
                "alias": alias_list,
            }
        )
    return keywords