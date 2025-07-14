BATCH_SIZE = 1_000
# ───────────── 프로젝트 루트(/app) 기준으로 경로 고정 ─────────────
from pathlib import Path
BASE_DIR     = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "keyword_dataset.csv"

import csv
from sqlalchemy import text
from db import engine

async def load_keywords():
    """
    keyword 테이블을 TRUNCATE 후 CSV 데이터 삽입
    """
    # ── CSV 읽기 ─────────────────────────────────────────────
    with DATASET_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # ── DB 작업 ─────────────────────────────────────────────
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE keyword;"))

        # CSV 헤더 → DB 컬럼 매핑 (camelCase → snake_case)
        insert_sql = text(
            """
            INSERT INTO keyword (keyword_name, keyword_type, keyword_alias)
            VALUES (:name, :type, :alias)
            """
        )

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            payload = [
                {
                    "name":  r["keywordName"].strip(),
                    "type":  (r.get("keywordType")  or "").strip(),
                    "alias": (r.get("keywordAlias") or "").strip(),
                }
                for r in batch
            ]
            await conn.execute(insert_sql, payload)

    print(f"✅ 키워드 {len(rows)}개를 로드했습니다.")
