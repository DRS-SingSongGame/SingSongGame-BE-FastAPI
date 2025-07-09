import random

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
    print("✅ analyze_sings_against_keyword() 호출됨")
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
            base_score  = int(match["score"] * 100)
            jitter      = random.randint(-5, 5)
            final_score = min(100, max(0, base_score + jitter))

            return {
                "matched": True,
                "fallback": match["fallback"],
                "title": match["title"],
                "artist": match["artist"],
                "score": final_score
            }
    return {
        "matched": False,
        "fallback": None,
        "title": None,
        "artist": None,
        "score": 0
    } 