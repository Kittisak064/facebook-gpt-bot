from flask import Flask, request, jsonify
import os
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import openai

app = Flask(__name__)

# ==== OpenAI (ใช้เวอร์ชัน 0.28.0) ====
openai.api_key = os.getenv("OPENAI_API_KEY")

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")  # ต้องตั้งใน Render
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # หัวคอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด


# ---------- Utils ----------
def _rewrite_with_gpt(user_message: str) -> str:
    """
    ถ้าไม่เจอสินค้า → ให้ GPT ช่วยรีไรท์คำตอบสุภาพ
    """
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"

ช่วยเขียนคำตอบใหม่เหมือนพนักงานขายออนไลน์:
- ตอบสั้น 1–2 ประโยค
- สุภาพ เป็นกันเอง มีอีโมจิเล็กน้อย
- ชวนให้ลูกค้าบอกชื่อสินค้าที่สนใจ
- อย่าพิมพ์เครื่องหมายวงเล็บเหล่านี้ [] หรือ {{}}
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพและช่วยเหลือลูกค้า"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=200
        )
        text = resp["choices"][0]["message"]["content"].strip()
        text = re.sub(r"[\{\}\[\]]", "", text)  # กันหลุด {} []
        return text
    except Exception:
        return "รบกวนช่วยบอกชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"


def _find_candidates(user_message, records):
    """
    หาสินค้าที่ตรงกับข้อความลูกค้า
    """
    candidates = []
    for row in records:
        name = str(row.get("ชื่อสินค้า", "")).strip()
        link = str(row.get("คำตอบ", "")).strip()   # ใช้ "คำตอบ" เป็นลิ้งก์

        if not name:
            continue

        # ตรงชื่อ 100%
        if user_message == name:
            return [{"name": name, "link": link}]

        # ถ้ามี user_message อยู่ในชื่อสินค้า
        if user_message in name:
            candidates.append({"name": name, "link": link})

        # ถ้า user_message อยู่ในคีย์เวิร์ด
        kw_raw = str(row.get("คีย์เวิร์ด", ""))
        kws = [k.strip() for k in kw_raw.split(",") if k.strip()]
        for kw in kws:
            if kw and kw in user_message:
                candidates.append({"name": name, "link": link})
                break

    return candidates


# ---------- Routes ----------
@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot with Google Sheets is running", 200


@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()

        if not user_message:
            return jsonify({"content": {"messages": [{"text": "⚠️ ไม่พบข้อความจากผู้ใช้"}]}}), 200

        try:
            records = sheet.get_all_records()
        except Exception:
            fallback = "ตอนนี้ระบบขัดข้องครับ 🙏 กรุณาพิมพ์ชื่อสินค้าที่สนใจอีกครั้ง เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"
            return jsonify({"content": {"messages": [{"text": fallback}]}}), 200

        candidates = _find_candidates(user_message, records)

        # ===== ตัดสินใจ =====
        if len(candidates) == 0:
            reply_text = _rewrite_with_gpt(user_message)

        elif len(candidates) == 1:
            c = candidates[0]
            reply_text = f"สำหรับ {c['name']} สามารถดูรายละเอียดและสั่งซื้อได้ที่นี่เลยครับ 👉 {c['link']}"

        elif len(candidates) <= 5:
            items = "\n".join([
                f"- {c['name']} 👉 {c['link']}" if c['link'] else f"- {c['name']}"
                for c in candidates
            ])
            reply_text = f"เราเจอสินค้าที่เกี่ยวข้องครับ 👇\n\n{items}\n\nกดลิ้งก์เพื่อดูรายละเอียดหรือสั่งซื้อได้เลยครับ 🙏"

        else:
            names = "\n".join([f"- {c['name']}" for c in candidates[:5]])
            reply_text = f"เกี่ยวกับคำนี้มีหลายตัวเลือกครับ 😊\n\n{names}\n\nกรุณาพิมพ์ชื่อที่ตรงกว่านี้อีกครั้งนะครับ 🙏"

        reply_text = re.sub(r"[\{\}\[\]]", "", reply_text)

        return jsonify({"content": {"messages": [{"text": reply_text}]}}), 200

    except Exception as e:
        safe_text = "ขออภัยครับ ระบบติดขัดเล็กน้อย 🙏 รบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ"
        return jsonify({"content": {"messages": [{"text": safe_text}]}}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
