from flask import Flask, request, jsonify
from openai import OpenAI
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import re

app = Flask(__name__)

# ==== OpenAI Client ====
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # ชื่อชีทต้องเป็น "FAQ"

# ===== Helper =====
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()

def _gpt_followup(user_message: str) -> str:
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
งานของคุณ: เป็นพนักงานขายร้านค้าออนไลน์
- ตอบสุภาพ เป็นกันเอง (1-2 ประโยค)
- แนะนำลูกค้าให้บอกชื่อสินค้าที่สนใจ
- ย้ำว่าลูกค้าสามารถกดลิงก์เพื่อสั่งซื้อได้
- ใส่อีโมจิเล็กน้อย
ตัวอย่างสินค้า: ไฟเซ็นเซอร์, หม้อหุงข้าว, ปลั๊กไฟ
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพ อ่อนโยน เป็นกันเอง"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=150
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "ตอนนี้ระบบขัดข้องเล็กน้อยครับ 😅 รบกวนบอกชื่อสินค้าที่สนใจอีกครั้งนะครับ 🙏"

@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running with Google Sheets", 200


@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()

        if not user_message:
            return jsonify({
                "content": {"messages": [{"text": "⚠️ ไม่พบข้อความจากผู้ใช้"}]}
            }), 200

        records = sheet.get_all_records()  # header: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด
        u = _norm(user_message)

        candidates = []
        for row in records:
            name = str(row.get("ชื่อสินค้า", "")).strip()
            link = str(row.get("คำตอบ", "")).strip()  # คำตอบ = ลิงก์
            kws = [k.strip() for k in str(row.get("คีย์เวิร์ด", "")).split(",") if k.strip()]
            kws_plus = list(set(kws + ([name] if name else [])))

            best = 0.0
            direct_hit = False
            for kw in kws_plus:
                if not kw:
                    continue
                if _norm(kw) in u or kw.lower() in user_message.lower():
                    direct_hit = True
                    best = 1.0
                    break
                score = SequenceMatcher(None, u, _norm(kw)).ratio()
                if score > best:
                    best = score

            if direct_hit or best >= 0.72:
                candidates.append({"name": name, "link": link, "score": best})

        if len(candidates) == 0:
            reply_text = _gpt_followup(user_message)

        elif len(candidates) == 1:
            c = candidates[0]
            pname, link = c["name"], c["link"]
            if link:
                reply_text = f"ได้เลยครับ 🙌 สนใจ {pname} สั่งซื้อได้ที่นี่เลยครับ 👉 {link}"
            else:
                reply_text = f"สนใจ {pname} ใช่ไหมครับ 😊 เดี๋ยวผมส่งรายละเอียดเพิ่มให้นะครับ"

        elif 2 <= len(candidates) <= 4:
            replies = [f"- {c['name']} 👉 {c['link']}" for c in candidates if c["link"]]
            reply_text = (
                "เกี่ยวกับสินค้าที่คุณพิมพ์มา ตอนนี้เรามีหลายแบบเลยครับ 😊\n"
                + "\n".join(replies) +
                "\n\nเลือกอันที่สนใจแล้วกดลิงก์สั่งซื้อได้เลยครับ 🙏"
            )

        else:
            candidates.sort(key=lambda x: x["score"], reverse=True)
            names = [c["name"] for c in candidates[:5]]
            reply_text = (
                "ตอนนี้เรามีหลายสินค้าที่ใกล้เคียงเลยครับ 😊\n"
                + "\n".join([f"- {n}" for n in names]) +
                "\n\nรบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้ง แล้วผมจะส่งลิงก์ให้ครับ 🙏"
            )

        return jsonify({
            "content": {"messages": [{"text": reply_text}]}
        }), 200

    except Exception as e:
        # 🔒 ลูกค้าไม่เห็น error จริง
        return jsonify({
            "content": {
                "messages": [
                    {"text": "ระบบกำลังปรับปรุงชั่วคราวครับ 😅 รบกวนลองใหม่อีกครั้งภายหลังนะครับ 🙏"}
                ]
            }
        }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
