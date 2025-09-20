import os
import requests
import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request

app = Flask(__name__)

# ==== CONFIG ====
openai.api_key = os.getenv("OPENAI_API_KEY")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token")
SHEET_KEY = os.getenv("SHEET_KEY")

if not openai.api_key:
    raise RuntimeError("❌ Missing OPENAI_API_KEY")
if not SHEET_KEY:
    raise RuntimeError("❌ Missing SHEET_KEY")

# ==== Google Sheets (Secret Files: credentials.json) ====
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_KEY).sheet1


def get_faqs():
    return sheet.get_all_records()


def build_reply(user_text):
    faqs = get_faqs()
    prompt = f"""
    ลูกค้าถาม: {user_text}
    นี่คือข้อมูลจาก Google Sheet:
    {faqs}

    ตอบลูกค้าอย่างสุภาพ กระชับ และตรงกับข้อมูล
    """
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือแอดมินร้าน"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"ขอโทษครับ เกิดข้อผิดพลาด: {e}"


# ==== ROUTES ====
@app.route("/")
def home():
    return "✅ Bot is running!"


@app.route("/webhook", methods=["GET"])
def verify():
    token_sent = request.args.get("hub.verify_token")
    if token_sent == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Invalid verification token"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    for entry in data.get("entry", []):
        for msg in entry.get("messaging", []):
            if "message" in msg and "text" in msg["message"]:
                sender_id = msg["sender"]["id"]
                user_msg = msg["message"]["text"]

                reply = build_reply(user_msg)

                # ส่งข้อความกลับไปที่ Facebook
                requests.post(
                    "https://graph.facebook.com/v20.0/me/messages",
                    params={"access_token": PAGE_ACCESS_TOKEN},
                    json={
                        "recipient": {"id": sender_id},
                        "message": {"text": reply}
                    }
                )
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
