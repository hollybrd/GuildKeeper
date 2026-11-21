"""Shopify API and public catalog adapter for GuildKeeper.

Public shop browsing works without an admin token if the storefront publishes a
products.json feed. Private order verification and discount creation require a
separately configured limited-scope Shopify Admin app on Oracle.


READING GUIDE
-------------
Low-level Shopify network access. Public products use the storefront's
published product feed; private orders and discounts need a separate Shopify app.
Client IDs and secrets come from the server environment, never from Discord input.
This module does not approve purchase XP; a moderator still reviews that separately.
"""
import os
import re
import secrets
import time
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
CATALOG_QUERY = """query($first:Int!){ products(first:$first,query:"status:active",sortKey:PUBLISHED_AT,reverse:true){ nodes { id title handle tags onlineStoreUrl publishedAt featuredImage { url } priceRangeV2 { minVariantPrice { amount } } compareAtPriceRange { maxVariantCompareAtPrice { amount } } } } }"""
CODE_MUTATION = """mutation($input:DiscountCodeBasicInput!){
  discountCodeBasicCreate(basicCodeDiscount:$input){
    codeDiscountNode { id }
    userErrors { field code }
  }
}"""


class ShopifyUnavailable(RuntimeError):
    pass


class ShopifyError(RuntimeError):
    pass


def public_domain():
    """A literal merchant-approved domain; never accept arbitrary user-supplied host."""
    return SHOP_URL


# Detect whether the merchant supplied Shopify credentials, not whether Shopify accepted them.
def admin_configured():
    return bool(os.getenv('SHOPIFY_ADMIN_DOMAIN') and (
        os.getenv('SHOPIFY_ADMIN_TOKEN') or (
            os.getenv('SHOPIFY_CLIENT_ID') and os.getenv('SHOPIFY_CLIENT_SECRET'))))


# Only allow the configured myshopify.com host for private Admin GraphQL requests.
def admin_endpoint():
    domain = os.getenv("SHOPIFY_ADMIN_DOMAIN", "").strip().lower()
    if not domain.endswith(".myshopify.com") or not SAFE_DOMAIN.fullmatch(domain):
        raise ShopifyUnavailable("Configure SHOPIFY_ADMIN_DOMAIN to your store's .myshopify.com address.")
    if not admin_configured():
        raise ShopifyUnavailable("Configure Shopify Client ID and Client Secret in Oracle deployment secrets.")
    return f"https://{domain}/admin/api/{API_VERSION}/graphql.json"


# Client-credentials access tokens for new Shopify Dev Dashboard custom apps
# expire after roughly 24 hours. Never persist them in Git or the database.
_token = None
_token_until = 0.0


