"""Restaurant POS demo: Flask routes with server-side validation and role checks."""
import os, uuid, sqlite3, io, socket
import qrcode
from qrcode.image.svg import SvgImage
from itsdangerous import URLSafeSerializer, BadSignature
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, Response, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from restaurant import storage
from restaurant.logic import *

app=Flask(__name__); app.secret_key=os.environ.get("SECRET_KEY","local-demo-change-this-secret")
app.config["MAX_CONTENT_LENGTH"]=4*1024*1024
UPLOAD=Path("/tmp/restaurant-pos-uploads" if os.environ.get("VERCEL") else Path(app.root_path)/"static"/"uploads"); UPLOAD.mkdir(parents=True,exist_ok=True)
storage.initialize()
QR_SALT="restaurant-table-qr-v1"
POINTS_PER_100=10
POINT_VALUE_BAHT=1
qr_serializer=URLSafeSerializer(app.secret_key, salt=QR_SALT)

def db_rows(sql,args=()):
    db=storage.connect(); rows=db.execute(sql,args).fetchall(); db.close(); return rows
def db_write(sql,args=()):
    db=storage.connect(); cur=db.execute(sql,args); db.commit(); value=cur.lastrowid; db.close(); return value
def reservation_window(raw_start, raw_end):
    start=datetime.strptime(clean_text(raw_start),"%Y-%m-%dT%H:%M")
    end=datetime.strptime(clean_text(raw_end),"%Y-%m-%dT%H:%M")
    if start <= datetime.now(): raise ValueError("กรุณาเลือกวันและเวลาเริ่มต้นในอนาคต")
    if end <= start: raise ValueError("เวลาสิ้นสุดต้องมากกว่าเวลาเริ่มต้น")
    duration=int((end-start).total_seconds()//60)
    if duration < 30: raise ValueError("ช่วงเวลาจองต้องไม่น้อยกว่า 30 นาที")
    if duration > 12*60: raise ValueError("ช่วงเวลาจองต้องไม่เกิน 12 ชั่วโมง")
    return start,end

def reservation_conflict(table_id, start, end, exclude_id=None):
    sql="SELECT id,name,reserved_at,reserved_end,status FROM reservations WHERE table_id=? AND status!=? AND reserved_at < ? AND reserved_end > ?"
    args=[table_id,"ยกเลิก",end.strftime("%Y-%m-%dT%H:%M"),start.strftime("%Y-%m-%dT%H:%M")]
    if exclude_id is not None:
        sql += " AND id!=?"; args.append(exclude_id)
    return db_rows(sql,tuple(args))

def signed_in(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if not session.get("user_id"): flash("กรุณาเข้าสู่ระบบก่อน", "error"); return redirect(url_for("login"))
        return fn(*a,**kw)
    return wrapper
def require(action):
    def decorator(fn):
        @wraps(fn)
        @signed_in
        def wrapper(*a,**kw):
            if not role_can(session.get("role",""),action): abort(403)
            return fn(*a,**kw)
        return wrapper
    return decorator
def local_lan_ip():
    """Return the computer LAN IPv4 address for local VS Code testing.

    UDP connect does not send application data; it lets the OS select the
    active network interface. Falls back to hostname resolution when needed.
    """
    try:
        sock=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8",80))
        ip=sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        ip=socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"

def qr_base_url():
    """Choose a URL that works from both the current computer and a phone.

    PUBLIC_BASE_URL wins when explicitly configured. Vercel uses the incoming
    HTTPS host. Local VS Code runs use the machine's LAN IP so a phone on the
    same Wi-Fi can scan the QR.
    """
    configured=os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    if os.environ.get("VERCEL"):
        return request.host_url.rstrip("/")
    host=request.host.split(":",1)[0].lower()
    if host not in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} and not host.startswith("127."):
        return request.host_url.rstrip("/")
    port=request.environ.get("SERVER_PORT", "5000")
    return f"http://{local_lan_ip()}:{port}"

def table_scan_url(tid):
    return f"{qr_base_url()}{url_for('customer_table_scan', tid=int(tid), token=table_qr_token(tid))}"

def table_qr_token(tid):
    return qr_serializer.dumps({"table_id":int(tid)})

def verify_table_qr(tid, token):
    try:
        data=qr_serializer.loads(token)
        return int(data.get("table_id")) == int(tid)
    except (BadSignature, TypeError, ValueError):
        return False

def member_for_user(user_id):
    rows=db_rows("SELECT * FROM members WHERE user_id=?",(user_id,))
    if rows: return rows[0]
    mid=db_write("INSERT OR IGNORE INTO members(user_id,points) VALUES(?,0)",(user_id,))
    rows=db_rows("SELECT * FROM members WHERE user_id=?",(user_id,))
    return rows[0] if rows else None

def customer_table_verified():
    return session.get("role")=="customer" and bool(session.get("qr_table_id"))

@app.context_processor
def common():
    member=member_for_user(session["user_id"]) if session.get("role")=="customer" else None
    return {"current_user":session.get("name"),"role":session.get("role"),"now":datetime.now(),
            "o_items":lambda oid: db_rows("SELECT * FROM order_items WHERE order_id=?",(oid,)),
            "menu_categories":MENU_CATEGORIES,"menu_image_url":menu_image_url,"is_spicy_menu":is_spicy_menu,
            "member_points": int(member["points"]) if member else 0, "table_qr_token": table_qr_token}

def menu_image_url(image,category):
    if image: return url_for("uploaded_menu_image",filename=Path(image).name)
    art={"ของหวาน":"dessert","อาหารอีสาน":"isan","ส้มตำ":"papaya","น้ำ":"drinks","อาหารตามสั่ง":"made-to-order","ของทอด":"fried","ของย่าง":"grilled"}.get(category,"food")
    return url_for("static",filename="images/menu-"+art+".svg")

def save_uploaded_menu_image(file):
    safe_upload_name(file.filename)
    filename=uuid.uuid4().hex+Path(secure_filename(file.filename)).suffix.lower()
    file.save(UPLOAD/filename)
    return filename

def best_selling_product_ids():
    return {row["product_id"] for row in db_rows("SELECT product_id,SUM(quantity) quantity FROM order_items WHERE cancelled=0 GROUP BY product_id ORDER BY quantity DESC LIMIT 6")}

def is_spicy_menu(name):
    return any(word in name for word in ("ตำ","ลาบ","ก้อย","น้ำตก","ต้มแซ่บ","กะเพรา","ยำ"))

@app.get("/menu-image/<path:filename>")
def uploaded_menu_image(filename):
    return send_from_directory(UPLOAD,Path(filename).name)

@app.route("/")
@signed_in
def dashboard():
    if session.get("role")=="customer": return redirect(url_for("customer_order"))
    sales=db_rows("SELECT COUNT(*) count,COALESCE(SUM(total),0) total FROM receipts WHERE date(created_at)=date('now','localtime')")[0]
    active=db_rows("SELECT COUNT(*) n FROM orders WHERE status NOT IN ('ชำระแล้ว','ยกเลิก')")[0]["n"]
    low=db_rows("SELECT COUNT(*) n FROM products WHERE stock<5")[0]["n"]
    top=db_rows("SELECT name,SUM(quantity) quantity FROM order_items WHERE cancelled=0 GROUP BY name ORDER BY quantity DESC LIMIT 5")
    return render_template("dashboard.html",sales=sales,active=active,low=low,top=top)

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        try:
            username=clean_text(request.form.get("username"),40); password=request.form.get("password","")
            user=db_rows("SELECT * FROM users WHERE username=?",(username,))
            if user and check_password_hash(user[0]["password"],password):
                session.update(user_id=user[0]["id"],username=username,role=user[0]["role"],name=user[0]["name"])
                if user[0]["role"]=="customer":
                    pending=session.pop("pending_qr_table",None)
                    if pending:
                        session["qr_table_id"]=int(pending)
                    return redirect(url_for("customer_order"))
                return redirect(url_for("dashboard"))
            flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "error")
        except ValueError as e: flash(str(e),"error")
    return render_template("login.html")
