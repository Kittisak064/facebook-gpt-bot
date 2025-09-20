import os
from flask import Flask, request, jsonify
from openai import OpenAI
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==== CONFIG ====
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # ดึง API Key จาก Render

PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token")

# ==== GOOGLE SHEETS ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)
sheet = gs_client.open_by_key("YOUR_SHEET_ID").sheet1
# 👉 เปลี่ยน "YOUR_SHEET_ID" เป็น ID ชีทของพี่

# ==== WEBHOOK VERIFY (Facebook) ====
@app.route("/webhook", methods=["GET"])
def verify():
    token_sent = request.args.get("hub.verify_token")
    if token_sent == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Invalid verification token"

# ==== FACEBOOK MESSAGE WEBHOOK ====
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

                # --- ส่งไปหา GPT ---
                prompt = f"""
                ลูกค้าถาม: {user_msg}
                นี่คือข้อมูลร้านจาก Google Sheet:
                {faqs}

                ตอบลูกค้าอย่างสุภาพ กระชับ และไม่มั่ว
                """

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "คุณคือแอดมินร้านที่ตอบลูกค้า"},
                        {"role": "user", "content": prompt}
                    ]
                )

                reply = response.choices[0].message.content

                # --- ส่งกลับไปยัง Facebook ---
                import requests
                requests.post(
                    "https://graph.facebook.com/v20.0/me/messages",
                    params={"access_token": PAGE_ACCESS_TOKEN},
                    json={
                        "recipient": {"id": sender_id},
                        "message": {"text": reply}
                    }
                )
    return "ok", 200

# ==== MANYCHAT ENDPOINT (ถ้าใช้ ManyChat) ====
@app.route("/manychat", methods=["POST"])
def manychat():
    data = request.get_json()
    user_msg = data.get("text", "")

    # --- ดึงข้อมูลจาก Google Sheet ---
    faqs = sheet.get_all_records()

    prompt = f"""
    ลูกค้าถาม: {user_msg}
    นี่คือข้อมูลร้านจาก Google Sheet:
    {faqs}

    ตอบลูกค้าอย่างสุภาพ กระชับ และไม่มั่ว
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "คุณคือแอดมินร้านที่ตอบลูกค้า"},
            {"role": "user", "content": prompt}
        ]
    )

    reply = response.choices[0].message.content
    return jsonify({"reply": reply})

@app.route("/")
def home():
    return "✅ Facebook GPT Bot is running with new OpenAI API!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
