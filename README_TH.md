# PopQuants — uSMART Workshop (US stocks)

**เวอร์ชันแบ่งปันสำหรับผู้เรียน** · `2026-01-02` ถึง `2026-11-20` (หรือวันราคาล่าสุดที่มีจริง) · `AMAT, LRCX, KLAC, TER` · read-only · **ไม่มีไฟล์รหัสจริง / Token / Private Key / เลขบัญชี**

> ข้อมูลสำหรับเทรดจริงกับข้อมูลเพื่อทำวิจัยไม่เหมือนกัน: สคริปต์นี้ไม่เปิด/ปิดสถานะในบัญชี และไม่มีฟังก์ชันส่งคำสั่งซื้อขาย ผลตอบแทนเป็นแบบจำลองรวมต้นทุน 5 bps/turnover แต่ยังไม่รวม borrow, financing, dividends และ fill จริง

## โครงสร้างโฟลเดอร์

```text
~/Documents/usmart_workshop/
├── run.py                     # เรียกทุกขั้นตอนจากที่นี่
├── requirements.txt
├── config/
│   ├── workshop.json          # ค่าหลักสูตร/วันที่/พารามิเตอร์ (ไม่มีข้อมูลลับ)
│   ├── templates/             # ตัวอย่างไฟล์คีย์ ไม่ใช่คีย์จริง
│   └── local/                 # ใส่คีย์บัญชีของผู้เรียนแต่ละคนเอง ห้ามแชร์
├── src/                       # โค้ดที่จำเป็นจริงเท่านั้น
│   ├── usmart_v8_asset_diagnostic.py
│   ├── usmart_nvda_30d.py
│   ├── popquants_pairs_strategy1.py
│   ├── popquants_strategy2.py
│   ├── popquants_strategy3_2026_teaching.py
│   ├── popquants_tearsheet_viewer.py
│   └── download_sec_13f.py
├── data/
│   ├── prices/us_stock_adjusted_daily.csv    # ต้องเตรียมเองหรือดึงจาก uSMART
│   └── sec_13f_zips/*.zip                    # ต้องดาวน์โหลดจาก SEC เอง
├── notebooks/
│   ├── 01_Pairs_Trading_Thai.ipynb
│   ├── 02_Triangulated_Thai.ipynb
│   └── 03_13F_Word2Vec_Thai.ipynb
└── outputs/
    ├── strategy1/
    ├── strategy2/
    ├── strategy3/
    └── dashboard/interactive_tearsheet.html
```

## ติดตั้งบน Mac

หาก ZIP อยู่ใน Downloads ให้รัน:

```bash
unzip -oq ~/Downloads/usmart_workshop.zip -d ~/Documents
cd ~/Documents/usmart_workshop
python3 -m pip install -r requirements.txt
python3 run.py doctor
```

**คีย์จริงห้ามนำมาอยู่ใน ZIP สำหรับแชร์** ผู้เรียนต้องคัดลอกจากบัญชีของตนเองใส่ `config/local/` เป็นไฟล์ข้อความ 3 ชื่อ โดยไม่เติม `.txt`:

```text
config/local/api_code     # ตัวเลข API Code ของบัญชีตนเอง; X-Channel
config/local/public_key   # Base64 public key สำหรับเข้ารหัส Login
config/local/private_key  # Base64 private key สำหรับเซ็น X-Sign
```

ตั้งสิทธิ์เฉพาะบนเครื่อง:

```bash
chmod 600 config/local/api_code config/local/public_key config/local/private_key
```

**ไม่จำเป็นต้องนำข้อมูลคีย์จริงลงบน Google Drive หรือ Github; ควรแจกเฉพาะ ZIP เปล่าเท่านั้น** หลังจากผู้สอน/ผู้เรียนใส่คีย์จริงแล้ว ห้าม ZIP โฟลเดอร์ `config/local/` ไปแจกต่อ

## เริ่มใช้งานตามลำดับ

### A — ตรวจสอบ Balance (ถ้ามีบัญชี uSMART ของตัวเอง)

```bash
python3 run.py balance
```

กรอก Country Calling Code, Phone และ **LOGIN password** ใน Terminal ตามที่โปรแกรมถาม (Password ถูกซ่อนไว้) ไม่ใส่รหัสผ่านลงไฟล์หรือ Notebook

### B — เตรียมข้อมูลราคาหุ้น

**กรณีผู้สอน** ที่มี CSV ราคาที่ดึง uSMART ไว้แล้วใน `~/Documents/usmart/`:

```bash
python3 run.py import-prices
```

**กรณีผู้เรียน** ที่มี API ของตนเองและต้องการดาวน์โหลดเอง:

```bash
python3 run.py fetch-prices
```

ข้อมูลราคาควรครอบคลุมปี 2025 สำหรับ Warm-up ก่อนเริ่มวัดผลในปี 2026; ใช้ Adjusted Daily Close (right=1). ต้องตรวจ Corporate Actions และ Market Data Permission

