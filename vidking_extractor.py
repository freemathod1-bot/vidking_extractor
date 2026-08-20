#!/usr/bin/env python3
"""
VidKing Stream Extractor
GitHub Actions compatible — reads inputs from env vars or CLI args,
writes output to results/<mediaType>_<tmdbId>[_sXeY].json

FIXES applied (v4):
  #1  Chrome UA updated to a real version (136) — fake v151 caused bot-detection 403s
  #2  Added sec-fetch-site / sec-fetch-mode / sec-fetch-dest to all requests
  #3  Added sec-ch-ua client-hint headers (Chrome always sends these)
  #4  Seed regex broadened — now catches seeds without a leading digit or dot
  #5  Removed vsrc / superflix from PROVIDERS (they return iframes, not m3u8 JSON)
  #6  tmdb_id coerced to int before crypto operations everywhere it matters
  #7  fetch_seed() retries with exponential back-off and falls back gracefully
  #8  lizer123.site requests now include full browser header set
  #9  Added Accept-Encoding + Connection headers to match real Chrome
  #10 Proxies removed — direct requests only
"""

import sys
import os
import re
import json
import time
import base64
import ctypes
import requests
import urllib3
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, quote

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── Constants ─────────────────────────────────────────────────────────────────

DB_BASE  = "https://db.speedracelight.com/3"
API_BASE = "https://api.speedracelight.com"
LIZER    = "https://lizer123.site"

# FIX #5: Removed "vsrc" and "superflix" — they serve iframes, not m3u8 JSON.
# Keeping only providers that return structured source arrays with direct URLs.
PROVIDERS = [
    "cdn",
    "hdmovie",
    "lamovie",
    "m4uhd",
]

CDN_PROXY_BASE     = "https://megaplalyermoy-soyy.onrender.com/proxy?url={encoded_url}&ref=https%3A%2F%2Fwww.vidking.net%2F&origin="
LAMOVIE_PROXY_BASE = "https://foxy-doxy.andruilsyestems.workers.dev/proxy?url={b64_url}&headers=eyJSZWZlcmVyIjoiaHR0cHM6Ly93d3cudmlka2luZy5uZXQvIn0%3D"

# FIX #1: Chrome 151 doesn't exist — updated to real Chrome 136 UA.
# FIX #2 / #3 / #9: Added sec-fetch-*, sec-ch-ua client hints, Accept-Encoding,
#                   and Connection headers to match what a real Chrome browser sends.
HEADERS = {
    "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Accept":                    "*/*",
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate, br",          # FIX #9
    "Connection":                "keep-alive",                  # FIX #9
    "Origin":                    "https://www.vidking.net",
    "Referer":                   "https://www.vidking.net/",
    # FIX #2: sec-fetch headers — without these the server sees a non-browser script
    "Sec-Fetch-Site":            "cross-site",
    "Sec-Fetch-Mode":            "cors",
    "Sec-Fetch-Dest":            "empty",
    # FIX #3: Chrome client-hint headers
    "sec-ch-ua":                 '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    "sec-ch-ua-mobile":          "?0",
    "sec-ch-ua-platform":        '"Windows"',
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
    # FIX #6: ensure tmdb_id is always int for XOR/arithmetic operations
    tmdb_id = int(tmdb_id)
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
    keystream = _generate_keystream(seed_str, int(tmdb_id), len(raw_bytes))  # FIX #6
    for idx in range(len(raw_bytes)):
        raw_bytes[idx] ^= keystream[idx]
    if raw_bytes[:4] != MAGIC_HEADER:
        raise ValueError("Decryption failed: bad seed or corrupted payload")
    return raw_bytes[4:].decode("utf-8")

# ── HTTP fetch (direct, no proxy) ─────────────────────────────────────────────

def fetch(url, extra_headers=None, retries=4, delay=1.0):
    headers = {**HEADERS, **(extra_headers or {})}
    last_error = None
    for i in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=20, verify=False)
            if resp.status_code == 429:
                wait = delay * (2 ** i)
                print(f"  [WARN] 429 rate-limited — retrying in {wait:.1f}s...")
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.ConnectionError as exc:
            last_error = exc
            print(f"  [WARN] Connection error — retrying... ({i+1}/{retries})")
            time.sleep(delay * 0.5)
        except requests.exceptions.Timeout as exc:
            last_error = exc
            print(f"  [WARN] Timeout — retrying... ({i+1}/{retries})")
            time.sleep(delay * 0.5)
        except Exception as exc:
            last_error = exc
            if i == retries - 1:
                raise
            time.sleep(delay)
    raise RuntimeError(f"All {retries} attempts failed. Last error: {last_error}")

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


