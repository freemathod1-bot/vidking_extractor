#!/usr/bin/env python3
"""
VidKing Stream Extractor with Residential Proxy Rotation
GitHub Actions compatible — reads inputs from env vars or CLI args,
writes output to results/<mediaType>_<tmdbId>[_sXeY].json
"""

import sys
import os
import re
import json
import time
import base64
import ctypes
import random
import requests
import urllib3
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, quote

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Residential Proxy Pool ────────────────────────────────────────────────────
# Format: (host, port, username, password, location)

PROXY_POOL = [
    # ── Batch 1 (user: zmglmvwl) ─────────────────────────────────────────────
    ("31.59.20.176",    6754, "zmglmvwl", "puz74ldkgj3f", "UK-London"),
    ("31.56.127.193",   7684, "zmglmvwl", "puz74ldkgj3f", "US-Seattle"),
    ("45.38.107.97",    6014, "zmglmvwl", "puz74ldkgj3f", "UK-London"),
    ("198.105.121.200", 6462, "zmglmvwl", "puz74ldkgj3f", "UK-London"),
    ("64.137.96.74",    6641, "zmglmvwl", "puz74ldkgj3f", "ES-Madrid"),
    ("198.23.243.226",  6361, "zmglmvwl", "puz74ldkgj3f", "US-LosAngeles"),
    ("38.154.185.97",   6370, "zmglmvwl", "puz74ldkgj3f", "US-Piscataway"),
    ("84.247.60.125",   6095, "zmglmvwl", "puz74ldkgj3f", "PL-Warsaw"),
    ("142.111.67.146",  5611, "zmglmvwl", "puz74ldkgj3f", "JP-Tokyo"),
    ("191.96.254.138",  6185, "zmglmvwl", "puz74ldkgj3f", "US-LosAngeles"),
    # ── Batch 2 (user: dxicdysy) ─────────────────────────────────────────────
    ("31.59.20.176",    6754, "dxicdysy",  "yndikr9coeto", "UK-London"),
    ("31.56.127.193",   7684, "dxicdysy",  "yndikr9coeto", "US-Seattle"),
    ("45.38.107.97",    6014, "dxicdysy",  "yndikr9coeto", "UK-London"),
    ("198.105.121.200", 6462, "dxicdysy",  "yndikr9coeto", "UK-London"),
    ("64.137.96.74",    6641, "dxicdysy",  "yndikr9coeto", "ES-Madrid"),
    ("198.23.243.226",  6361, "dxicdysy",  "yndikr9coeto", "US-LosAngeles"),
    ("38.154.185.97",   6370, "dxicdysy",  "yndikr9coeto", "US-Piscataway"),
    ("84.247.60.125",   6095, "dxicdysy",  "yndikr9coeto", "PL-Warsaw"),
    ("142.111.67.146",  5611, "dxicdysy",  "yndikr9coeto", "JP-Tokyo"),
    ("191.96.254.138",  6185, "dxicdysy",  "yndikr9coeto", "US-LosAngeles"),
]

_proxy_index = 0


def get_next_proxy():
    global _proxy_index
    entry = PROXY_POOL[_proxy_index % len(PROXY_POOL)]
    _proxy_index += 1
    host, port, user, pwd, loc = entry
    proxy_url = f"http://{user}:{pwd}@{host}:{port}"
    return {"http": proxy_url, "https": proxy_url}, loc


def get_random_proxy():
    entry = random.choice(PROXY_POOL)
    host, port, user, pwd, loc = entry
    proxy_url = f"http://{user}:{pwd}@{host}:{port}"
    return {"http": proxy_url, "https": proxy_url}, loc


# ── Constants ─────────────────────────────────────────────────────────────────

DB_BASE  = "https://db.speedracelight.com/3"
API_BASE = "https://api.speedracelight.com"
LIZER    = "https://lizer123.site"

