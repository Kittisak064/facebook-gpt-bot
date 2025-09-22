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

# ใช้ SHEET_ID จาก Environment Variable
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # ต้องมีคอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด

# ===== Helper =====
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()

def _gpt_followup(user_message: str) -> str:
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"

หน้าที่คุณ:
- เป็นพนักงานขายออนไลน์ ตอบกลับสุภาพ อ่อนโยน กระชับ
- ถ้าไม่รู้จักสินค้านี้ ให้ชวนลูกค้าบอกชื่อสินค้า เช่น "ไฟเซ็นเซอร์, หม้อหุงข้าว, ปลั๊กไฟ"
- อย่าใส่คำว่า "ร่างคำตอบ" หรือ "ลูกค้าพิมพ์" ในข้อความที่ตอบ
- ใส่อีโมจินิดหน่อยให้ดูเป็นกันเอง
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
        return "คุณสนใจสินค้าไหนครับ 😊 บอกชื่อได้เลย เดี๋ยวผมจะส่งลิงก์ให้ครับ"

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

        # โหลดข้อมูลจากชีท
        records = sheet.get_all_records()  # header: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด
        u = _norm(user_message)

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
                if _norm(kw) in u or kw.lower() in user_message.lower():
                    direct_hit = True
                    best = 1.0
                    break
                score = SequenceMatcher(None, u, _norm(kw)).ratio()
                if score > best:
                    best = score

            if direct_hit or best >= 0.72:
                candidates.append({"name": name or (kws_plus[0] if kws_plus else "สินค้านี้"),
                                   "link": link, "score": best})

        # ===== ตัดสินใจ =====
        if len(candidates) == 0:
            reply_text = _gpt_followup(user_message)

        elif len(candidates) == 1:
            c = candidates[0]
            pname = c["name"]
            link = c["link"]
            if link:
                reply_text = f"ได้เลยครับ 🙌 สั่งซื้อ {pname} ได้ที่นี่เลยครับ 👉 {link}"
            else:
                reply_text = f"สนใจ {pname} ใช่ไหมครับ 😊 เดี๋ยวผมส่งรายละเอียดเพิ่มเติมให้นะครับ"

        elif 2 <= len(candidates) <= 4:
            # ถ้าซ้ำไม่เกิน 4 → ส่งให้หมดเลย
            lines = []
            for c in candidates:
                if c["link"]:
                    lines.append(f"- {c['name']} 👉 {c['link']}")
                else:
                    lines.append(f"- {c['name']}")
            reply_text = (
                "มีหลายสินค้าที่ใกล้เคียงครับ 😊\n" +
                "\n".join(lines) +
                "\n\nเลือกอันที่สนใจได้เลยครับ 🙏"
            )

        else:
            # มากกว่า 4 → ให้ลูกค้าเลือก
            candidates.sort(key=lambda x: x["score"], reverse=True)
            names = [c["name"] for c in candidates[:5]]
            reply_text = (
                "เกี่ยวกับคำนี้ เรามีหลายตัวเลือกครับ 😊\n" +
                "\n".join([f"- {n}" for n in names]) +
                "\n\nช่วยพิมพ์ชื่อเต็มของสินค้าที่ต้องการ แล้วผมจะส่งลิงก์ให้เลยครับ 🙏"
            )

        return jsonify({
            "content": {"messages": [{"text": reply_text}]}
        }), 200

    except Exception:
        # ❌ ไม่ส่ง error detail ออกไปให้ลูกค้าเห็น
        return jsonify({
            "content": {"messages": [{"text": "ระบบขัดข้องชั่วคราวครับ 😅 รบกวนลองใหม่อีกครั้งภายหลังนะครับ 🙏"}]}
        }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
