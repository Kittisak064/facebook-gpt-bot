# app.py
from flask import Flask, request, jsonify
import os, re, random
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from difflib import SequenceMatcher

app = Flask(__name__)

# ========= Google Sheets =========
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]
# วางไฟล์ credentials.json ไว้ที่ root โปรเจกต์ (อย่า public)
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gs_client = gspread.authorize(creds)

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")  # ตั้งใน Render
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "FAQ")  # เปลี่ยนชื่อชีทได้ (default=FAQ)
sheet = gs_client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)  # คอลัมน์ต้องเป็น: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด

# ========= ตั้งค่า =========
FUZZY_THRESHOLD = float(os.getenv("FUZZY_THRESHOLD", "0.72"))  # ความผ่อนเรื่องสะกดผิด
MAX_LIST = int(os.getenv("MAX_LIST", "5"))  # เวลาเจอหลายชิ้น แสดงสูงสุดกี่ชิ้น/คำค้น

# ========= ตัวช่วย =========
def norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()

def contains(text: str, kw: str) -> bool:
    return kw.lower() in text.lower() or norm(kw) in norm(text)

def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()

def tokenize(text: str):
    # ตัดคำง่าย ๆ สำหรับภาษาไทย+อังกฤษเบื้องต้น (พอช่วยจับบางคำได้)
    # เน้นช่วย “ปลักไฟอัฉรอยะ” ให้เข้าใกล้ “ปลั๊กไฟอัจฉริยะ”
    return [t for t in re.split(r"[\s,.;:!?/|()\[\]{}]+", text.lower()) if t]

# ========= เทมเพลตคำตอบ =========
TEMPLATES_SINGLE = [
    "ได้เลยครับ 🙌 สั่งซื้อ {name} ได้ที่นี่ 👉 {link}",
    "จัดให้ครับ 😊 {name} สั่งซื้อได้ที่ลิงก์นี้เลยนะครับ 👉 {link}",
    "ขอบคุณที่สนใจ {name} ครับ ✨ สั่งซื้อได้ที่นี่ 👉 {link}"
]

TEMPLATES_MULTI_HEADER = [
    "📌 เจอหลายสินค้าที่เกี่ยวข้องครับ ลองดูรายการนี้นะครับ 😊",
    "มีหลายตัวเลือกที่น่าสนใจครับ 🙌 เลือกดูได้เลย:",
    "ตรวจสอบให้แล้วครับ พบหลายรายการดังนี้ครับ ✨"
]

FOLLOWUP_ASK = [
    "คุณสนใจสินค้าไหนครับ 😊 เช่น ไฟเซ็นเซอร์ หม้อหุงข้าว หรือปลั๊กไฟ?",
    "บอกชื่อสินค้าที่สนใจได้เลยครับ เช่น ปลั๊กติดผนัง หรือ โจ๊กถุงนะครับ 🙏",
    "อยากได้สินค้าอะไรเป็นพิเศษครับ ผมจะหาลิงก์ให้ทันทีครับ 😊"
]

COMMON_REPLIES = [
    # (คำที่เจอ, ข้อความตอบ)
    ("สวัสดี", "สวัสดีครับ 🙏 ยินดีให้บริการครับ บอกชื่อสินค้าที่สนใจได้เลยนะครับ"),
    ("ขอบคุณ", "ยินดีมากครับ 😊 หากต้องการลิงก์สั่งซื้อแจ้งผมได้เลย"),
    ("ขอบใจ", "ยินดีครับผม 🙌 ถ้าต้องการดูสินค้าเพิ่มเติม บอกผมได้เลยครับ"),
    ("โอเค", "โอเคครับ ✨ ถ้ามีคำถามเพิ่ม ทักมาได้เลยครับ"),
    ("ok", "โอเคครับผม 🙌 ต้องการลิงก์สินค้าตัวไหนบอกได้เลยครับ"),
]

def reply_manychat(text: str):
    return jsonify({"content": {"messages": [{"text": text}]}}), 200

@app.route("/", methods=["GET"])
def home():
    return "✅ Conversational FAQ Bot is running", 200

