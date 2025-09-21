from flask import Flask, request, jsonify
from openai import OpenAI
import os
import gspread
from google.oauth2.service_account import Credentials
from fuzzywuzzy import fuzz  # ใช้สำหรับจับคำใกล้เคียง

app = Flask(__name__)

# ==== OpenAI Client ====
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ==== Google Sheets Auth (ใช้ google-auth แทน oauth2client) ====
scope = ["https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive"]

creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
gs_client = gspread.authorize(creds)

# ใช้ SHEET_ID จาก Environment Variable
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
sheet = gs_client.open_by_key(SHEET_ID).worksheet("FAQ")  # ต้องมีชีทชื่อ FAQ

@app.route("/", methods=["GET"])
def home():
    return "✅ FAQ Bot is running with Google Sheets (auth fixed)", 200


@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({
                "content": {"messages": [{"text": "⚠️ ไม่พบข้อความจากผู้ใช้"}]}
            }), 200

        # ดึงข้อมูลจากชีท (3 คอลัมน์: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด)
        records = sheet.get_all_records()

        matched_products = []

        # 🔍 ตรวจหาคีย์เวิร์ด (รองรับพิมพ์ผิดเล็กน้อย)
        for row in records:
            keywords = str(row["คีย์เวิร์ด"]).split(",")
            for kw in keywords:
                kw = kw.strip()
                if not kw:
                    continue
                if kw in user_message or fuzz.partial_ratio(kw, user_message) > 80:
                    matched_products.append(row)
                    break

        if len(matched_products) == 1:
            # เจอสินค้าเดียว
            product = matched_products[0]
            product_name = product["ชื่อสินค้า"]
            product_answer = product["คำตอบ"]

            reply_text = f"ได้เลยครับ 🙌 {product_name}\n👉 {product_answer}"

        elif len(matched_products) > 1:
            # เจอหลายสินค้า
            product_names = [r["ชื่อสินค้า"] for r in matched_products]
            reply_text = (
                f"ตอนนี้เรามีสินค้าที่เกี่ยวข้องหลายรายการครับ 👇\n"
                + "\n".join([f"- {name}" for name in product_names])
                + "\n\nคุณสนใจตัวไหนครับ? พิมพ์ชื่อเต็มหรือใกล้เคียงได้เลยนะ 😊"
            )

        else:
            # ไม่เจออะไร
            reply_text = (
                "คุณสนใจสินค้าไหนครับ 😊 "
                "เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ?"
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