@app.route("/register",methods=["GET","POST"])
def register():
    if request.method=="POST":
        try:
            raw_username=request.form.get("username","")
            raw_name=request.form.get("name","")
            if raw_username[:1].isspace(): raise ValueError("ชื่อผู้ใช้ห้ามเว้นวรรคเป็นตัวอักษรตัวแรก")
            if raw_name[:1].isspace(): raise ValueError("ชื่อที่แสดงห้ามเว้นวรรคเป็นตัวอักษรตัวแรก")
            username=clean_text(raw_username,40)
            name=clean_text(raw_name,80)
            if not username: raise ValueError("กรุณาระบุชื่อผู้ใช้")
            if not name: raise ValueError("กรุณาระบุชื่อที่แสดง")
            password=request.form.get("password","")
            confirm=request.form.get("password_confirm","")
            if len(password)<8: raise ValueError("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร")
            if password != confirm: raise ValueError("รหัสผ่านและการยืนยันรหัสผ่านไม่ตรงกัน")
            uid=db_write("INSERT INTO users(username,password,role,name) VALUES(?,?,?,?)",
                          (username,generate_password_hash(password),"customer",name))
            db_write("INSERT OR IGNORE INTO members(user_id,points) VALUES(?,0)",(uid,))
            flash("สมัครสมาชิกสำเร็จ กรุณาเข้าสู่ระบบ","success"); return redirect(url_for("login"))
        except sqlite3.IntegrityError: flash("ชื่อผู้ใช้นี้มีแล้ว","error")
        except ValueError as e: flash(str(e),"error")
    return render_template("register.html")
@app.route("/logout",methods=["GET","POST"])
def logout(): session.clear(); return redirect(url_for("login"))

