"""Shopify API and public catalog adapter for GuildKeeper.

Public shop browsing works without an admin token if the storefront publishes a
products.json feed. Private order verification and discount creation require a
separately configured limited-scope Shopify Admin app on Oracle.
"""
import os
import re
import secrets
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlparse

import aiohttp

SHOP_URL = "https://gymguild.co.uk"
API_VERSION = "2026-10"
ORDER_REF = re.compile(r"^[#A-Za-z0-9_-]{1,48}$")
SAFE_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
ORDER_QUERY = """query($search:String!){
  orders(first:10,query:$search){
    nodes { id name email displayFinancialStatus cancelledAt
      displayFulfillmentStatus totalPriceSet { shopMoney { amount currencyCode } } }
  }
}"""
CODE_MUTATION = """mutation($input:DiscountCodeBasicInput!){
  discountCodeBasicCreate(basicCodeDiscount:$input){
    codeDiscountNode { id }
    userErrors { field message }
  }
}"""


class ShopifyUnavailable(RuntimeError):
    pass


class ShopifyError(RuntimeError):
    pass


def public_domain():
    """A literal merchant-approved domain; never accept arbitrary user-supplied host."""
    return SHOP_URL


def admin_configured():
    return bool(os.getenv("SHOPIFY_ADMIN_DOMAIN") and os.getenv("SHOPIFY_ADMIN_TOKEN"))


def admin_endpoint():
    domain = os.getenv("SHOPIFY_ADMIN_DOMAIN", "").strip().lower()
    if not domain.endswith(".myshopify.com") or not SAFE_DOMAIN.fullmatch(domain):
        raise ShopifyUnavailable("Configure SHOPIFY_ADMIN_DOMAIN to your store's .myshopify.com address.")
    if not os.getenv("SHOPIFY_ADMIN_TOKEN"):
        raise ShopifyUnavailable("Configure SHOPIFY_ADMIN_TOKEN securely on the Oracle deployment.")
    return f"https://{domain}/admin/api/{API_VERSION}/graphql.json"


async def catalog(limit=30):
    """Only online-visible public products, excluding placeholder Sample Product tags."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
        async with session.get(f"{SHOP_URL}/products.json",
                               params={"limit": min(100, max(1,limit))},
                               allow_redirects=False) as response:
            if response.status != 200:
                raise ShopifyUnavailable("Shopify's public catalog is unavailable; use the Shop Online link.")
            data=await response.json(content_type=None)
    products=[]
    for item in data.get("products", []):
        if "sample product" in {str(tag).casefold() for tag in item.get("tags", [])}:
            continue
        handle=item.get("handle", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,120}",handle):
            continue
        variants=item.get("variants") or []
        prices=[]
        compare=[]
        for variant in variants:
            try:
                prices.append(Decimal(str(variant["price"])))
                if variant.get("compare_at_price") not in (None, ""):
                    compare.append(Decimal(str(variant["compare_at_price"])))
            except (InvalidOperation, KeyError, TypeError):
                continue
        if not prices:
            continue
        image=(item.get("image") or {}).get("src") or (
            ((item.get("images") or [{}])[0]).get("src"))
        if image and (not image.startswith("https://") or
                      not urlparse(image).hostname.endswith((".shopify.com",".shopifycdn.net"))):
            image=None
        price=min(prices)
        sale=any(old>price for old in compare)
        products.append({
            "id":str(item["id"]), "title":str(item.get("title") or "Untitled")[:180],
            "handle":handle,"url":f"{SHOP_URL}/products/{quote(handle)}",
            "price":price,"compare_at":max(compare) if compare else None,
            "sale":sale,"image":image,
            "published_at":item.get("published_at") or "",
        })
    return products


async def graphql(query,variables):
    url=admin_endpoint()
    token=os.environ["SHOPIFY_ADMIN_TOKEN"]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(url,headers={
            "X-Shopify-Access-Token":token,"Content-Type":"application/json"},
            json={"query":query,"variables":variables},allow_redirects=False) as response:
            if response.status != 200:
                raise ShopifyError(f"Shopify API returned HTTP {response.status}. Check your app scopes and credentials.")
            data=await response.json()
    if data.get("errors"):
        raise ShopifyError("Shopify rejected the API request; check integration permissions.")
    return data.get("data") or {}


async def verify_order(reference, email):
    """Check exact order name, purchaser email and fully paid status.

    Order + purchaser email are not proof of identity; a moderator must still
    approve the existing purchase milestone request.
    """
    reference=reference.strip()
    email=email.strip().casefold()
    if not ORDER_REF.fullmatch(reference):
        raise ValueError("Enter the order number exactly as shown in Shopify (e.g. #1001).")
    if len(email)>254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email):
        raise ValueError("Enter the email address used at checkout.")
    data=await graphql(ORDER_QUERY,{"search":f"name:{reference}"})
    matches=[x for x in (data.get("orders") or {}).get("nodes",[])
             if x.get("name","").casefold()==reference.casefold()]
    if len(matches)!=1:
        return {"status":"not_verified","reason":"No exact matching Shopify order found."}
    order=matches[0]
    if (order.get("email") or "").strip().casefold()!=email:
        return {"status":"not_verified","reason":"Order details could not be verified."}
    if order.get("cancelledAt") or order.get("displayFinancialStatus")!="PAID":
        return {"status":"not_verified","reason":"This order is not currently fully paid and uncancelled."}
    return {"status":"verified","reference":order["name"],"order_gid":order["id"],
            "fulfillment":order.get("displayFulfillmentStatus") or "UNKNOWN"}


def discount_input(rank,ticket_id,code,now):
    percentages={"Master":10,"Legend":15,"Paragon":20}
    if rank not in percentages or ticket_id<1:
        raise ValueError("Reward rank or ticket is not valid.")
    return {
        "title":f"GYMGUILD GuildKeeper {rank} #{ticket_id}",
        "code":code,"startsAt":now.astimezone(timezone.utc).isoformat(),
        "endsAt":(now+timedelta(days=7)).astimezone(timezone.utc).isoformat(),
        "usageLimit":1,"appliesOncePerCustomer":True,
        "context":{"all":True},
        "customerGets":{"value":{"percentage":percentages[rank]/100},
                        "items":{"all":True}},
        "combinesWith":{"orderDiscounts":False,"productDiscounts":False,"shippingDiscounts":False},
    }


def new_code(rank,ticket_id):
    if rank not in {"Master","Legend","Paragon"}:
        raise ValueError("Invalid rank")
    return f"GG-{rank.upper()}-{ticket_id}-{secrets.token_hex(5).upper()}"


async def create_discount(rank,ticket_id,code,now):
    data=await graphql(CODE_MUTATION,{"input":discount_input(rank,ticket_id,code,now)})
    result=data.get("discountCodeBasicCreate") or {}
    errors=result.get("userErrors") or []
    if errors:
        raise ShopifyError("Shopify declined the discount: "+ "; ".join(
            str(x.get("message") or "Invalid settings") for x in errors)[:250])
    created=result.get("codeDiscountNode")
    if not created or not created.get("id"):
        raise ShopifyError("Shopify did not return a confirmed discount ID.")
    return created["id"]
