import sys
sys.path.append("./.venv/Lib/site-packages")
import asyncio
import base64
import random
from main import sio, rooms, round_buffer, round_events
from audio_utils import convert_format
from utils import broadcast_room_update
from game.analysis import analyze_recording
from game.rounds import run_rounds
from db import fetch_random_keywords

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
    nick = data["nickname"]

    if room_id not in rooms:
        rooms[room_id] = {"users": {}, "order": [], "host": sid, "state": "waiting"}

    room = rooms[room_id]

    if room["state"] == "playing":
        await sio.emit("redirect_lobby",
                       {"reason": "서버와 연결이 끊겨 게임에서 제외되었습니다."},
                       to=sid)
        await sio.disconnect(sid)
        return

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
    if sid not in room["order"]:
        room["order"].append(sid)

    await sio.enter_room(sid, room_id)
    await broadcast_room_update(room_id)
    await sio.emit(
        "room_chat",
        {"message": f"{nick}님이 입장하셨습니다.", "msgType": "join"},
        room=room_id,
    )

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
            # 현재 턴 Event 강제로 해제
            for key, ev in list(round_events.items()):
                if key.startswith(f"{rid}:{sid}:"):
                    ev.set()
                    round_events.pop(key, None)

            leaver = room["users"].pop(sid, None)
            room["order"] = [s for s in room["order"] if s != sid]

            if room["host"] == sid and room["users"]:
                new_host = next(iter(room["users"]))
                room["host"] = new_host
                room["users"][new_host]["ready"] = True
            await broadcast_room_update(rid)

            if not room["users"]:
                del rooms[rid]
            # 시스템 채팅 브로드캐스트
            if leaver and rid in rooms:
                nick = leaver["nickname"]
                await sio.emit(
                    "room_chat",
                    {"message": f"{nick}님이 게임 방을 나갔습니다.",
                     "msgType": 'leave'},
                    room=rid,
                )
            break
    await sio.disconnect(sid)

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
async def start_game(sid, data):
    room_id = data.get("roomId")
    max_rounds = int(data.get("maxRounds"))
    room = rooms.get(room_id)

    if not room or sid not in room["users"] or sid != room["host"]:
            return
    if room.get("state") == "playing":
        return
    
    KEYWORDS = [
        {"type": "가수", "name": "임한별", "alias": ["Lim Han Byul", "임한별"]},
        {"type": "가수", "name": "Red Velvet", "alias": ["레드벨벳", "redvelvet"]},
    ]

    # 플레이어 수에 맞춰 키워드 가져오기
    num_players = len(room["users"])
    total_keywords = num_players * max_rounds
    room_keywords = KEYWORDS or await fetch_random_keywords(total_keywords)
    room.update(
        {
            "state": "playing",
            "turn": 0,
            "round": 1,
            "max_rounds": max_rounds,
            "scores": {u: 0 for u in room["users"]},
            "keywords": room_keywords,
            "kw_idx": 0,
        }
    )

    await sio.emit(
        "game_intro",
        {"round": 1, "maxRounds": room["max_rounds"]},
        room=room_id,
    )
    await asyncio.sleep(11)
    await run_rounds(room_id)

@sio.on("chat")
async def handle_lobby_chat(sid, msg):
    await sio.emit("chat", msg)

@sio.on("room_chat")
async def handle_room_chat(sid, data=None):
    # data: { roomId, message, msgType='chat' }
    await sio.emit(
        "room_chat",
        {"message": data["message"], "msgType": data.get("msgType", "chat")},
        room=data["roomId"],
    )

@sio.on("submit_recording")
async def handle_submit_recording(sid, data):
    room_id    = data["roomId"]
    player_sid = data["playerSid"]
    turn = data.get("turn", -1)
    keyword    = data["keyword"]

    audio_raw  = data["audio"]  # bytes (WebM/Opus)

    # ── 🎙️ 서버-측 WAV 변환 ─────────────────────────────
    wav16k = convert_format(audio_raw, for_whisper=True)  # 16 kHz·mono·PCM16

    # 저장 버퍼
    key        = f"{room_id}:{player_sid}:{turn}"
    audio_b64  = base64.b64encode(wav16k).decode()        # **WAV** 데이터

    # 분석 비동기 태스크
    async def analyze():
        # audio: 클라이언트 원본 음성 파일
        # keyword: {type, name, alias}
        return await analyze_recording(audio_raw, keyword)

    # buffer 저장 및 이벤트 set (run_rounds 에서 생성된 이벤트가 있을 때만)
    round_buffer[key] = {"audio_b64": audio_b64, "future": asyncio.create_task(analyze())}
    if key in round_events:
        round_events[key].set() 