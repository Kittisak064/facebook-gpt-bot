from flask import Flask, request, jsonify
import os
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import openai

app = Flask(__name__)

# ==== OpenAI (ใช้ openai==0.28.0) ====
openai.api_key = os.getenv("OPENAI_API_KEY")

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # หัว: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด


# ---------- Utils ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()

def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _rewrite_with_gpt(user_message: str, base_reply: str) -> str:
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
คำตอบดิบจากระบบ: "{base_reply}"

ช่วยเขียนคำตอบใหม่ให้เหมือนพนักงานขายออนไลน์:
- ตอบสั้น กระชับ 1–3 ประโยค
- สุภาพ เป็นกันเอง มีอีโมจิเล็กน้อย
- ย้ำให้กดสั่งซื้อ/ดูรายละเอียดที่ "ลิงก์" เท่านั้น
- อย่าพิมพ์ [] หรือ {{}}
- ถ้าลูกค้าถามเรื่องราคา/ปลายทาง ให้บอกว่า ดู/เลือกได้ในลิงก์
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
        text = resp["choices"][0]["message"]["content"].strip()
        return re.sub(r"[\{\}\[\]]", "", text)
    except Exception:
        return base_reply

def _find_candidates(user_message: str, records, threshold: float = 0.70):
    u = _norm(user_message)
    candidates, seen = [], set()

    for row in records:
        name = str(row.get("ชื่อสินค้า", "")).strip()
        link = str(row.get("คำตอบ", "")).strip()
        kw_raw = str(row.get("คีย์เวิร์ด", ""))
        kws = [k.strip() for k in kw_raw.split(",") if k.strip()]

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
    return "✅ FAQ Bot running", 200

@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()

        if not user_message:
            return jsonify({"content": {"messages": [{"text": "⚠️ ไม่พบข้อความ"}]}}), 200

        try:
            records = sheet.get_all_records()
        except Exception:
            fallback = "ตอนนี้ระบบช้าชั่วคราวครับ 🙏 รบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้ง เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"
            return jsonify({"content": {"messages": [{"text": fallback}]}}), 200

        candidates = _find_candidates(user_message, records, threshold=0.70)

        if len(candidates) == 0:
            base = "รบกวนบอกชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"
            reply_text = _rewrite_with_gpt(user_message, base)

        elif len(candidates) == 1:
            c = candidates[0]
            if c["link"]:
                base = f"สำหรับ {c['name']} สามารถกดดูรายละเอียดและสั่งซื้อได้ที่นี่เลยครับ 👉 {c['link']}"
            else:
                base = f"สนใจ {c['name']} ใช่ไหมครับ เดี๋ยวผมส่งลิงก์ให้เพิ่มเติมนะครับ"
            reply_text = _rewrite_with_gpt(user_message, base)

        elif 2 <= len(candidates) <= 4:
            items = "\n".join([f"- {c['name']} 👉 {c['link']}" if c['link'] else f"- {c['name']}" for c in candidates])
            base = f"เราเจอสินค้าที่เกี่ยวข้องครับ 👇\n{items}\n\nเลือกตัวที่ต้องการได้เลยครับ"
            reply_text = _rewrite_with_gpt(user_message, base)

        else:
            names = "\n".join([f"- {c['name']}" for c in candidates[:5]])
            reply_text = (
                "เกี่ยวกับคำนี้มีหลายตัวเลือกเลยครับ 😊\n"
                f"{names}\n\n"
                "ช่วยพิมพ์ชื่อสินค้าที่ต้องการอีกนิด เดี๋ยวผมส่งลิงก์ให้ครับ 🙏"
            )

        reply_text = re.sub(r"[\{\}\[\]]", "", reply_text)
        return jsonify({"content": {"messages": [{"text": reply_text}]}}), 200

    except Exception:
        safe_text = "ขออภัยครับ ระบบขัดข้องเล็กน้อย 🙏 รบกวนลองพิมพ์ชื่อสินค้าอีกครั้ง"
        return jsonify({"content": {"messages": [{"text": safe_text}]}}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