@app.route("/menu",methods=["GET","POST"])
@require("manage")
def menu():
    if request.method=="POST":
        try:
            name=clean_text(request.form.get("name")); category=clean_text(request.form.get("category")); price=float(request.form.get("price","")); stock=int(request.form.get("stock","")); ok,msg=validate_product(name,price,stock,category)
            if not ok: raise ValueError(msg)
            options=clean_text(request.form.get("options","-") or "-",200); image=""; file=request.files.get("image")
            if file and file.filename:
                image=save_uploaded_menu_image(file)
            pid=db_write("INSERT INTO products(name,category,price,stock,image,available,options) VALUES(?,?,?,?,?,?,?)",(name,category,price,stock,image,int(stock>0),options))
            storage.audit(session["username"],"create","product",pid,{"name":name}); flash("เพิ่มเมนูแล้ว","success")
        except sqlite3.IntegrityError: flash("ชื่อเมนูซ้ำ","error")
        except (ValueError,TypeError) as e: flash(str(e) or "ข้อมูลไม่ถูกต้อง","error")
        return redirect(url_for("menu"))
    q=request.args.get("q",""); cat=request.args.get("category",""); sort=request.args.get("sort","name"); rows=[dict(r) for r in db_rows("SELECT * FROM products")]; filtered=filter_sort_products(rows,q,cat,sort); page=int(request.args.get("page",1)); products,pages=paginate(filtered,page,8)
    return render_template("menu.html",products=products,pages=pages,page=page,q=q,category=cat,sort=sort,categories=MENU_CATEGORIES)
@app.post("/menu/<int:pid>/edit")
@require("manage")
def edit_product(pid):
    try:
        existing=db_rows("SELECT * FROM products WHERE id=?",(pid,))
        if not existing: abort(404)
        name=clean_text(request.form.get("name")); category=clean_text(request.form.get("category")); price=float(request.form.get("price","")); stock=int(request.form.get("stock","")); ok,msg=validate_product(name,price,stock,category)
        if not ok: raise ValueError(msg)
        image=existing[0]["image"] or ""; file=request.files.get("image")
        if request.form.get("remove_image"): image=""
        elif file and file.filename: image=save_uploaded_menu_image(file)
        db_write("UPDATE products SET name=?,category=?,price=?,stock=?,available=?,image=? WHERE id=?",(name,category,price,stock,int(stock>0),image,pid)); storage.audit(session["username"],"update","product",pid,{"name":name,"stock":stock,"image_changed":bool(file and file.filename) or bool(request.form.get("remove_image"))}); flash("บันทึกการแก้ไขแล้ว","success")
    except sqlite3.IntegrityError: flash("ชื่อเมนูซ้ำ","error")
    except (ValueError,TypeError) as e: flash(str(e) or "ข้อมูลไม่ถูกต้อง","error")
    return redirect(url_for("menu"))
@app.post("/menu/<int:pid>/delete")
@require("manage")
def delete_product(pid):
    db_write("DELETE FROM products WHERE id=?",(pid,)); storage.audit(session["username"],"delete","product",pid,{}); flash("ลบเมนูแล้ว","success"); return redirect(url_for("menu"))

@app.get("/customer/table/<int:tid>/scan")
def customer_table_scan(tid):
    token=request.args.get("token","")
    table=db_rows("SELECT * FROM dining_tables WHERE id=?",(tid,))
    if not table or not verify_table_qr(tid,token):
        session.pop("pending_qr_table",None)
        session.pop("qr_table_id",None)
        return render_template(
            "customer_scan_required.html",
            scan_error="QR Code ไม่ถูกต้อง หรือ QR ของโต๊ะนี้ไม่สามารถใช้งานได้ กรุณาสแกน QR ที่ติดอยู่บนโต๊ะอีกครั้ง"
        ), 400
    session["pending_qr_table"]=tid
    if session.get("role")=="customer":
        session["qr_table_id"]=tid
        session.pop("pending_qr_table",None)
        flash(f"สแกน QR สำเร็จ: {table[0]['label']} สามารถสั่งอาหารได้แล้ว","success")
        return redirect(url_for("customer_order"))
    flash("สแกน QR สำเร็จ กรุณาเข้าสู่ระบบสมาชิกก่อน แล้วระบบจะผูกบัญชีกับโต๊ะนี้","info")
    return redirect(url_for("login"))

@app.get("/customer/table/<int:tid>/qr")
def table_qr(tid):
    """Generate a table QR as SVG without relying on Pillow/PNG handling.

    SVG is used here because it is much more reliable on serverless Python
    runtimes than creating a temporary PNG file or using PIL image responses.
    The SVG is a normal image and can be displayed/printed/scanned directly.
    """
    table=db_rows("SELECT id FROM dining_tables WHERE id=?",(tid,))
    if not table:
        abort(404)
    target=table_scan_url(tid)
    try:
        qr=qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=4,
        )
        qr.add_data(target)
        qr.make(fit=True)
        image=qr.make_image(image_factory=SvgImage)
        svg=image.to_string()
        return Response(svg, mimetype="image/svg+xml", headers={
            "Cache-Control":"no-store, max-age=0",
            "Content-Disposition":f'inline; filename="table-{tid}-qr.svg"'
        })
    except Exception:
        app.logger.exception("Unable to generate QR for table %s", tid)
        abort(500, description="ไม่สามารถสร้าง QR Code ของโต๊ะนี้ได้")

