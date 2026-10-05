"""SQLite persistence and seed data. Local demo database is created on first launch."""
import sqlite3, os, json, csv
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=os.environ.get("DATABASE_PATH",("/tmp/restaurant-pos.db" if os.environ.get("VERCEL") else str(ROOT/"instance"/"restaurant.db")))
SCHEMA="""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, category TEXT NOT NULL, price REAL NOT NULL, stock INTEGER NOT NULL DEFAULT 0, image TEXT DEFAULT '', available INTEGER NOT NULL DEFAULT 1, options TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS dining_tables(id INTEGER PRIMARY KEY, label TEXT UNIQUE NOT NULL, capacity INTEGER NOT NULL DEFAULT 2, status TEXT NOT NULL DEFAULT 'ว่าง', is_custom INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, table_id INTEGER, customer TEXT DEFAULT '', customer_user_id INTEGER, status TEXT NOT NULL, created_at TEXT NOT NULL, discount REAL DEFAULT 0, service_rate REAL DEFAULT .1, vat_rate REAL DEFAULT .07, split_parent INTEGER, FOREIGN KEY(table_id) REFERENCES dining_tables(id));
CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, name TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, options TEXT DEFAULT '', note TEXT DEFAULT '', status TEXT DEFAULT 'รอทำ', cancelled INTEGER DEFAULT 0, FOREIGN KEY(order_id) REFERENCES orders(id));
CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY, order_id INTEGER UNIQUE NOT NULL, subtotal REAL, discount REAL, service REAL, vat REAL, total REAL, payment_method TEXT, paid REAL, change_due REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, user TEXT, action TEXT, entity TEXT, entity_id INTEGER, details TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, guests INTEGER NOT NULL, reserved_at TEXT NOT NULL, reserved_end TEXT, status TEXT DEFAULT 'รอยืนยัน', customer_user_id INTEGER, table_id INTEGER);
CREATE TABLE IF NOT EXISTS ingredients(id INTEGER PRIMARY KEY, name TEXT UNIQUE, unit TEXT, stock REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS recipes(product_id INTEGER, ingredient_id INTEGER, amount REAL, PRIMARY KEY(product_id,ingredient_id));
CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, points INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS point_transactions(
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL,
    order_id INTEGER,
    points INTEGER NOT NULL,
    type TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(member_id) REFERENCES members(id),
    FOREIGN KEY(order_id) REFERENCES orders(id)
);
"""
def connect():
    os.makedirs(os.path.dirname(DB),exist_ok=True)
    db=sqlite3.connect(DB); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db
