import sys
sys.path.append("./.venv/Lib/site-packages")
import os
from dotenv import load_dotenv # type: ignore
load_dotenv()
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from collections import defaultdict
import socketio

# ASGI 서버 설정
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI()
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
    socketio_path="fast/socket.io"
)

# rooms, round_buffer, round_events, listen_acks 등은 여기에 유지
rooms = {}
round_buffer = {}
round_events = {}
listen_acks = defaultdict(set)

# ────────────────────────────── 키워드 목록
KEYWORDS = [
    {"type": "가수", "name": "장범준", "alias": ["Jang Beom June", "장범준"]},
    {"type": "가수", "name": "Red Velvet", "alias": ["레드벨벳", "redvelvet"]},
]

# ────────────────────────────── 각종 핸들러 및 게임 로직 import
from websocket.events import *
from game.logic import *
from game.rounds import *
from utils import *

# (필요시, 각 모듈에서 sio, rooms 등 공유 객체를 import 하거나, main.py에서 의존성 주입)