@app.route("/tables",methods=["GET","POST"])
@require("manage")
def tables():
    if request.method=="POST":
        try:
            label=clean_text(request.form.get("label")); capacity=int(request.form.get("capacity","")); status=request.form.get("status","ว่าง")
            if capacity<1 or status not in ("ว่าง","มีลูกค้า","จอง"): raise ValueError("จำนวนที่นั่งหรือสถานะไม่ถูกต้อง")
            tid=db_write("INSERT INTO dining_tables(label,capacity,status,is_custom) VALUES(?,?,?,1)",(label,capacity,status)); storage.audit(session["username"],"create","table",tid,{"label":label}); flash("เพิ่มโต๊ะแล้ว","success")
        except sqlite3.IntegrityError: flash("ชื่อโต๊ะซ้ำ","error")
        except (ValueError,TypeError) as e: flash(str(e),"error")
    return render_template("tables.html",tables=db_rows("SELECT t.*,o.id order_id FROM dining_tables t LEFT JOIN orders o ON o.table_id=t.id AND o.status NOT IN ('ชำระแล้ว','ยกเลิก') ORDER BY t.id"))
@app.post("/tables/<int:tid>/delete")
@require("manage")
def delete_table(tid):
    table=db_rows("SELECT * FROM dining_tables WHERE id=?",(tid,))
    if not table: abort(404)
    if not table[0]["is_custom"]:
        flash("ลบได้เฉพาะโต๊ะที่เพิ่มเอง","error"); return redirect(url_for("tables"))
    history=db_rows("SELECT COUNT(*) n FROM orders WHERE table_id=?",(tid,))[0]["n"]
    if history:
        flash("ลบโต๊ะนี้ไม่ได้ เพราะมีประวัติออเดอร์ผูกอยู่","error"); return redirect(url_for("tables"))
    db_write("DELETE FROM dining_tables WHERE id=?",(tid,)); storage.audit(session["username"],"delete","table",tid,{"label":table[0]["label"]}); flash("ลบโต๊ะที่เพิ่มแล้ว","success"); return redirect(url_for("tables"))
@app.post("/tables/<int:tid>/status")
@require("manage")
def table_status(tid):
    status=request.form.get("status")
    if status not in ("ว่าง","มีลูกค้า","จอง"): abort(400)
    db_write("UPDATE dining_tables SET status=? WHERE id=?",(status,tid)); storage.audit(session["username"],"update","table",tid,{"status":status}); return redirect(url_for("tables"))

@app.route("/pos",methods=["GET","POST"])
@require("sell")
def pos():
    if request.method=="POST":
        try:
            tid=int(request.form.get("table_id")); table=db_rows("SELECT * FROM dining_tables WHERE id=?",(tid,))
            if not table or table[0]["status"]=="จอง": raise ValueError("โต๊ะนี้ยังไม่พร้อมรับออเดอร์")
            items=[]
            for key,value in request.form.items():
                if key.startswith("qty_") and int(value or 0)>0:
                    pid=int(key[4:]); product=db_rows("SELECT * FROM products WHERE id=?",(pid,))
                    if not product or not product[0]["available"]: raise ValueError("เมนูนี้หมดหรือไม่มีในระบบ")
                    qty=int(value)
                    reserved=db_rows("SELECT COALESCE(SUM(i.quantity),0) n FROM order_items i JOIN orders o ON o.id=i.order_id WHERE i.product_id=? AND i.cancelled=0 AND o.status NOT IN ('ชำระแล้ว','ยกเลิก')",(pid,))[0]["n"]
                    available=int(product[0]["stock"])-int(reserved)
                    ok,msg=check_order_stock([(product[0]["name"],available,qty)])
                    if not ok: raise ValueError(msg)
                    portion,extra=rice_portion(product[0]["name"],product[0]["category"],request.form.get("portion_"+str(pid),"ปกติ"))
                    note=clean_text(request.form.get("note_"+str(pid),""),300)
                    items.append((product[0],qty,request.form.get("opt_"+str(pid),""),portion,extra,note))
            if not items: raise ValueError("เลือกอย่างน้อยหนึ่งเมนู")
            oid=db_write("INSERT INTO orders(table_id,customer,status,created_at) VALUES(?,?,?,datetime('now','localtime'))",(tid,request.form.get("customer",""),"รอรับออเดอร์"))
            for p,qty,opt,portion,extra,note in items:
                options=", ".join(part for part in (opt,portion) if part)
                db_write("INSERT INTO order_items(order_id,product_id,name,quantity,price,options,note) VALUES(?,?,?,?,?,?,?)",(oid,p["id"],p["name"],qty,float(p["price"])+extra,options,note))
            db_write("UPDATE dining_tables SET status='มีลูกค้า' WHERE id=?",(tid,)); storage.audit(session["username"],"create","order",oid,{"table":tid}); flash(f"รับออเดอร์ #{oid} แล้ว","success"); return redirect(url_for("orders"))
        except (ValueError,TypeError) as e: flash(str(e),"error")
    return render_template("pos.html",products=db_rows("SELECT * FROM products WHERE available=1 ORDER BY category,name"),tables=db_rows("SELECT * FROM dining_tables WHERE status!='จอง'"),best_ids=best_selling_product_ids())

@app.get("/customer/scan-required")
def customer_scan_required():
    return render_template("customer_scan_required.html", scan_error=None)

