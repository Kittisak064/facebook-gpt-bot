from flask import Flask, request, jsonify
import os
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import openai

app = Flask(__name__)

# ==== OpenAI (ใช้เวอร์ชัน 0.28.0) ====
openai.api_key = os.getenv("OPENAI_API_KEY")

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")  
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # ต้องมีหัวคอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด

# ---------- Utils ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()

def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _sanitize(text: str) -> str:
    # ลบ {} [] <> ทั้งหมด + trim ช่องว่าง
    clean = re.sub(r"[\{\}\[\]\<\>]", "", text).strip()
    # ถ้าไม่จบด้วย ครับ/ค่ะ ให้เติมครับ
    if not clean.endswith(("ครับ", "ค่ะ")):
        clean += " ครับ"
    return clean

def _rewrite_with_gpt(user_message: str, base_reply: str) -> str:
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
คำตอบดิบจากระบบ: "{base_reply}"

ช่วยเขียนคำตอบใหม่ให้เหมือนพนักงานขายออนไลน์:
- ตอบสั้น กระชับ 1–3 ประโยค
- สุภาพ เป็นกันเอง มีอีโมจิเล็กน้อย
- ย้ำว่าดูรายละเอียด/สั่งซื้อได้ในลิงก์
- ห้ามใส่ [] {{}} <> เด็ดขาด
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพ อ่อนโยน"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=220
        )
        raw = resp["choices"][0]["message"]["content"].strip()
        return _sanitize(raw)
    except Exception:
        return _sanitize(base_reply)

def _find_candidates(user_message: str, records, threshold: float = 0.70):
    u = _norm(user_message)
    candidates, seen = [], set()
    for row in records:
        name = str(row.get("ชื่อสินค้า", "")).strip()
        link = str(row.get("คำตอบ", "")).strip()
        kws = [k.strip() for k in str(row.get("คีย์เวิร์ด", "")).split(",") if k.strip()]
        kws_plus = list(set(kws + ([name] if name else [])))

        best, direct_hit = 0.0, False
        for kw in kws_plus:
            kw_norm = _norm(kw)
            if not kw_norm:
                continue
            if kw_norm in u or kw_norm == u:
                best, direct_hit = 1.0, True
                break
            sc = _similar(u, kw_norm)
            if sc > best:
                best = sc

        if direct_hit or best >= threshold:
            key = name or link
            if key and key not in seen:
                seen.add(key)
                candidates.append({"name": name or "สินค้า", "link": link, "score": best})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates

# ---------- Routes ----------
@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running", 200

@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()
        if not user_message:
            return jsonify({"content": {"messages": [{"text": "⚠️ ไม่พบข้อความจากผู้ใช้ ครับ"}]}}), 200

        try:
            records = sheet.get_all_records()
        except Exception:
            fallback = "ระบบช้าชั่วคราวครับ 🙏 รบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ"
            return jsonify({"content": {"messages": [{"text": fallback}]}}), 200

        candidates = _find_candidates(user_message, records)

        if len(candidates) == 0:
            base = "รบกวนช่วยบอกชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"
            reply_text = _rewrite_with_gpt(user_message, base)

        elif len(candidates) == 1:
            c = candidates[0]
            base = f"สำหรับ {c['name']} สามารถดูรายละเอียดและกดสั่งซื้อได้ที่นี่เลยครับ 👉 {c['link']}" if c['link'] else f"สนใจ {c['name']} ใช่ไหมครับ เดี๋ยวผมส่งลิงก์ให้เพิ่มเติมครับ"
            reply_text = _rewrite_with_gpt(user_message, base)

        elif 2 <= len(candidates) <= 5:
            items = "\n".join([f"- {c['name']} 👉 {c['link']}" if c['link'] else f"- {c['name']}" for c in candidates])
            base = f"เราเจอสินค้าที่เกี่ยวข้องครับ 👇\n{items}"
            reply_text = _rewrite_with_gpt(user_message, base)

        else:
            names = "\n".join([f"- {c['name']}" for c in candidates[:5]])
            base = f"เกี่ยวกับคำนี้มีหลายตัวเลือกเลยครับ 😊\n{names}\n\nรบกวนพิมพ์ชื่อสินค้าที่ต้องการ เดี๋ยวผมส่งลิงก์ให้ครับ"
            reply_text = _rewrite_with_gpt(user_message, base)

        return jsonify({"content": {"messages": [{"text": reply_text}]}}), 200

    except Exception:
        safe_text = "ขออภัยครับ ระบบติดขัดเล็กน้อย 🙏 รบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้งครับ"
        return jsonify({"content": {"messages": [{"text": safe_text}]}}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
