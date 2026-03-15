# Cross-Device Review Sync Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sync spaced repetition review data between phone (GitHub Pages) and PC via a Vercel serverless API that commits to GitHub.

**Architecture:** A single Vercel serverless function (`api/review.js`) handles GET (read from GitHub) and POST (merge + commit to GitHub). The frontend (`quiz.html`) is updated to sync via this API with debouncing. `serve.py` is simplified to remove git push logic.

**Tech Stack:** Vercel Serverless Functions (Node.js), GitHub Contents API, vanilla JS frontend

**Spec:** `docs/superpowers/specs/2026-03-15-vercel-review-sync-design.md`

---

## Chunk 1: Vercel Serverless Function

### Task 1: Create vercel.json

**Files:**
- Create: `vercel.json`

- [ ] **Step 1: Create vercel.json**

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

- [ ] **Step 2: Commit**

```bash
git add vercel.json
git commit -m "Add Vercel configuration for review sync API"
```

---

### Task 2: Create api/review.js — GET handler

**Files:**
- Create: `api/review.js`

- [ ] **Step 1: Create the serverless function with GET handler**

```javascript
// api/review.js
const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const GITHUB_REPO = process.env.GITHUB_REPO; // "owner/repo"
const SYNC_SECRET = process.env.SYNC_SECRET;
const FILE_PATH = 'review_data.json';

async function fetchFromGitHub() {
  const res = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${FILE_PATH}`,
    { headers: { Authorization: `token ${GITHUB_TOKEN}`, Accept: 'application/vnd.github.v3+json' } }
  );
  if (!res.ok) throw new Error(`GitHub GET failed: ${res.status}`);
  const data = await res.json();
  const content = JSON.parse(Buffer.from(data.content, 'base64').toString('utf-8'));
  return { content, sha: data.sha };
}

function checkAuth(req) {
  const auth = req.headers['authorization'];
  if (!auth || auth !== `Bearer ${SYNC_SECRET}`) {
    return false;
  }
  return true;
}

