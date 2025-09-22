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
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # header: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด

# --- Utils ---
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()

def clean_text(s: str) -> str:
    """ลบ Markdown [xxx](url) → เหลือ xxx url"""
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 \2", str(s))

def _gpt_followup(user_message: str) -> str:
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
คุณคือพนักงานขายออนไลน์ 
- ตอบสุภาพ กระชับ 1–2 ประโยค 
- ชวนลูกค้าบอกชื่อสินค้าที่สนใจ 
- ใส่อีโมจิเล็กน้อย
ตัวอย่างสินค้า: ไฟเซ็นเซอร์, หม้อหุงข้าว, ปลั๊กไฟ
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือผู้ช่วยขายออนไลน์ สุภาพ อ่อนโยน ชวนลูกค้าบอกชื่อสินค้า"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8,
            max_tokens=120
        )
        return clean_text(resp.choices[0].message.content.strip())
    except Exception:
        return "คุณสนใจสินค้าไหนครับ 😊 บอกชื่อสินค้าได้เลยครับ"

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
                candidates.append({"name": name or (kws_plus[0] if kws_plus else "สินค้านี้"),
                                   "link": link, "score": best})

        if len(candidates) == 0:
            reply_text = _gpt_followup(user_message)

        elif 1 <= len(candidates) <= 3:
            msgs = []
            for c in candidates:
                pname = c["name"]
                link = c["link"] or ""
                if link:
                    msgs.append(f"ได้เลยครับ 🙌 {pname} 👉 {link}")
                else:
                    msgs.append(f"สนใจ {pname} ใช่ไหมครับ 😊")
            reply_text = "\n\n".join(msgs)

        else:  # มากกว่า 3 ชิ้น
            candidates.sort(key=lambda x: x["score"], reverse=True)
            names = [c["name"] for c in candidates[:5]]
            bullet = "\n".join([f"- {n}" for n in names])
            reply_text = (
                "เกี่ยวกับคำนี้ เรามีหลายตัวเลือกครับ 😊\n"
                f"{bullet}\n\n"
                "ช่วยพิมพ์ชื่อสินค้าที่คุณสนใจ แล้วผมจะส่งลิงก์ให้เลยครับ 🙏"
            )

        reply_text = clean_text(reply_text)

        return jsonify({
            "content": {"messages": [{"text": reply_text}]}
        }), 200

    except Exception as e:
        return jsonify({
            "content": {"messages": [{"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}]}
        }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
