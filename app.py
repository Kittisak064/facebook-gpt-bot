import os
import requests
import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request

app = Flask(__name__)

# ==== API KEYS ====
openai.api_key = os.getenv("OPENAI_API_KEY")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token")

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open_by_key("1O4SOhp2JG-edaAWZZ7pwzL9uwm3F4Eif9jUeoFN7zu8").sheet1
  # เปลี่ยนชื่อเป็นชื่อ Google Sheet ของคุณ

# ==== Webhook Verify ====
@app.route("/webhook", methods=["GET"])
def verify():
    token_sent = request.args.get("hub.verify_token")
    if token_sent == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Invalid verification token"

# ==== Webhook Receive ====
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    for entry in data.get("entry", []):
        for msg in entry.get("messaging", []):
            if "message" in msg and "text" in msg["message"]:
                sender_id = msg["sender"]["id"]
                user_msg = msg["message"]["text"]

                # --- ดึงข้อมูลจาก Google Sheet ---
                faqs = sheet.get_all_records()

                # --- Prompt สำหรับ GPT ---
                prompt = f"""
                ลูกค้าถาม: {user_msg}
                นี่คือข้อมูลร้านจาก Google Sheet:
                {faqs}

                ตอบลูกค้าอย่างสุภาพ กระชับ และไม่มั่ว
                """

                gpt_response = openai.ChatCompletion.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "คุณคือแอดมินร้านที่ตอบลูกค้า"},
                        {"role": "user", "content": prompt}
                    ]
                )
                reply = gpt_response.choices[0].message["content"]

                # --- ส่งข้อความกลับไปที่ Facebook ---
                requests.post(
                    "https://graph.facebook.com/v20.0/me/messages",
                    params={"access_token": PAGE_ACCESS_TOKEN},
                    json={
                        "recipient": {"id": sender_id},
                        "message": {"text": reply}
                    }
                )
    return "ok", 200

@app.route("/")
def home():
    return "Facebook GPT Bot is running!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
