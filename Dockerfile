FROM python:3.11-slim

# 1) 시스템 패키지 설치 ─────────────────────────────
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates gcc \
 && rm -rf /var/lib/apt/lists/*

# 2) 파이썬 설정 최적화 ────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3) 종속성 먼저 복사 → 레이어 캐시 활용 ──────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4) 애플리케이션 소스 복사 ────────────────────────
COPY . .

# 5) 서버 실행 ─────────────────────────────────────
CMD ["uvicorn", "main:sio_app", "--host", "0.0.0.0", "--port", "8000"]