export default async function handler(req, res) {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (!checkAuth(req)) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  if (req.method === 'GET') {
    try {
      const { content, sha } = await fetchFromGitHub();
      return res.status(200).json({ data: content, sha });
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  // POST handler will be added in next task
  return res.status(405).json({ error: 'Method not allowed' });
}
```

- [ ] **Step 2: Commit**

```bash
git add api/review.js
git commit -m "Add Vercel serverless function with GET handler for review data"
```

---

### Task 3: Add POST handler with merge and retry logic

**Files:**
- Modify: `api/review.js`

- [ ] **Step 1: Add merge function and POST handler**

Add the `mergeReviewData` function after `checkAuth`:

```javascript
function mergeReviewData(clientData, serverData) {
  const merged = {};
  const allKeys = new Set([...Object.keys(clientData), ...Object.keys(serverData)]);
  for (const key of allKeys) {
    const client = clientData[key];
    const server = serverData[key];
    if (!server) { merged[key] = client; continue; }
    if (!client) { merged[key] = server; continue; }
    const clientLen = (client.history || []).length;
    const serverLen = (server.history || []).length;
    if (clientLen > serverLen) {
      merged[key] = client;
    } else if (serverLen > clientLen) {
      merged[key] = server;
    } else {
      // Equal length — keep more recent nextReview
      merged[key] = (client.nextReview || '') >= (server.nextReview || '') ? client : server;
    }
  }
  return merged;
}

async function commitToGitHub(content, sha) {
  const body = JSON.stringify({
    message: 'Sync review data',
    content: Buffer.from(JSON.stringify(content, null, 2), 'utf-8').toString('base64'),
    sha
  });
  const res = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${FILE_PATH}`,
    {
      method: 'PUT',
      headers: { Authorization: `token ${GITHUB_TOKEN}`, Accept: 'application/vnd.github.v3+json', 'Content-Type': 'application/json' },
      body
    }
  );
  return res;
}
```

Replace the `// POST handler will be added in next task` block with:

```javascript
  if (req.method === 'POST') {
    try {
      // Parse body — handle both JSON content-type and text/plain (sendBeacon)
      let clientData;
      if (typeof req.body === 'string') {
        clientData = JSON.parse(req.body);
      } else {
        clientData = req.body;
      }

      // Retry loop for 409 conflicts
      const MAX_RETRIES = 3;
      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        const { content: serverData, sha } = await fetchFromGitHub();
        const merged = mergeReviewData(clientData, serverData);
        const commitRes = await commitToGitHub(merged, sha);

        if (commitRes.ok) {
          return res.status(200).json({ data: merged });
        }
        if (commitRes.status === 409 && attempt < MAX_RETRIES - 1) {
          continue; // Retry with fresh SHA
        }
        const errBody = await commitRes.text();
        return res.status(commitRes.status).json({ error: errBody });
      }
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }
```

- [ ] **Step 2: Commit**

```bash
git add api/review.js
git commit -m "Add POST handler with merge strategy and 409 retry logic"
```

---

## Chunk 2: Frontend Changes (quiz.html)

### Task 4: Add API_URL constant and SYNC_SECRET

**Files:**
- Modify: `quiz.html:772-781` (globals section)

- [ ] **Step 1: Add API_URL and SYNC_SECRET constants**

After `let reviewData = {};` (line 780), add:

```javascript
  let syncPending = false; // Track if a sync failed and needs retry
  let saveTimeout = null;  // Debounce timer for saveReviewData

  // Sync configuration
  const SYNC_SECRET = 'PLACEHOLDER_SECRET'; // Will be replaced with actual secret
  const API_URL = window.location.hostname === 'localhost'
    ? ''
    : 'https://PLACEHOLDER.vercel.app';
```

Note: `PLACEHOLDER_SECRET` and `PLACEHOLDER.vercel.app` will be replaced with actual values after Vercel deployment.

- [ ] **Step 2: Commit**

```bash
git add quiz.html
git commit -m "Add sync configuration constants to quiz.html"
```

---

### Task 5: Update loadReviewData to use Vercel API

**Files:**
- Modify: `quiz.html:865-883` (loadReviewData function)

- [ ] **Step 1: Update mergeReviewData to iterate all keys**

Replace the existing `mergeReviewData` function (lines 850-863) with:

```javascript
  function mergeReviewData(serverData) {
    // Merge server data into local reviewData — keep longer history
    for (const key in serverData) {
      if (!reviewData[key]) {
        reviewData[key] = serverData[key];
      } else {
        const localLen = (reviewData[key].history || []).length;
        const serverLen = (serverData[key].history || []).length;
        if (serverLen > localLen) {
          reviewData[key] = serverData[key];
        } else if (serverLen === localLen && (serverData[key].nextReview || '') > (reviewData[key].nextReview || '')) {
          reviewData[key] = serverData[key];
        }
      }
    }
    saveReviewDataLocal();
  }
```

- [ ] **Step 2: Update loadReviewData to fetch from Vercel API**

Replace the existing `loadReviewData` function (lines 865-883) with:

```javascript
  async function loadReviewData() {
    reviewData = loadReviewDataLocal();
    try {
      const res = await fetch(`${API_URL}/api/review`, {
        headers: { 'Authorization': `Bearer ${SYNC_SECRET}` }
      });
      if (res.ok) {
        const result = await res.json();
        mergeReviewData(result.data);
        syncPending = false;
        return;
      }
    } catch (e) {}
    // Fallback: load review_data.json as static file
    try {
      const res = await fetch('review_data.json');
      if (res.ok) {
        mergeReviewData(await res.json());
      }
    } catch (e) {}
  }
```

- [ ] **Step 3: Commit**

```bash
git add quiz.html
git commit -m "Update loadReviewData to fetch from Vercel API with auth"
```

---

### Task 6: Update saveReviewData with debounce

**Files:**
- Modify: `quiz.html:885-897` (saveReviewData function)

- [ ] **Step 1: Replace saveReviewData with debounced version**

Replace the existing `saveReviewData` function (lines 885-897) with:

```javascript
  async function saveReviewData() {
    saveReviewDataLocal();
    // Debounce: wait 5s after last call before syncing
    if (saveTimeout) clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => syncToServer(), 5000);
  }

  async function syncToServer() {
    try {
      const res = await fetch(`${API_URL}/api/review`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${SYNC_SECRET}`
        },
        body: JSON.stringify(reviewData)
      });
      if (res.ok) {
        syncPending = false;
      } else {
        syncPending = true;
      }
    } catch (e) {
      syncPending = true;
    }
  }
