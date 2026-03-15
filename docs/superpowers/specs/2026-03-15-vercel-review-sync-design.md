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

1. Receive review data from client
2. Fetch current `review_data.json` from GitHub (with SHA)
3. Merge: for each question key, keep the version with the longer history array (or more recent `nextReview` if equal length)
4. Commit merged result back to GitHub (`PUT /repos/:owner/:repo/contents/review_data.json` with SHA)
5. Return merged data to client

## Merge Strategy

Server-side merge (same logic as existing `mergeReviewData` in quiz.html):

```
For each question key:
  if only in client data → keep client version
  if only in server data → keep server version
  if in both → keep the one with longer history array
               if equal length → keep the one with more recent nextReview
```

This handles the common case: one device rates questions the other hasn't seen yet. True conflicts (same question rated on both devices between syncs) are resolved by keeping the longer history.

## Frontend Changes (quiz.html)

### API URL Configuration

```javascript
const API_URL = window.location.hostname === 'localhost'
  ? ''  // use serve.py locally
  : 'https://<vercel-project>.vercel.app';
```

### loadReviewData()

1. Load from `localStorage` (instant, offline-first)
2. Fetch from `${API_URL}/api/review`
3. Merge server data into local data (keep longer history)
4. Save merged result to `localStorage`

### saveReviewData()

1. Save to `localStorage` immediately
2. POST to `${API_URL}/api/review` (fire-and-forget, silent fail)

### Page close (beforeunload)

- `sendBeacon` to `${API_URL}/api/review` with current `localStorage` review data
- Replaces current `sendBeacon` to `/api/sync`

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
        { "key": "Access-Control-Allow-Headers", "value": "Content-Type" }
      ]
    }
  ]
}
```

## Environment Variables (Vercel Dashboard)

| Variable | Value |
|----------|-------|
| `GITHUB_TOKEN` | Personal access token with `repo` scope |
| `GITHUB_REPO` | `JohannCFi/QuizHullReminder` |

## Security

- GitHub token stored as Vercel env var (never exposed to client)
- API is open but can only read/write `review_data.json` — acceptable for a personal project
- CORS configured to allow requests from GitHub Pages domain

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `api/review.js` | Create | Vercel serverless function |
| `vercel.json` | Create | Vercel project configuration |
| `quiz.html` | Modify | Update sync functions to point to Vercel API |

## What Stays the Same

- `review_data.json` format unchanged
- `localStorage` remains the primary client-side cache (offline-first)
- Question tags remain `localStorage`-only
- `serve.py` continues working for local dev (can optionally also route through Vercel)
- `notify.py` reads `review_data.json` from the repo — works as before since Vercel commits to GitHub

## Deployment Steps

1. Create Vercel project linked to the GitHub repo
2. Set environment variables (`GITHUB_TOKEN`, `GITHUB_REPO`)
3. Deploy (auto-deploy on push)
4. Update `API_URL` in `quiz.html` with the Vercel URL
5. Test: rate a question on phone → verify `review_data.json` updated on GitHub
