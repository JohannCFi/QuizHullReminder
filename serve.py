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
review_data_lock = threading.Lock()
PORT = 8000
VERCEL_URL = "https://quiz-hull-reminder.vercel.app"
SYNC_SECRET = "2b753dc98ae5fbd3b7c226cc608c8cbddf2e46c45a39ba9c7340044f0eb3bfe2"


def load_review_data():
    with review_data_lock:
        if os.path.exists(REVIEW_FILE):
            with open(REVIEW_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}


def save_review_data(data):
    with review_data_lock:
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
        # Special handling for revision config: keep most recent
        if key == "_revision_config":
            local_entry = local_data.get(key)
            server_entry = server_data.get(key)
            if not local_entry:
                merged[key] = server_entry
            elif not server_entry:
                merged[key] = local_entry
            else:
                local_time = local_entry.get("lastModified", "")
                server_time = server_entry.get("lastModified", "")
                merged[key] = local_entry if local_time >= server_time else server_entry
            continue
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
    due = sum(1 for k, v in data.items() if not k.startswith('_') and isinstance(v, dict) and v.get("nextReview", "2000-01-01") <= today)
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
    from notify import send_notification_if_due, send_telegram, load_json, save_json
    send_notification_if_due()
except Exception:
    pass


# ========== TELEGRAM BOT (long-polling) ==========
TELEGRAM_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_config.json")
QUESTIONS_THEO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "questions_theoriques.json")
QUESTIONS_PROB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "problemes.json")

CHAPTER_ALIASES = {}

def build_chapter_aliases():
    """Build chapter alias mapping from question files."""
    global CHAPTER_ALIASES
    chapters = []
    for path in [QUESTIONS_THEO, QUESTIONS_PROB]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for q in json.load(f):
                    if q["chapitre"] not in chapters:
                        chapters.append(q["chapitre"])
    CHAPTER_ALIASES = {}
    for i, ch in enumerate(chapters, 1):
        CHAPTER_ALIASES[str(i)] = ch
        # First word as alias (lowercase)
        first_word = ch.split()[0].lower()
        CHAPTER_ALIASES[first_word] = ch
        CHAPTER_ALIASES[ch.lower()] = ch


def resolve_chapter(text):
    """Fuzzy-match a chapter name from user input."""
    text = text.strip().lower()
    if text in CHAPTER_ALIASES:
        return CHAPTER_ALIASES[text]
    # Partial match
    for alias, name in CHAPTER_ALIASES.items():
        if text in alias or alias in text:
            return name
    return None


def handle_reviser(args, bot_token, chat_id):
    """Handle /reviser command."""
    if not args:
        chapters_list = "\n".join(f"  {i+1}. {ch}" for i, ch in enumerate(
            dict.fromkeys(CHAPTER_ALIASES.values())
        ))
        send_telegram(bot_token, chat_id, f"Quel chapitre ?\n{chapters_list}\n\nEx: /reviser 1")
        return

    chapter = resolve_chapter(args)
    if not chapter:
        send_telegram(bot_token, chat_id, f"Chapitre \"{args}\" non trouvé.")
        return

    data = load_review_data()
    config = data.setdefault("_revision_config", {"chapters": {}, "lastModified": ""})
    chapters = config.setdefault("chapters", {})

    if chapter not in chapters:
        chapters[chapter] = {
            "active": True,
            "activatedAt": date.today().isoformat(),
            "newPerDay": 7,
            "introducedQuestions": [],
            "masteredAt": None
        }
    else:
        chapters[chapter]["active"] = True
        chapters[chapter]["masteredAt"] = None
        if not chapters[chapter].get("activatedAt"):
            chapters[chapter]["activatedAt"] = date.today().isoformat()

    config["lastModified"] = date.today().isoformat() + "T00:00:00Z"
    save_review_data(data)
    push_to_vercel(data)
    send_telegram(bot_token, chat_id, f"✅ Révision activée pour *{chapter}*.\n7 nouvelles questions par jour.")


def handle_stop(args, bot_token, chat_id):
    """Handle /stop command."""
    if not args:
        send_telegram(bot_token, chat_id, "Quel chapitre arrêter ?\nEx: /stop 1")
        return

    chapter = resolve_chapter(args)
    if not chapter:
        send_telegram(bot_token, chat_id, f"Chapitre \"{args}\" non trouvé.")
        return

    data = load_review_data()
    config = data.get("_revision_config", {})
    chapters = config.get("chapters", {})

    if chapter not in chapters or not chapters[chapter].get("active"):
        send_telegram(bot_token, chat_id, f"La révision n'est pas active pour *{chapter}*.")
        return

    chapters[chapter]["active"] = False
    config["lastModified"] = date.today().isoformat() + "T00:00:00Z"
    save_review_data(data)
    push_to_vercel(data)
    send_telegram(bot_token, chat_id, f"⏸ Révision désactivée pour *{chapter}*.\nTa progression est conservée.")


