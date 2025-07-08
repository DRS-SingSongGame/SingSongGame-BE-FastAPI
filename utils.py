from main import sio, rooms

async def broadcast_room_update(room_id: str):
    room = rooms[room_id]
    users = [
        {
            "id": u["id"],
            "avatar": u["avatar"],
            "nickname": u["nickname"],
            "ready":    u["ready"],
            "isHost":   sid == room["host"],
            "sid":      sid,
            "mic":      u.get("mic", False),
        }
        for sid, u in room["users"].items()
    ]
    await sio.emit("room_update", {"users": users}, room=room_id) 