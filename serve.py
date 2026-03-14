import http.server
import webbrowser
import os
import threading
import subprocess
import json
from datetime import date

os.chdir(os.path.dirname(os.path.abspath(__file__)))

REVIEW_FILE = "review_data.json"
PORT = 8000


def load_review_data():
    if os.path.exists(REVIEW_FILE):
        with open(REVIEW_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_review_data(data):
    with open(REVIEW_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def git_push_review():
    """Commit and push review_data.json in background."""
    def _push():
        try:
            subprocess.run(["git", "add", REVIEW_FILE], check=True)
            result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
            if result.returncode != 0:  # There are staged changes
                subprocess.run(["git", "commit", "-m", "Sync review data"], check=True)
                subprocess.run(["git", "push"], check=True)
                print("[Git] review_data.json pushed.")
        except Exception as e:
            print(f"[Git] Sync error: {e}")
    threading.Thread(target=_push, daemon=True).start()


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
            data = load_review_data()
            self._json_response(data)
        elif self.path == "/api/review-summary":
            due = count_due_questions()
            self._json_response({"due": due, "date": date.today().isoformat()})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/review":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                save_review_data(data)
                self._json_response({"status": "ok"})
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
        elif self.path == "/api/sync":
            git_push_review()
            self._json_response({"status": "ok"})
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
