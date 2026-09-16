"""Tool usage examples for agent system prompts.

Shows the LLM how to call each tool and what responses look like.
"""

ORDER_TOOL_EXAMPLES = (
    "\n## Tool Usage Guide\n\n### get_user_orders(status?, limit?)\nLists the cur"
    "rent user's orders. No need to pass email — it uses the logged-in user a"
    "utomatically.\n- `get_user_orders()` → all orders\n- `get_user_orders(stat"
    'us="shipped")` → only shipped orders\n- `get_user_orders(status="placed",'
    " limit=5)` → up to 5 placed orders\n- Valid statuses: placed, confirmed, "
    'shipped, out_for_delivery, delivered, cancelled, returned\n→ Returns: [{"'
    'order_id": "uuid", "status": "shipped", "total": 129.99, "item_count": 2'
    ', "date": "2026-03-15T..."}]\n\n### get_order_details(order_id)\nFull order'
    ' info including items, shipping, and status timeline.\n→ Returns: {"order'
    '_id": "...", "status": "shipped", "total": 129.99, "items": [{"name": "S'
    'ony WH-1000XM5", "quantity": 1, "unit_price": 299.99}], "status_history"'
    ': [{"status": "placed", "timestamp": "..."}], "shipping_address": {"stre'
    'et": "...", "city": "...", "state": "..."}}\n\n### get_order_tracking(orde'
    "r_id)\nLatest tracking status and full timeline for shipped orders.\n→ Ret"
    'urns: {"current_status": "shipped", "location": "Distribution Center", "'
    'timeline": [...]}\n\n### cancel_order(order_id, reason)\nCancel an order. O'
    'nly works if status is "placed" or "confirmed".\n→ Returns: {"success": t'
    'rue, "refund_amount": 129.99} or {"error": "Cannot cancel — order alread'
    'y shipped"}\n\n### modify_order(order_id, new_address)\nChange shipping add'
    "ress. Only works if not yet shipped.\n- new_address must have: street, ci"
    "ty, state, zip, country\n\n### check_return_eligibility(order_id)\nCheck if"
    " an order can be returned (must be delivered within 30 days).\n→ Returns:"
    ' {"eligible": true, "days_remaining": 15} or {"eligible": false, "reason'
    '": "..."}\n\n### initiate_return(order_id, reason, refund_method)\nStart a '
    'return. refund_method: "original_payment" or "store_credit"\n→ Returns: {'
    '"return_id": "...", "label_url": "https://...", "status": "requested"}\n\n'
    "### get_user_profile()\nCurrent user's profile. No parameters needed.\n→ R"
    'eturns: {"name": "Alice Johnson", "email": "alice@example.com", "role": '
    '"customer", "loyalty_tier": "gold", "total_spend": 4200.00}\n'
)

PRODUCT_TOOL_EXAMPLES = (
    "\n## Tool Usage Guide\n\n### search_products(query, category?, min_price?, "
    "max_price?, min_rating?, sort_by?, limit?)\nSearch by keywords with optio"
    'nal filters.\n- `search_products(query="wireless headphones")` → text sea'
    'rch\n- `search_products(query="running shoes", category="Clothing", max_p'
    "rice=150)` → filtered\n- sort_by options: price_asc, price_desc, rating, "
    'newest\n→ Returns: [{"id": "uuid", "name": "Sony WH-1000XM5", "price": 29'
    '9.99, "rating": 4.7, "category": "Electronics", "on_sale": true}]\n\n### g'
    "et_product_details(product_id)\nFull product info with specs.\n→ Returns: "
    '{"id": "...", "name": "...", "description": "...", "specs": {"battery": '
    '"30 hours", "weight": "250g"}, "rating": 4.7, "review_count": 45}\n\n### c'
    "ompare_products(product_ids)\nSide-by-side comparison of 2-3 products. Pa"
    "ss a list of UUIDs.\n\n### semantic_search(query, limit?)\nAI-powered searc"
    'h for vague/descriptive queries. Best for: "something cozy for winter", '
    '"gift for a tech enthusiast", "workout gear for beginners"\n→ Returns: [{'
    '"id": "...", "name": "...", "similarity": 0.89, ...}]\n\n### find_similar_'
    "products(product_id, limit?)\nFind products similar to a given product us"
    "ing embeddings.\n\n### get_trending_products(category?, days?, limit?)\nPro"
    "ducts with most orders in the last N days.\n\n### check_stock(product_id)\n"
    'Stock levels across all warehouses.\n→ Returns: {"in_stock": true, "total'
    '_quantity": 150, "warehouses": [{"warehouse": "East", "quantity": 80}, .'
    "..]}\n\n### get_price_history(product_id, days?)\nPrice trends. days: 30, 6"
    '0, or 90.\n→ Returns: {"current_price": 99.99, "average_price": 112.50, "'
    'min_price": 89.99, "trend": "decreasing", "is_good_deal": true}\n\n### get'
    "_purchase_history(limit?)\nCurrent user's past orders for personalized re"
    "commendations.\n"
)