def initialize():
    from werkzeug.security import generate_password_hash
    db=connect(); db.executescript(SCHEMA)
    columns={row[1] for row in db.execute("PRAGMA table_info(dining_tables)")}
    if "is_custom" not in columns: db.execute("ALTER TABLE dining_tables ADD COLUMN is_custom INTEGER NOT NULL DEFAULT 0")
    order_columns={row[1] for row in db.execute("PRAGMA table_info(orders)")}
    if "customer_user_id" not in order_columns: db.execute("ALTER TABLE orders ADD COLUMN customer_user_id INTEGER")
    reservation_columns={row[1] for row in db.execute("PRAGMA table_info(reservations)")}
    if "customer_user_id" not in reservation_columns: db.execute("ALTER TABLE reservations ADD COLUMN customer_user_id INTEGER")
    if "table_id" not in reservation_columns: db.execute("ALTER TABLE reservations ADD COLUMN table_id INTEGER")
    if "reserved_end" not in reservation_columns: db.execute("ALTER TABLE reservations ADD COLUMN reserved_end TEXT")
    # Older reservations only had a start time. Give them a safe default 2-hour window.
    db.execute("UPDATE reservations SET reserved_end=strftime('%Y-%m-%dT%H:%M', datetime(replace(reserved_at,'T',' '), '+2 hours')) WHERE reserved_end IS NULL OR reserved_end=''")
    member_columns={row[1] for row in db.execute("PRAGMA table_info(members)")}
    if "points" not in member_columns: db.execute("ALTER TABLE members ADD COLUMN points INTEGER NOT NULL DEFAULT 0")
    order_columns={row[1] for row in db.execute("PRAGMA table_info(orders)")}
    item_columns={row[1] for row in db.execute("PRAGMA table_info(order_items)")}
    if "note" not in item_columns: db.execute("ALTER TABLE order_items ADD COLUMN note TEXT DEFAULT ''")
    if "points_redeemed" not in order_columns: db.execute("ALTER TABLE orders ADD COLUMN points_redeemed INTEGER NOT NULL DEFAULT 0")
    if "points_earned" not in order_columns: db.execute("ALTER TABLE orders ADD COLUMN points_earned INTEGER NOT NULL DEFAULT 0")
    # Ensure every customer account has a member wallet.
    db.execute("INSERT OR IGNORE INTO members(user_id,points) SELECT id,0 FROM users WHERE role='customer'")
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0:
        db.executemany("INSERT INTO users(username,password,role,name) VALUES(?,?,?,?)",[("admin",generate_password_hash("admin123"),"admin","ผู้ดูแลระบบ"),("staff",generate_password_hash("staff123"),"staff","พนักงานหน้าร้าน"),("customer",generate_password_hash("customer123"),"customer","ลูกค้าตัวอย่าง")])
        db.executemany("INSERT INTO products(name,category,price,stock,available,options) VALUES(?,?,?,?,?,?)",[("ข้าวกะเพราไก่","อาหารจานเดียว",65,30,1,"เผ็ดน้อย,เผ็ดกลาง,เผ็ดมาก"),("ผัดไทยกุ้งสด","อาหารจานเดียว",89,20,1,"ไม่ใส่ถั่ว,เพิ่มไข่"),("ชาไทย","เครื่องดื่ม",45,40,1,"หวานน้อย,หวานปกติ,หวานมาก"),("น้ำเปล่า","เครื่องดื่ม",15,50,1,""),("เค้กช็อกโกแลต","ของหวาน",75,0,0,""),("ต้มยำกุ้ง","อาหารไทย",159,12,1,"เผ็ดน้อย,เผ็ดมาก")])
        db.executemany("INSERT INTO dining_tables(label,capacity,status) VALUES(?,?,?)",[(f"โต๊ะ {i}",4,"จอง" if i==6 else "ว่าง") for i in range(1,9)])
        db.execute("INSERT INTO ingredients(name,unit,stock) VALUES('เนื้อไก่','กรัม',5000)"); db.commit()
    # Add the requested Isan menu catalog on both fresh and existing local databases.
    # Demo prices are starting values; admins can edit them from Menu Management.
    catalog={
        "ส้มตำ": ["ตำไทย","ตำไทยไข่เค็ม","ตำปู","ตำปูปลาร้า","ตำโคราช","ตำลาว","ตำซั่ว","ตำป่า","ตำแตง","ตำถั่ว","ตำส้มโอ","ตำกุ้งสด","ตำกุ้งสุก","ตำแซลมอนปลาร้า","ตำปูม้าดอง","ตำหมึกสาย","ตำทะเลรวมมิตร","ตำหอยแครง"],
        "อาหารอีสาน": ["ลาบหมู","ลาบไก่","ลาบเป็ดเอ็ดโฮด","ลาบเนื้อสุก","ลาบเนื้อสุกๆ ดิบๆ","ลาบปลาดุกฟู","ลาบปลาคัง","ลาบวุ้นเส้นหมูสับ","ลาบหมูกรอบ","ลาบไข่ต้ม","ลาบเต้าหู้ (มังสวิรัติ)","ก้อยเนื้อขม (ใส่ดีวัว)","ก้อยเนื้อไม่ขม","ก้อยกุ้งฝอย","ก้อยหอยเชอรี่","ตับหวานหมู","ตับหวานเนื้อ","น้ำตกหมูย่าง","น้ำตกเนื้อนุ่ม","น้ำตกคอหมูกรอบ","น้ำตกไก่ย่าง","ซุปหน่อไม้","ยำขนมจีนปลาทูปลาร้า","ซุปมะเขือ","หมกหน่อไม้ใบย่านาง","หมกสมองหมู","หมกไข่มดแดง","แกงอ่อมหมู","แกงอ่อมเนื้อ","แกงอ่อมไก่บ้าน","แกงเห็ดรวมใบย่านาง","แกงหวายใส่ไก่บ้าน","แกงไข่มดแดงส้มผักหวาน (ตามฤดูกาล)","ต้มแซ่บกระดูกหมูอ่อน","ต้มแซ่บเนื้อเปื่อย","ต้มแซ่บเครื่องในวัว","ต้มยำปลาคังน้ำใส","ต้มยำปลาคังน้ำข้น","ไก่บ้านต้มขมิ้น","ไก่บ้านต้มมะขามเปียก","ต้มแซ่บไข่มดแดง"],
        "ของย่าง": ["ไก่ย่างวิเชียรบุรี (ครึ่งตัว)","ไก่ย่างวิเชียรบุรี (ตัว)","ไก่ย่างเขาสวนกวาง (ครึ่งตัว)","ไก่ย่างเขาสวนกวาง (ตัว)","คอหมูกรอบย่างน้ำจิ้มแจ่ว","เสือร้องไห้ย่าง (เนื้อโพนยางคำ)","ปลาดุกย่าง","ปลาช่อนเผามะเขือยาวผา","ปลากะพงเผาเกลือ","ไส้อ่อนย่าง","ลิ้นวัวย่าง"],
        "ของทอด": ["ปีกไก่ทอดน้ำปลา","ไก่ทอดสมุนไพร","เอ็นไก่ทอดงา","หมูแดดเดียว","เนื้อแดดเดียวทอด","สามชั้นทอดน้ำปลา","หมูกรอบแจ่วบอง","ปลาช่อนลุยสวน","ปลากะพงทอดน้ำปลา"],
        "อาหารตามสั่ง": ["กะเพราหมูกรอบ","กะเพราหมูสับ","กะเพราหมูชิ้น","กะเพราเนื้อสไลซ์","กะเพราเนื้อเปื่อย","กะเพราไก่","กะเพราไก่กรอบ","กะเพราทะเล (กุ้งและหมึก)","กะเพราปลาทู","กะเพราไข่เยี่ยมม้าหมูสับ","กะเพราคอหมูย่าง (สูตรเด็ดประจำร้าน)","ข้าวสวย","กะเพราราดข้าว","กะเพราราดมาม่า","กะเพราถาด","ข้าวเหนียว","ข้าวกะเพราไก่","ผัดไทยกุ้งสด"],
        "น้ำ": ["น้ำเปล่า","ชาไทย","ชาเขียว","กาแฟเย็น","น้ำอัดลม","น้ำมะนาว","น้ำเก๊กฮวย"],
        "ของหวาน": ["เค้กช็อกโกแลต","บัวลอยมะพร้าวอ่อน","เฉาก๊วยนมสด","ข้าวเหนียวมะม่วง"]
    }
    legacy_categories={"ส้มตำ · ตำพื้นฐาน":"ส้มตำ","ส้มตำ · ตำทะเลและของสด":"ส้มตำ","ลาบ · ก้อย · น้ำตก · ซุป · ลาบ":"อาหารอีสาน","ลาบ · ก้อย · น้ำตก · ซุป · ก้อยและตับหวาน":"อาหารอีสาน","ลาบ · ก้อย · น้ำตก · ซุป · น้ำตก":"อาหารอีสาน","ลาบ · ก้อย · น้ำตก · ซุป · ซุปและยำอีสาน":"อาหารอีสาน","ปิ้ง · ย่าง · ทอด · ปิ้งย่าง":"ของย่าง","ปิ้ง · ย่าง · ทอด · เมนูทอด":"ของทอด","ต้ม · แกง · อ่อม · แกงอีสาน":"อาหารอีสาน","ต้ม · แกง · อ่อม · ต้มแซ่บและต้มยำ":"อาหารอีสาน","ผัดกะเพรา":"อาหารตามสั่ง","ข้าวและเมนูเส้น":"อาหารตามสั่ง","อาหารจานเดียว":"อาหารตามสั่ง","อาหารไทย":"อาหารอีสาน","เครื่องดื่ม":"น้ำ"}
    for old,new in legacy_categories.items(): db.execute("UPDATE products SET category=? WHERE category=?",(new,old))
    for category,names in catalog.items():
        for name in names:
            price=20 if category=="น้ำ" else 15 if name in ("ข้าวสวย","ข้าวเหนียว") else 99 if any(word in name for word in ("กุ้งสด","แซลมอน","ปูม้า","หอยแครง","ปลากะพง","ปลาช่อน")) else 79
            db.execute("INSERT OR IGNORE INTO products(name,category,price,stock,available) VALUES(?,?,?,?,1)",(name,category,price,20))
    db.commit()
    db.close()
def audit(user,action,entity,entity_id,details):
    db=connect(); db.execute("INSERT INTO audit_log(user,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,datetime('now','localtime'))",(user,action,entity,entity_id,json.dumps(details,ensure_ascii=False))); db.commit(); db.close()
def export_csv(path):
    db=connect()
    with open(path,"w",newline="",encoding="utf-8-sig") as f:
        writer=csv.writer(f); writer.writerow(["เมนู","หมวดหมู่","ราคา","คงเหลือ"]); writer.writerows(db.execute("SELECT name,category,price,stock FROM products"))
    db.close()
