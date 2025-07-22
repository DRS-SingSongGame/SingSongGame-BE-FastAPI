"""analysis.py – ACRCloud + Whisper/Serper 통합 (앨범 이미지 포함)

두 경로를 병렬 실행해 결과를 결합한다
1️⃣ ACRCloud Humming (8 kHz)
2️⃣ Whisper(STT) → Serper 검색 (16 kHz)

리턴 예시
---------
{
  "matched": true,
  "title": "벚꽃 엔딩",
  "artist": "버스커 버스커",
  "score": 93,
  "source": "acr",        # acr | stt
  "image": "https://...jpg"
}
"""
from __future__ import annotations

import asyncio, base64, hashlib, hmac, os, re, time, json, logging, difflib, random, unicodedata, io
from typing import Any, Dict, List, Tuple

import aiohttp
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from audio_utils import convert_format

logger = logging.getLogger(__name__)

# ───────────────────────────────────────── constants
ACR_HOST = "identify-ap-southeast-1.acrcloud.com"
ACR_URI  = "/v1/identify"
ACR_KEY  = os.getenv("ACR_KEY")
ACR_SEC  = os.getenv("ACR_SEC")

LF_API_KEY = os.getenv("LF_API_KEY")                          # LemonFox Whisper
LEMON_URL  = "https://api.lemonfox.ai/v1/audio/transcriptions"

SERPER_KEY       = os.getenv("SERPER_API_KEY")
SERPER_ENDPOINT  = "https://google.serper.dev/search"

OFFICIAL_DOMAINS = [
    "music.bugs.co.kr",
    "www.genie.co.kr",
    "www.vibe.naver.com",
]

