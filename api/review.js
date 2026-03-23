const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const GITHUB_REPO = process.env.GITHUB_REPO;
const SYNC_SECRET = process.env.SYNC_SECRET;
const FILE_PATH = process.env.FILE_PATH || 'review_data.json';

async function fetchFromGitHub() {
  const res = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${FILE_PATH}`,
    {
      headers: {
        Authorization: `token ${GITHUB_TOKEN}`,
        Accept: 'application/vnd.github.v3+json',
      },
    }
  );
  if (!res.ok) {
    if (res.status === 404) {
      return { content: {}, sha: null };
    }
    throw new Error(`GitHub fetch failed: ${res.status} ${await res.text()}`);
  }
  const json = await res.json();
  const decoded = Buffer.from(json.content, 'base64').toString('utf-8');
  return { content: JSON.parse(decoded), sha: json.sha };
}

function checkAuth(req) {
  const auth = req.headers['authorization'] || '';
  const token = auth.replace(/^Bearer\s+/i, '');
  if (token === SYNC_SECRET) return true;
  const url = new URL(req.url, `https://${req.headers.host}`);
  if (url.searchParams.get('secret') === SYNC_SECRET) return true;
  return false;
}

function mergeReviewData(clientData, serverData) {
  const allKeys = new Set([
    ...Object.keys(clientData || {}),
    ...Object.keys(serverData || {}),
  ]);
  const merged = {};
  for (const key of allKeys) {
    // Special handling for revision config: keep most recent
    if (key === '_revision_config') {
      const clientEntry = clientData?.[key];
      const serverEntry = serverData?.[key];
      if (!clientEntry) { merged[key] = serverEntry; }
      else if (!serverEntry) { merged[key] = clientEntry; }
      else {
        const clientTime = new Date(clientEntry.lastModified || 0).getTime();
        const serverTime = new Date(serverEntry.lastModified || 0).getTime();
        merged[key] = clientTime >= serverTime ? clientEntry : serverEntry;
      }
      continue;
    }
    const clientEntry = clientData?.[key];
    const serverEntry = serverData?.[key];
    if (!clientEntry) {
      merged[key] = serverEntry;
    } else if (!serverEntry) {
      merged[key] = clientEntry;
    } else {
      const clientHistory = Array.isArray(clientEntry.history) ? clientEntry.history : [];
      const serverHistory = Array.isArray(serverEntry.history) ? serverEntry.history : [];
      if (clientHistory.length > serverHistory.length) {
        merged[key] = clientEntry;
      } else if (serverHistory.length > clientHistory.length) {
        merged[key] = serverEntry;
      } else {
        const clientNext = new Date(clientEntry.nextReview || 0).getTime();
        const serverNext = new Date(serverEntry.nextReview || 0).getTime();
        merged[key] = clientNext >= serverNext ? clientEntry : serverEntry;
      }
    }
  }
  return merged;
}

async function commitToGitHub(content, sha) {
  const body = {
    message: `Sync review data ${new Date().toISOString()}`,
    content: Buffer.from(JSON.stringify(content, null, 2)).toString('base64'),
  };
  if (sha) {
    body.sha = sha;
  }
  const res = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${FILE_PATH}`,
    {
      method: 'PUT',
      headers: {
        Authorization: `token ${GITHUB_TOKEN}`,
        Accept: 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    }
  );
  if (!res.ok) {
    const err = new Error(`GitHub commit failed: ${res.status}`);
    err.status = res.status;
    err.body = await res.text();
    throw err;
  }
  return res.json();
}

export default async function handler(req, res) {
  // CORS preflight
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  // Auth check
  if (!checkAuth(req)) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  try {
    if (req.method === 'GET') {
      const { content, sha } = await fetchFromGitHub();
      return res.status(200).json({ data: content, sha });
    }

    if (req.method === 'POST') {
      // Handle both string (sendBeacon) and object body
      let clientData = req.body;
      if (typeof clientData === 'string') {
        clientData = JSON.parse(clientData);
      }

      const MAX_RETRIES = 3;
      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        const { content: serverData, sha } = await fetchFromGitHub();
        const merged = mergeReviewData(clientData, serverData);
        try {
          await commitToGitHub(merged, sha);
          return res.status(200).json({ data: merged });
        } catch (err) {
          if (err.status === 409 && attempt < MAX_RETRIES - 1) {
            continue;
          }
          throw err;
        }
      }
    }

    return res.status(405).json({ error: 'Method not allowed' });
  } catch (err) {
    console.error('API error:', err);
    return res.status(500).json({ error: err.message });
  }
}
