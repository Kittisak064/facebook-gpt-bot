import os
import json
import difflib
import requests
from flask import Flask, request, jsonify
from openai import OpenAI
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# ===============================
# CONFIG
# ===============================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_KEY = os.getenv("SHEET_KEY")  # Google Sheet ID
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH", "credentials.json")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not OPENAI_API_KEY:
    raise RuntimeError("❌ Missing OPENAI_API_KEY")
if not SHEET_KEY:
    raise RuntimeError("❌ Missing SHEET_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# ถ้าใส่ไฟล์ json key ไว้ใน Environment
if GOOGLE_CREDENTIALS_JSON:
    with open(CREDENTIALS_PATH, "w", encoding="utf-8") as f:
        f.write(GOOGLE_CREDENTIALS_JSON)

# ===============================
# Google Sheets
# ===============================
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_KEY).sheet1

def load_faqs():
    recs = sheet.get_all_records()
    out = []
    for r in recs:
        q, a = None, None
        for k, v in r.items():
            if str(k).strip().lower() in ("question", "q", "คำถาม"):
                q = str(v).strip()
            if str(k).strip().lower() in ("answer", "a", "response", "คำตอบ"):
                a = str(v).strip()
        if q:
            out.append({"question": q, "answer": a or ""})
    return out

FAQ_CACHE = load_faqs()

def find_best_answer(user_text, cutoff=0.65):
    questions = [f["question"] for f in FAQ_CACHE]
    matches = difflib.get_close_matches(user_text, questions, n=1, cutoff=cutoff)
    if matches:
        q = matches[0]
        idx = questions.index(q)
        return FAQ_CACHE[idx]["answer"]
    return None

def gpt_reply(user_text):
    context = "\n".join([f"Q: {f['question']}\nA: {f['answer']}" for f in FAQ_CACHE[:8]])
    prompt = f"""ลูกค้าถาม: {user_text}

FAQ:
{context}

ตอบสั้นๆ สุภาพ อิงข้อมูลจริงจาก FAQ ถ้ามี"""
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "คุณคือแอดมินร้าน"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=300
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"ขอโทษครับ ระบบขัดข้อง: {e}"

def build_reply(user_text):
    ans = find_best_answer(user_text)
    if ans and ans.strip():
        return ans
    return gpt_reply(user_text)

# ===============================
# ROUTES
# ===============================
@app.route("/")
def home():
    return "✅ Bot is running!"

@app.route("/manychat", methods=["POST"])
def manychat():
    data = request.get_json(silent=True) or {}
    user_text = data.get("text") or ""
    if not user_text:
        return jsonify({"reply": "❌ No text received"}), 200
    return jsonify({"reply": build_reply(user_text)}), 200

@app.route("/webhook", methods=["GET"])
def fb_verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Token mismatch", 403

@app.route("/webhook", methods=["POST"])
def fb_webhook():
    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for msg in entry.get("messaging", []):
            if "message" in msg and "text" in msg["message"]:
                sender = msg["sender"]["id"]
                reply = build_reply(msg["message"]["text"])
                if PAGE_ACCESS_TOKEN:
                    requests.post(
                        "https://graph.facebook.com/v20.0/me/messages",
                        params={"access_token": PAGE_ACCESS_TOKEN},
                        json={
                            "recipient": {"id": sender},
                            "message": {"text": reply}
                        }
                    )
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
