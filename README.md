# Facebook GPT Bot with Google Sheets

บอทตอบแชท Facebook Messenger โดยใช้ OpenAI GPT และข้อมูลจาก Google Sheets
Deploy บน Render ได้ทันที

## การติดตั้ง
1. สร้าง Google Service Account → ดาวน์โหลดไฟล์ `credentials.json`
2. ตั้งค่า Environment Variables บน Render:
   - `OPENAI_API_KEY`
   - `PAGE_ACCESS_TOKEN`
   - `VERIFY_TOKEN`
3. Deploy
