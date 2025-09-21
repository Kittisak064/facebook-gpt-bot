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


def _norm(s: str) -> str:
    """normalize string → lowercase + ลบช่องว่าง"""
    return re.sub(r"\s+", "", str(s)).lower()


def _gpt_reply(user_message, candidates):
    """ให้ GPT แต่งประโยคตอบตามสถานการณ์"""
    if len(candidates) == 0:
        prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
งานของคุณ: เป็นแอดมินร้านค้าออนไลน์ 
- ตอบสั้น ๆ (1–2 ประโยค)
- ชวนลูกค้าบอกชื่อสินค้าที่สนใจ
- สุภาพ เป็นกันเอง ใส่อีโมจินิดหน่อย
"""
    elif len(candidates) == 1:
        c = candidates[0]
        prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
สินค้าเจอ: {c['name']} (ลิงก์: {c['link']})
งานของคุณ: ตอบสุภาพ อ่อนโยน ใส่อีโมจิ และส่งลิงก์ให้ด้วย
"""
    elif 2 <= len(candidates) <= 4:
        items = "\n".join([f"- {c['name']} 👉 {c['link']}" for c in candidates])
        prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
เจอสินค้าที่เกี่ยวข้องหลายชิ้น:
{items}
งานของคุณ: อธิบายสั้น ๆ ว่ามีหลายตัวเลือก ให้ลูกค้าเลือกเองได้เลย
"""
    else:
        names = [c["name"] for c in candidates[:6]]
        bullet = "\n".join([f"- {n}" for n in names])
        prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
เจอสินค้าหลายแบบ:
{bullet}
งานของคุณ: ตอบสุภาพว่ามีหลายแบบ ให้ลูกค้าพิมพ์ชื่อเต็มหรือใกล้เคียง 
"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพ อ่อนโยน ตอบเหมือนคนจริง 😊"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        max_tokens=250
    )
    return resp.choices[0].message.content.strip()


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

        # 📊 โหลดข้อมูลจากชีท
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
                candidates.append({"name": name or "สินค้านี้", "link": link, "score": best})

        # ✅ ใช้ GPT แต่งคำตอบ
        reply_text = _gpt_reply(user_message, candidates)

        return jsonify({
            "content": {"messages": [{"text": reply_text}]}
        }), 200

    except Exception as e:
        return jsonify({
            "content": {"messages": [{"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}]}
        }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
