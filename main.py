import os
import json
import time
import requests
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# ---------- الإعدادات الثابتة ----------
SOURCE_CHANNEL = "kirkukdiary"          # قناة المصدر (عامة)
CHANNEL_LINK = "https://t.me/ShakoMakoKirkuk"  # يُضاف بآخر كل منشور

# ---------- الأسرار (من GitHub Secrets) ----------
TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_SESSION = os.environ["TG_SESSION"]

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TARGET_CHANNEL = os.environ["TARGET_CHANNEL"]  # مثال: @ShakoMakoKirkuk

STATE_FILE = "last_id.json"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent"


def load_last_id():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("last_id", 0)
    return 0


def save_last_id(last_id):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_id": last_id}, f)


def process_with_gemini(text):
    """
    يرسل النص لـ Gemini ليقوم بأمرين:
    1) تحديد إذا كان المنشور إعلانياً (is_ad)
    2) إعادة صياغته بأسلوب صحفي خاص بالقناة، بدون أي ذكر أو رابط لقناة المصدر
    """
    prompt = f"""أنت محرر أخبار محترف تعمل لصالح قناة إخبارية اسمها "شكو ماكو كركوك".

مهمتك مع النص التالي المأخوذ من مصدر خارجي:

1. حدد هل هذا المنشور "إعلان تجاري / ترويجي" (is_ad = true) أو منشور إخباري/محتوى عادي (is_ad = false).
2. إذا لم يكن إعلاناً، أعد صياغة النص بالكامل بأسلوب صحفي عربي طبيعي ومحترف، وكأنه مكتوب أصلاً من طرف القناة، مع:
   - حذف أي ذكر لاسم أو يوزر أي قناة مصدر
   - حذف أي رابط تلكرام لا يخص القناة الحالية
   - حذف أي عبارة تشير إلى "المصدر" أو "منقول عن" أو ما شابه

النص الأصلي:
\"\"\"{text}\"\"\"

أعد الإجابة فقط بصيغة JSON التالية بدون أي شرح إضافي وبدون Markdown:
{{"is_ad": true, "rewritten_text": ""}}
"""
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    body = {"contents": [{"parts": [{"text": prompt}]}]}

    resp = requests.post(GEMINI_URL, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    parsed = json.loads(raw.strip())
    return bool(parsed.get("is_ad", False)), (parsed.get("rewritten_text") or "").strip()


def send_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TARGET_CHANNEL,
        "text": text,
        "disable_web_page_preview": True,
    })


def send_photo(file_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(file_path, "rb") as f:
        requests.post(url, data={"chat_id": TARGET_CHANNEL, "caption": caption}, files={"photo": f})


def send_video(file_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    with open(file_path, "rb") as f:
        requests.post(url, data={"chat_id": TARGET_CHANNEL, "caption": caption}, files={"video": f})


def send_document(file_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(file_path, "rb") as f:
        requests.post(url, data={"chat_id": TARGET_CHANNEL, "caption": caption}, files={"document": f})


def main():
    last_id = load_last_id()
    client = TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH)

    with client:
        # أول تشغيل فقط (ما فيه last_id.json محفوظ): ابدأ من آخر 5 منشورات بس، مو كل التاريخ
        if last_id == 0:
            latest = client.get_messages(SOURCE_CHANNEL, limit=1)
            if latest:
                last_id = max(latest[0].id - 5, 0)

        messages = list(client.iter_messages(SOURCE_CHANNEL, min_id=last_id, reverse=True))
        new_last_id = last_id

        for msg in messages:
            new_last_id = max(new_last_id, msg.id)

            # تجاهل المنشورات المُعاد توجيهها من قنوات أخرى
            if msg.forward:
                continue

            text = (msg.message or "").strip()

            # تجاهل الرسائل الفارغة من نص بالكامل (صورة بدون تعليق مثلاً)
            if not text:
                continue

            try:
                is_ad, rewritten = process_with_gemini(text)
            except Exception as e:
                print(f"خطأ بمعالجة الرسالة {msg.id}: {e}")
                continue

            if is_ad or not rewritten:
                continue  # تجاهل الإعلانات

            final_text = f"{rewritten}\n\n{CHANNEL_LINK}"

            try:
                if msg.photo:
                    file_path = client.download_media(msg, file="temp_media")
                    send_photo(file_path, final_text)
                    os.remove(file_path)
                elif msg.video:
                    file_path = client.download_media(msg, file="temp_media")
                    send_video(file_path, final_text)
                    os.remove(file_path)
                elif msg.document or msg.audio or msg.voice:
                    file_path = client.download_media(msg, file="temp_media")
                    send_document(file_path, final_text)
                    os.remove(file_path)
                else:
                    send_text(final_text)
            except Exception as e:
                print(f"خطأ بإرسال الرسالة {msg.id}: {e}")

            time.sleep(2)  # تجنب تجاوز حدود Telegram/Gemini

        save_last_id(new_last_id)


if __name__ == "__main__":
    main()