# ───────────────────────────────────────── helpers
def _parse_title_artist(raw: str) -> tuple[str | None, str | None]:
    """검색 결과 title 문자열을 (곡명, 가수)로 정제."""
    raw = re.sub(r"(가사|lyrics|official).*?$", "", raw, flags=re.I).strip()
    raw = re.sub(
        r"\s*[-–—/]\s*(벅스|bugs|지니|genie|멜론|melon|vibe).*?$",
        "",
        raw,
        flags=re.I,
    ).strip()

    # 흔한 구분자 우선 탐색
    for sep in [" - ", " – ", " — ", " / ", "/"]:
        if sep in raw:
            left, right = map(str.strip, raw.split(sep, 1))
            return left, right

    # '곡명 (가수)' 패턴
    m = re.match(r"(.+?)\s*\(\s*([^)]+?)\s*\)$", raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None

def _boost_official(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    boosted = [it for it in items if any(d in it.get("link", "") for d in OFFICIAL_DOMAINS)]
    return boosted + [it for it in items if it not in boosted]

async def _extract_album_image(url: str, session: aiohttp.ClientSession) -> str | None:
    try:
        async with session.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"}) as r:
            txt = await r.text()
        soup = BeautifulSoup(txt, "html.parser")
        tag  = soup.find("meta", property="og:image")
        return tag["content"] if tag else None
    except Exception:
        return None

def _match_keyword(keyword: Dict[str, Any], title: str, artist: str) -> bool:
    """logic.py의 keyword_match 간략 이식."""
    ktype  = keyword.get("type")
    kname  = keyword.get("name", "")
    kalias = keyword.get("alias", [])

    if ktype == "제목":
        return kname.lower() in title.lower()

    if artist.lower() == kname.lower():
        return True

    def normalize(s: str) -> str:
        s = re.sub(r"[()\[\]]", "", s.lower())                       # 괄호 제거
        return re.split(r',|&|/|feat\.?|with', s)[0].strip()

    if normalize(artist) == normalize(kname):
        return True

    return any(a and a.lower() in artist.lower() for a in kalias)

def _similarity(a: str, b: str) -> float:
    """Levenshtein 기반 유사도 (0.0 ~ 1.0)"""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()

JOSA = ("은", "는", "이", "가", "을", "를", "에", "의", "로", "과", "와")

VOWEL_SWAP = {  # jung index: 0~20 (unicode 한글 구성 규칙)
    1: 5, 5: 1,     # ㅐ ↔ ㅔ  (재/제, 내/네 …)
    0: 4, 4: 0,     # ㅏ ↔ ㅓ  (가/거 …)
    8: 13, 13: 8,   # ㅗ ↔ ㅜ  (소/수 …)
}

def _swap_syllable_vowel(ch: str) -> str | None:
    """한글 음절의 모음만 교체(받침X) → 새 글자 하나 반환"""
    code = ord(ch) - 0xAC00
    if not (0 <= code <= 11171):
        return None                       # 한글 완성형 아님
    cho, jung, jong = code // 588, (code % 588) // 28, code % 28
    if jong != 0 or jung not in VOWEL_SWAP:  # 받침 있거나 교환표 없음
        return None
    jung2 = VOWEL_SWAP[jung]
    return chr(0xAC00 + (cho * 588) + (jung2 * 28))

def _strip_josa(token: str) -> str:
    """끝에 붙은 조사 한 글자를 떼어낸다."""
    if len(token) >= 2 and token[-1] in JOSA:
        return token[:-1]
    return token

def _to_initials(hangul: str) -> str:
    """'윤미래' → 'ㅇㅁㄹ' (초성열)"""
    CHO = [chr(c) for c in range(0x1100, 0x1113)]
    res = []
    for ch in hangul:
        code = ord(ch) - 0xAC00
        if 0 <= code <= 11171:
            res.append(CHO[code // 588])
        else:
            res.append(ch)
    return "".join(res)

def _normalize_korean(text: str) -> str:
    # KC 정규화 + 소문자, 공백·특수문자 축약
    txt = unicodedata.normalize("NFKC", text).lower()
    txt = re.sub(r"[^가-힣a-z0-9]+", " ", txt).strip()
    return txt

def _keyword_variants(name: str, aliases: List[str]) -> List[str]:
    basics = [name] + [_normalize_korean(a) for a in aliases if a]
    extras = []

    # 기존 변형: 초성·한글만
    for w in basics:
        extras += [_to_initials(w), re.sub(r"[^가-힣]", "", w)]

    # 🎯 추가: 모음군 교환 변형
    for w in basics:
        chars = list(w)
        for i, ch in enumerate(chars):
            repl = _swap_syllable_vowel(ch)
            if repl:
                changed = chars.copy()
                changed[i] = repl
                extras.append("".join(changed))

    return list({w for w in basics + extras if w})

def remove_keyword_like_tokens(stt_text: str, keyword: dict) -> str:
    raw_alias = keyword.get("alias", "")
    if isinstance(raw_alias, list):
        alias_list = raw_alias
    else:                                 # "a|b|c" 형태
        alias_list = raw_alias.split("|") if raw_alias else []

    targets = _keyword_variants(keyword["name"], alias_list)
    clean_tokens = []
    for raw_tok in _normalize_korean(stt_text).split():
        tok = _strip_josa(raw_tok)                    # ② 조사 제거
        matched = any(
            fuzz.ratio(tok, kw) >= 75                 # ④ 유사도 75%
            for kw in targets
        )
        if not matched:
            clean_tokens.append(raw_tok)

    return " ".join(clean_tokens)

# ───────────────────────────────────────── scoring
def _score_acr(sim: float) -> int:
    """ACRCloud: 80점 기본 + 유사도(0~1) × 15 → 80~95점 + 랜덤 보정 1~5점 → 최종 81~100점"""
    base = max(80, min(95, 80 + int(round(sim * 15))))
    bonus = random.randint(1, 5)
    return min(base + bonus, 100)

def _score_stt(sim: float) -> int:
    """STT·Serper: 60점 기본 + 종합점수(0~1) × 15 → 60~75점 + 랜덤 보정 1~5점 → 최종 61~80점"""
    base = max(60, min(75, 60 + int(round(sim * 15))))
    bonus = random.randint(1, 5)
    return min(base + bonus, 80)
# ───────────────────────────────────────── external calls
async def _call_acr(session: aiohttp.ClientSession, wav: bytes) -> Dict[str, Any]:
    ts = str(int(time.time()))
    sign_str = "\n".join(["POST", ACR_URI, ACR_KEY, "audio", "1", ts])
    signature = base64.b64encode(hmac.new(ACR_SEC.encode(), sign_str.encode(), hashlib.sha1).digest()).decode()

    form = aiohttp.FormData()
    form.add_field("access_key",        ACR_KEY)
    form.add_field("data_type",         "audio")
    form.add_field("signature_version", "1")
    form.add_field("signature",         signature)
    form.add_field("sample_bytes",      str(len(wav)))
    form.add_field("timestamp",         ts)
    form.add_field("sample", wav, filename="sample.wav", content_type="audio/wav")

    async with session.post(f"https://{ACR_HOST}{ACR_URI}", data=form, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)

async def _call_whisper(session: aiohttp.ClientSession, wav: bytes) -> str:
    form = aiohttp.FormData()
    form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
    form.add_field("language", "korean")
    form.add_field("response_format", "json")
    headers = {"Authorization": f"Bearer {LF_API_KEY}"}

    async with session.post(LEMON_URL, data=form, headers=headers, timeout=15) as r:
        r.raise_for_status()
        j = await r.json()
    return j.get("text", "").strip()

async def _serper_search(session: aiohttp.ClientSession, query: str) -> Tuple[str | None, str | None, str | None]:
    if not query:
        return None, None, None

    payload = {"q": query, "num": 10, "gl": "kr", "hl": "ko"}
    headers = {"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"}
    async with session.post(SERPER_ENDPOINT, json=payload, headers=headers, timeout=8) as r:
        r.raise_for_status()
        data = await r.json()

    # 1) Knowledge Graph 우선
    kg = data.get("knowledgeGraph", {})
    title = artist = None
    if kg and kg.get("type") in {"Song", "Single"}:
        title  = kg.get("title")
        attrs  = {k.lower(): v for k, v in kg.get("attributes", {}).items()}
        artist = attrs.get("artist") or attrs.get("artists") or kg.get("artist")

    # 2) Organic 결과에서 보완
    items = _boost_official(data.get("organic", []))
    if (not title or not artist) and items:
        for it in items:
            t, a = _parse_title_artist(it.get("title", ""))
            if t and a:
                title, artist = title or t, artist or a
                if title and artist:
                    break

    # 3) 앨범 이미지
    image = None
    for it in items:
        link = it.get("link", "")
        if any(d in link for d in OFFICIAL_DOMAINS):
            image = await _extract_album_image(link, session)
            if image:
                break

    return title, artist, image

# ───────────────────────────────────────── main entry
async def analyze_recording(raw: bytes, keyword: Dict[str, Any]) -> Dict[str, Any]:
    """녹음 bytes + keyword → 판정 dict."""
    wav_hum = convert_format(raw, for_whisper=False)   # 8 kHz
    wav_stt = convert_format(raw, for_whisper=True)    # 16 kHz

    async with aiohttp.ClientSession() as session:
        acr_task  = asyncio.create_task(_call_acr(session, wav_hum))
        stt_task  = asyncio.create_task(_call_whisper(session, wav_stt))

        acr_json  = await acr_task
        lyrics    = await stt_task
        print("\n🟦 Whisper 추출 가사:\n", lyrics)

        if keyword.get("type") == "가수":
            lyrics_clean = remove_keyword_like_tokens(lyrics, keyword)
            print("🟢 키워드 제거 후:", lyrics_clean or "<empty>")
        else:                     # 제목 키워드는 그대로 둠
            lyrics_clean = lyrics

        if not lyrics_clean.strip():
            print("🛑 키워드만 포함 → Serper 건너뜀")
            lyrics_clean = None    # 아래에서 falsy 체크용

        # Serper Search
        s_title, s_artist, s_img = await _serper_search(
            session,
            (lyrics_clean[:100] + " 가사") if lyrics_clean else ""
        )

        # 🔵 Serper 결과 출력
        print("\n🟦 Serper 검색 결과:")
        print(f"title  : {s_title}")
        print(f"artist : {s_artist}")
        print(f"image  : {s_img}")

        # ── 1) ACRCloud 우선 매칭
        hum_tracks = acr_json.get("metadata", {}).get("humming", [])

        print("\n🟦 ACRCloud Top 5:")
        for i, trk in enumerate(hum_tracks[:5]):
            title  = trk.get("title", "")
            artist = trk.get("artists", [{}])[0].get("name", "")
            score  = trk.get("score", "")
            print(f"{i+1}. {title} / {artist} ({score})")

        for trk in hum_tracks:
            t_title  = trk.get("title", "")
            t_artist = trk.get("artists", [{}])[0].get("name", "")
            if _match_keyword(keyword, t_title, t_artist):
                sim = float(trk.get("score", 0))
                score = _score_acr(sim)
                print(f"🔵 ACR 유사도: {sim:.2f} → 점수: {score}")
                return {
                    "matched": True,
                    "title":   t_title,
                    "artist":  t_artist,
                    "score":   score,
                    "source":  "acr",
                    "image":   s_img,  # 이미 Serper에서 얻은 이미지 재사용
                }

        # ── 2) ACR 실패 → STT·Serper
        if s_title and s_artist and _match_keyword(keyword, s_title, s_artist):
            if s_title and s_artist and _match_keyword(keyword, s_title, s_artist):
                title_in_lyrics  = s_title.lower() in lyrics.lower() if lyrics else False
                artist_in_lyrics = s_artist.lower() in lyrics.lower() if lyrics else False

                sim_title  = _similarity(lyrics, s_title) if lyrics else 0
                sim_artist = _similarity(lyrics, s_artist) if lyrics else 0
                sim_lev    = 0.5 * sim_title + 0.5 * sim_artist

                sim = 0.2 * title_in_lyrics + 0.2 * artist_in_lyrics + 0.6 * sim_lev
                score = _score_stt(sim)

                print("\n🟨 STT 유사도 디버깅:")
                print(f"- title 포함 여부      : {title_in_lyrics}")
                print(f"- artist 포함 여부     : {artist_in_lyrics}")
                print(f"- Levenshtein title     : {sim_title:.2f}")
                print(f"- Levenshtein artist    : {sim_artist:.2f}")
                print(f"- 가중 평균 sim         : {sim:.2f}")
                print(f"- 최종 점수             : {score}")

                return {
                    "matched": True,
                    "title":   s_title,
                    "artist":  s_artist,
                    "score":   score,
                    "source":  "stt",
                    "image":   s_img,
                }


        # ── 3) 완전 실패
        return {"matched": False, "title": None, "artist": None, "score": 0, "image": None}
