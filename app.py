from flask import Flask, request, jsonify
from openai import OpenAI
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# ==== OpenAI Client ====
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ==== Google Sheets API ====
SERVICE_ACCOUNT_FILE = "credentials.json"  # ไฟล์ service account
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")  # ตั้งใน Render หรือ local env
SHEET_NAME = "FAQ"

service = build("sheets", "v4", credentials=creds)
sheet = service.spreadsheets()

def get_faq_records():
    """ดึงข้อมูลทั้งหมดจากชีท FAQ"""
    result = sheet.values().get(
        spreadsheetId=SHEET_ID,
        range=SHEET_NAME
    ).execute()

    values = result.get("values", [])
    if not values or len(values) < 2:
        return []

    headers = values[0]
    records = [dict(zip(headers, row)) for row in values[1:]]
    return records


@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running with Google Sheets API", 200


@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({
                "content": {"messages": [{"text": "⚠️ ไม่พบข้อความจากผู้ใช้"}]}
            }), 200

        # ดึงข้อมูลจาก Google Sheets
        records = get_faq_records()

        matched = []
        for row in records:
            keywords = str(row.get("คีย์เวิร์ด", "")).replace(" ", "").split(",")
            for kw in keywords:
                if kw and kw in user_message:
                    matched.append(row)
                    break

        if matched:
            # ถ้าเจอสินค้าเดียว → ส่งคำตอบเลย
            if len(matched) == 1:
                product = matched[0]["ชื่อสินค้า"]
                answer = matched[0]["คำตอบ"]
                reply_text = f"ได้เลยครับ 🙌 สั่งซื้อ {product} ได้ที่นี่เลย 👉 {answer}"

            else:
                # ถ้ามีหลายสินค้า → ให้ลูกค้าเลือก
                product_list = "\n".join([f"- {m['ชื่อสินค้า']}" for m in matched])
                reply_text = f"สินค้าที่เกี่ยวข้องกับคำค้นนี้มีหลายรายการครับ 😊\n{product_list}\n\nกรุณาพิมพ์ชื่อเต็มของสินค้าที่คุณสนใจครับ 🙏"

        else:
            # ถ้าไม่เจอคีย์เวิร์ด
            reply_text = "คุณสนใจสินค้าไหนครับ 😊 เช่น ปลั๊กไฟติดผนัง หรือ โจ๊กถุง?"

        # ✅ ส่งกลับไป ManyChat
        return jsonify({
            "content": {"messages": [{"text": reply_text}]}
        }), 200

    except Exception as e:
        return jsonify({
            "content": {"messages": [{"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}]}
        }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