@app.route("/customer/order",methods=["GET","POST"])
@require("order")
def customer_order():
    if not customer_table_verified():
        return redirect(url_for("login") if not session.get("user_id") else url_for("customer_scan_required"))
    verified_tid=int(session["qr_table_id"])
    if request.method=="POST":
        try:
            tid=int(request.form.get("table_id") or verified_tid)
            if tid != verified_tid: raise ValueError("ต้องสั่งอาหารจากโต๊ะที่สแกน QR ไว้เท่านั้น")
            table=db_rows("SELECT * FROM dining_tables WHERE id=?",(tid,))
            if not table or table[0]["status"]=="จอง": raise ValueError("โต๊ะนี้ถูกจองแล้ว กรุณาเลือกโต๊ะอื่น")
            items=[]
            for key,value in request.form.items():
                if key.startswith("qty_") and int(value or 0)>0:
                    pid=int(key[4:]); product=db_rows("SELECT * FROM products WHERE id=?",(pid,))
                    if not product or not product[0]["available"]: raise ValueError("เมนูนี้หมดหรือไม่มีในระบบ")
                    qty=int(value)
                    reserved=db_rows("SELECT COALESCE(SUM(i.quantity),0) n FROM order_items i JOIN orders o ON o.id=i.order_id WHERE i.product_id=? AND i.cancelled=0 AND o.status NOT IN ('ชำระแล้ว','ยกเลิก')",(pid,))[0]["n"]
                    available=int(product[0]["stock"])-int(reserved)
                    ok,msg=check_order_stock([(product[0]["name"],available,qty)])
                    if not ok: raise ValueError(msg)
                    portion,extra=rice_portion(product[0]["name"],product[0]["category"],request.form.get("portion_"+str(pid),"ปกติ"))
                    note=clean_text(request.form.get("note_"+str(pid),""),300)
                    items.append((product[0],qty,request.form.get("opt_"+str(pid),""),portion,extra,note))
            if not items: raise ValueError("เลือกอย่างน้อยหนึ่งเมนู")
            oid=db_write("INSERT INTO orders(table_id,customer,customer_user_id,status,created_at) VALUES(?,?,?,?,datetime('now','localtime'))",(tid,session["name"],session["user_id"],"รอรับออเดอร์"))
            for product,qty,option,portion,extra,note in items:
                options=", ".join(part for part in (option,portion) if part)
                db_write("INSERT INTO order_items(order_id,product_id,name,quantity,price,options,note) VALUES(?,?,?,?,?,?,?)",(oid,product["id"],product["name"],qty,float(product["price"])+extra,options,note))
            db_write("UPDATE dining_tables SET status='มีลูกค้า' WHERE id=?",(tid,))
            storage.audit(session["username"],"create","customer_order",oid,{"table":tid})
            flash(f"ส่งคำสั่งซื้อ #{oid} ให้ร้านแล้ว","success")
            return redirect(url_for("customer_history"))
        except (ValueError,TypeError) as e:
            flash(str(e),"error")
    categories=MENU_CATEGORIES
    category=request.args.get("category","")
    products=db_rows("SELECT * FROM products WHERE available=1 AND (?='' OR category=?) ORDER BY category,name",(category,category))
    table=db_rows("SELECT * FROM dining_tables WHERE id=?",(verified_tid,))
    return render_template("customer_order.html",products=products,categories=categories,category=category,
                           tables=table,best_ids=best_selling_product_ids(),verified_table=table[0] if table else None)

@app.get("/customer/member")
@require("order")
def customer_member():
    member=member_for_user(session["user_id"])
    transactions=db_rows("SELECT * FROM point_transactions WHERE member_id=? ORDER BY id DESC LIMIT 100",(member["id"],))
    return render_template("customer_member.html",transactions=transactions)

@app.get("/customer/history")
@require("order")
def customer_history():
    return render_template("customer_history.html",orders=db_rows("SELECT o.*,t.label FROM orders o LEFT JOIN dining_tables t ON t.id=o.table_id WHERE o.customer_user_id=? ORDER BY o.id DESC",(session["user_id"],)))

@app.route("/customer/reservations",methods=["GET","POST"])
@require("order")
def customer_reservations():
    if request.method=="POST":
        try:
            name=clean_text(request.form.get("name")); phone=clean_text(request.form.get("phone","-") or "-",30); guests=int(request.form.get("guests","")); table_id=int(request.form.get("table_id",""))
            start,end=reservation_window(request.form.get("reserved_at"),request.form.get("reserved_end"))
            table=db_rows("SELECT * FROM dining_tables WHERE id=?",(table_id,))
            if not table: raise ValueError("ไม่พบโต๊ะที่เลือก")
            if guests<1: raise ValueError("จำนวนผู้เข้าต้องมากกว่า 0")
            if guests>int(table[0]["capacity"]): raise ValueError(f"โต๊ะ {table[0]['label']} รองรับได้สูงสุด {table[0]['capacity']} คน")
            conflict=reservation_conflict(table_id,start,end)
            if conflict:
                raise ValueError(f"โต๊ะ {table[0]['label']} ถูกจองช่วง {conflict[0]['reserved_at']} - {conflict[0]['reserved_end']} แล้ว กรุณาเลือกช่วงเวลาอื่น")
            rid=db_write("INSERT INTO reservations(name,phone,guests,reserved_at,reserved_end,status,customer_user_id,table_id) VALUES(?,?,?,?,?,?,?,?)",(name,phone,guests,start.strftime("%Y-%m-%dT%H:%M"),end.strftime("%Y-%m-%dT%H:%M"),"รอยืนยัน",session["user_id"],table_id))
            storage.audit(session["username"],"create","customer_reservation",rid,{"table":table_id,"start":start.isoformat(timespec="minutes"),"end":end.isoformat(timespec="minutes")}); flash("ส่งคำขอจองโต๊ะตามช่วงเวลาแล้ว","success")
        except (ValueError,TypeError) as e: flash(str(e) or "ข้อมูลการจองไม่ถูกต้อง","error")
    reservations=db_rows("SELECT r.*,t.label table_label FROM reservations r LEFT JOIN dining_tables t ON t.id=r.table_id WHERE r.customer_user_id=? ORDER BY r.reserved_at DESC",(session["user_id"],))
    return render_template("customer_reservations.html",reservations=reservations,tables=db_rows("SELECT * FROM dining_tables ORDER BY label"))

