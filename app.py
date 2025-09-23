from flask import Flask, request, jsonify
import os
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import openai

app = Flask(__name__)

# ==== OpenAI ====
openai.api_key = os.getenv("OPENAI_API_KEY")

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")  
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  
# หัวคอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด


# ---------- Utils ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _rewrite_with_gpt(user_message: str, base_reply: str) -> str:
    """
    ใช้ GPT เฉพาะตอนที่ไม่เจอสินค้า หรือถามกว้างๆ
    """
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
ระบบตอบ: "{base_reply}"

ช่วยเขียนคำตอบใหม่ให้เหมือนพนักงานขายออนไลน์:
- ตอบสุภาพ สั้น กระชับ
- ใช้ภาษาง่าย อ่านเข้าใจง่ายสำหรับผู้สูงอายุ
- มีอีโมจิเล็กน้อย
- ห้ามเขียนลิงก์มั่ว ต้องคงข้อความลิงก์ไว้ถ้ามี
- ห้ามพิมพ์สัญลักษณ์ {{}} หรือ []
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ที่ช่วยลูกค้าด้วยความสุภาพและเป็นกันเอง"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            max_tokens=200
        )
        text = resp["choices"][0]["message"]["content"].strip()
        text = re.sub(r"[\{\}\[\]]", "", text)  # กันเผื่อ GPT ใส่ { } []
        return text
    except Exception:
        return base_reply


def _find_candidates(user_message: str, records, threshold: float = 0.70):
    """
    หาสินค้าที่ตรง/ใกล้เคียงจากคีย์เวิร์ด + ชื่อสินค้า
    """
    u = _norm(user_message)
    candidates = []
    seen = set()

    for row in records:
        name = str(row.get("ชื่อสินค้า", "")).strip()
        link = str(row.get("คำตอบ", "")).strip()
        kw_raw = str(row.get("คีย์เวิร์ด", ""))
        kws = [k.strip() for k in kw_raw.split(",") if k.strip()]

        kws_plus = list(set(kws + ([name] if name else [])))

        best = 0.0
        direct_hit = False
        for kw in kws_plus:
            kw_norm = _norm(kw)
            if not kw_norm:
                continue

            if kw_norm in u or kw_norm == u:
                best = 1.0
                direct_hit = True
                break

            sc = _similar(u, kw_norm)
            if sc > best:
                best = sc

        if direct_hit or best >= threshold:
            key = name or link
            if key and key not in seen:
                seen.add(key)
                candidates.append({"name": name or "สินค้า", "link": link, "score": best})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


# ---------- Routes ----------
@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running", 200


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
            fallback = "ระบบช้าชั่วคราวครับ 🙏 กรุณาพิมพ์ชื่อสินค้าที่สนใจอีกครั้ง เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"
            return jsonify({"content": {"messages": [{"text": fallback}]}}), 200

        candidates = _find_candidates(user_message, records, threshold=0.70)

        # ========== ตัดสินใจ ==========
        if len(candidates) == 0:
            base = "รบกวนช่วยบอกชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"
            reply_text = _rewrite_with_gpt(user_message, base)

        elif len(candidates) == 1:
            c = candidates[0]
            reply_text = f"สำหรับ {c['name']} สามารถดูรายละเอียดและสั่งซื้อได้ที่นี่ครับ 👉 {c['link']}"

        elif 2 <= len(candidates) <= 5:
            items = "\n".join([
                f"- {c['name']} 👉 {c['link']}" if c['link'] else f"- {c['name']}"
                for c in candidates
            ])
            reply_text = (
                f"เกี่ยวกับคำนี้ มีสินค้าที่เกี่ยวข้องดังนี้ครับ 👇\n{items}\n\n"
                f"กดลิงก์เพื่อดูรายละเอียดหรือสั่งซื้อได้เลยครับ 🙏"
            )

        else:
            names = "\n".join([f"- {c['name']}" for c in candidates[:5]])
            reply_text = (
                "เกี่ยวกับคำนี้มีหลายตัวเลือกครับ 😊\n"
                f"{names}\n\n"
                "ช่วยพิมพ์ชื่อสินค้าที่ต้องการอีกครั้ง เดี๋ยวผมส่งลิงก์ให้ทันทีครับ 🙏"
            )

        reply_text = re.sub(r"[\{\}\[\]]", "", reply_text)

        return jsonify({"content": {"messages": [{"text": reply_text}]}}), 200

    except Exception:
        safe_text = "ขออภัยครับ ระบบติดขัดเล็กน้อย 🙏 กรุณาพิมพ์ชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ"
        return jsonify({"content": {"messages": [{"text": safe_text}]}}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
