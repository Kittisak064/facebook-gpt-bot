from flask import Flask, request, jsonify
from openai import OpenAI
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import re

app = Flask(__name__)

# ==== OpenAI Client ====
client = OpenAI()  # ไม่ต้องใส่ api_key ตรงนี้ เพราะ Render อ่านจาก ENV ให้แล้ว

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

# ใช้ SHEET_ID จาก Environment Variable
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # คอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()

def _gpt_followup(user_message: str) -> str:
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
งานของคุณ: เป็นพนักงานขายร้านค้าออนไลน์ ตอบกลับสั้น ๆ (1-2 ประโยค)
- สุภาพ อ่อนโยน ตอบแบบคนจริง
- ชวนให้ลูกค้าบอกชื่อสินค้าที่สนใจ
- เน้นให้ลูกค้าสั่งซื้อผ่านลิ้งเท่านั้น ไม่อธิบายราคา/เก็บปลายทาง
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพ เป็นกันเอง และบอกให้สั่งผ่านลิ้ง"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=150
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "คุณสนใจสินค้าไหนครับ 😊 บอกชื่อสินค้าได้เลยครับ เดี๋ยวผมส่งลิ้งให้ครับ"

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

        candidates = []
        for row in records:
            name = str(row.get("ชื่อสินค้า", "")).strip()
            link = str(row.get("คำตอบ", "")).strip()
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

        elif len(candidates) <= 3:
            replies = []
            for c in candidates:
                pname, link = c["name"], c["link"]
                if link:
                    replies.append(f"ได้เลยครับ 🙌 {pname} 👉 {link}")
                else:
                    replies.append(f"สนใจ {pname} ใช่ไหมครับ 😊 เดี๋ยวส่งรายละเอียดให้ครับ")
            reply_text = "\n\n".join(replies)

        else:
            candidates.sort(key=lambda x: x["score"], reverse=True)
            names = [c["name"] for c in candidates[:5]]
            bullet = "\n".join([f"- {n}" for n in names])
            reply_text = (
                "จากข้อความของคุณ มีสินค้าที่เกี่ยวข้องหลายตัวครับ 👇\n"
                f"{bullet}\n\n"
                "รบกวนพิมพ์ชื่อสินค้าที่สนใจ แล้วผมจะส่งลิ้งให้ครับ 🙏"
            )

        return jsonify({
            "content": {"messages": [{"text": reply_text}]}
        }), 200

    except Exception as e:
        return jsonify({
            "content": {"messages": [{"text": "⚠️ ระบบขัดข้องชั่วคราวครับ กำลังแก้ไข 🙏"}]}
        }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
