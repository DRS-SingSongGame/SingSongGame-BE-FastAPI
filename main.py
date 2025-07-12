import sys
sys.path.append("./.venv/Lib/site-packages")
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from collections import defaultdict
import socketio
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

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
    socketio_path="/fast/socket.io"
)

# rooms, round_buffer, round_events, listen_acks 등은 여기에 유지
rooms = {}
round_buffer = {}
round_events = {}
listen_acks = defaultdict(set)

# ────────────────────────────── 각종 핸들러 및 게임 로직 import
from websocket.events import *
from game.rounds import *
from utils import *

@app.get("/fast/healthz")
async def healthz():
    return {"status": "ok"}
