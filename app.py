from flask import Flask, request, jsonify
from openai import OpenAI
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials

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
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")
  # ดึงชีทแรก

@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running with Google Sheets", 200


@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({"reply": "⚠️ ไม่พบข้อความจากผู้ใช้"}), 400

        # ดึงข้อมูลทั้งหมดจากชีท
        records = sheet.get_all_records()

        matched_keyword = None
        faqs = []

        # วนหาคีย์เวิร์ด (รองรับหลายคีย์เวิร์ดใน 1 ช่อง โดยคั่นด้วย , หรือ ช่องว่าง)
        for row in records:
            keywords = str(row["คีย์เวิร์ด"]).replace(" ", "").split(",")
            for kw in keywords:
                if kw and kw in user_message:
                    matched_keyword = kw
                    faqs = [r for r in records if kw in str(r["คีย์เวิร์ด"])]
                    break
            if matched_keyword:
                break

        if matched_keyword and faqs:
            # สร้าง FAQ เฉพาะสินค้านั้น
            faq_text = "\n".join([
                f"Q: {r['คำถาม']} | A: {r['คำตอบ']}" for r in faqs
            ])

            prompt = f"""
            ลูกค้าถาม: {user_message}
            นี่คือ FAQ ของสินค้า [{matched_keyword}]:
            {faq_text}

            ตอบลูกค้าอย่างสุภาพ เป็นกันเอง ใส่อีโมจินิดหน่อย 😊 ใช้ข้อมูลจาก FAQ เท่านั้น
            """

            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "คุณคือผู้ช่วยร้านค้า พูดสุภาพ อธิบายง่าย ใส่อีโมจิเล็กน้อย"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.4,
                max_tokens=400
            )
            reply_text = resp.choices[0].message.content.strip()

        else:
            # ถ้ายังจับคีย์เวิร์ดไม่ได้
            reply_text = "คุณสนใจสินค้าไหนครับ 😊 เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ?"

        return jsonify({"reply": reply_text})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