# Exchange client credentials for a short-lived Admin API token and cache it in memory.
async def admin_token():
    global _token, _token_until
    legacy = os.getenv("SHOPIFY_ADMIN_TOKEN")
    if legacy:
        return legacy
    domain = os.getenv("SHOPIFY_ADMIN_DOMAIN", "").strip().lower()
    # Reuse the exact domain validation of admin_endpoint().
    admin_endpoint()
    client_id = os.getenv("SHOPIFY_CLIENT_ID")
    client_secret = os.getenv("SHOPIFY_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise ShopifyUnavailable("Client ID and Client Secret are not configured.")
    if _token is not None and time.monotonic() < _token_until:
        return _token
    url = f"https://{domain}/admin/oauth/access_token"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(url, data={
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "client_credentials",
        }, allow_redirects=False) as response:
            if response.status != 200:
                raise ShopifyUnavailable("Shopify authentication failed. Check the app installation and credentials.")
            data = await response.json()
    access_token = data.get("access_token")
    expires_in = data.get("expires_in")
    if not isinstance(access_token, str) or not access_token or not isinstance(expires_in, (int,float)):
        raise ShopifyUnavailable("Shopify authentication returned an invalid token response.")
    _token, _token_until = access_token, time.monotonic() + max(0, expires_in - 120)
    return _token



# Fetch only public products and construct safe, official Shopify product links.
async def public_catalog(limit=30):
    """Only online-visible public products, excluding placeholder Sample Product tags."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
        async with session.get(f"{SHOP_URL}/products.json",
                               params={"limit": min(100, max(1,limit))},
                               allow_redirects=False) as response:
            if response.status != 200:
                raise ShopifyUnavailable("Shopify's public catalog is unavailable; use the Shop Online link.")
            data=await response.json(content_type=None)
    return normalize_products(data.get("products", []))


async def catalog(limit=30):
    """Use the public feed when available; otherwise use the authorised Admin API."""
    try:
        return await public_catalog(limit)
    except (ShopifyUnavailable, aiohttp.ClientError, TimeoutError, ValueError):
        if not admin_configured():
            raise ShopifyUnavailable("Public catalog unavailable; configure the Shopify app.")
    try:
        data = await graphql(CATALOG_QUERY, {"first": min(30, max(1, limit))})
    except aiohttp.ClientError as exc:
        raise ShopifyUnavailable("Shopify catalog connection failed.") from exc
    items = []
    for product in (data.get("products") or {}).get("nodes", []):
        # Never expose draft or unpublished products through the private API.
        if not product.get("onlineStoreUrl"):
            continue
        items.append({
            "id": product["id"].rsplit("/", 1)[-1], "title": product.get("title"),
            "handle": product.get("handle"), "tags": product.get("tags") or [],
            "published_at": product.get("publishedAt"),
            "image": {"src": (product.get("featuredImage") or {}).get("url")},
            "variants": [{"price": ((product.get("priceRangeV2") or {}).get("minVariantPrice") or {}).get("amount"),
                          "compare_at_price": ((product.get("compareAtPriceRange") or {}).get("maxVariantCompareAtPrice") or {}).get("amount")}],
        })
    return normalize_products(items)


def normalize_products(items):
    products=[]
    for item in items:
        if "sample product" in {str(tag).casefold() for tag in item.get("tags", [])}:
            continue
        handle=item.get("handle") or ""
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
                      not (urlparse(image).hostname or "").endswith((".shopify.com",".shopifycdn.net"))):
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


# Send authenticated Shopify Admin API requests; never log or expose the access token.
async def graphql(query,variables):
    url=admin_endpoint()
    token=await admin_token()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(url,headers={
            "X-Shopify-Access-Token":token,"Content-Type":"application/json"},
            json={"query":query,"variables":variables},allow_redirects=False) as response:
            if response.status != 200:
                raise ShopifyError(f"Shopify API returned HTTP {response.status}. Check your app scopes and credentials.")
            data=await response.json()
    if data.get("errors"):
        # Only return fixed, non-sensitive diagnostic messages; never raw Shopify errors.
        codes = {e.get("extensions", {}).get("code") for e in data["errors"] if isinstance(e, dict)}
        if "ACCESS_DENIED" in codes:
            raise ShopifyError("Shopify access denied. Confirm the app has the required approved scopes.")
        if "MAX_COST_EXCEEDED" in codes or "THROTTLED" in codes:
            raise ShopifyError("Shopify query limit reached. Try again shortly.")
        raise ShopifyError("Shopify rejected the API query; check app permissions and API version.")
    return data.get("data") or {}


# Compare the exact order number, checkout email and PAID/uncancelled status.
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


# Build strict one-use, seven-day discount settings from a known Guild rank.
def discount_input(rank,ticket_id,code,now):
    percentages={"Master":10,"Legend":15,"Paragon":20}
    if rank not in percentages or ticket_id<1:
        raise ValueError("Reward rank or ticket is not valid.")
    return {
        "title":f"GYMGUILD GuildKeeper {rank} #{ticket_id}",
        "code":code,"startsAt":now.astimezone(timezone.utc).isoformat(),
        "endsAt":(now+timedelta(days=7)).astimezone(timezone.utc).isoformat(),
        "usageLimit":1,"appliesOncePerCustomer":True,
        "context":{"all":"ALL"},
        "customerGets":{"value":{"percentage":percentages[rank]/100},
                        "items":{"all":True}},
        "combinesWith":{"orderDiscounts":False,"productDiscounts":False,"shippingDiscounts":False},
    }


# Create a hard-to-guess code instead of using predictable user IDs alone.
def new_code(rank,ticket_id):
    if rank not in {"Master","Legend","Paragon"}:
        raise ValueError("Invalid rank")
    return f"GG-{rank.upper()}-{ticket_id}-{secrets.token_hex(5).upper()}"


# Ask Shopify to create a discount; return success only when Shopify confirms an ID.
async def create_discount(rank,ticket_id,code,now):
    data=await graphql(CODE_MUTATION,{"input":discount_input(rank,ticket_id,code,now)})
    result=data.get("discountCodeBasicCreate") or {}
    errors=result.get("userErrors") or []
    if errors:
        raise ShopifyError("Shopify declined the discount: "+ "; ".join(
            str(x.get("code") or "Invalid settings") + " (" + ".".join(x.get("field") or []) + ")" for x in errors)[:250])
    created=result.get("codeDiscountNode")
    if not created or not created.get("id"):
        raise ShopifyError("Shopify did not return a confirmed discount ID.")
    return created["id"]


# Look up the exact reserved code, without changing any Shopify state.
DISCOUNT_LOOKUP = """query($code:String!){codeDiscountNodeByCode(code:$code){id codeDiscount{__typename ... on DiscountCodeBasic { title status endsAt usageLimit asyncUsageCount customerGets { value { ... on DiscountPercentage { percentage } } } } } } }"""


async def lookup_discount(code):
    data = await graphql(DISCOUNT_LOOKUP, {"code": code})
    # Absence is authoritative only when Shopify returns this explicit field.
    if "codeDiscountNodeByCode" not in data:
        raise ShopifyError("Shopify did not confirm the discount lookup.")
    return data["codeDiscountNodeByCode"]