# FIX #7: fetch_seed now retries across multiple proxies and has a broadened
#         seed regex (FIX #4) that catches seeds with no leading digit or dot.
def _extract_seed_from_text(text):
    """Try to extract a seed string from raw response text.

    The seed can be:
      - JSON: {"seed": "2.aBcDeFgHiJ..."}
      - Plain string like "2.aBcDeFgHiJ..."  (digit-dot-alphanumeric)
      - Pure alphanumeric token ≥ 12 chars with no dot  (FIX #4: broadened pattern)
    """
    # 1. Try JSON parse first
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("seed"):
            return str(data["seed"])
    except Exception:
        pass

    # 2. Original pattern: digit dot alphanumeric
    m = re.search(r"(\d+\.[A-Za-z0-9_-]{10,})", text)
    if m:
        return m.group(1)

    # 3. FIX #4: Broadened — pure alphanumeric token ≥ 12 chars (no dot required)
    m = re.search(r"['\"]([A-Za-z0-9_-]{12,})['\"]", text)
    if m:
        return m.group(1)

    # 4. Last resort: any word-like token ≥ 16 chars
    m = re.search(r"\b([A-Za-z0-9_-]{16,})\b", text)
    if m:
        return m.group(1)

    return None


def fetch_seed(tmdb_id, max_attempts=8):
    """Fetch seed with retry/back-off and fallback parsing. (FIX #7)"""
    url = f"{API_BASE}/seed?mediaId={tmdb_id}"
    last_text = ""
    for attempt in range(max_attempts):
        try:
            resp = fetch(url, retries=2, delay=0.5)
            text = resp.text.strip()
            seed = _extract_seed_from_text(text)
            if seed:
                return seed
            last_text = text
            print(f"  [WARN] Seed attempt {attempt+1}: could not parse seed from: {text[:100]!r}")
        except Exception as exc:
            print(f"  [WARN] Seed attempt {attempt+1} failed: {exc}")
        time.sleep(0.5)
    raise RuntimeError(f"Could not extract seed after {max_attempts} attempts. Last response: {last_text[:200]}")


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
            decrypted = decrypt_vidking_payload(text, seed, int(meta["tmdbId"]))  # FIX #6
            return json.loads(decrypted)
        except Exception:
            return None
    except Exception:
        return None


# FIX #8: lizer fetches now use the full HEADERS dict (including sec-fetch-* etc.)
def fetch_lizer_getm3u8(stream_id):
    url = f"{LIZER}/getm3u8/{stream_id}"
    try:
        resp = fetch(url)   # uses full HEADERS — FIX #8
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
        resp = fetch(url)   # uses full HEADERS — FIX #8
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
        seed = fetch_seed(tmdb_id)   # FIX #7: retries with proxy rotation
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

# ── Parse a single VidKing embed URL ─────────────────────────────────────────

def parse_embed_url(line):
    """
    Parse a VidKing embed URL into (media_type, tmdb_id, season, episode).

    Supported formats in tmdbids.txt:
      https://www.vidking.net/embed/movie/218
      https://www.vidking.net/embed/tv/1396/2/5
      218                          (bare ID → treated as movie)
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # TV:  /embed/tv/<id>/<season>/<episode>
    m = re.search(r"/embed/tv/(\d+)/(\d+)/(\d+)", line)
    if m:
        return "tv", m.group(1), m.group(2), m.group(3)

    # Movie: /embed/movie/<id>
    m = re.search(r"/embed/movie/(\d+)", line)
    if m:
        return "movie", m.group(1), "1", "1"

    # Bare TMDB ID
    if re.fullmatch(r"\d+", line):
        return "movie", line, "1", "1"

    print(f"[SKIP] Cannot parse line: {line}")
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ids_file = os.environ.get("TMDB_IDS_FILE", "tmdbids.txt")

    if not os.path.exists(ids_file):
        print(f"[ERROR] {ids_file} not found. Create it with one VidKing embed URL per line.")
        sys.exit(1)

    with open(ids_file, encoding="utf-8") as f:
        lines = f.readlines()

    entries = [parse_embed_url(l) for l in lines]
    entries = [e for e in entries if e]

    if not entries:
        print(f"[ERROR] No valid entries found in {ids_file}.")
        sys.exit(1)

    print(f"[START] VidKing Extractor — {len(entries)} title(s) from {ids_file}")
    print()

    total_start  = time.time()
    saved_files  = []
    any_failed   = False

    for idx, (media_type, tmdb_id, season, episode) in enumerate(entries, 1):
        label = (f"{media_type.upper()} {tmdb_id} S{season.zfill(2)}E{episode.zfill(2)}"
                 if media_type == "tv" else f"{media_type.upper()} {tmdb_id}")
        print(f"[{idx}/{len(entries)}] {label}")

        start = time.time()
        try:
            results = extract(media_type, tmdb_id, season, episode)
        except Exception as exc:
            print(f"  [ERROR] Extraction failed: {exc}")
            save_results([], media_type, tmdb_id, season, episode)
            any_failed = True
            print()
            continue

        fname = save_results(results, media_type, tmdb_id, season, episode)
        saved_files.append(fname)
        elapsed = time.time() - start
        print(f"  [DONE] {len(results)} stream(s) in {elapsed:.2f}s → {fname}")
        print()

    print(f"[FINISHED] {len(saved_files)}/{len(entries)} succeeded in "
          f"{time.time() - total_start:.2f}s")

    # Print all saved JSON files to stdout (visible in Actions log)
    for fname in saved_files:
        print(f"\n{'='*60}")
        print(f"FILE: {fname}")
        print('='*60)
        with open(fname) as f:
            print(f.read())

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        import requests
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
        import requests
    main()
