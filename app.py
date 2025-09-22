from flask import Flask, request, jsonify
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import re
import openai

app = Flask(__name__)

# ==== OpenAI Client ====
openai.api_key = os.getenv("OPENAI_API_KEY")

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # คอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()

def _gpt_polish(user_message: str, raw_reply: str) -> str:
    """ให้ GPT ทำให้คำตอบสุภาพ/เป็นธรรมชาติ"""
    try:
        prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
ร่างคำตอบ: "{raw_reply}"

หน้าที่ของคุณ:
- ปรับร่างคำตอบให้สุภาพ อ่อนโยน เป็นกันเอง
- เน้นให้ลูกค้าคลิกลิงก์สั่งซื้อเท่านั้น ไม่ต้องอธิบายรายละเอียดสินค้าเยอะ
- ถ้าลูกค้าพยายามถามราคา, เก็บปลายทาง หรือสั่งในแชท → ให้ตอบสุภาพๆ ว่าต้องสั่งผ่านลิงก์เท่านั้น
"""
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ สุภาพ เป็นกันเอง"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            max_tokens=200
        )
        return resp.choices[0].message["content"].strip()
    except Exception:
        return raw_reply

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

        candidates = []  # [{"name": str, "link": str, "score": float}]
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
                candidates.append({"name": name or "สินค้านี้", "link": link, "score": best})

        reply_text = ""
        if len(candidates) == 0:
            reply_text = "คุณสนใจสินค้าไหนครับ 😊 บอกชื่อสินค้ามาได้เลย เดี๋ยวผมส่งลิงก์ให้ครับ"
        elif len(candidates) == 1:
            c = candidates[0]
            pname, link = c["name"], c["link"]
            if link:
                reply_text = f"ได้เลยครับ 🙌 สั่งซื้อ {pname} ได้ที่นี่ครับ 👉 {link}"
            else:
                reply_text = f"สนใจ {pname} ใช่ไหมครับ 😊 เดี๋ยวผมเช็กข้อมูลให้นะครับ"
        elif 2 <= len(candidates) <= 4:
            parts = []
            for c in candidates:
                pname, link = c["name"], c["link"]
                if link:
                    parts.append(f"👉 {pname}: {link}")
            reply_text = "เจอสินค้าที่เกี่ยวข้องหลายรายการครับ 🙏\n" + "\n".join(parts)
        else:
            names = [c["name"] for c in sorted(candidates, key=lambda x: x["score"], reverse=True)[:5]]
            bullet = "\n".join([f"- {n}" for n in names])
            reply_text = (
                "เกี่ยวกับคำนี้เรามีสินค้าหลายตัวเลยครับ 😊\n"
                f"{bullet}\n\n"
                "ช่วยพิมพ์ชื่อเต็มของสินค้าที่ต้องการ เดี๋ยวผมส่งลิงก์ให้ครับ 🙏"
            )

        polished = _gpt_polish(user_message, reply_text)

        return jsonify({
            "content": {"messages": [{"text": polished}]}
        }), 200

    except Exception as e:
        return jsonify({
            "content": {"messages": [{"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}]}
        }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