### C — เตรียม SEC 13F สำหรับ Strategy 3

**กรณีผู้สอน** ที่มี ZIP เดิมอยู่แล้ว:

```bash
python3 run.py import-sec
```

**กรณีผู้เรียน** ที่ยังไม่มี 13F ให้ดาวน์โหลดจาก SEC โดยใช้ **อีเมลติดต่อของตัวเอง**:

```bash
python3 run.py download-13f --email "YOUR_EMAIL@example.com"
```

ไฟล์ SEC จะถูกตรวจสอบและเก็บใน `data/sec_13f_zips/` อัตโนมัติ ข้อมูลมีขนาดใหญ่กว่า 1 GB; ใช้พื้นที่ดิสก์ให้เพียงพอ

### D — รันตามลำดับ Strategy 1 → 2 → 3 → Dashboard

```bash
python3 run.py strategy1
python3 run.py strategy2
python3 run.py strategy3
python3 run.py dashboard
```

หรือเมื่อเตรียม Price CSV และ 13F ครบแล้ว:

```bash
python3 run.py all
```

**ผลลัพธ์ทั้งหมด** อยู่ใต้ `outputs/` และ Dashboard อยู่ที่ `outputs/dashboard/interactive_tearsheet.html` สามารถเปิดใน Chrome ได้โดยไม่ต้องต่ออินเทอร์เน็ตหลังสร้างสำเร็จ

### E — เปิด Notebook

```bash
jupyter lab notebooks
```

เริ่มจาก 01 → 02 → 03 กด Shift+Enter ทีละ Cell เพื่อแสดงสมการและตารางเป็นภาษาไทย **แต่ละ Notebook ใช้ข้อมูลในโฟลเดอร์นี้**; ไม่เรียก API หรือส่งคำสั่งซื้อขาย

## สิ่งที่ Dashboard แสดง

Equity, Drawdown, Performance Scorecard, Risk vs Return (X = annualized standard deviation, Y = total net return), Monthly Heatmap (สลับ Strategy 1/2/3), Rolling 63-day Sharpe, Signal Explorer รายหุ้น และ Target Position Changes แบบ Scroll เฉพาะในกล่อง 10 แถว

*Benchmark*: Equal-weight initial buy & hold (AMAT, LRCX, KLAC, TER อย่างละ 25%) ไม่มี Rebalancing และไม่มีต้นทุน เทียบกันบน **ช่วงวันที่ทับซ้อนกันจริง** พร้อมป้องกันการนำผลที่มีช่วงข้อมูลต่างกันมาวางต่อกันโดยตรง

## ข้อจำกัดสำคัญสำหรับการสอน

- Strategy 1 เป็นคู่ AMAT/LRCX; Strategy 2 เป็น Network 4 หุ้น 6 คู่; Strategy 3 คัดความสัมพันธ์ด้วย Word2Vec จาก 13F จริงที่พร้อมใช้หลัง Filing Date และ 45 วัน แล้วเทรดใน Universe 4 หุ้นเดียวกัน
- Strategy 3 เก็บ Top 160 Holdings ต่อ Filing เพื่อจำกัดการใช้ทรัพยากรและใช้ Equal-weight Votes; ไม่ใช่การจำลองงานวิจัยขนาดใหญ่ครบทุกเงื่อนไข
- **Warning Gensim `our_dot_float` ที่เคยพบยังต้องทดสอบ Numerical Reproducibility** ก่อนนำผลปี 2026 ไปกล่าวเป็น Edge ที่ยืนยันแล้ว
- Python 2026 Backtest ใช้ราคาปิดย้อนหลังและ Next-bar Proxy; *ไม่ใช่* Fill ที่ทำได้จริงในตลาด
- ใช้ 13F ของ SEC ไม่ใช่ Synthetic Holdings; ไม่มีผลตอบแทนสำเร็จรูปอยู่ใน ZIP เพื่อไม่ให้ผลวิจัยปลอมปะปน

ตรวจสอบการเชื่อมไฟล์ในโปรเจกต์ด้วย `python3 run.py doctor` เมื่อเกิด Error

## เก็บชุดแชร์ให้ปลอดภัยในอนาคต

หลังเพิ่มคีย์และราคาจริงบนเครื่องแล้ว **ห้าม ZIP ทั้งโฟลเดอร์ Documents/usmart_workshop แจก** เพราะจะมี `config/local` และข้อมูลส่วนบุคคล ให้ใช้:

```bash
cd ~/Documents/usmart_workshop
python3 make_safe_zip.py
```

จะได้ `~/Documents/usmart_workshop_share_safe.zip` ซึ่งเก็บเฉพาะโค้ด, Notebook, Template และคู่มือ โดยไม่รวม Keys, Token, CSV หรือ 13F ZIP ครับ