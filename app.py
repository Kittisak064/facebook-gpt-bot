from flask import Flask, request, jsonify
import openai
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import re

app = Flask(__name__)

# ==== OpenAI Client (ใหม่) ====
openai.api_key = os.getenv("OPENAI_API_KEY")


# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

# ใช้ SHEET_ID จาก Environment Variable
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # header: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด


def _norm(s: str) -> str:
    """Normalize ข้อความ"""
    return re.sub(r"\s+", "", str(s)).lower()


def _gpt_message(user_message: str, context: str) -> str:
    """ให้ GPT ช่วยแต่งคำตอบสุภาพ"""
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
บริบท: {context}

ตอบกลับสั้น ๆ 1-3 ประโยค
- พูดสุภาพ เป็นกันเอง ใส่อีโมจิเล็กน้อย
- ถ้าลูกค้าพยายามสั่ง/ถามราคา/เก็บปลายทาง → ย้ำว่าต้องสั่งซื้อผ่านลิ้งเท่านั้น
- ถ้าเจอหลายสินค้า → สรุปรายการให้เลือก
- ถ้าเจอ 1 สินค้า → ส่งลิ้งในคำตอบได้เลย
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ ตอบสุภาพ เป็นกันเอง"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            max_tokens=200
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return context  # fallback


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

        records = sheet.get_all_records()
        u = _norm(user_message)

        # --- Step 1: ตรวจสอบว่า user พยายามสั่งตรง / ถามราคา / เก็บปลายทาง ---
        trigger_words = ["สั่ง", "เก็บปลายทาง", "cod", "ราคา", "เท่า", "บาท"]
        if any(t in user_message.lower() for t in trigger_words):
            reply_text = _gpt_message(
                user_message,
                "ตอนนี้ลูกค้าพยายามสั่งซื้อหรือถามเรื่องราคาครับ ให้ตอบว่าสั่งซื้อได้ผ่านลิ้งเท่านั้น แล้วส่งลิ้งถ้ามีสินค้าเจอ"
            )
            return jsonify({"content": {"messages": [{"text": reply_text}]} }), 200

        # --- Step 2: หาสินค้า match ---
        candidates = []
        for row in records:
            name = str(row.get("ชื่อสินค้า", "")).strip()
            link = str(row.get("คำตอบ", "")).strip()
            kw_raw = str(row.get("คีย์เวิร์ด", ""))
            kws = [k.strip() for k in kw_raw.split(",") if k.strip()]
            kws_plus = list(set(kws + ([name] if name else [])))

            best = 0.0
            direct_hit = False
            for kw in kws_plus:
                if not kw:
                    continue
                if _norm(kw) in u:
                    direct_hit = True
                    best = 1.0
                    break
                score = SequenceMatcher(None, u, _norm(kw)).ratio()
                if score > best:
                    best = score

            if direct_hit or best >= 0.72:
                candidates.append({"name": name, "link": link})

        # --- Step 3: ตัดสินใจตอบ ---
        if len(candidates) == 0:
            # ไม่เจอสินค้าเลย → ชวนสุภาพ
            reply_text = _gpt_message(user_message, "ลูกค้าไม่ได้พิมพ์ตรงกับสินค้า ให้ชวนบอกชื่อสินค้า")

        elif len(candidates) == 1:
            # เจอชัดเจน
            c = candidates[0]
            context = f"สินค้า {c['name']} สั่งซื้อได้ที่ลิ้ง {c['link']}"
            reply_text = _gpt_message(user_message, context)

        elif 2 <= len(candidates) <= 3:
            # เจอ 2-3 ชิ้น → ส่งลิ้งทั้งหมด
            items = "\n".join([f"- {c['name']} 👉 {c['link']}" for c in candidates])
            context = f"พบหลายสินค้า:\n{items}"
            reply_text = _gpt_message(user_message, context)

        else:
            # เจอเกิน 3 → ให้เลือก
            names = "\n".join([f"- {c['name']}" for c in candidates[:5]])
            context = f"สินค้าที่พบมีหลายรายการ:\n{names}\nให้ลูกค้าเลือก 1 ชิ้น"
            reply_text = _gpt_message(user_message, context)

        return jsonify({
            "content": {"messages": [{"text": reply_text}]}
        }), 200

    except Exception as e:
        return jsonify({
            "content": {"messages": [{"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}]}
        }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
