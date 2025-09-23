from flask import Flask, request, jsonify
import os
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import openai

app = Flask(__name__)

# ==== OpenAI (ใช้ไลบรารีเวอร์ชัน 0.28.0) ====
openai.api_key = os.getenv("OPENAI_API_KEY")

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # ต้องมีหัวคอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด


# ---------- Utils ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _rewrite_with_gpt(user_message: str, base_reply: str = "") -> str:
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
คำตอบดิบจากระบบ: "{base_reply}"

ช่วยเขียนคำตอบใหม่ให้เหมือนพนักงานขายออนไลน์:
- ตอบสั้น กระชับ 1–3 ประโยค
- สุภาพ เป็นกันเอง มีอีโมจิเล็กน้อย
- ย้ำให้ลูกค้ากดสั่งซื้อ/ดูรายละเอียดที่ "ลิงก์" เท่านั้น
- ห้ามพิมพ์ [] หรือ {{}}
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพ อ่อนโยน ช่วยลูกค้าซื้อผ่านลิงก์"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=200
        )
        text = resp["choices"][0]["message"]["content"].strip()
        text = re.sub(r"[\{\}\[\]]", "", text)
        return text
    except Exception:
        return base_reply or "รบกวนช่วยบอกชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ 🙏"


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


# ---------- Routes ----------
@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running with Google Sheets", 200


@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()
        if not user_message:
            return jsonify({"content": {"messages": [{"text": "⚠️ ไม่พบข้อความจากผู้ใช้"}]}}), 200

        # ===== 1) กันคำต้องห้าม =====
        restricted_patterns = [
            r"ราคา", r"เท่า", r"กี่บาท",
            r"ปลายทาง", r"เก็บเงินปลายทาง",
            r"สั่ง(ซื้อ)?", r"ซื้อได้ไหม", r"อยากได้"
        ]
        if any(re.search(p, user_message) for p in restricted_patterns):
            reply_text = "ขออภัยครับ 🙏 ตอนนี้การสั่งซื้อสามารถทำได้ผ่านลิงก์เท่านั้นครับ 👉 กรุณากดที่ลิงก์เพื่อสั่งซื้อ"
            return jsonify({"content": {"messages": [{"text": reply_text}]}}), 200

        # ===== 2) โหลดชีท =====
        try:
            records = sheet.get_all_records()
        except Exception:
            fallback = "ตอนนี้ระบบช้าชั่วคราวครับ 🙏 รบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ"
            return jsonify({"content": {"messages": [{"text": fallback}]}}), 200

        # ===== 3) Exact match ก่อน =====
        exact_match = None
        u_norm = _norm(user_message)
        for row in records:
            if _norm(row.get("ชื่อสินค้า", "")) == u_norm:
                exact_match = {"name": row.get("ชื่อสินค้า", ""), "link": row.get("คำตอบ", "")}
                break

        if exact_match:
            reply_text = f"สำหรับ {exact_match['name']} ดูรายละเอียดและสั่งซื้อได้เลยครับ 👉 {exact_match['link']}"
        else:
            # ===== 4) Fuzzy match =====
            candidates = _find_candidates(user_message, records, threshold=0.70)

            if len(candidates) == 0:
                reply_text = _rewrite_with_gpt(user_message)

            elif len(candidates) <= 5:
                items = "\n".join([
                    f"- {c['name']} 👉 {c['link']}" if c['link'] else f"- {c['name']}"
                    for c in candidates
                ])
                reply_text = (
                    "เกี่ยวกับคำนี้มีหลายสินค้าที่เกี่ยวข้องครับ 😊\n"
                    f"{items}\n\n"
                    "กดลิงก์เพื่อดูรายละเอียดและสั่งซื้อได้เลยครับ 🙏"
                )
            else:
                names = "\n".join([f"- {c['name']}" for c in candidates[:5]])
                reply_text = (
                    "เกี่ยวกับคำนี้มีหลายตัวเลือกเลยครับ 😊\n"
                    f"{names}\n\n"
                    "ช่วยพิมพ์ชื่อสินค้าที่ต้องการอีกครั้ง เดี๋ยวผมส่งลิงก์ให้ครับ 🙏"
                )

        reply_text = re.sub(r"[\{\}\[\]]", "", reply_text)

        return jsonify({"content": {"messages": [{"text": reply_text}]}}), 200

    except Exception as e:
        safe_text = "ขออภัยครับ ระบบติดขัดเล็กน้อย 🙏 รบกวนพิมพ์ชื่อสินค้าที่สนใจอีกครั้งได้ไหมครับ"
        return jsonify({"content": {"messages": [{"text": safe_text}]}}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
