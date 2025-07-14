import sys
sys.path.append("./.venv/Lib/site-packages")
import os
from fastapi import FastAPI
from service.keyword_loader import load_keywords
from fastapi.middleware.cors import CORSMiddleware
from collections import defaultdict
import socketio
from fastapi import FastAPI, Depends
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from sqlalchemy import text

# ASGI 서버 설정
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 🚀 서버 시작 시 실행
    if os.getenv("INITIAL_KEYWORD_LOAD", "1") == "1":
        await load_keywords()

    yield
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
sio_app = socketio.ASGIApp(
    sio,
    other_asgi_app=app,
    socketio_path="/fast/socket.io"
)

# rooms, round_buffer, round_events, listen_acks 등은 여기에 유지
rooms = {}
round_buffer = {}
round_events = {}

# ────────────────────────────── 각종 핸들러 및 게임 로직 import
from websocket.events import *
from game.rounds import *
from utils import *

@app.get("/fast/healthz")
async def healthz():
    return {"status": "ok"}
