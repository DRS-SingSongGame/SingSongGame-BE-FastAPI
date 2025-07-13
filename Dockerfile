# 1. 베이스 이미지
FROM python:3.10-slim

# 2. 작업 디렉터리 설정
WORKDIR /app

# 3. 시스템 패키지 설치 (ffmpeg & libsndfile)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg \
       libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 4. Python 패키지 설치
#    requirements.txt 에 필요한 모든 패키지(예: fastapi, uvicorn, aiohttp 등)가 나열되어 있어야 합니다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 애플리케이션 소스 복사
COPY . .

# 6. 환경변수 (로깅 버퍼링 해제 등)
ENV PYTHONUNBUFFERED=1

# 7. 컨테이너 시작 명령
#    Socket.IO 앱을 사용 중이라면 main:sio_app, 일반 FastAPI 앱이면 main:app 으로 바꿔주세요.
CMD ["uvicorn", "main:sio_app", "--host", "0.0.0.0", "--port", "8000"]