"""
Notification Telegram pour le Quiz Hull.
Envoie un message quand des questions sont à réviser (chapitres actifs uniquement).

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


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_revision_config(review_data):
    """Extract revision config from review data."""
    config = review_data.get("_revision_config", {})
    return config.get("chapters", {})


def get_due_questions():
    """Count due questions for active revision chapters only."""
    review_data = load_json(REVIEW_FILE)
    today = date.today().isoformat()
    revision_chapters = get_revision_config(review_data)

    # Get active chapters
    active_chapters = {
        name for name, conf in revision_chapters.items() if conf.get("active")
    }

    if not active_chapters:
        return 0, 0, 0, {}

    # Load questions to get chapter info
    theoriques = load_json(QUESTIONS_THEO) if os.path.exists(QUESTIONS_THEO) else []
    problemes = load_json(QUESTIONS_PROB) if os.path.exists(QUESTIONS_PROB) else []

    # Build key -> chapter map (only active chapters)
    all_keys = {}
    for q in theoriques:
        if q["chapitre"] in active_chapters:
            all_keys[f"theorique-{q['id']}"] = q["chapitre"]
    for q in problemes:
        if q["chapitre"] in active_chapters:
            all_keys[f"probleme-{q['id']}"] = q["chapitre"]

    reviewed_due = 0
    never_seen = 0
    due_by_chapter = {}
    config_changed = False

    for chapter in active_chapters:
        ch_conf = revision_chapters[chapter]
        introduced = set(ch_conf.get("introducedQuestions", []))
        chapter_keys = {k for k, ch in all_keys.items() if ch == chapter}

        # Count due among introduced questions
        for key in chapter_keys:
            if key in introduced:
                card = review_data.get(key)
                if not card:
                    never_seen += 1
                    due_by_chapter[chapter] = due_by_chapter.get(chapter, 0) + 1
                elif card.get("nextReview", "2000-01-01") <= today:
                    reviewed_due += 1
                    due_by_chapter[chapter] = due_by_chapter.get(chapter, 0) + 1

        # Introduce new questions for today
        today_count = 0
        intro_today = ch_conf.get("introducedToday", {})
        if intro_today.get("date") == today:
            today_count = intro_today.get("count", 0)
        can_introduce = max(0, ch_conf.get("newPerDay", 7) - today_count)
        not_yet = [k for k in chapter_keys if k not in introduced]
        new_ones = not_yet[:can_introduce]

        for key in new_ones:
            never_seen += 1
            due_by_chapter[chapter] = due_by_chapter.get(chapter, 0) + 1
            introduced.add(key)

        if new_ones:
            ch_conf["introducedQuestions"] = list(introduced)
            ch_conf["introducedToday"] = {"date": today, "count": today_count + len(new_ones)}
            config_changed = True

    if config_changed:
        review_data["_revision_config"]["lastModified"] = f"{today}T00:00:00Z"
        save_json(REVIEW_FILE, review_data)

    total_due = reviewed_due + never_seen
    return total_due, reviewed_due, never_seen, due_by_chapter


def check_mastery(review_data):
    """Check if any active chapter is now fully mastered. Returns list of newly mastered chapters."""
    revision_chapters = get_revision_config(review_data)
    theoriques = load_json(QUESTIONS_THEO) if os.path.exists(QUESTIONS_THEO) else []
    problemes = load_json(QUESTIONS_PROB) if os.path.exists(QUESTIONS_PROB) else []

    # Build chapter -> keys map
    chapter_keys = {}
    for q in theoriques:
        chapter_keys.setdefault(q["chapitre"], []).append(f"theorique-{q['id']}")
    for q in problemes:
        chapter_keys.setdefault(q["chapitre"], []).append(f"probleme-{q['id']}")

    newly_mastered = []
    for name, conf in revision_chapters.items():
        if not conf.get("active") or conf.get("masteredAt"):
            continue
        keys = chapter_keys.get(name, [])
        if not keys:
            continue
        all_mastered = all(
            review_data.get(k, {}).get("interval", 0) >= 21 for k in keys
        )
        if all_mastered:
            conf["masteredAt"] = date.today().isoformat()
            newly_mastered.append(name)

    return newly_mastered


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

    total_due, reviewed_due, never_seen, due_by_chapter = get_due_questions()

    # Check mastery and send notifications
    review_data = load_json(REVIEW_FILE)
    newly_mastered = check_mastery(review_data)
    if newly_mastered:
        review_data["_revision_config"]["lastModified"] = date.today().isoformat() + "T00:00:00Z"
        save_json(REVIEW_FILE, review_data)
        for ch in newly_mastered:
            try:
                send_telegram(bot_token, chat_id, f"🎉 *Le chapitre \"{ch}\" est maîtrisé !*")
                print(f"[Telegram] Notification de maîtrise envoyée pour : {ch}")
            except Exception as e:
                print(f"[Telegram] Erreur d'envoi maîtrise : {e}")

    if total_due == 0:
        return

    # Build message
    if reviewed_due > 0 and never_seen > 0:
        detail = f"({reviewed_due} à réviser et {never_seen} nouvelles)"
    elif never_seen > 0:
        detail = f"({never_seen} nouvelles)"
    else:
        detail = f"({reviewed_due} à réviser)"

    lines = [
        "*Quiz Hull — Révision du jour*",
        "",
        f"Tu as *{total_due} question(s)* aujourd'hui ! {detail}",
        "",
    ]

    if due_by_chapter:
        lines.append("*Détail par chapitre :*")
        for ch, count in sorted(due_by_chapter.items(), key=lambda x: -x[1]):
            lines.append(f"• {ch} : {count}")
        lines.append("")

    lines.append("[Lance ton quiz](https://johanncfi.github.io/QuizHullReminder/quiz.html)")

    message = "\n".join(lines)

    try:
        send_telegram(bot_token, chat_id, message)
        print(f"[Telegram] Notification envoyée : {total_due} question(s) à réviser.")
    except Exception as e:
        print(f"[Telegram] Erreur d'envoi : {e}")


if __name__ == "__main__":
    if not os.path.exists(CONFIG_FILE):
        print("telegram_config.json introuvable.")
        print("Consultez les instructions en haut de ce fichier pour configurer le bot.")
    else:
        send_notification_if_due()
        total, _, _, _ = get_due_questions()
        if total == 0:
            print("Aucune question a reviser aujourd'hui. Pas de notification envoyee.")