def handle_status(bot_token, chat_id):
    """Handle /status command."""
    data = load_review_data()
    config = data.get("_revision_config", {})
    chapters_conf = config.get("chapters", {})
    today = date.today().isoformat()

    # Load all questions
    theoriques = []
    problemes = []
    if os.path.exists(QUESTIONS_THEO):
        with open(QUESTIONS_THEO, "r", encoding="utf-8") as f:
            theoriques = json.load(f)
    if os.path.exists(QUESTIONS_PROB):
        with open(QUESTIONS_PROB, "r", encoding="utf-8") as f:
            problemes = json.load(f)

    # Build chapter -> keys map
    chapter_keys = {}
    for q in theoriques:
        chapter_keys.setdefault(q["chapitre"], []).append(f"theorique-{q['id']}")
    for q in problemes:
        chapter_keys.setdefault(q["chapitre"], []).append(f"probleme-{q['id']}")

    all_chapters = list(dict.fromkeys(
        [q["chapitre"] for q in theoriques] + [q["chapitre"] for q in problemes]
    ))

    lines = ["*📊 Statut de la révision*", ""]

    for ch in all_chapters:
        ch_conf = chapters_conf.get(ch, {})
        keys = chapter_keys.get(ch, [])
        total = len(keys)
        introduced = len(ch_conf.get("introducedQuestions", []))
        mastered = sum(1 for k in keys if data.get(k, {}).get("interval", 0) >= 21)
        due = sum(1 for k in keys if k in set(ch_conf.get("introducedQuestions", [])) and (
            not data.get(k) or data.get(k, {}).get("nextReview", "2000-01-01") <= today
        ))

        if ch_conf.get("active"):
            status_icon = "🟢"
            if ch_conf.get("masteredAt"):
                status_text = "Maîtrisé !"
            else:
                status_text = f"{mastered}/{total} maîtrisées · {introduced}/{total} introduites · {due} dues"
        else:
            status_icon = "⚪"
            status_text = f"{total} questions"

        lines.append(f"{status_icon} *{ch}*")
        lines.append(f"   {status_text}")
        lines.append("")

    send_telegram(bot_token, chat_id, "\n".join(lines))


def telegram_poll_loop():
    """Long-polling loop for Telegram bot commands."""
    if not os.path.exists(TELEGRAM_CONFIG):
        return

    try:
        tg_config = load_json(TELEGRAM_CONFIG)
    except Exception:
        return

    bot_token = tg_config.get("bot_token")
    chat_id = tg_config.get("chat_id")
    if not bot_token or not chat_id:
        return

    build_chapter_aliases()
    offset = 0
    print("[Telegram Bot] Démarré. En attente de commandes...")

    while True:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={offset}&timeout=30"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=35) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message", {})
                text = message.get("text", "").strip()
                msg_chat_id = str(message.get("chat", {}).get("id", ""))

                if msg_chat_id != str(chat_id):
                    continue

                try:
                    if text.startswith("/reviser"):
                        args = text[len("/reviser"):].strip()
                        handle_reviser(args, bot_token, chat_id)
                    elif text.startswith("/stop"):
                        args = text[len("/stop"):].strip()
                        handle_stop(args, bot_token, chat_id)
                    elif text.startswith("/status"):
                        handle_status(bot_token, chat_id)
                    elif text.startswith("/start"):
                        send_telegram(bot_token, chat_id,
                            "👋 *Quiz Hull Bot*\n\n"
                            "Commandes disponibles :\n"
                            "• /reviser [chapitre] — Activer la révision\n"
                            "• /stop [chapitre] — Arrêter la révision\n"
                            "• /status — Voir le statut\n\n"
                            "Ex: /reviser 1 ou /reviser Introduction"
                        )
                except Exception as cmd_err:
                    import traceback
                    print(f"[Telegram Bot] Erreur commande '{text}': {cmd_err}")
                    traceback.print_exc()
                    try:
                        send_telegram(bot_token, chat_id, f"Erreur: {cmd_err}")
                    except Exception:
                        pass

        except Exception as e:
            if "timed out" not in str(e).lower():
                import traceback
                print(f"[Telegram Bot] Erreur: {e}")
                traceback.print_exc()
            import time
            time.sleep(2)


# Sync local review data with Vercel/GitHub on startup
print("[sync] Synchronisation avec le serveur...")
server_data = fetch_from_vercel()
if server_data is not None:
    local_data = load_review_data()
    merged = merge_review_data(local_data, server_data)
    save_review_data(merged)
    print("[sync] Fichier local mis à jour.")
else:
    print("[sync] Impossible de synchroniser, utilisation des données locales.")

# Start Telegram bot in background thread
bot_thread = threading.Thread(target=telegram_poll_loop, daemon=True)
bot_thread.start()

server = http.server.HTTPServer(("", PORT), QuizHandler)
print(f"Serveur lance sur http://localhost:{PORT}/quiz.html")
print("Appuyez sur Ctrl+C pour arreter.")

threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}/quiz.html")).start()
server.serve_forever()
