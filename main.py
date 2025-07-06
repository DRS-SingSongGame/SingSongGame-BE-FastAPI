import sys
sys.path.append("./.venv/Lib/site-packages")
import asyncio
import base64
import hashlib
import hmac
import time
from collections import defaultdict
import os
from dotenv import load_dotenv # type: ignore
load_dotenv()
import requests
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
sio_app = socketio.ASGIApp(sio, other_asgi_app=app)

# ────────────────────────────── 키워드 목록
KEYWORDS = [
    {"type": "artist", "name": "Day6", "alias": ["데이식스", "DAY6"]},
    {"type": "artist", "name": "BLACKPINK", "alias": ["블랙핑크", "Black Pink"]},
]

# ────────────────────────────── 게임 방 상태
rooms: dict[str, dict] = {}

# ────────────────────────────── 라운드 동기화 버퍼
round_buffer: dict[str, dict] = {}
round_events: dict[str, asyncio.Event] = {}
listen_acks: dict[str, set[str]] = defaultdict(set)

# ────────────────────────────── 유틸리티
async def broadcast_room_update(room_id: str):
    room = rooms[room_id]
    users = [
        {
            "nickname": u["nickname"],
            "ready":    u["ready"],
            "isHost":   sid == room["host"],
            "sid":      sid,
            "mic":      u.get("mic", False),
        }
        for sid, u in room["users"].items()
    ]
    await sio.emit("room_update", {"users": users}, room=room_id)

# ────────────────────────────── 이벤트 핸들러
@sio.event
async def connect(sid, environ):
    print(f"🔌 {sid} connected")

@sio.event
async def join_room(sid, data):
    room_id, nick = data["roomId"], data["nickname"]
    if room_id not in rooms:
        rooms[room_id] = {"users": {}, "order": [], "host": sid, "state": "waiting"}
    room = rooms[room_id]
    room["users"][sid] = {"nickname": nick, "ready": sid == room["host"], "mic": False}
    if sid not in room["order"]:
        room["order"].append(sid)
    await sio.enter_room(sid, room_id)
    await broadcast_room_update(room_id)

@sio.event
async def toggle_ready(sid):
    for rid, room in rooms.items():
        if sid in room["users"]:
            room["users"][sid]["ready"] ^= True
            await broadcast_room_update(rid)
            break

@sio.event
async def leave_room(sid):
    for rid, room in rooms.items():
        if sid in room["users"]:
            del room["users"][sid]
            await sio.leave_room(sid, rid)
            await broadcast_room_update(rid)
            break

@sio.event
async def disconnect(sid):
    await leave_room(sid)

@sio.event
async def mic_ready(sid, data):
    room_id = data["roomId"]
    if room_id in rooms and sid in rooms[room_id]["users"]:
        rooms[room_id]["users"][sid]["mic"] = True
        await broadcast_room_update(room_id)

# ────────────────────────────── 게임 시작
@sio.event
async def start_game(sid):
    for room_id, room in rooms.items():
        if sid not in room["users"] or sid != room["host"]:
            continue
        if not all(u.get("ready") and u.get("mic") for u in room["users"].values()):
            await sio.emit(
                "start_failed",
                {"reason": "모든 플레이어가 Ready 상태이고, 마이크를 허용해야 합니다."},
                to=sid,
            )
            return
        room.update({"turn": 0, "scores": {u: 0 for u in room["users"]}, "state": "playing", "keywords": KEYWORDS.copy()})

        await sio.emit("game_intro", {}, room=room_id)
        await asyncio.sleep(5)
        await run_rounds(room_id)
        break

@sio.on("chat")
async def handle_lobby_chat(sid, msg):
    await sio.emit("chat", msg)

@sio.on("room_chat")
async def handle_room_chat(sid, data):
    await sio.emit("room_chat", {"message": data["message"]}, room=data["roomId"])

@sio.on("listen_finished")
async def handle_listen_finished(sid, data):
    listen_acks[data["roomId"]].add(sid)

# --- 채점 관련 함수 (상단에 위치) ---
def keyword_match(song: dict, keyword: dict) -> dict | None:
    import re
    def normalize(s: str) -> str:
        return re.split(r',|&|/|feat\.?|with', s.lower())[0].strip()
    title, artist = song["title"], song["artist"]
    ktype, kname, kalias = keyword["type"], keyword["name"], keyword.get("alias", [])
    if ktype == "title":
        if kname.lower() in title.lower():
            return {**song, "fallback": 0}
        return None
    else:
        if artist == kname:
            return {**song, "fallback": 0}
        if normalize(artist) == normalize(kname):
            return {**song, "fallback": 1}
        for a in kalias:
            if a and a.lower() in artist.lower():
                return {**song, "fallback": 2}
    return None

