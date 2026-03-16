"""
Notification Telegram pour le Quiz Hull.
Envoie un message quand des questions sont à réviser.

Configuration requise : telegram_config.json avec bot_token et chat_id.
Voir les instructions dans le fichier pour créer un bot.

=== GUIDE DE CONFIGURATION ===
1. Ouvrir Telegram, chercher @BotFather
2. Envoyer /newbot
3. Choisir un nom (ex: "Hull Quiz Reminder") et un username (ex: "hull_quiz_bot")
4. Copier le token donné par BotFather
5. Ouvrir une conversation avec ton nouveau bot et envoyer /start
6. Aller sur https://api.telegram.org/bot<TON_TOKEN>/getUpdates
7. Chercher "chat":{"id": XXXXX} dans la réponse — c'est ton chat_id
8. Créer telegram_config.json dans ce dossier :
   {
     "bot_token": "123456:ABC-DEF...",
     "chat_id": "987654321"
   }
"""

import json
import os
import urllib.request
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REVIEW_FILE = os.path.join(SCRIPT_DIR, "review_data.json")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "telegram_config.json")
QUESTIONS_THEO = os.path.join(SCRIPT_DIR, "questions_theoriques.json")
QUESTIONS_PROB = os.path.join(SCRIPT_DIR, "problemes.json")


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_due_questions():
    review_data = load_json(REVIEW_FILE)
    if not review_data:
        return 0, {}

    today = date.today().isoformat()
    due_by_chapter = {}

    # Load questions to get chapter info
    theoriques = load_json(QUESTIONS_THEO) if os.path.exists(QUESTIONS_THEO) else []
    problemes = load_json(QUESTIONS_PROB) if os.path.exists(QUESTIONS_PROB) else []

    # Build key -> chapter map
    chapter_map = {}
    for q in theoriques:
        chapter_map[f"theorique-{q['id']}"] = q["chapitre"]
    for q in problemes:
        chapter_map[f"probleme-{q['id']}"] = q["chapitre"]

    total_due = 0
    for key, card in review_data.items():
        if card.get("nextReview", "2000-01-01") <= today:
            total_due += 1
            ch = chapter_map.get(key, "Autre")
            due_by_chapter[ch] = due_by_chapter.get(ch, 0) + 1

    return total_due, due_by_chapter


def send_telegram(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_notification_if_due():
    """Called by serve.py at startup and by the scheduled task."""
    if not os.path.exists(CONFIG_FILE):
        return

    config = load_json(CONFIG_FILE)
    bot_token = config.get("bot_token")
    chat_id = config.get("chat_id")
    if not bot_token or not chat_id:
        return

    total_due, due_by_chapter = get_due_questions()
    if total_due == 0:
        return

    # Build message
    lines = [
        "*Quiz Hull \u2014 R\u00e9vision du jour*",
        "",
        f"Tu as *{total_due} question(s)* \u00e0 r\u00e9viser aujourd'hui !",
        "",
    ]

    if due_by_chapter:
        lines.append("*D\u00e9tail par chapitre :*")
        for ch, count in sorted(due_by_chapter.items(), key=lambda x: -x[1]):
            lines.append(f"\u2022 {ch} : {count}")
        lines.append("")

    lines.append("[Lance ton quiz](https://johanncfi.github.io/QuizHullReminder/quiz.html)")

    message = "\n".join(lines)

    try:
        send_telegram(bot_token, chat_id, message)
        print(f"[Telegram] Notification envoy\u00e9e : {total_due} question(s) \u00e0 r\u00e9viser.")
    except Exception as e:
        print(f"[Telegram] Erreur d'envoi : {e}")


if __name__ == "__main__":
    if not os.path.exists(CONFIG_FILE):
        print("telegram_config.json introuvable.")
        print("Consultez les instructions en haut de ce fichier pour configurer le bot.")
    else:
        send_notification_if_due()
        total, _ = get_due_questions()
        if total == 0:
            print("Aucune question a reviser aujourd'hui. Pas de notification envoyee.")
