"""系统提示词使用的共享数据库模式说明。

各常量描述相关表、字段、合法值及关系，帮助模型选择工具和解释结果；
其字符串内容属于模型输入，本轮保持不变。
"""

USER_SCHEMA_CONTEXT = (
    "\n## Database: Users\n- **users**: id (UUID), email, name, role, loyalty_t"
    "ier, total_spend, created_at, is_active\n  - Roles: customer, power_user,"
    " seller, admin\n  - Loyalty tiers: bronze (default), silver (spend >= $10"
    "00, 5% discount), gold (spend >= $3000, 10% discount + free shipping + p"
    "riority support)\n  - The current user's email is available — always use "
    "it to scope queries\n"
)

ORDER_SCHEMA_CONTEXT = (
    "\n## Database: Orders & Returns\n- **orders**: id (UUID), user_id (FK→user"
    "s), status, total, shipping_address (JSONB: street/city/state/zip/countr"
    "y), shipping_carrier, tracking_number, coupon_code, discount_amount, cre"
    "ated_at\n  - Status flow: placed → confirmed → shipped → out_for_delivery"
    " → delivered\n  - Also: cancelled (from placed/confirmed), returned (from"
    " delivered)\n- **order_items**: order_id (FK→orders), product_id (FK→prod"
    "ucts), quantity, unit_price, subtotal\n- **order_status_history**: order_"
    "id (FK→orders), status, notes, location, timestamp — full tracking timel"
    "ine\n- **returns**: order_id (FK→orders), user_id (FK→users), reason, sta"
    "tus, return_label_url, refund_method, refund_amount, created_at, resolve"
    "d_at\n  - Return statuses: requested → approved → shipped_back → received"
    " → refunded (or denied)\n  - Refund methods: original_payment, store_cred"
    "it\n  - Return window: 30 days from delivery\n"
)

PRODUCT_SCHEMA_CONTEXT = (
    "\n## Database: Products\n- **products**: id (UUID), name, description, cat"
    "egory, brand, price, original_price, image_url, rating (1.0-5.0), review"
    "_count, specs (JSONB), is_active, created_at\n  - Categories: Electronics"
    ", Clothing, Home, Sports, Books\n  - specs JSONB varies by category (e.g."
    ", Electronics: battery, weight, connectivity; Clothing: material, fit)\n "
    " - If original_price > price, the product is on sale\n- **product_embeddi"
    "ngs**: product_id (FK→products), embedding (vector 1536-dim) — for seman"
    "tic search\n- **price_history**: product_id (FK→products), price, recorde"
    "d_at — 90 days of daily prices\n"
)

INVENTORY_SCHEMA_CONTEXT = """
## Database: Inventory & Shipping
- **warehouses**: id (UUID), name, location, region
  - Regions: east (Richmond VA), central (Dallas TX), west (Portland OR)
- **warehouse_inventory**: warehouse_id + product_id (composite PK), quantity, reorder_threshold
  - low_stock = quantity <= reorder_threshold
- **carriers**: id (UUID), name, speed_tier, base_rate
  - Speed tiers: standard (5-7 days, ~$6-10), express (2-3 days, ~$15-21), overnight (1 day, ~$30-40)
- **shipping_rates**: carrier_id (FK→carriers), region_from, region_to, price, estimated_days_min, estimated_days_max
- **restock_schedule**: product_id (FK→products), warehouse_id (FK→warehouses), expected_quantity, expected_date
"""

PRICING_SCHEMA_CONTEXT = (
    "\n## Database: Pricing & Promotions\n- **coupons**: code (unique), descrip"
    "tion, discount_type, discount_value, min_spend, max_discount, usage_limi"
    "t, times_used, valid_from, valid_until, applicable_categories (TEXT[]), "
    "user_specific_email, is_active\n  - Discount types: percentage (e.g., 10%"
    " off), fixed (e.g., $25 off)\n  - applicable_categories: NULL means all c"
    "ategories, or array like {Electronics,Home}\n  - user_specific_email: NUL"
    "L means any user, or specific email for personal coupons\n- **promotions*"
    "*: name, type, rules (JSONB), start_date, end_date, is_active\n  - Types:"
    " bundle (buy multiple products for discount), buy_x_get_y (quantity disc"
    "ount), flash_sale (category-wide discount)\n  - rules JSONB varies: bundl"
    "e has {products: [...], discount_pct: N}, buy_x_get_y has {category, min"
    "_quantity, discount_pct}\n- **loyalty_tiers**: name, min_spend, discount_"
    "pct, free_shipping_threshold, priority_support\n  - bronze: $0+, 0% disco"
    "unt\n  - silver: $1000+, 5% discount, free shipping over $75\n  - gold: $3"
    "000+, 10% discount, free shipping always, priority support\n"
)

REVIEW_SCHEMA_CONTEXT = (
    "\n## Database: Reviews\n- **reviews**: id (UUID), product_id (FK→products)"
    ", user_id (FK→users), rating (1-5 integer), title, body, verified_purcha"
    "se (boolean), helpful_count, is_flagged (suspicious), created_at\n  - ver"
    "ified_purchase: true if the reviewer actually bought the product\n  - is_"
    "flagged: true for reviews detected as potentially fake (generic language"
    ", 5-star burst, unverified)\n  - Rating distribution across all products "
    "roughly: 5★ 35%, 4★ 35%, 3★ 15%, 2★ 10%, 1★ 5%\n"
)