@app.post("/customer/reservations/<int:rid>/cancel")
@require("order")
def cancel_customer_reservation(rid):
    reservation=db_rows("SELECT * FROM reservations WHERE id=? AND customer_user_id=?",(rid,session["user_id"]))
    if not reservation: abort(404)
    if reservation[0]["status"]!="ยกเลิก":
        db_write("UPDATE reservations SET status='ยกเลิก' WHERE id=?",(rid,))
        storage.audit(session["username"],"cancel","reservation",rid,{})
    flash("ยกเลิกคำขอจองแล้ว","success"); return redirect(url_for("customer_reservations"))
@app.route("/orders")
@require("sell")
def orders(): return render_template("orders.html",orders=db_rows("SELECT o.*,t.label FROM orders o LEFT JOIN dining_tables t ON t.id=o.table_id ORDER BY o.created_at DESC,o.id DESC"))
@app.post("/orders/<int:oid>/item/<int:iid>/cancel")
@require("sell")
def cancel_item(oid,iid):
    db_write("UPDATE order_items SET cancelled=1 WHERE id=? AND order_id=?",(iid,oid)); storage.audit(session["username"],"cancel","order_item",iid,{"order":oid}); flash("ยกเลิกรายการแล้ว","success"); return redirect(url_for("order_detail",oid=oid))
@app.get("/orders/<int:oid>")
@require("sell")
def order_detail(oid):
    order=db_rows("SELECT o.*,t.label FROM orders o LEFT JOIN dining_tables t ON t.id=o.table_id WHERE o.id=?",(oid,))
    if not order: abort(404)
    items=db_rows("SELECT * FROM order_items WHERE order_id=?",(oid,))
    bill_preview=calculate_bill([dict(i) for i in items if not i["cancelled"]],float(order[0]["discount"] or 0),float(order[0]["service_rate"] or 0),float(order[0]["vat_rate"] or 0))
    member=member_for_user(order[0]["customer_user_id"]) if order[0]["customer_user_id"] else None
    return render_template("order.html",order=order[0],items=items,tables=db_rows("SELECT * FROM dining_tables WHERE status='ว่าง'"),bill_preview=bill_preview,member_points=int(member["points"]) if member else 0)
