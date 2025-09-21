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
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # ต้องมีชีทชื่อ FAQ

@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running with Google Sheets", 200


@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({
                "content": {
                    "messages": [{"text": "⚠️ ไม่พบข้อความจากผู้ใช้"}]
                }
            }), 200

        # ดึงข้อมูลจากชีท (3 คอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด)
        records = sheet.get_all_records()

        matched_row = None

        # หาคีย์เวิร์ด (รองรับหลายคำคั่นด้วย ,)
        for row in records:
            keywords = str(row["คีย์เวิร์ด"]).replace(" ", "").split(",")
            for kw in keywords:
                if kw and kw in user_message:
                    matched_row = row
                    break
            if matched_row:
                break

        if matched_row:
            product_name = matched_row["ชื่อสินค้า"]
            product_link = matched_row["คำตอบ"]

            prompt = f"""
            ลูกค้าถาม: {user_message}
            สินค้าที่ลูกค้าน่าจะสนใจ: {product_name}
            ลิ้งสั่งซื้อ: {product_link}

            👉 ตอบลูกค้าอย่างสุภาพ เป็นกันเอง ใส่อีโมจินิดหน่อย 😊 
            แทรกลิ้งสั่งซื้อแบบเป็นธรรมชาติ ไม่แข็งทื่อ
            """

            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "คุณคือผู้ช่วยร้านค้า พูดสุภาพ อธิบายง่าย ใส่อีโมจิเล็กน้อย"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=300
            )
            reply_text = resp.choices[0].message.content.strip()

        else:
            # ถ้ายังจับคีย์เวิร์ดไม่ได้
            reply_text = "คุณสนใจสินค้าไหนครับ 😊 เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ?"

        # ✅ ส่งกลับแบบ ManyChat
        return jsonify({
            "content": {
                "messages": [
                    {"text": reply_text}
                ]
            }
        }), 200

    except Exception as e:
        return jsonify({
            "content": {
                "messages": [
                    {"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}
                ]
            }
        }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
