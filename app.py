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
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # คอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด

def _norm(s: str) -> str:
    # normalize เพื่อให้จับใกล้เคียงดีขึ้น (ตัดช่องว่าง/ตัวพิเศษ + ตัวพิมพ์เล็ก)
    return re.sub(r"\s+", "", str(s)).lower()

def _gpt_followup(user_message: str) -> str:
    # ถ้าไม่เจอคีย์เวิร์ด ให้ GPT แต่งประโยคชวนให้บอกชื่อสินค้า (ไม่พูดซ้ำ)
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
งานของคุณ: เป็นพนักงานขายร้านค้าออนไลน์ ตอบกลับสั้น ๆ (1-2 ประโยค)
- ชวนให้ลูกค้าบอกชื่อสินค้าที่สนใจ
- สุภาพ เป็นกันเอง ใส่อีโมจิเล็กน้อย
- ตัวอย่างสินค้า: ไฟเซ็นเซอร์, หม้อหุงข้าว, ปลั๊กไฟ
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพ เป็นกันเอง กระตุ้นให้ลูกค้าพิมพ์ชื่อสินค้า"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8,
            max_tokens=120
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "คุณสนใจสินค้าไหนครับ 😊 บอกชื่อสินค้าได้ไหมครับ"

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

        records = sheet.get_all_records()  # ต้องมี header: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด
        u = _norm(user_message)

        # สแกนหา match (ตรง/ใกล้เคียง) ต่อ 1 แถวสินค้า
        candidates = []  # [{"name": str, "link": str, "score": float}]
        for row in records:
            name = str(row.get("ชื่อสินค้า", "")).strip()
            link = str(row.get("คำตอบ", "")).strip()
            kw_raw = str(row.get("คีย์เวิร์ด", ""))
            kws = [k.strip() for k in kw_raw.split(",") if k.strip()]
            # รวม "ชื่อสินค้า" เข้าไปเป็นคีย์เวิร์ดด้วย เพื่อให้ลูกค้าพิมพ์ชื่อเต็มแล้วเจอ
            kws_plus = list(set(kws + ([name] if name else [])))

            best = 0.0
            direct_hit = False
            for kw in kws_plus:
                if not kw:
                    continue
                # ตรงแบบ contains ก่อน (แรงสุด)
                if _norm(kw) in u or kw.lower() in user_message.lower():
                    direct_hit = True
                    best = 1.0
                    break
                # ไม่ตรง → วัดใกล้เคียง
                score = SequenceMatcher(None, u, _norm(kw)).ratio()
                if score > best:
                    best = score

            # เกณฑ์ผ่าน: ตรงเลย หรือ ใกล้เคียง >= 0.72 (ปรับได้)
            if direct_hit or best >= 0.72:
                candidates.append({"name": name or (kws_plus[0] if kws_plus else "สินค้านี้"),
                                   "link": link, "score": best})

        # ตัดสินใจจาก candidates
        if len(candidates) == 0:
            # ไม่เจออะไรเลย → ให้ GPT ชวนบอกชื่อสินค้า
            reply_text = _gpt_followup(user_message)

        elif len(candidates) == 1:
            # เจอชัดเจน 1 ชิ้น → ส่งลิงก์ด้วยประโยคสุภาพ
            c = candidates[0]
            pname = c["name"]
            link = c["link"] or ""
            if not link:
                reply_text = f"สนใจ {pname} ใช่ไหมครับ 😊 เดี๋ยวผมส่งรายละเอียดให้เพิ่มเติมนะครับ"
            else:
                reply_text = f"ได้เลยครับ 🙌 สั่งซื้อ {pname} ได้ที่นี่เลยครับ 👉 {link}"

        else:
            # เจอหลายชิ้น → ให้ลูกค้าเลือกชื่อสินค้า (พิมพ์ชื่อเต็มหรือใกล้เคียง)
            # เรียงตาม score มาก → น้อย แล้วโชว์ top 5 พอ
            candidates.sort(key=lambda x: x["score"], reverse=True)
            names = [c["name"] for c in candidates[:5]]
            bullet = "\n".join([f"- {n}" for n in names])
            reply_text = (
                "เกี่ยวกับคำนี้ เรามีหลายตัวเลือกครับ 😊\n"
                f"{bullet}\n\n"
                "พิมพ์ชื่อสินค้า (เต็มหรือใกล้เคียง) ที่ต้องการ แล้วผมจะส่งลิงก์ให้เลยครับ 🙏"
            )

        # ส่งกลับรูปแบบ ManyChat
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
