# Design: Cross-Device Review Data Sync via Vercel

## Problem

The quiz app stores spaced repetition review data in `localStorage` per browser. When using the app on GitHub Pages from a phone, ratings are lost — they never sync back to the shared `review_data.json` in the GitHub repo. Only the PC (via `serve.py` + git push) can persist review data.

## Solution

Deploy a Vercel serverless API that reads/writes `review_data.json` in the GitHub repo via the GitHub API. Both phone (GitHub Pages) and PC point to this API for sync.

## Architecture

```
Phone (GitHub Pages)  ──→  Vercel API  ──→  GitHub API (commit review_data.json)
                       ←──            ←──
PC (serve.py)         ──→  Vercel API  ──→  GitHub API
                       ←──            ←──
```

Both devices use Vercel as the sync layer. The GitHub repo remains the single source of truth.

## Vercel Serverless Function

**File:** `api/review.js`

### GET /api/review

1. Fetch `review_data.json` from GitHub API (`GET /repos/:owner/:repo/contents/review_data.json`)
2. Decode base64 content
3. Return JSON + the file's SHA (needed for updates)

### POST /api/review

1. Receive review data from client (parse body as JSON regardless of Content-Type — needed for `sendBeacon` which sends as `text/plain`)
2. Fetch current `review_data.json` from GitHub (with SHA)
3. Merge using server-side merge strategy (see below)
4. Commit merged result back to GitHub (`PUT /repos/:owner/:repo/contents/review_data.json` with SHA)
5. On **409 Conflict** (SHA changed between read and write): re-fetch, re-merge, retry (max 3 attempts)
6. Return merged data to client

## Merge Strategy

Server-side merge (canonical implementation — replaces the client-side `mergeReviewData`):

```
Collect all keys from BOTH client and server data, then:
  if key only in client data → keep client version
  if key only in server data → keep server version
  if key in both → keep the one with longer history array
                   if equal length → keep the one with more recent nextReview
```

**Known limitation:** When the same question is rated on both devices between syncs, the shorter history is discarded entirely. This is acceptable for a personal project where concurrent use is rare. The merge is "last-longest-wins", not a true history union.

## Frontend Changes (quiz.html)

### API URL Configuration

```javascript
const API_URL = window.location.hostname === 'localhost'
  ? ''  // use serve.py locally
  : 'https://<vercel-project>.vercel.app';
```

When on localhost, saves still go to `serve.py` at `/api/review`. `serve.py` should be updated to POST to Vercel instead of doing its own git push, so that all sync goes through the same path. Alternatively, `serve.py` can be simplified to just serve static files, and the PC also syncs via Vercel.

### loadReviewData()

1. Load from `localStorage` (instant, offline-first)
2. Fetch from `${API_URL}/api/review`
3. Merge server data into local data (keep longer history)
4. Save merged result to `localStorage`

### saveReviewData()

1. Save to `localStorage` immediately
2. Debounce POST to `${API_URL}/api/review` — wait 5 seconds after last rating before syncing. This avoids hammering the GitHub API when rapidly reviewing questions.

### Page close (beforeunload)

- `sendBeacon` with `new Blob([JSON.stringify(data)], {type: 'application/json'})` to `${API_URL}/api/review`
- Replaces current `sendBeacon` to `/api/sync`
- This is the safety net: if debounced save hasn't fired yet, the beacon catches it

### Offline resilience

- On `visibilitychange` (tab becomes visible again), retry sync if the last save failed
- This handles the case where the phone was offline during a review session

## Vercel Configuration

**File:** `vercel.json`

```json
{
  "version": 2,
  "functions": {
    "api/review.js": {
      "memory": 128,
      "maxDuration": 10
    }
  },
  "headers": [
    {
      "source": "/api/(.*)",
      "headers": [
        { "key": "Access-Control-Allow-Origin", "value": "*" },
        { "key": "Access-Control-Allow-Methods", "value": "GET, POST, OPTIONS" },
        { "key": "Access-Control-Allow-Headers", "value": "Content-Type, Authorization" }
      ]
    }
  ]
}
```

Note: CORS is `*` because the app is accessed from GitHub Pages (varying subdomains) and localhost. This is acceptable given the shared secret authentication below.

## Environment Variables (Vercel Dashboard)

| Variable | Value |
|----------|-------|
| `GITHUB_TOKEN` | Personal access token with `repo` scope |
| `GITHUB_REPO` | `JohannCFi/QuizHullReminder` |
| `SYNC_SECRET` | Shared secret for API authentication |

## Security

- GitHub token stored as Vercel env var (never exposed to client)
- API protected by a shared secret sent as `Authorization: Bearer <SYNC_SECRET>` header. The secret is hardcoded in `quiz.html` — acceptable since it only grants access to review data, not the GitHub PAT
- CORS is `*` (see note above)

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `api/review.js` | Create | Vercel serverless function |
| `vercel.json` | Create | Vercel project configuration |
| `quiz.html` | Modify | Update sync functions to point to Vercel API, add debounce, fix sendBeacon |
| `serve.py` | Modify | Remove git push logic, let Vercel handle sync (or route through Vercel) |

## What Stays the Same

- `review_data.json` format unchanged
- `localStorage` remains the primary client-side cache (offline-first)
- Question tags remain `localStorage`-only
- `notify.py` reads `review_data.json` from the repo — works as before since Vercel commits to GitHub

## Rate Limits

- GitHub API: 5,000 requests/hour (authenticated). Each POST = 2 calls (GET + PUT). With debouncing (one sync per 5s max), a 50-question session = ~10-20 API calls. Well within limits.
- Vercel free tier: 100K function invocations/month. More than sufficient.

## Deployment Steps

1. Create Vercel project linked to the GitHub repo
2. Set environment variables (`GITHUB_TOKEN`, `GITHUB_REPO`, `SYNC_SECRET`)
3. Deploy (auto-deploy on push)
4. Update `API_URL` in `quiz.html` with the Vercel URL
5. Test: rate a question on phone → verify `review_data.json` updated on GitHub
