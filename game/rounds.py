from main import sio, rooms, round_buffer, round_events, listen_acks
import asyncio

async def run_rounds(room_id: str):
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    order = room["order"][:]
    max_rounds = room["max_rounds"]

    for rnd in range(1, max_rounds + 1):
        room["round"] = rnd

        for turn, sid_turn in enumerate(order):
            kw_idx = room["kw_idx"]
            keyword = room["keywords"][kw_idx]
            room["kw_idx"] += 1
            nick = room["users"][sid_turn]["nickname"]

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
            await asyncio.sleep(10)

            # 2) 녹음 시작
            await sio.emit("record_begin", {"playerSid": sid_turn, "turn": turn}, room=room_id)
            await asyncio.sleep(10)

            # 3) 이벤트 및 버퍼 초기화
            key = f"{room_id}:{sid_turn}:{turn}"
            event = asyncio.Event()
            round_events[key] = event
            await event.wait()
            buf = round_buffer.pop(key, None)
            if not buf:
                continue
            analysis_future = buf["future"]
            audio_b64       = buf["audio_b64"]
            del round_events[key]

            # 4) listen phase
            await sio.emit("listen_phase", {"playerSid": sid_turn, "audio": audio_b64}, room=room_id)
            try:
                await asyncio.wait_for(_wait_for_acks(room_id, set(room["users"].keys())), timeout=12)
            except asyncio.TimeoutError:
                pass
            listen_acks.pop(room_id, None)

            # 5) 분석 결과 전송
            result = await analysis_future
            if sid_turn in room["scores"]:
                room["scores"][sid_turn] += result.get("score", 0)
        
            result_payload = {
                **result,
                "playerNick": nick,
                "playerSid":  sid_turn,
            }
            await sio.emit("round_result", result_payload, room=room_id)
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