PRICING_TOOL_EXAMPLES = (
    "\n## Tool Usage Guide\n\n### validate_coupon(code, cart_total?, category?)\n"
    "Check if a coupon code is valid and calculate discount.\n- `validate_coup"
    'on(code="WELCOME10")` → basic validation\n- `validate_coupon(code="TECHSA'
    'VE", cart_total=200, category="Electronics")` → with context\n→ Returns: '
    '{"valid": true, "discount_amount": 30.00, "description": "15% off Electr'
    'onics"} or {"valid": false, "reason": "Coupon expired"}\n\n### optimize_ca'
    'rt(product_ids_with_quantities)\nFind the best price for a cart. Pass [{"'
    'product_id": "uuid", "quantity": 1}, ...].\n→ Returns: {"original_total":'
    ' 500.00, "best_coupon": "TECHSAVE", "coupon_discount": 75.00, "loyalty_d'
    'iscount": 50.00, "final_total": 375.00}\n\n### get_active_deals()\nList all'
    ' current promotions and non-expired coupons.\n→ Returns: {"coupons": [...'
    '], "promotions": [...]}\n\n### check_bundle_eligibility(product_ids)\nCheck'
    " if products qualify for bundle/quantity discounts.\n\n### get_loyalty_tie"
    "r()\nCurrent user's tier and benefits. No parameters needed.\n→ Returns: {"
    '"tier": "gold", "discount_pct": 10, "free_shipping_threshold": 0, "prior'
    'ity_support": true}\n\n### calculate_loyalty_discount(cart_total)\nCalculat'
    'e tier-specific discount for a cart total.\n→ Returns: {"tier": "gold", "'
    'discount_pct": 10, "discount_amount": 50.00, "free_shipping": true}\n'
)

REVIEW_TOOL_EXAMPLES = (
    "\n## Tool Usage Guide\n\n### get_product_reviews(product_id, sort_by?, limi"
    "t?, offset?)\nPaginated reviews. sort_by: newest, helpful, rating_high, r"
    'ating_low.\n→ Returns: {"reviews": [{"rating": 5, "title": "Great!", "bod'
    'y": "...", "verified": true, "reviewer": "Alice", "date": "..."}], "tota'
    'l": 45}\n\n### analyze_sentiment(product_id)\nAggregate sentiment analysis '
    'for a product.\n→ Returns: {"avg_rating": 4.5, "total_reviews": 45, "dist'
    'ribution": {"5": 20, "4": 15, "3": 5, "2": 3, "1": 2}, "sentiment": "ver'
    'y_positive", "pros": ["great battery", "comfortable"], "cons": ["heavy",'
    ' "expensive"]}\n\n### get_sentiment_by_topic(product_id)\nBreakdown by topi'
    'c: quality, value, shipping, design, durability.\n→ Returns: {"topics": ['
    '{"topic": "quality", "mentions": 30, "avg_rating": 4.8}, {"topic": "valu'
    'e", "mentions": 20, "avg_rating": 3.9}]}\n\n### get_sentiment_trend(produc'
    't_id, months?)\nMonthly sentiment trend. Returns: {"months": [{"month": "'
    '2026-01", "avg_rating": 4.2, "count": 12}, ...], "direction": "improving'
    '"}\n\n### detect_fake_reviews(product_id)\nFind suspicious reviews. Returns'
    ': {"suspicious_count": 3, "total_reviews": 45, "flagged": [...], "risk_l'
    'evel": "low"}\n\n### search_reviews(product_id, keyword)\nSearch review tex'
    "t for a keyword.\n\n### draft_seller_response(review_id)\nGenerate a profes"
    "sional response template for a review.\n\n### compare_product_reviews(prod"
    "uct_ids)\nCompare sentiment across 2-3 products.\n"
)

INVENTORY_TOOL_EXAMPLES = (
    "\n## Tool Usage Guide\n\n### check_stock(product_id)\nStock across all wareh"
    'ouses. No user context needed.\n→ Returns: {"in_stock": true, "total_quan'
    'tity": 150, "warehouses": [{"warehouse": "East Coast", "region": "east",'
    ' "quantity": 80, "low_stock": false}]}\n\n### get_warehouse_availability(p'
    'roduct_id)\nDetailed availability with restock info.\n→ Returns: {"warehou'
    'ses": [...], "upcoming_restocks": [{"warehouse": "West", "expected_quant'
    'ity": 50, "expected_date": "2026-04-15"}]}\n\n### get_restock_schedule(pro'
    'duct_id)\nWhen out-of-stock products will be restocked.\n→ Returns: [{"war'
    'ehouse": "East", "expected_quantity": 50, "expected_date": "2026-04-15"}'
    "]\n\n### estimate_shipping(product_id, destination_region)\nShipping option"
    's and costs. destination_region: east, central, or west.\n→ Returns: {"op'
    'tions": [{"carrier": "Standard", "price": 5.99, "days": "5-7"}, {"carrie'
    'r": "Express", "price": 14.99, "days": "2-3"}]}\n\n### compare_carriers(re'
    "gion_from, region_to)\nCompare all carriers for a route.\n\n### get_trackin"
    "g_status(order_id)\nTracking for a specific order (user-scoped).\n\n### cal"
    "culate_fulfillment_plan(product_ids, destination_region)\nOptimal warehou"
    "se routing for multi-item orders.\n\n### place_backorder(product_id, quant"
    "ity)\nCreate a backorder for out-of-stock items.\n"
)