```

- [ ] **Step 2: Commit**

```bash
git add quiz.html
git commit -m "Add debounced saveReviewData with syncToServer"
```

---

### Task 7: Update beforeunload and add visibilitychange

**Files:**
- Modify: `quiz.html:1579-1582` (beforeunload handler)

- [ ] **Step 1: Replace the beforeunload handler**

Replace the existing handler (lines 1579-1582):
```javascript
  // Sync review data to GitHub when leaving the page
  window.addEventListener('beforeunload', () => {
    navigator.sendBeacon('/api/sync');
  });
```

With:
```javascript
  // Sync review data when leaving the page (safety net for debounced saves)
  window.addEventListener('beforeunload', () => {
    if (saveTimeout) clearTimeout(saveTimeout);
    const blob = new Blob([JSON.stringify(reviewData)], { type: 'application/json' });
    navigator.sendBeacon(`${API_URL}/api/review?secret=${SYNC_SECRET}`, blob);
  });

  // Retry sync when tab becomes visible again (offline resilience)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && syncPending) {
      syncToServer();
    }
  });
```

Note: `sendBeacon` cannot set custom headers, so the secret is passed as a query parameter. The server must also check `req.query.secret` for beacon requests.

- [ ] **Step 2: Commit**

```bash
git add quiz.html
git commit -m "Update beforeunload to use sendBeacon with Blob, add visibilitychange retry"
```

---

### Task 8: Update api/review.js to accept secret via query param

**Files:**
- Modify: `api/review.js`

- [ ] **Step 1: Update checkAuth to also accept query parameter**

Replace the `checkAuth` function:

```javascript
function checkAuth(req) {
  // Check Authorization header (normal fetch) or query param (sendBeacon)
  const auth = req.headers['authorization'];
  if (auth === `Bearer ${SYNC_SECRET}`) return true;
  const url = new URL(req.url, `https://${req.headers.host}`);
  if (url.searchParams.get('secret') === SYNC_SECRET) return true;
  return false;
}
```

- [ ] **Step 2: Commit**

```bash
git add api/review.js
git commit -m "Accept auth secret via query param for sendBeacon compatibility"
```

---

## Chunk 3: Simplify serve.py

### Task 9: Remove git push logic from serve.py

**Files:**
- Modify: `serve.py`

- [ ] **Step 1: Remove git_push_review function and /api/sync endpoint**

Remove the `git_push_review` function (lines 27-39) entirely.

In `do_POST`, remove the `/api/sync` branch (lines 80-82):
```python
        elif self.path == "/api/sync":
            git_push_review()
            self._json_response({"status": "ok"})
```

Remove the `import subprocess` (line 5) since it's no longer needed.

- [ ] **Step 2: Commit**

```bash
git add serve.py
git commit -m "Remove git push logic from serve.py — sync handled by Vercel"
```

---

## Chunk 4: Deployment & Testing

### Task 10: Update .gitignore and deploy

**Files:**
- Modify: `.gitignore` (if needed)

- [ ] **Step 1: Ensure api/ directory is not gitignored**

Check `.gitignore` and make sure `api/` is not excluded. The `api/review.js` file must be committed for Vercel to deploy it.

- [ ] **Step 2: Push to GitHub**

```bash
git push origin main
```

- [ ] **Step 3: Deploy on Vercel**

Manual steps:
1. Go to vercel.com → New Project → Import `JohannCFi/QuizHullReminder`
2. Set environment variables:
   - `GITHUB_TOKEN`: create a GitHub Personal Access Token (fine-grained, repo scope for `QuizHullReminder`, contents read+write permission)
   - `GITHUB_REPO`: `JohannCFi/QuizHullReminder`
   - `SYNC_SECRET`: generate a random string (e.g. `openssl rand -hex 32`)
3. Deploy

- [ ] **Step 4: Update quiz.html with actual Vercel URL and secret**

Replace `PLACEHOLDER.vercel.app` with actual Vercel URL and `PLACEHOLDER_SECRET` with actual `SYNC_SECRET` value.

```bash
git add quiz.html
git commit -m "Configure actual Vercel API URL and sync secret"
git push origin main
```

- [ ] **Step 5: Test end-to-end**

1. Open quiz on phone (GitHub Pages URL)
2. Rate a question
3. Wait 5 seconds (debounce)
4. Check GitHub repo → `review_data.json` should be updated
5. Open quiz on PC → should load the rating from step 2
6. Rate another question on PC
7. Refresh on phone → should see rating from step 6
