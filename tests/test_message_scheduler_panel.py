"""Offline tests for the permanent GuildKeeper Message Scheduler dashboard."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import bot
import announcements_scheduler as core
import message_scheduler_panel as panel
import shopify_events


class SchedulerPanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.original_db=bot.DB
        self.original_channel=bot.ANNOUNCEMENTS_CHANNEL_ID
        bot.DB=Path(self.temp.name)/"schedule-panel.db"
        bot.ANNOUNCEMENTS_CHANNEL_ID=555
        bot.setup_db()
        core.initialize()

    def tearDown(self):
        bot.DB=self.original_db
        bot.ANNOUNCEMENTS_CHANNEL_ID=self.original_channel
        self.temp.cleanup()

    def test_panel_persistent_buttons_and_staff_command(self):
        view=panel.SchedulerDashboard()
        self.assertTrue(view.is_persistent())
        self.assertEqual([x.label for x in view.children],
            ["Posting Timeline","Scheduled Messages","Automations",
             "New Message","New Automation","Refresh Dashboard"])
        self.assertTrue(all(x.custom_id for x in view.children))
        self.assertIsNotNone(bot.bot.tree.get_command("admin_scheduler_post"))

    def test_default_automations_preserved_until_customized(self):
        week=core.automation_policy("weekly_quests")
        self.assertTrue(week["enabled"])
        self.assertFalse(week["configured"])
        self.assertFalse(core.automation_policy("shop_new")["configured"])
        core.save_automation("weekly_quests",555,"My quests","New week!",False,123)
        new=core.automation_policy("weekly_quests")
        self.assertFalse(new["enabled"])
        self.assertEqual(new["title"],"My quests")
        self.assertEqual(new["channel_id"],555)
        core.set_automation_enabled("weekly_quests",True,124)
        self.assertTrue(core.automation_policy("weekly_quests")["enabled"])
        core.delete_automation("weekly_quests")
        self.assertTrue(core.automation_policy("weekly_quests")["enabled"])
        self.assertFalse(core.automation_policy("weekly_quests")["configured"])

    def test_automations_isolated_by_guild_and_validate_inputs(self):
        core.save_automation("shop_new",123,"New arrival","Welcome!",True,11)
        with self.assertRaises(ValueError):
            core.save_automation("unknown",123,"X","",True,11)
        with self.assertRaises(ValueError):
            core.save_automation("shop_sale",123,"","",True,11)
        with self.assertRaises(ValueError):
            core.set_automation_enabled("shop_sale",False,11)
        with bot.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM scheduler_automations WHERE guild_id=?",
                (bot.GUILD_ID,)).fetchone()[0],1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM scheduler_automations WHERE guild_id=?",
                (bot.GUILD_ID+1,)).fetchone()[0],0)

    def test_timeline_uses_recurring_uk_dates_and_respects_pause(self):
        now=datetime(2026,11,18,12,tzinfo=timezone.utc)
        data={"channel_id":555,"title":"Tuesday tips","body":"Train","image_url":"",
              "recurrence":"weekly","next_run_utc":"2026-11-24T09:00:00+00:00",
              "month_day":24,"ping_guildblast":0}
        core.create(data,12)
        entries=core.timeline(now=now)
        self.assertEqual(entries,sorted(entries,key=lambda x:(x[0],x[1])))
        labels=[x[1] for x in entries]
        self.assertIn("#1 Tuesday tips",labels)
        self.assertIn("Weekly Side Quest refresh",labels)
        self.assertIn("Monthly leaderboard recap",labels)
        core.save_automation("weekly_quests",555,"Weekly","",False,10)
        self.assertNotIn("Weekly Side Quest refresh",
                         [x[1] for x in core.timeline(now=now)])
        self.assertIn("📆",panel.timeline_text())

    async def test_custom_automation_routes_event_to_configured_channel(self):
        core.save_automation("weekly_quests",123,"New quests","Check them out",True,9)
        with patch.object(core,"automatic_event",new=AsyncMock(return_value=True)) as post:
            await core.configured_notice("weekly_quests","weekly-refresh-2026-11-16",
                                         "Default title","Original content")
            post.assert_awaited_once_with(
                "weekly-refresh-2026-11-16","New quests",
                "Check them out\n\nOriginal content",123,
                ping_guildblast=False)
        core.set_automation_enabled("weekly_quests",False,9)
        with patch.object(core,"automatic_event",new=AsyncMock()) as post:
            result=await core.configured_notice("weekly_quests","later","Title","Body")
            self.assertFalse(result)
            post.assert_not_awaited()

    def test_automation_ping_setting_defaults_off_and_persists(self):
        self.assertFalse(core.automation_policy("weekly_quests")["ping_guildblast"])
        self.assertFalse(core.automation_policy("shop_new")["ping_guildblast"])
        core.save_automation("weekly_quests",555,"Fresh quests","",True,9,
                             ping_guildblast=True)
        self.assertTrue(core.automation_policy("weekly_quests")["ping_guildblast"])
        self.assertEqual(core.automation("weekly_quests")["ping_guildblast"],1)
        self.assertIn("GuildBlast ping: **On**",panel.automation_detail("weekly_quests"))
        self.assertIn("🔔 @GuildBlast",panel.automation_text())
        self.assertEqual(
            panel.AutomationForm(9,"weekly_quests",555).ping.default,"yes")
        core.save_automation("weekly_quests",555,"Fresh quests","",True,9,
                             ping_guildblast=False)
        self.assertFalse(core.automation_policy("weekly_quests")["ping_guildblast"])
        core.delete_automation("weekly_quests")
        self.assertFalse(core.automation_policy("weekly_quests")["ping_guildblast"])

    async def test_automation_ping_flag_reaches_delivery(self):
        core.save_automation("shop_sale",555,"Sale","",True,9,ping_guildblast=True)
        with patch.object(core,"automatic_event",new=AsyncMock(return_value=True)) as send:
            await core.configured_notice("shop_sale","sale-42","Default","Details")
        send.assert_awaited_once_with(
            "sale-42","Sale","Details",555,ping_guildblast=True)

    def test_existing_automation_database_migrates_without_losing_settings(self):
        # Simulate a database written by the previous GuildKeeper release.
        with bot.connect() as db:
            db.execute("DROP TABLE scheduler_automations")
            db.execute("""CREATE TABLE scheduler_automations (
              guild_id INTEGER NOT NULL, kind TEXT NOT NULL,
              channel_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
              title TEXT NOT NULL DEFAULT '', intro TEXT NOT NULL DEFAULT '',
              updated_by INTEGER NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY (guild_id,kind))""")
            db.execute("""INSERT INTO scheduler_automations
              VALUES (?,?,?,?,?,?,?,?)""",
              (bot.GUILD_ID,"monthly_leaderboard",555,1,"My Recap","Keep going",
               9,bot.utc_now().isoformat()))
        core.initialize()
        saved=core.automation("monthly_leaderboard")
        self.assertEqual(saved["title"],"My Recap")
        self.assertEqual(saved["intro"],"Keep going")
        self.assertEqual(saved["ping_guildblast"],0)

    def test_message_listing_and_timeline_support_multiple_pages(self):
        import announcement_ui
        now=bot.utc_now()
        for i in range(19):
            core.create({"channel_id":555,"title":f"Post {i}","body":"Training",
                "image_url":"","recurrence":"once",
                "next_run_utc":(now+__import__("datetime").timedelta(days=i+1)).isoformat(),
                "month_day":1,"ping_guildblast":0},123)
        self.assertEqual(len(core.upcoming(limit=8,offset=8)),8)
        self.assertIn("Page 2",announcement_ui.listing(1))
        self.assertIn("Previous Page",
            [button.label for button in announcement_ui.UpcomingMenu(4,1).children
             if isinstance(button,discord.ui.Button)])
        self.assertIn("Page 2",panel.timeline_text(page=1))
        self.assertEqual([b.label for b in panel.TimelineMenu(4).children],
                         ["Previous Page","Next Page"])

    def test_automation_choices_and_preview(self):
        core.save_automation("shop_new",555,"New!","Browse",True,9)
        choices=panel.AutomationChoice(9,create_only=True)
        self.assertNotIn("shop_new",[o.value for o in choices.children[0].options])
        self.assertIn("shop_new",[o.value for o in panel.AutomationList(9).children[0].options])
        self.assertIn("New!",panel.automation_detail("shop_new"))
        embed=panel.dashboard_embed()
        self.assertIn("MESSAGE SCHEDULER",embed.title)

    async def test_staff_permissions_are_enforced(self):
        view=panel.SchedulerDashboard()
        interaction=SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(panel,"allowed",return_value=False):
            self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()

    async def test_shopify_automation_event_honors_individual_rule(self):
        from decimal import Decimal
        item={"id":"11","title":"New tee","price":Decimal("35"),"sale":False,
              "url":"https://gymguild.co.uk/products/tee"}
        with patch.object(shopify_events.client,"catalog",
                          new=AsyncMock(side_effect=[[item],[item,dict(item,id="12")]])), \
             patch.object(core,"configured_notice",
                          new=AsyncMock(return_value=True)) as post:
            core.save_automation("shop_new",555,"New!","",True,1)
            core.save_automation("shop_sale",555,"Sale!","",False,1)
            await shopify_events.tick()
            with bot.connect() as db:
                db.execute("DELETE FROM v12_config WHERE key=?",
                           (f"shopify-last-poll-{bot.GUILD_ID}",))
            await shopify_events.tick()
        post.assert_awaited_once()
        self.assertEqual(post.call_args.args[0],"shop_new")
