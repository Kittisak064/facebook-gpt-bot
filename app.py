import os
import json
import difflib
from flask import Flask, request, jsonify
import openai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# -----------------------
# Configuration (env vars)
# -----------------------
# Required env vars:
# OPENAI_API_KEY        -> OpenAI API key
# GOOGLE_CREDENTIALS_JSON -> (optional) full JSON string of service account key
# SHEET_KEY             -> Google Sheet ID (the long id in sheet URL)
# CREDENTIALS_PATH      -> optional path to JSON file (if you uploaded secret file). Default: "credentials.json"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_KEY = os.getenv("SHEET_KEY")
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH", "credentials.json")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")  # optional

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable")
if not SHEET_KEY:
    raise RuntimeError("Missing SHEET_KEY environment variable")

openai.api_key = OPENAI_API_KEY

# If user provided credentials JSON as env var, write it to file for gspread to use
if GOOGLE_CREDENTIALS_JSON:
    with open(CREDENTIALS_PATH, "w", encoding="utf-8") as f:
        f.write(GOOGLE_CREDENTIALS_JSON)

# Setup gspread client
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
gc = gspread.authorize(creds)

# open sheet and cache header/rows
def load_sheet_records():
    sh = gc.open_by_key(SHEET_KEY)
    sheet = sh.sheet1
    records = sheet.get_all_records()  # list of dicts
    # Expect sheet columns like: "question", "answer" (case-insensitive)
    normalized = []
    for r in records:
        q = None
        a = None
        # find question & answer columns
        for k, v in r.items():
            key_lower = k.strip().lower()
            if key_lower in ("question", "q", "คำถาม"):
                q = str(v).strip()
            if key_lower in ("answer", "a", "response", "คำตอบ"):
                a = str(v).strip()
        # fallback: if first column and second column
        if q is None and len(r) >= 1:
            # pick first non-empty column as question
            for k2, v2 in r.items():
                if str(v2).strip():
                    q = str(v2).strip()
                    break
        if a is None and len(r) >= 2:
            # pick second column as answer (best-effort)
            seen = 0
            for k2, v2 in r.items():
                if seen == 0:
                    seen += 1
                    continue
                if seen == 1:
                    a = str(v2).strip()
                    break
        if q:
            normalized.append({"question": q, "answer": a or ""})
    return normalized

FAQ_CACHE = load_sheet_records()

# Utility: find best match via difflib
def find_best_answer(user_text, faqs, cutoff=0.6):
    """
    Return best matched answer (string) or None.
    Uses difflib to compare the user_text with FAQ questions.
    """
    questions = [f["question"] for f in faqs]
    if not questions:
        return None, None
    matches = difflib.get_close_matches(user_text, questions, n=1, cutoff=cutoff)
    if matches:
        best = matches[0]
        idx = questions.index(best)
        return faqs[idx]["answer"], best
    # fallback: check substring
    lower = user_text.lower()
    for f in faqs:
        if f["question"].lower() in lower or lower in f["question"].lower():
            return f["answer"], f["question"]
    return None, None

# Use OpenAI to generate reply using sheet context
def openai_reply(user_text, faqs, max_tokens=400):
    # Prepare few-shot context: include some top FAQ Q/A pairs to guide the model
    sample_pairs = []
    for i, f in enumerate(faqs[:8]):  # include up to 8 pairs
        q = f["question"]
        a = f["answer"] or "ไม่มีคำตอบที่ให้ไว้"
        sample_pairs.append(f"Q: {q}\nA: {a}")
    context = "\n\n".join(sample_pairs)

    system_msg = (
        "คุณเป็นแอดมินร้าน ช่วยตอบลูกค้าอย่างสุภาพและกระชับ หากคำถามตรงกับข้อมูลใน 'FAQ' ให้ใช้คำตอบนั้นก่อน"
    )
    user_prompt = f"""ลูกค้าถาม: {user_text}

นี่คือข้อมูลจาก Google Sheet (FAQ) — โปรดใช้เป็นข้อมูลอ้างอิง:
{context}

ถ้าตรงกับ FAQ ให้ตอบตาม FAQ ถ้าไม่ ให้ตอบด้วยความช่วยเหลือทั่วไป (กระชับ) และไม่สร้างข้อมูลเท็จ
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # ถ้าไม่มีสิทธิ์ เปลี่ยนเป็น "gpt-4o" หรือ "gpt-3.5-turbo"
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        text = resp.choices[0].message["content"].strip()
        return text
    except Exception as e:
        # fallback small reply
        return "ขอโทษครับ เกิดปัญหาในการเรียก GPT ตอนนี้ โปรดลองอีกครั้งภายหลัง."

@app.route("/manychat", methods=["POST"])
def manychat():
    """
    Expected request JSON from ManyChat (minimum):
    { "text": "user message", "user_id": "subscriber id" }

    Response MUST be JSON, e.g.
    { "reply": "ข้อความที่ต้องการให้ ManyChat เก็บ/ส่ง" }
    """
    data = request.get_json(silent=True) or {}
    user_text = None
    # ManyChat body will depend on how you configured body in the External Request step.
    # We'll try common keys:
    if isinstance(data, dict):
        user_text = data.get("text") or data.get("message") or data.get("body") or data.get("input")
    if not user_text and request.form:
        # fallback to form field
        user_text = request.form.get("text")
    if not user_text:
        # nothing to do
        return jsonify({"reply": "ผมไม่เห็นข้อความที่ส่งมา — กรุณาส่งค่า 'text' ใน body ของ request"}), 200

    # Try to find in FAQ
    answer, matched_q = find_best_answer(user_text, FAQ_CACHE, cutoff=0.6)
    if answer:
        reply_text = answer
        # if answer empty, fallback to openai
        if not reply_text.strip():
            reply_text = openai_reply(user_text, FAQ_CACHE)
    else:
        # not found -> call OpenAI to generate
        reply_text = openai_reply(user_text, FAQ_CACHE)

    # Return JSON with field "reply" for ManyChat response mapping
    return jsonify({"reply": reply_text}), 200

@app.route("/", methods=["GET"])
def home():
    return "Facebook GPT bot (ManyChat endpoint) running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
