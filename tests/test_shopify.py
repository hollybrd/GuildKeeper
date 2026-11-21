"""Shopify storefront and reward-code integration regression tests.

All tests are offline: no Shopify order is submitted and no real discount created.


READING GUIDE
-------------
Simulates Shopify responses and reward tickets. No live orders or discounts are created; tests verify exact order matching and non-duplicated reward codes.

Each setUp() uses a temporary SQLite database rather than GuildKeeper's
real player database. Assertions compare actual results with expected rules.
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import discord
import shopify_client
import shopify_discord
import shopify_rewards
import shopify_events


class FakeResponse:
    status=200
    def __init__(self,body):
        self.body=body
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        return False
    async def json(self,**kwargs):
        return self.body


class FakeSession:
    def __init__(self,body):
        self.body=body
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        return False
    def get(self,*args,**kwargs):
        return FakeResponse(self.body)


class ShopifyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.original_db=bot.DB
        bot.DB=Path(self.temp.name)/"shopify.db"
        bot.setup_db()
        shopify_discord.ensure_tables()
        shopify_events.ensure_tables()

    def tearDown(self):
        bot.DB=self.original_db
        self.temp.cleanup()

    def test_shop_panel_is_persistent_and_has_no_checkout_inside_discord(self):
        view=shopify_discord.ShopHub()
        self.assertTrue(view.is_persistent())
        self.assertEqual(
            [b.label for b in view.children],
            ["Browse Products","New Arrivals","On Sale","Verify Order","Shop Online"])
        self.assertEqual(next(b.url for b in view.children if b.label=="Shop Online"),
                         "https://gymguild.co.uk")
        self.assertIsNotNone(bot.bot.tree.get_command("admin_shop_post"))

    async def test_catalog_only_shows_real_public_store_products(self):
        body={"products":[
            {"id":1,"handle":"sample-shirt","title":"Placeholder","tags":["Sample Product"],
             "variants":[{"price":"20.00"}]},
            {"id":2,"handle":"oversized-tee","title":"GYMGUILD tee","tags":[],
             "variants":[{"price":"34.99","compare_at_price":"39.99"}],
             "image":{"src":"https://cdn.shopify.com/images/shirt.jpg"},
             "published_at":"2026-11-18T00:00:00Z"},
        ]}
        with patch.object(shopify_client.aiohttp,"ClientSession",
                          return_value=FakeSession(body)):
            products=await shopify_client.catalog()
        self.assertEqual(len(products),1)
        self.assertEqual(products[0]["title"],"GYMGUILD tee")
        self.assertEqual(products[0]["price"],Decimal("34.99"))
        self.assertTrue(products[0]["sale"])
        self.assertEqual(products[0]["url"],"https://gymguild.co.uk/products/oversized-tee")
        self.assertIn("£34.99",shopify_discord.price_label(products[0]))

    async def test_catalog_falls_back_to_authorised_published_products(self):
        body = {"products": {"nodes": [
            {"id": "gid://shopify/Product/2", "title": "GYMGUILD tee",
             "handle": "oversized-tee", "tags": [], "onlineStoreUrl": "https://gymguild.co.uk/products/oversized-tee",
             "publishedAt": "2026-11-18T00:00:00Z", "featuredImage": {"url": "https://cdn.shopify.com/images/shirt.jpg"},
             "priceRangeV2": {"minVariantPrice": {"amount": "34.99"}},
             "compareAtPriceRange": {"maxVariantCompareAtPrice": {"amount": "39.99"}}},
            {"id": "gid://shopify/Product/3", "title": "Unpublished", "onlineStoreUrl": None},
            {"id": "gid://shopify/Product/4", "title": "Sample", "handle": "sample",
             "tags": ["Sample Product"], "onlineStoreUrl": "https://gymguild.co.uk/products/sample",
             "priceRangeV2": {"minVariantPrice": {"amount": "10.00"}}}
        ]}}
        with patch.object(shopify_client, "public_catalog", new=AsyncMock(side_effect=shopify_client.ShopifyUnavailable())), \
             patch.object(shopify_client, "admin_configured", return_value=True), \
             patch.object(shopify_client, "graphql", new=AsyncMock(return_value=body)) as query:
            products = await shopify_client.catalog()
        query.assert_awaited_once()
        self.assertEqual([p["id"] for p in products], ["2"])
        self.assertEqual(products[0]["price"], Decimal("34.99"))
        self.assertTrue(products[0]["sale"])

    def test_config_requires_exact_myshopify_domain_and_token(self):
        with patch.dict(os.environ,{"SHOPIFY_ADMIN_DOMAIN":"fake.attacker.test",
                                    "SHOPIFY_ADMIN_TOKEN":"testing"},clear=False):
            with self.assertRaises(shopify_client.ShopifyUnavailable):
                shopify_client.admin_endpoint()
        with patch.dict(os.environ,{"SHOPIFY_ADMIN_DOMAIN":"gymguild.myshopify.com",
                                    "SHOPIFY_ADMIN_TOKEN":"testing"},clear=False):
            self.assertEqual(shopify_client.admin_endpoint(),
                "https://gymguild.myshopify.com/admin/api/2026-10/graphql.json")


    async def test_2026_client_credentials_exchange_and_in_memory_cache(self):
        class OAuthResponse:
            status=200
            async def __aenter__(self):
                return self
            async def __aexit__(self,*args):
                return False
            async def json(self):
                return {"access_token":"fresh-test-token","expires_in":86399}

        class OAuthSession:
            def __init__(self):
                self.requests=[]
            async def __aenter__(self):
                return self
            async def __aexit__(self,*args):
                return False
            def post(self,url,**kwargs):
                self.requests.append((url,kwargs))
                return OAuthResponse()

        session=OAuthSession()
        previous=(shopify_client._token,shopify_client._token_until)
        try:
            shopify_client._token=None
            shopify_client._token_until=0
            with patch.dict(os.environ,{
                "SHOPIFY_ADMIN_DOMAIN":"gymguild.myshopify.com",
                "SHOPIFY_CLIENT_ID":"test-id",
                "SHOPIFY_CLIENT_SECRET":"test-secret",
                "SHOPIFY_ADMIN_TOKEN":"",
            }),patch.object(shopify_client.aiohttp,"ClientSession",return_value=session):
                self.assertTrue(shopify_client.admin_configured())
                self.assertEqual(await shopify_client.admin_token(),"fresh-test-token")
                self.assertEqual(await shopify_client.admin_token(),"fresh-test-token")
            self.assertEqual(len(session.requests),1)
            self.assertEqual(session.requests[0][0],
                "https://gymguild.myshopify.com/admin/oauth/access_token")
            self.assertEqual(session.requests[0][1]["data"]["grant_type"],
                             "client_credentials")
        finally:
            shopify_client._token,shopify_client._token_until=previous

    async def test_order_validation_never_trusts_order_number_alone(self):
        order={"id":"gid://shopify/Order/987","name":"#1234","email":"buyer@example.com",
               "displayFinancialStatus":"PAID","cancelledAt":None,
               "displayFulfillmentStatus":"UNFULFILLED"}
        with patch.object(shopify_client,"graphql",new=AsyncMock(
            return_value={"orders":{"nodes":[order]}})) as api_call:
            ok=await shopify_client.verify_order("#1234","Buyer@example.com")
            wrong=await shopify_client.verify_order("#1234","other@example.com")
        self.assertEqual(ok["status"],"verified")
        self.assertEqual(ok["order_gid"],"gid://shopify/Order/987")
        self.assertEqual(wrong["status"],"not_verified")
        self.assertNotIn("email",ok)
        self.assertEqual(api_call.await_count,2)
        with patch.object(shopify_client,"graphql",new=AsyncMock(
            return_value={"orders":{"nodes":[dict(order,displayFinancialStatus="PENDING")]}})):
            unpaid=await shopify_client.verify_order("#1234","buyer@example.com")
        self.assertNotEqual(unpaid["status"],"verified")

    def test_discount_input_is_one_use_7_day_and_correct_rank(self):
        now=datetime(2026,11,18,12,tzinfo=timezone.utc)
        for rank,percentage in (("Master",.10),("Legend",.15),("Paragon",.20)):
            data=shopify_client.discount_input(rank,42,"GG-CODE",now)
            self.assertEqual(data["usageLimit"],1)
            self.assertEqual(data["customerGets"]["value"]["percentage"],percentage)
            self.assertEqual((datetime.fromisoformat(data["endsAt"])-now).days,7)
            self.assertFalse(data["combinesWith"]["orderDiscounts"])
        with self.assertRaises(ValueError):
            shopify_client.discount_input("Novice",1,"GG",now)

    async def test_reward_code_only_created_once_and_delivered_to_ticket(self):
        ticket_id=777
        target_id=25
        with bot.connect() as db:
            db.execute("INSERT INTO reward_tickets "
                       "(id,guild_id,user_id,rank,channel_id,status,requested_at) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (ticket_id,bot.GUILD_ID,target_id,"Legend",1001,
                        "open",bot.utc_now().isoformat()))
        send=AsyncMock(return_value=SimpleNamespace(id=999))
        interaction=SimpleNamespace(channel_id=1001,channel=SimpleNamespace(send=send),
                                    user=SimpleNamespace(id=111))
        with patch.object(shopify_client,"admin_configured",return_value=True), \
             patch.object(shopify_client,"create_discount",new=AsyncMock(
                 return_value="gid://shopify/DiscountCodeNode/1")) as create:
            result=await shopify_rewards.issue_or_deliver(bot,interaction,ticket_id)
            again=await shopify_rewards.issue_or_deliver(bot,interaction,ticket_id)
        self.assertIn("delivered",result)
        self.assertIn("already delivered",again)
        create.assert_awaited_once()
        send.assert_awaited_once()
        row=shopify_rewards.get_ticket_code(bot,ticket_id)
        self.assertEqual(row["status"],"delivered")
        with bot.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM reward_tickets WHERE id=?",
                              (ticket_id,)).fetchone()["status"],"fulfilled")

    async def test_uncertain_discount_never_retries_or_mints_new_code(self):
        ticket_id=778
        with bot.connect() as db:
            db.execute("INSERT INTO reward_tickets "
                       "(id,guild_id,user_id,rank,channel_id,status,requested_at) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (ticket_id,bot.GUILD_ID,50,"Master",1002,
                        "open",bot.utc_now().isoformat()))
        interaction=SimpleNamespace(channel_id=1002,
            channel=SimpleNamespace(send=AsyncMock()))
        with patch.object(shopify_client,"admin_configured",return_value=True), \
             patch.object(shopify_client,"create_discount",new=AsyncMock(
                 side_effect=shopify_client.ShopifyError("uncertain"))) as create:
            first=await shopify_rewards.issue_or_deliver(bot,interaction,ticket_id)
            second=await shopify_rewards.issue_or_deliver(bot,interaction,ticket_id)
        self.assertIn("not confirm",first)
        self.assertIn("uncertain",second)
        create.assert_awaited_once()
        self.assertEqual(shopify_rewards.get_ticket_code(bot,ticket_id)["status"],"uncertain")


class StoreEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old_db=bot.DB
        self.old_channel=bot.ANNOUNCEMENTS_CHANNEL_ID
        bot.DB=Path(self.temp.name)/"shopify-events.db"
        bot.setup_db()
        shopify_events.ensure_tables()
        bot.ANNOUNCEMENTS_CHANNEL_ID=123

    def tearDown(self):
        bot.DB=self.old_db
        bot.ANNOUNCEMENTS_CHANNEL_ID=self.old_channel
        self.temp.cleanup()

    async def test_first_scan_baselines_then_new_product_announcement(self):
        first=[{"id":"1","title":"Tee","price":Decimal("35"),"sale":False,
                "url":"https://gymguild.co.uk/products/tee"}]
        next_items=first+[{"id":"2","title":"Hoodie","price":Decimal("55"),"sale":False,
                           "url":"https://gymguild.co.uk/products/hoodie"}]
        with patch.dict(os.environ,{"SHOPIFY_AUTO_ANNOUNCE":"1"}), \
             patch.object(shopify_events.client,"catalog",new=AsyncMock(
                 side_effect=[first,next_items])), \
             patch.object(shopify_events.announcements_scheduler,"automatic_event",
                          new=AsyncMock(return_value=True)) as announce:
            await shopify_events.tick()
            announce.assert_not_awaited()
            with bot.connect() as db:
                db.execute("DELETE FROM v12_config WHERE key=?",
                           (f"shopify-last-poll-{bot.GUILD_ID}",))
            await shopify_events.tick()
        announce.assert_awaited_once()
        self.assertIn("New GYMGUILD drop",announce.call_args.args[1])

    async def test_opt_out_makes_zero_network_calls(self):
        with patch.dict(os.environ,{"SHOPIFY_AUTO_ANNOUNCE":"0"}), \
             patch.object(shopify_events.client,"catalog",new=AsyncMock()) as catalog:
            await shopify_events.tick()
        catalog.assert_not_awaited()
