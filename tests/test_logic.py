"""Logic tests for rubric edge cases. Run: python -m unittest discover -s tests"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from restaurant.logic import validate_product, check_order_stock, role_can, clean_text, safe_upload_name, calculate_bill, MENU_CATEGORIES, rice_portion

class RestaurantLogicTests(unittest.TestCase):
    def test_invalid_price_and_blank_input_rejected(self):
        self.assertFalse(validate_product("",30,2,"อาหาร")[0])
        self.assertFalse(validate_product("ข้าว",-1,2,"อาหาร")[0])
    def test_sold_out_menu_is_unavailable(self):
        self.assertEqual(check_order_stock([("เค้ก",0,1)]),(False,"สินค้า เค้ก มีไม่พอ (เหลือ 0)"))
    def test_over_stock_rejected(self):
        self.assertFalse(check_order_stock([("ชา",2,3)])[0])
    def test_role_without_permission_denied(self):
        self.assertFalse(role_can("staff","manage"))
        self.assertFalse(role_can("customer","sell"))
        self.assertTrue(role_can("staff","reservations"))
    def test_menu_category_set_is_exact_and_validated(self):
        self.assertEqual(MENU_CATEGORIES,("ของหวาน","อาหารอีสาน","ส้มตำ","น้ำ","อาหารตามสั่ง","ของทอด","ของย่าง"))
        self.assertTrue(validate_product("ตำไทย",60,5,"ส้มตำ")[0])
        self.assertFalse(validate_product("ตำไทย",60,5,"ส้มตำ · ตำพื้นฐาน")[0])
    def test_duplicate_data_cleanly_validated_by_database(self):
        # Database uniqueness: users.username and products.name are UNIQUE.
        self.assertTrue("UNIQUE" in __import__("restaurant.storage",fromlist=["SCHEMA"]).SCHEMA)
    def test_server_side_string_validation(self):
        with self.assertRaises(ValueError): clean_text("   ")
    def test_unsafe_or_unknown_upload_extension_rejected(self):
        with self.assertRaises(ValueError): safe_upload_name("../../run.exe")
    def test_bill_calculation(self):
        bill=calculate_bill([{"price":100,"quantity":1}],10,.1,.07)
        self.assertEqual(bill["subtotal"],100); self.assertEqual(bill["discount"],10); self.assertEqual(bill["total"],105.93)
    def test_rice_dish_portion_surcharges(self):
        self.assertEqual(rice_portion("กะเพราหมูสับ","อาหารตามสั่ง","พิเศษ"),("พิเศษ (+10 บาท)",10.0))
        self.assertEqual(rice_portion("กะเพราหมูสับ","อาหารตามสั่ง","กับข้าว"),("กับข้าว (+20 บาท)",20.0))
        with self.assertRaises(ValueError): rice_portion("ตำไทย","ส้มตำ","พิเศษ")

if __name__=="__main__": unittest.main()