@app.route("/manychat", methods=["POST"])
def manychat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()
        if not user_message:
            return reply_manychat("⚠️ ไม่พบข้อความจากผู้ใช้")

        # ---------- 1) จัดการข้อความทั่วไปก่อน ----------
        for key, val in COMMON_REPLIES:
            if key in user_message:
                return reply_manychat(val)

        # ---------- 2) โหลดข้อมูลชีท ----------
        # คอลัมน์บังคับ: ชื่อสินค้า | คำตอบ | คีย์เวิร์ด
        records = sheet.get_all_records()

        # สร้าง index คีย์เวิร์ดและแม็ปสินค้า
        # products = [{"name": ..., "link": ..., "keywords": [...]}]
        products = []
        for row in records:
            name = str(row.get("ชื่อสินค้า", "")).strip()
            link = str(row.get("คำตอบ", "")).strip()
            kw_raw = str(row.get("คีย์เวิร์ด", "")).strip()
            kws = [k.strip() for k in kw_raw.split(",") if k.strip()]
            # รวมชื่อสินค้าเข้าไปเป็นคำค้นด้วย เผื่อลูกค้าพิมพ์ชื่อเต็ม
            keyset = list(set(kws + ([name] if name else [])))
            products.append({"name": name, "link": link, "keywords": keyset})

        u = user_message
        tokens = tokenize(user_message)

        # ---------- 3) หาแมตช์หลายคีย์เวิร์ดในข้อความเดียว ----------
        # matched_products: รายการสินค้าที่เกี่ยวข้องทั้งหมด (ไม่ซ้ำชื่อ)
        matched_products = []

        for p in products:
            # ถ้าชื่อ/คีย์เวิร์ดของสินค้านี้ "เข้าเค้า" กับ user_message → match
            best_score = 0.0
            direct = False
            for kw in p["keywords"]:
                if not kw:
                    continue
                if contains(u, kw):
                    direct = True
                    best_score = 1.0
                    break
                # fuzzy กับโทเคนท้าย ๆ ก่อน
                for t in reversed(tokens):
                    sc = similar(t, kw)
                    if sc > best_score:
                        best_score = sc
            if direct or best_score >= FUZZY_THRESHOLD:
                # กันชื่อซ้ำ
                if not any(x["name"] == p["name"] for x in matched_products):
                    matched_products.append(p)

        # ---------- 4) ถ้าไม่เจอเลย ----------
        if len(matched_products) == 0:
            return reply_manychat(random.choice(FOLLOWUP_ASK))

        # ---------- 5) ถ้าเจอ 1 ชิ้น → ตอบเป็นธรรมชาติพร้อมลิงก์ ----------
        if len(matched_products) == 1:
            p = matched_products[0]
            name = p["name"] or "สินค้านี้"
            link = p["link"] or ""
            if link:
                text = random.choice(TEMPLATES_SINGLE).format(name=name, link=link)
            else:
                text = f"{name} พร้อมให้บริการครับ 😊 สนใจรายละเอียดเพิ่มเติมบอกผมได้เลยครับ"
            return reply_manychat(text)

        # ---------- 6) ถ้าเจอหลายชิ้น ----------
        # กลุ่มตาม "คำกว้าง" ที่โผล่ในข้อความ เช่น 'ปลั๊ก', 'ไฟ', 'โจ๊ก'
        # วิธีง่าย: จากสินค้าที่ match แล้ว ลองหา keyword ที่อยู่ในข้อความด้วย
        # ถ้าแยกไม่ออกจริง ๆ ก็รวมเป็นรายการเดียว
        groups = {}  # key -> list of products
        any_grouped = False
        for p in matched_products:
            placed = False
            for kw in p["keywords"]:
                if kw and contains(u, kw):
                    groups.setdefault(kw, [])
                    # กันซ้ำต่อกลุ่ม
                    if not any(x["name"] == p["name"] for x in groups[kw]):
                        groups[kw].append(p)
                        placed = True
                        any_grouped = True
                        break
            if not placed:
                groups.setdefault("_misc", [])
                if not any(x["name"] == p["name"] for x in groups["_misc"]):
                    groups["_misc"].append(p)

        lines = [random.choice(TEMPLATES_MULTI_HEADER)]
        # จัดรูปแบบแสดงผล
        for kw, items in groups.items():
            # ตัดจำนวนรายการต่อคำค้น
            items = items[:MAX_LIST]
            if kw == "_misc":
                lines.append("รายการที่เกี่ยวข้อง:")
            else:
                lines.append(f'สำหรับคำว่า “{kw}” มีตัวเลือกดังนี้:')
            for it in items:
                n = it["name"] or "สินค้า"
                l = it["link"] or ""
                if l:
                    lines.append(f"- {n} 👉 {l}")
                else:
                    lines.append(f"- {n}")

            lines.append("")  # เว้นบรรทัด

        lines.append("ถ้าต้องการตัวไหน พิมพ์ชื่อสินค้ามาได้เลยครับ ผมจะส่งลิงก์ให้อีกครั้ง 😊")
        return reply_manychat("\n".join([ln for ln in lines if ln.strip() != ""]))

    except Exception as e:
        return jsonify({
            "content": {"messages": [{"text": f"⚠️ มีข้อผิดพลาด: {str(e)}"}]}
        }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