def analyze_sings_against_keyword(acr_response, keyword):
    sings = []
    for song in acr_response.get("metadata", {}).get("humming", []):
        title = song.get("title", "")
        artist = song.get("artists", [{}])[0].get("name", "")
        score = song.get("score", 0)
        sings.append({"title": title, "artist": artist, "score": score})
    
    print(sings)
    for song in sings:
        match = keyword_match(song, keyword)
        if match:
            return {
                "matched": True,
                "fallback": match["fallback"],
                "title": match["title"],
                "artist": match["artist"],
                "score": int(match["score"] * 100)
            }
    return {
        "matched": False,
        "fallback": None,
        "title": None,
        "artist": None,
        "score": -1
    }

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
        ACR_HOST, http_uri = "identify-ap-southeast-1.acrcloud.com", "/v1/identify"
        ACR_KEY = os.getenv("ACR_KEY")
        ACR_SEC = os.getenv("ACR_SEC")
        data_type, version = "audio", "1"
        timestamp = str(int(time.time()))
        string_to_sign = "\n".join(["POST", http_uri, ACR_KEY, data_type, version, timestamp])
        signature = base64.b64encode(hmac.new(ACR_SEC.encode(), string_to_sign.encode(), hashlib.sha1).digest()).decode()
        files = {
            "sample": ("recording.webm", audio, "audio/webm"),
            "access_key": (None, ACR_KEY),
            "data_type": (None, data_type),
            "signature": (None, signature),
            "sample_bytes": (None, str(len(audio))),
            "timestamp": (None, timestamp),
            "signature_version": (None, version),
        }
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: requests.post(f"https://{ACR_HOST}{http_uri}", files=files, timeout=10)
        )
        return analyze_sings_against_keyword(resp.json(), keyword)

    # buffer 저장 및 이벤트 set (run_rounds 에서 생성된 이벤트가 있을 때만)
    round_buffer[key] = {"audio_b64": audio_b64, "future": asyncio.create_task(analyze())}
    if key in round_events:
        round_events[key].set()

# ────────────────────────────── 라운드 진행
async def run_rounds(room_id: str):
    room = rooms[room_id]
    order = room["order"]
    kw_pool = room["keywords"]

    for turn, sid_turn in enumerate(order):
        if sid_turn not in room["users"] or not kw_pool:
            continue
        keyword = kw_pool.pop(0)
        nick    = room["users"][sid_turn]["nickname"]

        # 1) 키워드 공개
        await sio.emit(
            "keyword_phase",
            {"playerSid": sid_turn, "playerNick": nick, "keyword": keyword},
            room=room_id,
        )
        await asyncio.sleep(5)

        # 2) 녹음 시작
        await sio.emit("record_begin", {"playerSid": sid_turn, "turn": turn}, room=room_id)
        await asyncio.sleep(10)

        # 3) 이벤트 및 버퍼 초기화
        key = f"{room_id}:{sid_turn}:{turn}"
        event = asyncio.Event()
        round_events[key] = event
        await event.wait()
        buf = round_buffer.pop(key)
        analysis_future = buf["future"]
        audio_b64       = buf["audio_b64"]
        del round_events[key]

        # 4) listen phase
        listen_acks.pop(room_id, None)
        await sio.emit("listen_phase", {"playerSid": sid_turn, "audio": audio_b64}, room=room_id)
        try:
            await asyncio.wait_for(_wait_for_acks(room_id, set(room["users"].keys())), timeout=12)
        except asyncio.TimeoutError:
            pass
        listen_acks.pop(room_id, None)

        # 5) 분석 결과 전송
        result = await analysis_future
        room["scores"][sid_turn] += result.get("score", 0)
        await sio.emit("round_result", result, room=room_id)
        await asyncio.sleep(5)

    # 6) 최종 결과
    final_scores = [
        {"nickname": room["users"][sid]["nickname"], "score": score}
        for sid, score in room["scores"].items() if sid in room["users"]
    ]
    await sio.emit("game_result", {"scores": final_scores}, room=room_id)

async def _wait_for_acks(room_id: str, sids: set[str]):
    while True:
        if listen_acks.get(room_id, set()) >= sids:
            return
        await asyncio.sleep(0.1)