@app.post("/orders/<int:oid>/checkout")
@require("sell")
def checkout(oid):
    order=db_rows("SELECT * FROM orders WHERE id=?",(oid,)); items=db_rows("SELECT * FROM order_items WHERE order_id=? AND cancelled=0",(oid,))
    if not order or not items: abort(400)
    if order[0]["status"]=="ชำระแล้ว": return redirect(url_for("receipt",oid=oid))
    try:
        discount=float(request.form.get("discount",0))
        redeem=int(request.form.get("points_redeemed",0) or 0)
        service_rate=float(request.form.get("service_rate",10))/100; vat_rate=float(request.form.get("vat_rate",7))/100
        if discount<0 or redeem<0 or service_rate<0 or vat_rate<0 or service_rate>1 or vat_rate>1: raise ValueError("อัตราส่วนลด/ภาษีไม่ถูกต้อง")
        if order[0]["customer_user_id"]:
            member=member_for_user(order[0]["customer_user_id"])
            available_points=int(member["points"]) if member else 0
            if redeem>available_points: raise ValueError("แต้มสมาชิกไม่พอ")
            point_discount=redeem*POINT_VALUE_BAHT
            subtotal_for_points=sum(float(i["price"]) * int(i["quantity"]) for i in items)
            manual_discount=max(float(request.form.get("discount",0)),0)
            if manual_discount + point_discount > subtotal_for_points:
                raise ValueError("แต้มที่ใช้มากเกินส่วนลดที่สามารถใช้กับบิลนี้ได้")
        else:
            if redeem: raise ValueError("ออเดอร์นี้ไม่ใช่ออเดอร์สมาชิก")
            point_discount=0
        discount += point_discount
        bill=calculate_bill([dict(i) for i in items],discount,service_rate,vat_rate)
        # Award 10 points for every completed 100 baht of food subtotal.
        earned=(int(bill["subtotal"])//100)*POINTS_PER_100 if order[0]["customer_user_id"] else 0
        paid_text=request.form.get("paid","").strip(); paid=float(paid_text) if paid_text else bill["total"]
        if paid<bill["total"]: raise ValueError("ยอดรับชำระไม่พอ")
        db_write("INSERT INTO receipts(order_id,subtotal,discount,service,vat,total,payment_method,paid,change_due,created_at) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'))",(oid,bill["subtotal"],bill["discount"],bill["service"],bill["vat"],bill["total"],request.form.get("payment_method","เงินสด"),paid,round(paid-bill["total"],2)))
        for i in items: db_write("UPDATE products SET stock=MAX(stock-?,0),available=CASE WHEN stock-?<=0 THEN 0 ELSE available END WHERE id=?",(i["quantity"],i["quantity"],i["product_id"]))
        db_write("UPDATE orders SET status='ชำระแล้ว',discount=?,service_rate=?,vat_rate=?,points_redeemed=?,points_earned=? WHERE id=?",
                 (discount,service_rate,vat_rate,redeem,earned,oid))
        if order[0]["customer_user_id"]:
            member=member_for_user(order[0]["customer_user_id"])
            if redeem:
                db_write("UPDATE members SET points=points-? WHERE user_id=?",(redeem,order[0]["customer_user_id"]))
                db_write("INSERT INTO point_transactions(member_id,order_id,points,type,note,created_at) VALUES(?,?,?,?,?,datetime('now','localtime'))",
                         (member["id"],oid,-redeem,"redeem","ใช้แต้มเป็นส่วนลด"))
            if earned:
                db_write("UPDATE members SET points=points+? WHERE user_id=?",(earned,order[0]["customer_user_id"]))
                db_write("INSERT INTO point_transactions(member_id,order_id,points,type,note,created_at) VALUES(?,?,?,?,?,datetime('now','localtime'))",
                         (member["id"],oid,earned,"earn","สะสม 10 แต้มต่อยอดอาหารทุก 100 บาท"))
        db_write("UPDATE dining_tables SET status='ว่าง' WHERE id=?",(order[0]["table_id"],))
        storage.audit(session["username"],"checkout","order",oid,{**bill,"points_redeemed":redeem,"points_earned":earned})
        return redirect(url_for("receipt",oid=oid))
    except (ValueError,TypeError) as e: flash(str(e),"error"); return redirect(url_for("order_detail",oid=oid))
@app.get("/receipt/<int:oid>")
@signed_in
def receipt(oid):
    r=db_rows("SELECT r.*,o.id order_id,t.label FROM receipts r JOIN orders o ON o.id=r.order_id LEFT JOIN dining_tables t ON t.id=o.table_id WHERE o.id=?",(oid,))
    if not r: abort(404)
    return render_template("receipt.html",receipt=r[0],items=db_rows("SELECT * FROM order_items WHERE order_id=? AND cancelled=0",(oid,)))

@app.route("/kitchen")
@require("kitchen")
def kitchen(): return render_template("kitchen.html",orders=db_rows("SELECT o.*,t.label FROM orders o LEFT JOIN dining_tables t ON t.id=o.table_id WHERE o.status NOT IN ('ชำระแล้ว','ยกเลิก','เสร็จแล้ว') ORDER BY o.created_at,o.id"))
@app.post("/kitchen/<int:oid>/status")
@require("kitchen")
def kitchen_status(oid):
    status=request.form.get("status")
    if status not in ("กำลังทำ","เสร็จแล้ว"): abort(400)
    db_write("UPDATE orders SET status=? WHERE id=?",(status,oid)); db_write("UPDATE order_items SET status=? WHERE order_id=? AND cancelled=0",(status,oid)); storage.audit(session["username"],"update","order",oid,{"status":status}); return redirect(url_for("kitchen"))
@app.get("/reports")
@require("report")
def reports():
    summary=db_rows("SELECT COUNT(*) bills,COALESCE(SUM(total),0) revenue,COALESCE(AVG(total),0) average FROM receipts")[0]
    best=db_rows("SELECT name,SUM(quantity) quantity,SUM(quantity*price) revenue FROM order_items WHERE cancelled=0 GROUP BY name ORDER BY quantity DESC LIMIT 10")
    daily=db_rows("SELECT date(created_at) day,SUM(total) revenue,COUNT(*) bills FROM receipts GROUP BY date(created_at) ORDER BY day DESC LIMIT 14")
    return render_template("reports.html",summary=summary,best=best,daily=daily)
@app.get("/audit")
@require("manage")
def audit_logs(): return render_template("audit.html",logs=db_rows("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100"))
@app.route("/reservations",methods=["GET","POST"])
@require("manage")
def reservations():
    if request.method=="POST":
        try:
            name=clean_text(request.form.get("name"),80)
            phone=clean_text(request.form.get("phone","-") or "-",30)
            guests=int(request.form.get("guests",""))
            table_id=int(request.form.get("table_id",""))
            start,end=reservation_window(request.form.get("reserved_at"),request.form.get("reserved_end"))
            if not name: raise ValueError("กรุณาระบุชื่อลูกค้า")
            if guests<1: raise ValueError("จำนวนผู้เข้าต้องมากกว่า 0")
            table=db_rows("SELECT * FROM dining_tables WHERE id=?",(table_id,))
            if not table: raise ValueError("ไม่พบโต๊ะที่เลือก")
            if guests>int(table[0]["capacity"]): raise ValueError(f"โต๊ะ {table[0]['label']} รองรับได้สูงสุด {table[0]['capacity']} คน")
            conflict=reservation_conflict(table_id,start,end)
            if conflict:
                raise ValueError(f"โต๊ะ {table[0]['label']} ถูกจองช่วง {conflict[0]['reserved_at']} - {conflict[0]['reserved_end']} แล้ว กรุณาเลือกช่วงเวลาอื่น")
            rid=db_write("INSERT INTO reservations(name,phone,guests,reserved_at,reserved_end,status,table_id) VALUES(?,?,?,?,?,?,?)",(name,phone,guests,start.strftime("%Y-%m-%dT%H:%M"),end.strftime("%Y-%m-%dT%H:%M"),"รอยืนยัน",table_id))
            storage.audit(session["username"],"create","reservation",rid,{"name":name,"table":table_id,"start":start.isoformat(timespec="minutes"),"end":end.isoformat(timespec="minutes")})
            flash("บันทึกการจองตามช่วงเวลาแล้ว","success")
        except (ValueError,TypeError) as e: flash(str(e) or "ข้อมูลการจองไม่ถูกต้อง","error")
    return render_template("reservations.html",reservations=db_rows("SELECT r.*,t.label table_label FROM reservations r LEFT JOIN dining_tables t ON t.id=r.table_id ORDER BY r.reserved_at DESC"),tables=db_rows("SELECT * FROM dining_tables ORDER BY label"))

@app.get("/staff/reservations")
@require("reservations")
def staff_reservations():
    rows=db_rows("SELECT r.*,t.label table_label FROM reservations r LEFT JOIN dining_tables t ON t.id=r.table_id ORDER BY CASE r.status WHEN 'รอยืนยัน' THEN 0 WHEN 'พนักงานรับทราบ' THEN 1 ELSE 2 END,r.reserved_at")
    return render_template("staff_reservations.html",reservations=rows)

@app.post("/staff/reservations/<int:rid>/status")
@require("reservations")
def staff_reservation_status(rid):
    status=request.form.get("status")
    if status not in ("พนักงานรับทราบ","ยืนยันแล้ว","ยกเลิก"): abort(400)
    rows=db_rows("SELECT * FROM reservations WHERE id=?",(rid,))
    if not rows: abort(404)
    old=rows[0]
    if old["status"]!="ยกเลิก":
        db_write("UPDATE reservations SET status=? WHERE id=?",(status,rid))
        storage.audit(session["username"],"update","reservation",rid,{"from":old["status"],"to":status})
    flash("อัปเดตสถานะการจองแล้ว","success"); return redirect(url_for("staff_reservations"))
@app.post("/orders/<int:oid>/move")
@require("sell")
def move_table(oid):
    try:
        new=int(request.form.get("table_id")); target=db_rows("SELECT * FROM dining_tables WHERE id=?",(new,)); old=db_rows("SELECT table_id FROM orders WHERE id=?",(oid,))
        if not target or target[0]["status"]!="ว่าง" or not old: raise ValueError("โต๊ะปลายทางไม่ว่างหรือไม่พบออเดอร์")
        db_write("UPDATE dining_tables SET status='ว่าง' WHERE id=?",(old[0]["table_id"],)); db_write("UPDATE dining_tables SET status='มีลูกค้า' WHERE id=?",(new,)); db_write("UPDATE orders SET table_id=? WHERE id=?",(new,oid)); storage.audit(session["username"],"move","order",oid,{"table":new}); flash("ย้ายโต๊ะแล้ว","success")
    except (ValueError,TypeError) as e: flash(str(e),"error")
    return redirect(url_for("order_detail",oid=oid))
@app.get("/download/sample.csv")
@require("manage")
def sample_csv(): return send_file(Path(app.root_path)/"samples"/"menu_sample.csv",as_attachment=True)
@app.post("/upload/menu")
@require("manage")
def upload_menu():
    import csv
    try:
        f=request.files.get("file")
        if not f or not f.filename or not f.filename.lower().endswith(".csv"): raise ValueError("เลือกไฟล์ CSV ก่อน")
        target=Path(app.root_path)/"instance"/"uploaded_menu.csv"; target.parent.mkdir(exist_ok=True); f.save(target)
        with target.open(encoding="utf-8-sig",newline="") as src:
            for row in csv.DictReader(src):
                name=clean_text(row["name"]); category=clean_text(row["category"]); price=float(row["price"]); stock=int(row["stock"]); ok,msg=validate_product(name,price,stock,category)
                if not ok: raise ValueError(msg)
                db_write("INSERT INTO products(name,category,price,stock,available) VALUES(?,?,?,?,?)",(name,category,price,stock,int(stock>0)))
        flash("นำเข้าเมนูจาก CSV แล้ว","success")
    except Exception as e: flash(f"นำเข้าไม่สำเร็จ: {e}","error")
    return redirect(url_for("menu"))
@app.errorhandler(403)
def forbidden(e): return render_template("error.html",code=403,message="บัญชีนี้ไม่มีสิทธิ์ทำรายการนี้"),403
@app.errorhandler(413)
def too_large(e): return render_template("error.html",code=413,message="ไฟล์ใหญ่เกิน 4 MB"),413
@app.errorhandler(404)
def missing(e): return render_template("error.html",code=404,message="ไม่พบหน้าหรือข้อมูลที่ต้องการ"),404
if __name__=="__main__": app.run(host=os.environ.get("FLASK_HOST","0.0.0.0"), port=int(os.environ.get("PORT","5000")), debug=os.environ.get("FLASK_DEBUG")=="1")
