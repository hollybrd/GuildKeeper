"""Regression tests for the durable GuildKeeper announcement scheduler."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import bot
import announcements_scheduler as schedule
import announcement_ui
import admin_hub

UK=ZoneInfo("Europe/London")


class AnnouncementSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old_db=bot.DB
        bot.DB=Path(self.temp.name)/"schedule-test.db"
        bot.setup_db()
        schedule.api=bot
        announcement_ui.api=bot
        schedule.initialize()
        self.guild=SimpleNamespace(id=bot.GUILD_ID)
        self.now=datetime(2026,11,18,10,tzinfo=timezone.utc)

    def tearDown(self):
        bot.DB=self.old_db
        self.temp.cleanup()

    def data(self, when="2026-11-19 09:30", recurrence="once", ping=False):
        with patch.object(schedule,"check_channel",return_value=SimpleNamespace(id=123)):
            return schedule.build_data(
                self.guild,123,"Quest reminder","Log in and complete your quest",
                when,recurrence,"https://example.org/banner.png",ping,now=self.now)

    def test_uk_dst_and_recurrences(self):
        with self.assertRaisesRegex(ValueError,"does not exist"):
            schedule.parse_local("2027-03-28 01:30")
        with self.assertRaises(ValueError):
            schedule.parse_local("19/11/2026 18:00")
        daily=schedule.next_occurrence(
            schedule.parse_local("2027-03-27 09:00"),"daily",27,
            datetime(2027,3,27,9,tzinfo=timezone.utc))
        self.assertEqual(daily.astimezone(UK).strftime("%Y-%m-%d %H:%M"),"2027-03-28 09:00")
        feb=schedule.next_occurrence(
            schedule.parse_local("2027-01-31 18:00"),"monthly",31,
            datetime(2027,1,31,19,tzinfo=timezone.utc))
        self.assertEqual(feb.strftime("%Y-%m-%d %H:%M"),"2027-02-28 18:00")
        mar=schedule.next_occurrence(feb,"monthly",31,
                                     datetime(2027,2,28,19,tzinfo=timezone.utc))
        self.assertEqual(mar.strftime("%Y-%m-%d %H:%M"),"2027-03-31 18:00")
        weekly=schedule.next_occurrence(
            schedule.parse_local("2026-11-19 09:30"),"weekly",19,
            datetime(2026,11,19,12,tzinfo=timezone.utc))
        self.assertEqual(weekly.strftime("%Y-%m-%d"),"2026-11-26")

    def test_create_edit_cancel_and_delete(self):
        data=self.data(recurrence="weekly")
        created=schedule.create(data,456)
        saved=schedule.get(created)
        self.assertEqual(saved["status"],"active")
        self.assertEqual(saved["recurrence"],"weekly")
        self.assertEqual(saved["image_url"],"https://example.org/banner.png")
        updated=self.data(when="2026-11-20 20:00",recurrence="monthly",ping=True)
        schedule.update(created,updated)
        self.assertEqual(schedule.get(created)["recurrence"],"monthly")
        self.assertEqual(schedule.get(created)["ping_guildblast"],1)
        schedule.change_status(created,"cancelled")
        self.assertEqual(schedule.get(created)["status"],"cancelled")
        with self.assertRaises(ValueError):
            schedule.change_status(created,"active")
        schedule.change_status(created,"deleted")
        self.assertEqual(schedule.get(created)["status"],"deleted")

    def test_reject_invalid_and_unsafe_posting(self):
        with patch.object(schedule,"check_channel",return_value=SimpleNamespace(id=123)):
            with self.assertRaisesRegex(ValueError,"future"):
                schedule.build_data(self.guild,123,"X","Hi","2026-11-17 12:00","once",now=self.now)
            with self.assertRaisesRegex(ValueError,"@everyone"):
                schedule.build_data(self.guild,123,"X","Hi @everyone","2026-11-19 12:00","once",now=self.now)
            with self.assertRaisesRegex(ValueError,"HTTPS"):
                schedule.build_data(self.guild,123,"X","Hi","2026-11-19 12:00","once",
                                    image_url="http://site.example/pic.png",now=self.now)
            with self.assertRaisesRegex(ValueError,"Choose"):
                schedule.build_data(self.guild,123,"X","Hi","2026-11-19 12:00","yearly",now=self.now)

    async def test_due_one_off_sends_only_once(self):
        job=schedule.create(self.data(),101)
        sent=SimpleNamespace(id=999)
        channel=SimpleNamespace(send=AsyncMock(return_value=sent))
        with patch.object(schedule,"check_channel",return_value=channel), \
             patch.object(bot.bot,"get_guild",return_value=self.guild):
            after=datetime(2026,11,19,10,tzinfo=timezone.utc)
            self.assertEqual(await schedule.dispatch_one(job,after),True)
            self.assertEqual(await schedule.dispatch_one(job,after),False)
        channel.send.assert_awaited_once()
        self.assertEqual(schedule.get(job)["status"],"sent")
        self.assertEqual(schedule.get(job)["last_message_id"],999)
        self.assertEqual(schedule.failed_count(),0)

    async def test_recurring_post_skips_downtime_but_keeps_local_time(self):
        job=schedule.create(self.data(recurrence="daily"),101)
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=204)))
        with patch.object(schedule,"check_channel",return_value=channel), \
             patch.object(bot.bot,"get_guild",return_value=self.guild):
            after=datetime(2026,11,25,12,tzinfo=timezone.utc)
            self.assertTrue(await schedule.dispatch_one(job,after))
        saved=schedule.get(job)
        self.assertEqual(saved["status"],"active")
        self.assertEqual(datetime.fromisoformat(saved["next_run_utc"]).astimezone(UK)
                         .strftime("%Y-%m-%d %H:%M"),"2026-11-26 09:30")
        channel.send.assert_awaited_once()

    async def test_uncertain_failure_is_not_automatically_retried(self):
        job=schedule.create(self.data(),101)
        with patch.object(schedule,"check_channel",side_effect=ValueError("Channel unavailable")), \
             patch.object(bot.bot,"get_guild",return_value=self.guild):
            after=datetime(2026,11,19,10,tzinfo=timezone.utc)
            self.assertFalse(await schedule.dispatch_one(job,after))
            self.assertFalse(await schedule.dispatch_one(job,after))
        self.assertEqual(schedule.get(job)["status"],"error")
        self.assertEqual(schedule.failed_count(),1)

    async def test_weekly_auto_notice_only_after_board(self):
        with patch.object(bot.bot,"get_guild",return_value=self.guild), \
             patch.object(schedule,"automatic_event",new=AsyncMock(return_value=True)) as post:
            # Without board: never announce a weekly refresh.
            await schedule.run_automatic(self.now)
            post.assert_not_awaited()
            with bot.connect() as db:
                db.execute("INSERT INTO schedule_posts VALUES (?,?)",
                           (bot.GUILD_ID,"weekly-board-"+bot.week_key(self.now)))
            await schedule.run_automatic(self.now)
        post.assert_awaited_once()
        self.assertEqual(post.call_args.args[0],"weekly-refresh-"+bot.week_key(self.now))

    async def test_monthly_recap_keeps_lifetime_xp_and_posts_on_first(self):
        now=datetime(2026,12,1,9,tzinfo=timezone.utc)
        with bot.connect() as db:
            db.execute("INSERT INTO xp_log(guild_id,user_id,amount,reason,created_at) "
                       "VALUES (?,?,?,?,?)",
                       (bot.GUILD_ID,50,125,"Weekly",datetime(2026,11,10,tzinfo=timezone.utc).isoformat()))
        with patch.object(bot.bot,"get_guild",return_value=self.guild), \
             patch.object(schedule,"automatic_event",new=AsyncMock(return_value=True)) as post:
            await schedule.run_automatic(now)
        self.assertEqual(post.await_count,1)
        arguments=post.call_args.args
        self.assertEqual(arguments[0],"monthly-leaderboard-2026-12")
        self.assertIn("125 XP",arguments[2])
        self.assertIn("Lifetime XP and ranks do not reset",arguments[2])
        with bot.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM members WHERE guild_id=? AND user_id=?",
                                         (bot.GUILD_ID,50)).fetchone())

    def test_admin_hub_exposes_schedule_management(self):
        labels=[b.label for b in admin_hub.AnnouncementMenu(101).children]
        self.assertEqual(labels,["Schedule Message","Upcoming Messages",
                                 "Automatic Notices","Compose Announcement"])
        self.assertEqual(len(announcement_ui.ChooseChannel(101).children),1)
        self.assertEqual(len(announcement_ui.FrequencyMenu(101,123).children[0].options),4)
        self.assertEqual(len(announcement_ui.JobMenu(101,999).children),4)
