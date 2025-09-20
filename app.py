import os
import json
import requests
import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request

app = Flask(__name__)

# ===============================
# CONFIG
# ===============================
openai.api_key = os.getenv("OPENAI_API_KEY")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token")

# ===============================
# Google Sheets
# ===============================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)

# 👉 เปลี่ยนเป็น Google Sheet ID ของคุณเอง
SHEET_ID = "1O4SOhp2JG-edaAWZZ7pwzL9uwm3F4Eif9jUeoFN7zu8"
sheet = client.open_by_key(SHEET_ID).sheet1


# ===============================
# FACEBOOK WEBHOOK VERIFY
# ===============================
@app.route("/webhook", methods=["GET"])
def verify():
    token_sent = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if token_sent == VERIFY_TOKEN:
        return challenge
    return "Invalid verification token"


# ===============================
# FACEBOOK WEBHOOK RECEIVE
# ===============================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if "entry" in data:
        for entry in data["entry"]:
            for msg in entry.get("messaging", []):
                if "message" in msg and "text" in msg["message"]:
                    sender_id = msg["sender"]["id"]
                    user_msg = msg["message"]["text"]

                    faqs = sheet.get_all_records()

                    prompt = f"""
                    ลูกค้าถาม: {user_msg}
                    นี่คือข้อมูลจาก Google Sheet:
                    {faqs}

                    กรุณาตอบลูกค้าเป็นข้อความสั้นๆ สุภาพ และชัดเจน
                    """

                    gpt_response = openai.ChatCompletion.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": "คุณคือแอดมินร้านที่ตอบลูกค้า"},
                            {"role": "user", "content": prompt},
                        ],
                    )
                    reply = gpt_response.choices[0].message["content"]

                    send_message(sender_id, reply)
    return "ok", 200


# ===============================
# MANYCHAT WEBHOOK
# ===============================
@app.route("/manychat", methods=["POST"])
def manychat():
    data = request.get_json()
    user_msg = data.get("text", "")

    faqs = sheet.get_all_records()

    prompt = f"""
    ลูกค้าถาม: {user_msg}
    นี่คือข้อมูลจาก Google Sheet:
    {faqs}

    กรุณาตอบลูกค้าเป็นข้อความสั้นๆ สุภาพ และชัดเจน
    """

    gpt_response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "คุณคือแอดมินร้านที่ตอบลูกค้า"},
            {"role": "user", "content": prompt},
        ],
    )
    reply = gpt_response.choices[0].message["content"]

    return {"reply": reply}


# ===============================
# ส่งข้อความกลับ Facebook
# ===============================
def send_message(recipient_id, message_text):
    url = "https://graph.facebook.com/v20.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN}
    headers = {"Content-Type": "application/json"}
    data = {"recipient": {"id": recipient_id}, "message": {"text": message_text}}
    requests.post(url, params=params, headers=headers, data=json.dumps(data))


# ===============================
# ROUTE ทดสอบ
# ===============================
@app.route("/")
def home():
    return "✅ Facebook GPT Bot is running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
