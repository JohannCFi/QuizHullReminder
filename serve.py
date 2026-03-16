import http.server
import webbrowser
import os
import threading
import json
import urllib.request
import urllib.error
from datetime import date

os.chdir(os.path.dirname(os.path.abspath(__file__)))

REVIEW_FILE = "review_data.json"
PORT = 8000
VERCEL_URL = "https://quiz-hull-reminder.vercel.app"
SYNC_SECRET = "2b753dc98ae5fbd3b7c226cc608c8cbddf2e46c45a39ba9c7340044f0eb3bfe2"


def load_review_data():
    if os.path.exists(REVIEW_FILE):
        with open(REVIEW_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_review_data(data):
    with open(REVIEW_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_from_vercel():
    """Fetch review data from Vercel API (which reads from GitHub)."""
    try:
        req = urllib.request.Request(
            f"{VERCEL_URL}/api/review",
            headers={"Authorization": f"Bearer {SYNC_SECRET}"}
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            result = json.loads(res.read().decode("utf-8"))
            return result.get("data", {})
    except Exception as e:
        print(f"[sync] Impossible de récupérer depuis Vercel: {e}")
        return None


def push_to_vercel(data):
    """Push review data to Vercel API (which merges and commits to GitHub)."""
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            f"{VERCEL_URL}/api/review",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {SYNC_SECRET}",
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        print(f"[sync] Impossible d'envoyer vers Vercel: {e}")
        return None


def merge_review_data(local_data, server_data):
    """Same merge logic as Vercel API and frontend."""
    if not server_data:
        return local_data
    all_keys = set(list(local_data.keys()) + list(server_data.keys()))
    merged = {}
    for key in all_keys:
        local_entry = local_data.get(key)
        server_entry = server_data.get(key)
        if not local_entry:
            merged[key] = server_entry
        elif not server_entry:
            merged[key] = local_entry
        else:
            local_history = local_entry.get("history", [])
            server_history = server_entry.get("history", [])
            if len(local_history) > len(server_history):
                merged[key] = local_entry
            elif len(server_history) > len(local_history):
                merged[key] = server_entry
            else:
                local_next = local_entry.get("nextReview", "")
                server_next = server_entry.get("nextReview", "")
                merged[key] = local_entry if local_next >= server_next else server_entry
    return merged



def count_due_questions():
    data = load_review_data()
    today = date.today().isoformat()
    due = sum(1 for v in data.values() if v.get("nextReview", "2000-01-01") <= today)
    return due


def print_due_summary():
    due = count_due_questions()
    if due > 0:
        print(f"\n{'='*50}")
        print(f"  {due} question(s) a reviser aujourd'hui !")
        print(f"{'='*50}\n")
    else:
        print("\nAucune question a reviser aujourd'hui.\n")


class QuizHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/review":
            local_data = load_review_data()
            server_data = fetch_from_vercel()
            if server_data is not None:
                merged = merge_review_data(local_data, server_data)
                save_review_data(merged)
                self._json_response({"data": merged})
            else:
                self._json_response({"data": local_data})
        elif self.path == "/api/review-summary":
            due = count_due_questions()
            self._json_response({"due": due, "date": date.today().isoformat()})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/review" or self.path.startswith("/api/review?"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                client_data = json.loads(body.decode("utf-8"))
                # Save locally
                local_data = load_review_data()
                merged = merge_review_data(client_data, local_data)
                save_review_data(merged)
                # Push to Vercel (which merges with GitHub)
                result = push_to_vercel(merged)
                if result and "data" in result:
                    save_review_data(result["data"])
                    self._json_response({"data": result["data"]})
                else:
                    self._json_response({"data": merged})
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
        else:
            self.send_error(404, "Not Found")

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Silence GET/POST logs for API calls
        if "/api/" not in str(args[0]):
            super().log_message(format, *args)


print_due_summary()

# Try to send Telegram notification
try:
    from notify import send_notification_if_due
    send_notification_if_due()
except Exception:
    pass

server = http.server.HTTPServer(("", PORT), QuizHandler)
print(f"Serveur lance sur http://localhost:{PORT}/quiz.html")
print("Appuyez sur Ctrl+C pour arreter.")

threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}/quiz.html")).start()
server.serve_forever()
