# PopQuants — US Stocks Arbitrage Workshop

เวิร์กช็อปภาษาไทยสำหรับเรียนรู้ **Pairs Trading → Triangulated Statistical Arbitrage → SEC 13F + Word2Vec** ผ่านหุ้นสหรัฐฯ ด้วย Python, Jupyter Notebook และ Interactive Tear Sheet

> ใช้เพื่อการศึกษาและทำ Backtest เท่านั้น ไม่ส่งคำสั่งซื้อขายเงินจริง และไม่รับประกันผลตอบแทน

## เนื้อหา

| ส่วน | สิ่งที่เรียนรู้ |
| --- | --- |
| Strategy 1 | Pairs Trading ระหว่าง AMAT/LRCX: Spread, Z-score, Trading Signals |
| Strategy 2 | Triangulated Statistical Arbitrage: คู่หุ้นหลายคู่, Votes, Depth, Consistency |
| Strategy 3 | SEC Form 13F และ Word2Vec: เรียนรู้ความคล้ายกันของหุ้นจากการถือครองของสถาบัน |
| Dashboard | Equity, Drawdown, Sharpe, Monthly Heatmap, Trading Signals, Benchmark |
| Notebooks | อธิบายสูตรด้วยภาษาไทย พร้อมแสดงการคำนวณทีละขั้น |

**หุ้นตัวอย่าง:** AMAT, LRCX, KLAC, TER  
**ช่วงทดสอบ:** 2 มกราคม – 20 พฤศจิกายน 2026 (ถึงวันซื้อขายล่าสุดที่ข้อมูลรองรับ)

## เริ่มต้นใช้งานบน Mac

```bash
git clone https://github.com/popquants/us_arbitrage.git
cd us_arbitrage
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 run.py doctor
```

### 1. เตรียมข้อมูลราคา

หากมีข้อมูลจากโปรเจกต์ uSMART เดิม:

```bash
python3 run.py import-prices
```

หากมีบัญชี API ของตัวเอง ให้เพิ่มข้อมูลคีย์ตามหัวข้อด้านล่างก่อน แล้วเรียก:

```bash
python3 run.py fetch-prices
```

### 2. เตรียมข้อมูล SEC 13F

```bash
python3 run.py import-sec
```

หรือดาวน์โหลดข้อมูลสาธารณะจาก SEC:

```bash
python3 run.py download-13f --email "YOUR_EMAIL@example.com"
```

### 3. รันกลยุทธ์และสร้าง Dashboard

```bash
python3 run.py strategy1
python3 run.py strategy2
python3 run.py strategy3
python3 run.py dashboard
```

หรือรันทั้งหมดด้วย `python3 run.py all`

เปิดรายงานด้วย `open outputs/dashboard/interactive_tearsheet.html`

## ตรวจสอบยอดเงินบัญชี uSMART (ไม่บังคับ)

ใน `config/templates/` มีเฉพาะไฟล์ตัวอย่างสำหรับ `api_code`, `public_key`, `private_key` เท่านั้น ผู้เรียนต้องใช้ API ของ **ตนเอง** และสร้างไฟล์ภายใน `config/local/`:

```text
config/local/api_code
config/local/public_key
config/local/private_key
```

จากนั้นรัน:

```bash
chmod 600 config/local/api_code config/local/public_key config/local/private_key
python3 run.py balance
```

โปรแกรมจะรับรหัสผ่านผ่าน Terminal ไม่ต้องบันทึกในโค้ด **ห้ามนำคีย์หรือ Token จริงขึ้น GitHub**

## โครงสร้างโปรเจกต์

```text
us_arbitrage/
├── run.py
├── config/            # ตัวอย่างการตั้งค่า (ไม่มีคีย์จริง)
├── data/              # ผู้เรียนเตรียมข้อมูลราคาและ SEC 13F เอง
├── src/               # API / Strategy 1–3 / Dashboard
├── notebooks/         # Notebook ภาษาไทยทั้ง 3 กลยุทธ์
├── outputs/           # ผล Backtest (ไม่อัปโหลดขึ้น GitHub)
├── requirements.txt
├── README_TH.md       # คู่มือฉบับละเอียด
└── FILEMAP.md         # รายการไฟล์และความเชื่อมโยง
```

เปิด Notebook ได้ด้วย `python3 -m jupyter lab notebooks`

## ข้อควรทราบ

- Backtest ไม่ใช่ผลเทรดจริง; ค่าต้นทุน 5 bps เป็นเพียงสมมติฐาน และยังไม่ได้รวมต้นทุนยืมหุ้น/Financing ทุกชนิด
- Strategy 3 ใช้ SEC 13F จริง แต่ยังต้องตรวจสอบ Numerical Stability ของ Gensim/Word2Vec เพิ่มเติม
- Benchmark เป็น Equal-weight Buy & Hold ไม่ได้ปรับให้มีความเสี่ยงเท่ากับ Long/Short Portfolio
- อย่าอัปโหลดข้อมูลส่วนตัว, Key, Token, รายงานสินทรัพย์ หรือข้อมูลบัญชีลง Repository

รายละเอียดเพิ่มเติม: [README_TH.md](README_TH.md) · [FILEMAP.md](FILEMAP.md)  
แหล่งข้อมูล 13F: https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
