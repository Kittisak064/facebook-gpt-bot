from flask import Flask, request, jsonify
from openai import OpenAI
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from fuzzywuzzy import fuzz
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
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # ต้องมีชีทชื่อ FAQ


@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running with Google Sheets", 200


@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip().lower()

        if not user_message:
            return jsonify({
                "content": {"messages": [{"text": "⚠️ ไม่พบข้อความจากผู้ใช้"}]}
            }), 200

        # ===== 1) ดักคำต้องห้าม (ราคา/เก็บปลายทาง/สั่งในแชท) =====
        restricted_patterns = [
            r"ราคา", r"เท่า", r"กี่บาท",
            r"ปลายทาง", r"เก็บเงินปลายทาง",
            r"สั่ง(ซื้อ)?", r"ซื้อได้ไหม", r"อยากได้"
        ]
        if any(re.search(p, user_message) for p in restricted_patterns):
            reply_text = "ขออภัยครับ 🙏 ตอนนี้การสั่งซื้อสามารถทำได้ผ่านลิงก์เท่านั้นครับ 👉 กรุณากดที่ลิงก์เพื่อสั่งซื้อ"
            return jsonify({
                "content": {"messages": [{"text": reply_text}]}
            }), 200

        # ===== 2) ค้นหาจาก Google Sheet =====
        records = sheet.get_all_records()
        matched = []

        for row in records:
            name = row.get("ชื่อสินค้า", "").strip()
            answer = row.get("คำตอบ", "").strip()
            keywords = str(row.get("คีย์เวิร์ด", "")).split(",")
            for kw in keywords:
                kw = kw.strip()
                if not kw:
                    continue
                if kw in user_message or fuzz.partial_ratio(kw, user_message) > 80:
                    matched.append({"name": name, "answer": answer})
                    break

        # ===== 3) ตัดสินใจ =====
        if len(matched) == 0:
            # ไม่เจอ → ให้ GPT แต่งประโยคสุภาพชวนบอกสินค้า
            prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
งานของคุณ: เป็นพนักงานขายออนไลน์ ตอบสั้น ๆ (1-2 ประโยค) 
- ชวนลูกค้าบอกชื่อสินค้าที่สนใจ
- สุภาพ ใส่อีโมจิเล็กน้อย
- ตัวอย่างสินค้า: ไฟเซ็นเซอร์, หม้อหุงข้าว, ปลั๊กไฟ
- ห้ามใส่วงเล็บ [] หรือ {{}}
"""
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพและเป็นกันเอง"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.8,
                max_tokens=120
            )
            reply_text = resp.choices[0].message.content.strip()

        elif len(matched) == 1:
            # มีสินค้าเดียว → ส่งลิงก์เลย
            product = matched[0]
            reply_text = f"ได้เลยครับ 🙌 สั่งซื้อ {product['name']} ได้ที่นี่ครับ 👉 {product['answer']}"

        elif 2 <= len(matched) <= 4:
            # 2-4 สินค้า → ส่งลิสต์พร้อมลิงก์
            items = "\n".join([f"- {m['name']} 👉 {m['answer']}" for m in matched])
            reply_text = f"เราเจอสินค้าที่เกี่ยวข้องครับ 👇\n{items}"

        else:
            # มากกว่า 4 → ให้ลูกค้าเลือก
            names = "\n".join([f"- {m['name']}" for m in matched[:5]])
            reply_text = (
                "เกี่ยวกับคำนี้ เรามีหลายสินค้าเลยครับ 😊\n"
                f"{names}\n\n"
                "รบกวนพิมพ์ชื่อสินค้าที่ต้องการ เดี๋ยวผมส่งลิงก์ให้ครับ 🙏"
            )

        # ===== 4) ส่งกลับแบบ ManyChat =====
        return jsonify({
            "content": {
                "messages": [{"text": reply_text}]
            }
        }), 200

    except Exception as e:
        return jsonify({
            "content": {
                "messages": [{"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}]
            }
        }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