PROVIDERS = [
    "cdn",
    "hdmovie",
    "lamovie",
    "m4uhd",
    "vsrc",
    "superflix",
]

CDN_PROXY_BASE     = "https://megaplalyermoy-soyy.onrender.com/proxy?url={encoded_url}&ref=https%3A%2F%2Fwww.vidking.net%2F&origin="
LAMOVIE_PROXY_BASE = "https://foxy-doxy.andruilsyestems.workers.dev/proxy?url={b64_url}&headers=eyJSZWZlcmVyIjoiaHR0cHM6Ly93d3cudmlka2luZy5uZXQvIn0%3D"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Origin":          "https://www.vidking.net",
    "Referer":         "https://www.vidking.net/",
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Decryption Constants ──────────────────────────────────────────────────────

HL_CONSTS = [
    1116352408, 1899447441, 3049323471, 3921009573,
    961987163,  1508970993, 2453635748, 2870763221,
    3624381080, 310598401,  607225278,  1426881987,
    1925078388, 2162078206, 2614888103, 3248222580,
]
F_CONSTS     = [1732584193, 4023233417, 2562383102, 271733878]
JS_CONST     = 61
SF_CONST     = 8
MS_CONST     = 2654435769
MAGIC_HEADER = bytes([109, 118, 109, 49])  # b"mvm1"

# ── Proxy URL Helpers ─────────────────────────────────────────────────────────

def cdn_proxy(url):
    return CDN_PROXY_BASE.replace("{encoded_url}", quote(url, safe=""))

def lamovie_proxy(url):
    b64        = base64.b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    pad        = (4 - len(b64) % 4) % 4
    b64_padded = b64 + "=" * pad
    return LAMOVIE_PROXY_BASE.replace("{b64_url}", quote(b64_padded, safe=""))

# ── uint32 / math helpers ─────────────────────────────────────────────────────

def _u32(v):
    return v & 0xFFFFFFFF

def _imul(a, b):
    result = ctypes.c_int32(a & 0xFFFFFFFF).value * ctypes.c_int32(b & 0xFFFFFFFF).value
    return _u32(result)

def _ps(l, o):
    l = _u32(l)
    o = o & 31
    if o == 0:
        return l
    return _u32((l << o) | (l >> (32 - o)))

def _ci(l):
    l = _u32(l)
    l = l ^ (l >> 16)
    l = _u32(_imul(l, 2246822507))
    l = l ^ (l >> 13)
    l = _u32(_imul(l, 3266489909))
    l = l ^ (l >> 16)
    return _u32(l)

def _af(seed_str):
    o = _u32(F_CONSTS[0])
    for e, ch in enumerate(seed_str):
        o = _ps(_u32(o ^ _imul(ord(ch), HL_CONSTS[e & 15])), 5)
    return _ci(o)

def _wf(seed_str):
    o = {i: i for i in range(256)}
    e = 0
    for i in range(256):
        e = (e + o[i] + ord(seed_str[i % len(seed_str)])) & 255
        o[i], o[e] = o[e], o[i]
    return o

def _vf(seed_str):
    o = 2166136261
    for ch in seed_str:
        o = _u32(_imul(_u32(o ^ ord(ch)), 16777619))
    return _ci(o)

def _nf(l, o, e):
    return _u32((l ^ o) | (l & o & e))

def _bf(l):
    return _u32(_imul(l, l + 1) & 1) == 0

def _iff(l):
    return _u32(_imul(l, l + 1) & 1) == 1

def _rf(seed_str, tmdb_id):
    if _iff(len(seed_str)):
        return {"S": _wf(seed_str), "acc": _af(seed_str)}
    e = {}
    i = _ci(_u32(_vf(seed_str) ^ _ci(_u32(_u32(tmdb_id) ^ MS_CONST))))
    for r in range(SF_CONST):
        if _bf(r):
            n = i % JS_CONST
            i = _ps(_u32(i + MS_CONST), 7 + (r & 7))
            e[n] = _u32(i ^ _ci(i))
            i = _ci(_u32(i + n))
        else:
            e[r] = HL_CONSTS[r & 15]
    return {"S": e, "acc": _ci(_u32(i ^ 2779096485))}

