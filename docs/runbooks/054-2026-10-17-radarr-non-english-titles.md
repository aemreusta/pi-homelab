# Runbook 054 — Radarr and non-English film titles (2026-10-17)

Radarr's automatic search can miss films whose releases use a non-English title,
even when an indexer has them. This runbook records what Radarr actually does,
why Turkish releases fail to match, the state of movie 27 ("Alone" /
"Issız Adam", 2008), and the manual workaround.

## What Radarr searches

Radarr 6.4 searches each indexer with the English title and the original title,
each followed by the year. For movie 27 the Prowlarr log shows both terms, sent
to TPB as `Alone 2008` and `Issız Adam 2008`. Alternative titles come from TMDb.
Movie 27 has only a Chinese one, so it adds nothing.

## Why Turkish releases still fail

Two separate effects stop a match:

1. **Indexer text search.** TPB does not fold the dotless `ı`. It returned
   nothing for `Issız Adam` but five rows for `Issiz Adam`. LimeTorrents and
   TorrentProject2 return rows for both spellings.
2. **Radarr title matching.** Radarr cleans titles with a German umlaut rule
   (`ü`→`ue`) and keeps `ı`. Turkish releases are usually spelled in ASCII
   (`Issiz Adam`, `Kaybedenler Kulubu`), so they map to no movie and Radarr
   rejects them as "Unknown Movie". Releases that keep the original letters
   do map. Checked with `/api/v3/parse` on 17 October:

| Release title | Maps to |
|---|---|
| `Issiz.Adam.2008.DVDRip.XviD-LTRG` | nothing |
| `Issız Adam 2008 DVDRip` | movie 27 |
| `Kaybedenler.Kulubu.2011.1080p.WEB-DL` | nothing |
| `Kaybedenler Kulübü (2011) [WEB DL] [1080p]` | movie 26 |

Adding the ASCII spelling as an alternative title on TMDb would fix the match
for future searches; Radarr refreshes alternative titles from TMDb. That is an
external edit, so it is the operator's call.

## Movie 27 (Alone / Issız Adam, 2008)

Searches by the Turkish title find only DVD-era releases: XviD DVDRips of about
0.6–0.8 GB, one DVDSCR and two 2.3 GB rips, with 0–2 seeders. An earlier draft of
this runbook concluded from them that no HD release existed. That was wrong,
because those searches never tried the English title on LimeTorrents.

After `deploy-v2026.10.20.2`, Radarr's own search for movie 27 (20 October, 123
results) sends `Alone 2008` and `Issız Adam 2008` to all three indexers.
LimeTorrents returns YTS's `Alone (2008) (1080p) [WEBRip] [YTS MX]` (1.93 GB,
18 seeders) and its 720p copy. Both map to movie 27 by English title and year,
and the HD-1080p profile approves the 1080p release. YTS's catalogue lists
exactly these two web releases (720p 1.04 GB, 1080p 1.93 GB) under IMDb
`tt1322930`, *Issiz Adam*, Turkish, 113 min, so the match is the right film. The
torrent also carries YTS's English `.srt`.

The ASCII `Issiz Adam …` rows still map to nothing. TPB and TorrentProject2 also
map `Alone.2008.DVDRip.XviD-Ltu` and `Alone 2008 DVDRip XviD-BiFOS` by English
title and year. Their identity is unverified, and DVD quality is rejected anyway.

When a non-English film looks missing, search Prowlarr by its English title and
year as well; YTS-style releases often use only that.

## Workaround: push one Prowlarr result into Radarr

Use this when Prowlarr has a correct release that Radarr cannot match. The push
goes through Radarr's normal decision engine (quality profile, size ceilings,
seeders), so keep the release's real quality tokens. Only the title wording
changes, never the quality.

Run on the Pi. The keys stay in shell variables and are never printed:

```bash
ssh pi@raspberrypi.local
PK=$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' ~/docker/media/config/prowlarr/config.xml)
RK=$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' ~/docker/media/config/radarr/config.xml)
```

1. Search every indexer, without a category filter, in both spellings. Indexers
   label some films Other (8000). Keep the result file for step 3; it holds the
   Prowlarr key inside each download URL, so it is private and removed at the end:

   ```bash
   umask 077
   curl -s -H "X-Api-Key: $PK" -o /tmp/prowlarr-search.json \
     'http://127.0.0.1:9696/api/v1/search?type=search&limit=100&query=Issiz%20Adam'
   python3 -c 'import json
   for i,r in enumerate(json.load(open("/tmp/prowlarr-search.json"))):
       print(i, r["indexer"], r["size"]//2**20, "MiB", r.get("seeders"), r["title"])'
   ```

2. Pick a title Radarr maps to the right movie. Use the original spelling or the
   English title, plus the year and the release's real quality tokens. Check it:

   ```bash
   curl -s -G -H "X-Api-Key: $RK" http://127.0.0.1:7878/api/v3/parse \
     --data-urlencode 'title=Alone 2008 1080p WEB-DL' \
     | python3 -c 'import json,sys; m=json.load(sys.stdin).get("movie"); print(m and (m["id"], m["title"]))'
   ```

3. Push the chosen row (`ROW` is its number from step 1). Prowlarr builds each
   `downloadUrl` with the host the search came from (`127.0.0.1:9696`). Radarr
   runs in a container, so the host must become `prowlarr:9696`. TPB rows carry
   the same proxied link as `magnetUrl` instead; a real `magnet:` link is pushed
   as is:

   ```bash
   python3 -c 'import json,sys
   r=json.load(open("/tmp/prowlarr-search.json"))[int(sys.argv[1])]
   push={"title": sys.argv[2], "protocol": "torrent", "publishDate": r["publishDate"],
       "size": r["size"], "seeders": r.get("seeders"), "leechers": r.get("leechers")}
   url=(r.get("downloadUrl") or r["magnetUrl"]).replace("http://127.0.0.1:9696", "http://prowlarr:9696")
   push["magnetUrl" if url.startswith("magnet:") else "downloadUrl"]=url
   print(json.dumps(push))' \
     ROW 'Alone 2008 1080p WEB-DL' > /tmp/push.json
   curl -s -H "X-Api-Key: $RK" -H 'Content-Type: application/json' \
     -X POST http://127.0.0.1:7878/api/v3/release/push --data @/tmp/push.json \
     | python3 -c 'import json,sys; [print(d.get("approved"), d.get("rejections")) for d in json.load(sys.stdin)]'
   rm -f /tmp/push.json /tmp/prowlarr-search.json
   ```

   The output prints `True []` when Radarr sent the release to qBittorrent.
   Otherwise it lists the rejections. The movie's history then shows
   `releaseSource: ReleasePush`.

Movie 26 was pushed this way on 16 October as `Losers Club 2011 1080p WEB-DL H264`.
That was the LimeTorrents 1080p release labelled Other (8000).
