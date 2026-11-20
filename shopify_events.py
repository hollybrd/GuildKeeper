"""Optional, quiet Shopify catalog-change announcements.

Default OFF. First poll establishes a baseline, so existing store inventory is
not announced as a surprise new launch when the feature is enabled.


READING GUIDE
-------------
Optional product-change monitoring. Disabled until the merchant explicitly
sets SHOPIFY_AUTO_ANNOUNCE=1. The first scan records a baseline only, so
existing products are not all incorrectly presented as new releases.
"""
import json
import os
from datetime import timedelta
from decimal import Decimal

import shopify_client as client
import announcements_scheduler


api=None


# Check the store-owner opt-in flag from the server configuration.
def enabled():
    return os.getenv("SHOPIFY_AUTO_ANNOUNCE","0").strip()=="1"


# Create per-product snapshots used to compare successive catalog scans.
def ensure_tables():
    with api.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS shopify_catalog_seen (
          guild_id INTEGER NOT NULL, product_id TEXT NOT NULL,
          title TEXT NOT NULL, price TEXT NOT NULL,
          sale INTEGER NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(guild_id,product_id)
        )""")


# Avoid querying Shopify more frequently than the configured polling interval.
def _last_poll():
    with api.connect() as db:
        row=db.execute("SELECT value FROM v12_config WHERE key=?",
                       (f"shopify-last-poll-{api.GUILD_ID}",)).fetchone()
    return row["value"] if row else None


# Compare visible product snapshots and send at most a few new/sale notices.
async def tick():
    policy_new=announcements_scheduler.automation_policy("shop_new")
    policy_sale=announcements_scheduler.automation_policy("shop_sale")
    if not (policy_new["enabled"] or policy_sale["enabled"]):
        return
    ensure_tables()
    now=api.utc_now()
    previous=_last_poll()
    if previous:
        try:
            if now-api.datetime.fromisoformat(previous)<timedelta(minutes=30):
                return
        except (ValueError,TypeError):
            pass
    # Record poll time before lookup, avoiding hammering a blocked storefront.
    with api.connect() as db:
        db.execute("INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)",
                   (f"shopify-last-poll-{api.GUILD_ID}",now.isoformat()))
    try:
        products=await client.catalog(limit=100)
    except (client.ShopifyUnavailable,client.ShopifyError,TimeoutError):
        api.log.warning("Shopify catalog event scan unavailable; not announcing changes")
        return
    if not products:
        return
    with api.connect() as db:
        old={r["product_id"]:r for r in db.execute(
            "SELECT * FROM shopify_catalog_seen WHERE guild_id=?",(api.GUILD_ID,))}
        for item in products:
            db.execute("""INSERT INTO shopify_catalog_seen
              (guild_id,product_id,title,price,sale,updated_at)
              VALUES (?,?,?,?,?,?)
              ON CONFLICT(guild_id,product_id) DO UPDATE SET
              title=excluded.title,price=excluded.price,sale=excluded.sale,
              updated_at=excluded.updated_at""",
              (api.GUILD_ID,item["id"],item["title"],str(item["price"]),
               int(item["sale"]),now.isoformat()))
    if not old:
        api.log.info("Shopify catalog baseline initialized with %s products",len(products))
        return
    # Prevent floods of notifications after large imports. Remaining product
    # changes are synchronized silently; admins can schedule curated launches.
    changes=[]
    for item in products:
        before=old.get(item["id"])
        if before is None:
            changes.append(("new",item))
        elif ((item["sale"] and not before["sale"]) or
              item["price"]<Decimal(before["price"])):
            changes.append(("sale",item))
    for kind,item in changes[:3]:
        if kind=="new":
            title="✨ New GYMGUILD drop"
            body=f'**{item["title"]}** is now available.\n{item["url"]}\n'
        else:
            title="🏷️ GYMGUILD price update"
            body=f'**{item["title"]}** is now {item["price"]:.2f} GBP.\n{item["url"]}\n'
        await announcements_scheduler.configured_notice(
            "shop_new" if kind=="new" else "shop_sale",
            f"shopify-{kind}-{item['id']}-{str(item['price'])}",title,body)
    if len(changes)>3:
        api.log.warning("Detected %s Shopify product changes; announced at most 3",len(changes))


# Initialize the module and its SQLite tables when GuildKeeper starts.
def install(module):
    global api
    api=module
    ensure_tables()