def _cf(state, o):
    e = state["S"]
    i = state["acc"]
    r = i % JS_CONST
    n = -1 if (r in e) else 0
    u = _u32(e.get(r, 0))
    d = _u32(_imul(MS_CONST, o + 1))
    g = _nf(i, _u32(u ^ d), n)
    g = _u32(_ps(_u32(g + i), r & 31) ^ _ps(i, (_imul(r, 7) & 31)))
    i = _ci(_u32(g + MS_CONST))
    e[r] = _u32(i)
    state["acc"] = i
    return _u32(i)

def _generate_keystream(seed_str, tmdb_id, length):
    state     = _rf(seed_str, tmdb_id)
    keystream = bytearray(length)
    n = 0
    u = 0
    while u < length:
        d  = _cf(state, n)
        n += 1
        keystream[u] = d & 255;           u += 1
        if u < length:
            keystream[u] = (d >> 8)  & 255; u += 1
        if u < length:
            keystream[u] = (d >> 16) & 255; u += 1
        if u < length:
            keystream[u] = (d >> 24) & 255; u += 1
    return keystream

def decrypt_vidking_payload(ciphertext_b64, seed_str, tmdb_id):
    padded = ciphertext_b64.replace("-", "+").replace("_", "/")
    padded += "=" * ((4 - len(padded) % 4) % 4)
    raw_bytes = bytearray(base64.b64decode(padded))
    keystream = _generate_keystream(seed_str, tmdb_id, len(raw_bytes))
    for idx in range(len(raw_bytes)):
        raw_bytes[idx] ^= keystream[idx]
    if raw_bytes[:4] != MAGIC_HEADER:
        raise ValueError("Decryption failed: bad seed or corrupted payload")
    return raw_bytes[4:].decode("utf-8")

# ── HTTP fetch with proxy rotation ────────────────────────────────────────────

def fetch(url, extra_headers=None, retries=6, delay=1.0):
    headers = {**HEADERS, **(extra_headers or {})}
    last_error = None
    for i in range(retries):
        proxies, loc = get_next_proxy()
        try:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=20, verify=False)
            if resp.status_code == 429:
                print(f"  [WARN] 429 on proxy {loc} — rotating...")
                time.sleep(delay)
                continue
            return resp
        except requests.exceptions.ProxyError as exc:
            last_error = exc
            print(f"  [WARN] Proxy error [{loc}] — rotating... ({i+1}/{retries})")
            time.sleep(delay * 0.5)
        except requests.exceptions.ConnectionError as exc:
            last_error = exc
            print(f"  [WARN] Connection error [{loc}] — rotating... ({i+1}/{retries})")
            time.sleep(delay * 0.5)
        except requests.exceptions.Timeout as exc:
            last_error = exc
            print(f"  [WARN] Timeout [{loc}] — rotating... ({i+1}/{retries})")
        except Exception as exc:
            last_error = exc
            if i == retries - 1:
                raise
            time.sleep(delay)
    raise RuntimeError(f"All {retries} proxy attempts failed. Last error: {last_error}")

# ── Core pipeline ─────────────────────────────────────────────────────────────

def fetch_metadata(media_type, tmdb_id):
    url  = f"{DB_BASE}/{media_type}/{tmdb_id}?append_to_response=external_ids&language=en-US"
    resp = fetch(url)
    if resp.status_code != 200:
        raise RuntimeError(f"Metadata HTTP {resp.status_code}")
    data     = resp.json()
    title    = data.get("title") or data.get("name") or data.get("original_title") or ""
    date_str = data.get("release_date") or data.get("first_air_date") or ""
    year     = date_str[:4] if date_str else ""
    ext      = data.get("external_ids") or {}
    imdb_id  = ext.get("imdb_id") or data.get("imdb_id") or ""
    return {"tmdbId": tmdb_id, "imdbId": imdb_id, "title": title,
            "year": year, "mediaType": media_type}

