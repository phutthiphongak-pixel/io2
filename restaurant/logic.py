"""Business rules separated from web routes; uses only Python standard library."""
from math import isfinite

ROLES = ("admin", "staff", "customer")
MENU_CATEGORIES = ("ของหวาน", "อาหารอีสาน", "ส้มตำ", "น้ำ", "อาหารตามสั่ง", "ของทอด", "ของย่าง")

def validate_product(name: str, price: float, stock: int, category: str) -> tuple[bool, str]:
    if not name.strip() or not category.strip(): return False, "กรุณากรอกชื่อและหมวดหมู่"
    if category not in MENU_CATEGORIES: return False, "เลือกหมวดเมนูจาก 7 หมวดที่ร้านกำหนด"
    if not isfinite(float(price)) or price < 0: return False, "ราคาต้องเป็นตัวเลขตั้งแต่ 0 ขึ้นไป"
    if int(stock) < 0: return False, "สต็อกต้องไม่ติดลบ"
    return True, ""

def calculate_bill(items: list, discount: float=0, service_rate: float=0.10, vat_rate: float=0.07) -> dict:
    subtotal = sum(float(i["price"]) * int(i["quantity"]) for i in items)
    discount = min(max(float(discount), 0), subtotal)
    service = round((subtotal-discount)*service_rate, 2)
    vat = round((subtotal-discount+service)*vat_rate, 2)
    return {"subtotal": round(subtotal,2), "discount": round(discount,2), "service":service, "vat":vat, "total":round(subtotal-discount+service+vat,2)}

def paginate(rows: list, page: int=1, per_page: int=8) -> tuple:
    page = max(int(page), 1); size=max(int(per_page),1)
    total_pages=1; remaining=max(len(rows)-size,0)
    while remaining>0:
        total_pages += 1; remaining -= size
    return rows[(page-1)*size:page*size], total_pages

def filter_sort_products(products: list, query: str="", category: str="", sort: str="name") -> list:
    q=query.casefold(); result=[p for p in products if q in p["name"].casefold() and (not category or p["category"]==category)]
    key = {"price":"price", "stock":"stock", "name":"name"}.get(sort,"name")
    return sorted(result, key=lambda p:p[key])

def check_order_stock(items: list) -> tuple:
    """Items are (name, available, requested); never allow zero/out-of-stock."""
    for name, available, requested in items:
        if requested <= 0: return False, f"จำนวน {name} ต้องมากกว่า 0"
        if requested > available: return False, f"สินค้า {name} มีไม่พอ (เหลือ {available})"
    return True, ""

def rice_portion(name: str, category: str, choice: str) -> tuple[str, float]:
    """Validate rice meal portion on the server and return its label and surcharge."""
    if choice in ("", "ปกติ"): return "", 0.0
    allowed=category=="อาหารตามสั่ง" and (name.startswith("กะเพรา") or name=="ข้าวกะเพราไก่") and name not in ("กะเพราราดมาม่า","กะเพราถาด")
    if not allowed: raise ValueError("เมนูนี้ไม่มีตัวเลือกเพิ่มพิเศษหรือกับข้าว")
    if choice=="พิเศษ": return "พิเศษ (+10 บาท)", 10.0
    if choice=="กับข้าว": return "กับข้าว (+20 บาท)", 20.0
    raise ValueError("ตัวเลือกขนาดอาหารไม่ถูกต้อง")

def safe_upload_name(filename: str) -> str:
    from pathlib import Path
    cleaned=Path(filename).name.replace(" ","_")
    if not cleaned or Path(cleaned).suffix.lower() not in {".png",".jpg",".jpeg",".webp",".gif"}: raise ValueError("รองรับไฟล์ภาพ PNG, JPG, WEBP, GIF")
    return cleaned

def role_can(role: str, action: str) -> bool:
    if role == "admin": permissions={"manage","sell","kitchen","report","reservations"}
    elif role == "staff": permissions={"sell","kitchen","reservations"}
    elif role == "customer": permissions={"order"}
    else: permissions=set()
    return action in permissions

def clean_text(value: str, max_length: int=120) -> str:
    value=str(value or "").strip()
    if not value or len(value)>max_length: raise ValueError("ข้อมูลต้องไม่ว่างและยาวไม่เกินกำหนด")
    return value

def tally_top_items(rows: list) -> list:
    totals={}
    for name, quantity in rows: totals[name]=totals.get(name,0)+int(quantity)
    return sorted(totals.items(),key=lambda pair:pair[1],reverse=True)
