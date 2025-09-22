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
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # คอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด


# ---------- Utils ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _find_candidates(user_message: str, records, threshold: float = 0.70):
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


def _rewrite_with_gpt(user_message: str):
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"

ช่วยเขียนคำตอบเหมือนพนักงานขายออนไลน์:
- ตอบสั้น กระชับ 1–2 ประโยค
- สุภาพ อ่านง่าย เหมาะกับผู้สูงอายุ
- มีอีโมจิเล็กน้อย
- ห้ามมี [] หรือ {{}}
- ถ้าไม่เจอสินค้าที่ตรงจริง ๆ ให้ชวนลูกค้าบอกชื่อสินค้า เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว ปลั๊กไฟ
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพและอ่านง่าย"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=120
        )
        text = resp["choices"][0]["message"]["content"].strip()
        text = re.sub(r"[\{\}\[\]]", "", text)
        return text
    except Exception:
        return "รบกวนช่วยบอกชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ 🙏"


# ---------- Routes ----------
@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot with GPT + Google Sheets is running", 200


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
            fallback = "ตอนนี้ระบบช้าชั่วคราวครับ 🙏 รบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้ง เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"
            return jsonify({"content": {"messages": [{"text": fallback}]}}), 200

        candidates = _find_candidates(user_message, records)

        # ========== ตัดสินใจ ==========
        if len(candidates) == 0:
            reply_text = _rewrite_with_gpt(user_message)

        elif len(candidates) <= 5:
            items = "\n".join([f"- {c['name']} 👉 {c['link']}" if c['link'] else f"- {c['name']}" for c in candidates])
            reply_text = (
                f"สวัสดีครับ 😊 เกี่ยวกับคำนี้เรามีสินค้าที่เกี่ยวข้องดังนี้ครับ:\n\n"
                f"{items}\n\n"
                f"กรุณากดที่ลิงก์เพื่อดูรายละเอียดหรือสั่งซื้อได้เลยครับ 🙏"
            )

        else:
            names = "\n".join([f"- {c['name']}" for c in candidates[:5]])
            reply_text = (
                f"เกี่ยวกับคำนี้มีหลายตัวเลือกเลยครับ 😊\n\n"
                f"{names}\n\n"
                f"รบกวนพิมพ์ชื่อสินค้าที่สนใจ เดี๋ยวผมส่งลิงก์ให้ทันทีครับ 🙏"
            )

        reply_text = re.sub(r"[\{\}\[\]]", "", reply_text)

        return jsonify({"content": {"messages": [{"text": reply_text}]}}), 200

    except Exception:
        safe_text = "ขออภัยครับ ระบบติดขัดเล็กน้อย 🙏 รบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ"
        return jsonify({"content": {"messages": [{"text": safe_text}]}}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
