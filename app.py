from flask import Flask, request, jsonify
import openai
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher
import re

app = Flask(__name__)

# ==== OpenAI Client ====
openai.api_key = os.getenv("OPENAI_API_KEY")

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

# ใช้ SHEET_ID จาก Environment Variable
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # ต้องมีชีทชื่อ FAQ

# -------------------------------
# Helper
# -------------------------------
def _norm(s: str) -> str:
    """Normalize string: lowercase + remove spaces/symbols"""
    return re.sub(r"\s+", "", str(s)).lower()

def _gpt_followup(user_message: str) -> str:
    """ถ้าไม่เจอสินค้า ให้ GPT ช่วยแต่งประโยคชวน"""
    prompt = f"""
ลูกค้าพิมพ์: "{user_message}"
งานของคุณ: เป็นพนักงานขายร้านค้าออนไลน์ 
- ตอบกลับสั้น ๆ (1-2 ประโยค) 
- ชวนให้ลูกค้าบอกชื่อสินค้าที่สนใจ 
- สุภาพ อ่อนโยน เป็นกันเอง ใส่อีโมจิเล็กน้อย
- ตัวอย่างสินค้า: ไฟเซ็นเซอร์, หม้อหุงข้าว, ปลั๊กไฟ
"""
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "คุณคือพนักงานขายออนไลน์ พูดสุภาพ อ่อนโยน และช่วยกระตุ้นให้ลูกค้าพิมพ์ชื่อสินค้า"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8,
            max_tokens=150
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "คุณสนใจสินค้าไหนครับ 😊 บอกชื่อสินค้าได้เลยครับ"

# -------------------------------
# Routes
# -------------------------------
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

        # โหลดข้อมูลจากชีท (คอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด)
        records = sheet.get_all_records()
        u = _norm(user_message)

        candidates = []  # [{"name": str, "answer": str, "score": float}]
        for row in records:
            name = str(row.get("ชื่อสินค้า", "")).strip()
            answer = str(row.get("คำตอบ", "")).strip()
            kw_raw = str(row.get("คีย์เวิร์ด", ""))
            kws = [k.strip() for k in kw_raw.split(",") if k.strip()]

            # รวมชื่อสินค้าเข้าไปในคีย์เวิร์ด
            kws_plus = list(set(kws + ([name] if name else [])))

            best = 0.0
            direct_hit = False
            for kw in kws_plus:
                if not kw:
                    continue
                # ตรงเป๊ะ
                if _norm(kw) in u:
                    direct_hit = True
                    best = 1.0
                    break
                # ถ้าไม่ตรง → วัดความใกล้เคียง
                score = SequenceMatcher(None, u, _norm(kw)).ratio()
                if score > best:
                    best = score

            if direct_hit or best >= 0.72:
                candidates.append({"name": name, "answer": answer, "score": best})

        # -------------------------------
        # ตัดสินใจตอบ
        # -------------------------------
        if len(candidates) == 0:
            reply_text = _gpt_followup(user_message)

        elif len(candidates) == 1:
            c = candidates[0]
            if c["answer"].startswith("http"):
                reply_text = f"ได้เลยครับ 🙌 สั่งซื้อ {c['name']} ได้ที่นี่เลยครับ 👉 {c['answer']}"
            else:
                reply_text = f"สำหรับ {c['name']} นะครับ 😊\n{c['answer']}"

        elif 2 <= len(candidates) <= 4:
            # ส่งลิงก์ของทุกสินค้าที่ตรง
            lines = []
            for c in candidates:
                if c["answer"].startswith("http"):
                    lines.append(f"- {c['name']} 👉 {c['answer']}")
                else:
                    lines.append(f"- {c['name']} : {c['answer']}")
            reply_text = "ผมเจอสินค้าที่เกี่ยวข้องหลายรายการครับ 👇\n" + "\n".join(lines)

        else:
            # ถ้ามากกว่า 4 ตัว ให้ลูกค้าเลือกเอง
            candidates.sort(key=lambda x: x["score"], reverse=True)
            names = [c["name"] for c in candidates[:6]]
            bullet = "\n".join([f"- {n}" for n in names])
            reply_text = (
                "เกี่ยวกับคำนี้ เรามีหลายสินค้าเลยครับ 😊\n"
                f"{bullet}\n\n"
                "ช่วยพิมพ์ชื่อสินค้าที่สนใจเพิ่มเติมหน่อยนะครับ 🙏"
            )

        return jsonify({
            "content": {"messages": [{"text": reply_text}]}
        }), 200

    except Exception as e:
        return jsonify({
            "content": {"messages": [{"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}]}
        }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
