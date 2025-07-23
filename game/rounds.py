from main import sio, rooms, round_buffer, round_events
import asyncio

TURN_TIMEOUT = 12   # 녹음 제출 대기
LISTEN_LEN   = 10
RECORD_LEN   = 10
KW_LEN       = 9

async def run_rounds(room_id: str):
    room = rooms.get(room_id)
    if not room: return

    max_rounds = room["max_rounds"]

    for rnd in range(1, max_rounds + 1):
        room["round"] = rnd

        turn_idx = 0
        while turn_idx < len(room["order"]):
            sid_turn = room["order"][turn_idx]

            # 탈주자면 즉시 skip
            if sid_turn not in room["users"]:
                turn_idx += 1
                continue

            # ──❶ 플레이어가 keyword 페이즈 사이에 나갔으면 즉시 skip
            if sid_turn not in room["users"]:
                continue          # turn_idx 그대로 → 새 플레이어가 같은 인덱스로 당겨짐

            keyword = room["keywords"][room["kw_idx"]]
            room["kw_idx"] += 1
            nick = room["users"][sid_turn]["nickname"]

            key_kw = f"{room_id}:{sid_turn}:{turn_idx}:kw"
            kw_event = asyncio.Event()
            round_events[key_kw] = kw_event            # leave_room()에서 set 가능
            await sio.emit(
                "keyword_phase",
                {
                    "playerSid": sid_turn,
                    "playerNick": nick,
                    "keyword": keyword,
                    "round": rnd,
                    "maxRounds": max_rounds,
                },
                room=room_id,
            )
            try:
                await asyncio.wait_for(kw_event.wait(), timeout=KW_LEN)
            except asyncio.TimeoutError:
                pass                                   # 8초 정상 경과
            finally:
                round_events.pop(key_kw, None)         # 정리

            if sid_turn not in room["users"]:
                continue

            # 2) 녹음 시작
            key   = f"{room_id}:{sid_turn}:{turn_idx}"
            event = asyncio.Event()
            round_events[key] = event

            await sio.emit("record_begin",
                           {"playerSid": sid_turn, "turn": turn_idx},
                           room=room_id)

            # • 녹음 제출, • 중도 탈주(set()), • 10 초 경과  → 셋 중 먼저 도달
            try:
                await asyncio.wait_for(event.wait(), timeout=RECORD_LEN + 2)
            except asyncio.TimeoutError:
                pass    # 그냥 넘어가면 아래에서 buf가 없어서 skip 처리됨

            buf = round_buffer.pop(key, None)
            if not buf:
                # 제출이 없었거나 탈주 → skip
                if sid_turn in room["users"]:
                    turn_idx += 1   # ‘남아 있지만 제출X’만 인덱스 증가
                continue

            analysis_future = buf["future"]
            audio_b64       = buf["audio_b64"]

            # 4) listen phase
            await sio.emit(
                "listen_phase",
                {
                    "playerSid": sid_turn,
                    "audio": audio_b64,     # 16 kHz·mono·PCM16 WAV를 base64
                    "mime":  "audio/wav",
                },
                room=room_id,
            )
            await asyncio.sleep(LISTEN_LEN)

            # 5) 분석 결과 전송
            try:
                result = await asyncio.wait_for(
                    analysis_future,
                    timeout=LISTEN_LEN + 0.5,   # 10.5 s 내 미도착 → 실패 처리
                )
            except asyncio.TimeoutError:
                analysis_future.cancel()
                result = {
                    "matched": False,
                    "title":   None,
                    "artist":  None,
                    "score":   0,
                    "image":   None,
                }
            if sid_turn in room["scores"]:
                room["scores"][sid_turn] += result.get("score", 0)
        
            await sio.emit("round_result",
                           {**result, "playerNick": nick, "playerSid": sid_turn},
                           room=room_id)
            await asyncio.sleep(6)          # result 표시 대기
            turn_idx += 1                   # 정상 완료 → 인덱스 증가
        
        # while end
    # for rnd

    # 6) 최종 결과 (남아 있는 인원만 표시)
    final_scores = [
        {"nickname": u["nickname"], "score": room["scores"][sid]}
        for sid, u in room["users"].items()
    ]
    await sio.emit("game_result", {"scores": final_scores}, room=room_id)