# VidKing Stream Extractor

Extracts HLS stream URLs from VidKing via residential proxy rotation.  
Results are saved as JSON to the `results/` folder and committed back to this repo automatically.

---

## Usage

### Manual run (GitHub Actions UI)

1. Go to **Actions → VidKing Stream Extractor → Run workflow**
2. Fill in:
   - `media_type` — `movie` or `tv`
   - `tmdb_id` — TMDB numeric ID (e.g. `218`)
   - `season` / `episode` — TV only
3. Click **Run workflow**

The result JSON will be committed to `results/` within seconds.

### Scheduled run

By default the workflow runs **every 6 hours** and extracts the TMDB ID set in the `env:` block at the top of `.github/workflows/extract.yml`.  
Edit `DEFAULT_TMDB_ID` and `DEFAULT_MEDIA_TYPE` there to change the target.

### Local run

```bash
pip install -r requirements.txt

# Movie
python vidking_extractor.py movie 218

# TV show  S02E05
python vidking_extractor.py tv 1396 2 5
```

Or via environment variables:

```bash
INPUT_MEDIA_TYPE=movie INPUT_TMDB_ID=218 python vidking_extractor.py
```

---

## Output format

Results are written to `results/<mediaType>_<tmdbId>[_sXXeYY].json`:

```json
{
  "success": true,
  "status": "extract from live",
  "embed_url": "https://www.vidking.net/embed/movie/218",
  "serial": "1",
  "time": "4:25 PM 18 August 2026, Bangladesh Standard",
  "mediaType": "movie",
  "tmdbId": "218",
  "season": "1",
  "episode": "1",
  "results": [
    {
      "label": "cdn | 1080p | direct",
      "url": "https://..."
    },
    {
      "label": "cdn | 1080p | proxy",
      "url": "https://..."
    }
  ]
}
```

---

## Proxy pool

20 residential proxies across 2 credential batches (UK, US, ES, PL, JP).  
Rotation is round-robin with automatic failover on 429 / connection errors.

---

## Repo permissions required

The workflow needs **write** access to commit results back.  
Go to **Settings → Actions → General → Workflow permissions** and set it to **Read and write permissions**.
