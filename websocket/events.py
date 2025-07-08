import sys
sys.path.append("./.venv/Lib/site-packages")
import asyncio
import base64
import os
import random
import time
import hmac
import hashlib
import requests
from collections import defaultdict
from fastapi import FastAPI
import socketio
from main import sio, rooms, round_buffer, round_events, listen_acks, KEYWORDS
from utils import broadcast_room_update
from game.logic import analyze_sings_against_keyword
from game.rounds import run_rounds

# rooms, round_buffer, round_events, listen_acks 등은 main.py에서 import 하거나 별도 관리 필요

# 이벤트 핸들러 함수들 (main.py에서 복사)
# ... (핸들러 함수들 복사 및 필요시 의존성 import) 

@sio.event
async def connect(sid, environ):
    print(f"🔌 {sid} connected")

@sio.event
async def join_room(sid, data):
    room_id = data["roomId"]
    user_id  = data["userId"]

    if room_id not in rooms:
        rooms[room_id] = {"users": {}, "order": [], "host": sid, "state": "waiting"}

    room = rooms[room_id]

    stale_sids = [old_sid for old_sid, u in room["users"].items() if u["id"] == user_id]
    for old_sid in stale_sids:
        room["users"].pop(old_sid, None)
        room["order"]  = [s for s in room["order"] if s != old_sid]

        if room["host"] == old_sid:
            room["host"] = sid

    room["users"][sid] = {
        "id": data["userId"],
        "avatar": data["avatar"],
        "nickname": data["nickname"],
        "ready": sid == room["host"],
        "mic": False
    }
    room["order"].append(sid)

    await sio.enter_room(sid, room_id)
    await broadcast_room_update(room_id)

@sio.event
async def toggle_ready(sid, data=None):
    for rid, room in rooms.items():
        if sid in room["users"]:
            room["users"][sid]["ready"] ^= True
            await broadcast_room_update(rid)
            break

@sio.event
async def leave_room(sid, data=None):
    for rid, room in rooms.items():
        if sid in room["users"]:
            room["users"].pop(sid, None)
            room["order"] = [s for s in room["order"] if s != sid]

            if room["host"] == sid and room["users"]:
                room["host"] = next(iter(room["users"].keys()))

            await broadcast_room_update(rid)
            if not room["users"]:
                del rooms[rid]
            break

@sio.event
async def disconnect(sid, data=None):
    await leave_room(sid)

@sio.event
async def mic_ready(sid, data):
    room_id = data["roomId"]
    if room_id in rooms and sid in rooms[room_id]["users"]:
        rooms[room_id]["users"][sid]["mic"] = True
        await broadcast_room_update(room_id)

@sio.event
async def start_game(sid, data=None):
    for room_id, room in rooms.items():
        if sid not in room["users"] or sid != room["host"]:
            continue

        if room.get("state") == "playing":
            return

        room.update({"turn": 0, "scores": {u: 0 for u in room["users"]}, "state": "playing", "keywords": KEYWORDS.copy()})

        await sio.emit("game_intro", {}, room=room_id)
        await asyncio.sleep(10)
        await run_rounds(room_id)
        break

@sio.on("chat")
async def handle_lobby_chat(sid, msg):
    await sio.emit("chat", msg)

@sio.on("room_chat")
async def handle_room_chat(sid, data=None):
    await sio.emit("room_chat", {"message": data["message"]}, room=data["roomId"])

@sio.on("listen_finished")
async def handle_listen_finished(sid, data=None):
    room_id = str(data["roomId"])

    if room_id not in listen_acks:
        listen_acks[room_id] = set()

    listen_acks[room_id].add(sid)

@sio.on("submit_recording")
async def handle_submit_recording(sid, data):
    room_id    = data["roomId"]
    player_sid = data["playerSid"]
    turn = data.get("turn", -1)
    keyword    = data["keyword"]
    audio      = data["audio"]  # bytes

    # 저장 버퍼
    key = f"{room_id}:{player_sid}:{turn}"
    audio_b64 = base64.b64encode(audio).decode()

    # 분석 비동기 태스크
    async def analyze():
        try:
            # ACR 인증 정보
            ACR_HOST = "identify-ap-southeast-1.acrcloud.com"
            http_uri = "/v1/identify"
            ACR_KEY = os.getenv("ACR_KEY", "").strip()
            ACR_SEC = os.getenv("ACR_SEC", "").strip()
            if not ACR_KEY or not ACR_SEC:
                raise ValueError("ACR_KEY 또는 ACR_SEC 환경변수가 비어 있습니다")
            data_type, version = "audio", "1"
            timestamp = str(int(time.time()))
            string_to_sign = "POST\n{}\n{}\n{}\n{}\n{}".format(
                http_uri, ACR_KEY, data_type, version, timestamp
            )
            print("=== string_to_sign ===")
            print(repr(string_to_sign))  # \n 들이 정확히 들어갔는지 보기 위함

            signature = base64.b64encode(hmac.new(ACR_SEC.encode(), string_to_sign.encode(), hashlib.sha1).digest()).decode()
            # 웹에서 받은 녹음 파일 (이 예시에선 audio 변수가 바깥에서 정의되어 있다고 가정)
            files = {"sample": ("recording.webm", audio, "audio/webm")}
            data = {
                "access_key": ACR_KEY,
                "data_type": "audio",
                "signature_version": "1",
                "signature": signature,
                "sample_bytes": str(len(audio)),
                "timestamp": timestamp,
            }
            print("[DEBUG] ACRCloud 요청 준비 완료")
            # ACRCloud로 비동기 POST 요청
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: requests.post(f"https://{ACR_HOST}{http_uri}", files=files, timeout=10, data=data))
            print(f"[DEBUG] ACRCloud 응답 코드: {resp.status_code}")
            print(f"[DEBUG] ACRCloud 응답 본문 (앞부분): {resp.text[:300]}")
            resp.raise_for_status()  # 4xx/5xx 예외 발생
            return analyze_sings_against_keyword(resp.json(), keyword)

        except Exception as e:
            print("[ERROR] ACR 분석 중 예외 발생:")
            import traceback
            traceback.print_exc()
            return {
                "matched": False,
                "fallback": None,
                "title": None,
                "artist": None,
                "score": -1,
            }

    # buffer 저장 및 이벤트 set (run_rounds 에서 생성된 이벤트가 있을 때만)
    round_buffer[key] = {"audio_b64": audio_b64, "future": asyncio.create_task(analyze())}
    if key in round_events:
        round_events[key].set() 