def fetch_seed(tmdb_id):
    url  = f"{API_BASE}/seed?mediaId={tmdb_id}"
    resp = fetch(url)
    text = resp.text
    try:
        data = json.loads(text)
        if data and data.get("seed"):
            return data["seed"]
    except Exception:
        pass
    m = re.search(r"(\d+\.[A-Za-z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    clean = re.sub(r"[^\x20-\x7e]", "", text)
    m = re.search(r"(\d+\.[A-Za-z0-9_-]{10,})", clean)
    if m:
        return m.group(1)
    raise RuntimeError(f"Could not extract seed: {text[:200]}")

def fetch_sources(meta, seed, provider, season="1", episode="1"):
    params = urlencode({
        "title":     meta["title"],
        "mediaType": meta["mediaType"],
        "year":      meta["year"],
        "episodeId": episode,
        "seasonId":  season,
        "tmdbId":    meta["tmdbId"],
        "imdbId":    meta["imdbId"],
        "enc":       "2",
        "seed":      seed,
        "_t":        str(int(time.time() * 1000)),
    })
    url   = f"{API_BASE}/{provider}/sources-with-title?{params}"
    extra = {"Cache-Control": "no-cache, no-store, must-revalidate",
             "Pragma": "no-cache", "Expires": "0"}
    try:
        resp = fetch(url, extra_headers=extra)
        if resp.status_code != 200:
            return None
        text = resp.text.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except Exception:
                pass
        try:
            decrypted = decrypt_vidking_payload(text, seed, int(meta["tmdbId"]))
            return json.loads(decrypted)
        except Exception:
            return None
    except Exception:
        return None

def fetch_lizer_getm3u8(stream_id):
    url = f"{LIZER}/getm3u8/{stream_id}"
    try:
        resp = fetch(url)
        ct   = resp.headers.get("Content-Type", "")
        text = resp.text
        if "mpegURL" in ct or "m3u8" in ct or text.strip().startswith("#EXTM3U"):
            return text
        return None
    except Exception:
        return None

def fetch_lizer_stream(b64_path):
    url = f"{LIZER}/stream/{b64_path}"
    try:
        resp = fetch(url)
        text = resp.text
        return text if "#EXTM3U" in text else None
    except Exception:
        return None

def build_lizer_stream_urls(stream_hash):
    shard, qualities, urls = "41", ["360p", "480p", "720p", "1080p"], []
    for q in qualities:
        path = f"m3u8/{shard}/{stream_hash}/{q}/{q}.m3u8?id="
        b64  = base64.b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")
        urls.append(f"{LIZER}/stream/{b64}")
    return urls

def cdn_quality_from_url(url):
    m = re.search(r"index-s(\d+p)-v\d+-a\d+\.m3u8", url)
    if m:
        return m.group(1)
    return "master" if "master.m3u8" in url else "unknown"

# ── Main extraction pipeline ──────────────────────────────────────────────────

def extract(media_type, tmdb_id, season="1", episode="1"):
    found_urls = []
    seen       = set()

    try:
        meta = fetch_metadata(media_type, tmdb_id)
        print(f"  [OK] Metadata: {meta['title']} ({meta['year']})")
    except Exception as exc:
        print(f"  [ERR] Metadata failed: {exc}")
        meta = {"tmdbId": tmdb_id, "imdbId": "", "title": "",
                "year": "", "mediaType": media_type}

    try:
        seed = fetch_seed(tmdb_id)
        print(f"  [OK] Seed: {seed[:30]}...")
    except Exception as exc:
        print(f"  [ERR] Seed failed: {exc}")
        return found_urls

    for provider in PROVIDERS:
        print(f"  [>>] Provider: {provider}")
        sources_data = fetch_sources(meta, seed, provider, season, episode)
        if not sources_data:
            print(f"  [--] No data from {provider}")
            continue

        raw_json_str = json.dumps(sources_data)

        # ── 1. Structured sources array ───────────────────────────────────────
        source_items = sources_data.get("sources", [])
        if isinstance(source_items, list):
            for item in source_items:
                if not item or not item.get("url"):
                    continue
                u = item["url"]
                q = item.get("quality", "Unknown")

                if provider == "cdn":
                    qt = cdn_quality_from_url(u)
                    pu = cdn_proxy(u)
                    if qt == "1080p":
                        if u not in seen:
                            seen.add(u)
                            found_urls.append({"label": f"cdn | {qt} | direct", "url": u})
                        pk = "PROXY:" + u
                        if pk not in seen:
                            seen.add(pk)
                            found_urls.append({"label": f"cdn | {qt} | proxy", "url": pu})
                    else:
                        pk = "PROXY:" + u
                        if pk not in seen:
                            seen.add(pk)
                            found_urls.append({"label": f"cdn | {qt} | proxy", "url": pu})

                elif provider == "lamovie":
                    pu = lamovie_proxy(u)
                    pk = "PROXY:" + u
                    if pk not in seen:
                        seen.add(pk)
                        found_urls.append({"label": f"lamovie | {q} | proxy", "url": pu})

                else:
                    if u not in seen:
                        seen.add(u)
                        found_urls.append({"label": f"{provider} | {q}", "url": u})
                        if "lizer123.site/getm3u8/" in u:
                            sid = u.split("/getm3u8/")[-1].split("?")[0]
                            fetch_lizer_getm3u8(sid)

        # ── 2. Direct m3u8 URLs in JSON text ─────────────────────────────────
        for u in re.findall(r"https?://[^\s'\"\\]+\.m3u8[^\s'\"\\]*", raw_json_str):
            if provider == "cdn":
                qt = cdn_quality_from_url(u)
                pu = cdn_proxy(u)
                if qt == "1080p":
                    if u not in seen:
                        seen.add(u)
                        found_urls.append({"label": f"cdn | {qt} | direct", "url": u})
                    pk = "PROXY:" + u
                    if pk not in seen:
                        seen.add(pk)
                        found_urls.append({"label": f"cdn | {qt} | proxy", "url": pu})
                else:
                    pk = "PROXY:" + u
                    if pk not in seen:
                        seen.add(pk)
                        found_urls.append({"label": f"cdn | {qt} | proxy", "url": pu})
            elif provider == "lamovie":
                pu = lamovie_proxy(u)
                pk = "PROXY:" + u
                if pk not in seen:
                    seen.add(pk)
                    found_urls.append({"label": "lamovie | proxy", "url": pu})
            else:
                if u not in seen:
                    seen.add(u)
                    found_urls.append({"label": f"{provider} | hls", "url": u})

        # ── 3. Lizer stream IDs ───────────────────────────────────────────────
        if provider not in ("cdn", "lamovie"):
            pat = r'"(?:id|hash|streamId|videoId|stream_id)"\s*:\s*"([A-Za-z0-9_-]{6,32})"'
            for m in re.finditer(pat, raw_json_str):
                sid     = m.group(1)
                get_url = f"{LIZER}/getm3u8/{sid}"
                if get_url not in seen:
                    playlist = fetch_lizer_getm3u8(sid)
                    if playlist and "#EXTM3U" in playlist:
                        seen.add(get_url)
                        found_urls.append({"label": f"{provider} | lizer", "url": get_url})
                    else:
                        for su in build_lizer_stream_urls(sid):
                            if su not in seen:
                                m3u8_text = fetch_lizer_stream(su.split("/stream/")[-1])
                                if m3u8_text and "#EXTM3U" in m3u8_text:
                                    seen.add(su)
                                    found_urls.append({"label": f"{provider} | lizer", "url": su})
                                    break

        cnt = sum(1 for f in found_urls if f["label"].startswith(provider))
        print(f"  [OK] {provider}: {cnt} stream(s) collected")

    return found_urls

# ── Bangladesh Standard Time helper ──────────────────────────────────────────

def bst_now():
    """Return current time in Bangladesh Standard Time (UTC+6)."""
    bst = timezone(timedelta(hours=6))
    now = datetime.now(bst)
    # e.g. "4:25 PM 18 August 2026, Bangladesh Standard"
    return now.strftime("%-I:%M %p %d %B %Y, Bangladesh Standard")

# ── Save results ──────────────────────────────────────────────────────────────

def save_results(results, media_type, tmdb_id, season, episode):
    os.makedirs("results", exist_ok=True)

    if media_type == "tv":
        fname = f"results/{media_type}_{tmdb_id}_s{season.zfill(2)}e{episode.zfill(2)}.json"
        embed_url = f"https://www.vidking.net/embed/tv/{tmdb_id}/{season}/{episode}"
    else:
        fname = f"results/{media_type}_{tmdb_id}.json"
        embed_url = f"https://www.vidking.net/embed/movie/{tmdb_id}"

    serial = f"{season}" if media_type == "tv" else "1"

    payload = {
        "success":   bool(results),
        "status":    "extract from live",
        "embed_url": embed_url,
        "serial":    serial,
        "time":      bst_now(),
        "mediaType": media_type,
        "tmdbId":    tmdb_id,
        "season":    season,
        "episode":   episode,
        "results":   results,
    }

    with open(fname, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"  [SAVED] {fname}")
    return fname

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Priority: CLI args > environment variables > defaults
    # Usage: python vidking_extractor.py <media_type> <tmdb_id> [season] [episode]
    # Or set: INPUT_MEDIA_TYPE, INPUT_TMDB_ID, INPUT_SEASON, INPUT_EPISODE

    if len(sys.argv) >= 3:
        media_type = sys.argv[1].lower()
        tmdb_id    = sys.argv[2]
        season     = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("INPUT_SEASON", "1")
        episode    = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("INPUT_EPISODE", "1")
    else:
        media_type = os.environ.get("INPUT_MEDIA_TYPE", "movie").lower()
        tmdb_id    = os.environ.get("INPUT_TMDB_ID", "")
        season     = os.environ.get("INPUT_SEASON", "1")
        episode    = os.environ.get("INPUT_EPISODE", "1")

    if not tmdb_id:
        print("[ERROR] TMDB ID is required. Pass as CLI arg or set INPUT_TMDB_ID env var.")
        sys.exit(1)

    if media_type not in ("movie", "tv"):
        print(f"[ERROR] Invalid media type '{media_type}'. Use 'movie' or 'tv'.")
        sys.exit(1)

    print(f"[START] VidKing Extractor — {media_type.upper()} | TMDB: {tmdb_id}" +
          (f" | S{season.zfill(2)}E{episode.zfill(2)}" if media_type == "tv" else ""))
    print(f"[PROXY] Pool loaded: {len(PROXY_POOL)} proxies")

    start = time.time()
    try:
        results = extract(media_type, tmdb_id, season, episode)
    except Exception as exc:
        print(f"[ERROR] Extraction failed: {exc}")
        # Still save a failure JSON
        save_results([], media_type, tmdb_id, season, episode)
        sys.exit(1)

    fname = save_results(results, media_type, tmdb_id, season, episode)
    elapsed = time.time() - start
    print(f"[DONE] {len(results)} stream(s) found in {elapsed:.2f}s")
    print(f"[FILE] {fname}")

    # Print to stdout for GitHub Actions step summary
    print("\n--- OUTPUT JSON ---")
    with open(fname) as f:
        print(f.read())

if __name__ == "__main__":
    try:
        import requests
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
        import requests
    main()
