"""Private GuildKeeper inbox and DM preference regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

import bot


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = bot.DB
        bot.DB = Path(self.tmp.name) / 'notifications.db'
        bot.setup_db()
        self.gid = 12345
        self.uid = 98765

    def tearDown(self):
        bot.DB = self.original_db
        self.tmp.cleanup()

    def test_quest_progress_shows_active_or_inactive_pause(self):
        self.assertIn('No active pause', bot.weekly_progress_text(self.gid, self.uid))
        self.assertIn('No active pause', bot.starter_progress_text(self.gid, self.uid))
        self.assertIn('No active pause', bot.milestone_quests_embed(self.gid, self.uid).fields[-1].value)
        with bot.connect() as db:
            db.execute('INSERT INTO pauses(guild_id,user_id,week_key,created_at) VALUES (?,?,?,?)',
                       (self.gid, self.uid, bot.week_key(), bot.utc_now().isoformat()))
        self.assertIn('Pause ACTIVE', bot.weekly_progress_text(self.gid, self.uid))
        self.assertIn('no Weekly Side Quest XP', bot.starter_progress_text(self.gid, self.uid))
        self.assertIn('Pause ACTIVE', bot.milestone_quests_embed(self.gid, self.uid).fields[-1].value)
        with bot.connect() as db:
            db.execute('INSERT INTO resumed_pauses(guild_id,user_id,week_key,resumed_at) VALUES (?,?,?,?)',
                       (self.gid, self.uid, bot.week_key(), bot.utc_now().isoformat()))
        self.assertIn('weekly XP earning has resumed', bot.weekly_progress_text(self.gid, self.uid))

    def test_dm_preference_defaults_enabled_and_persists(self):
        self.assertTrue(bot.bot_dms_enabled(self.gid, self.uid))
        bot.set_bot_dms(self.gid, self.uid, False)
        self.assertFalse(bot.bot_dms_enabled(self.gid, self.uid))
        bot.set_bot_dms(self.gid, self.uid, True)
        self.assertTrue(bot.bot_dms_enabled(self.gid, self.uid))

    def test_xp_award_and_deduction_stay_in_the_inbox_and_audit_log(self):
        bot.add_xp(self.gid, self.uid, 50, 'Starter reward')
        bot.add_xp(self.gid, self.uid, -25, 'Inactive week 2026-11-16')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 25)
        self.assertIn('Starter reward', bot.inbox_text(self.gid, self.uid, 'all'))
        self.assertIn('Inactive week', bot.inbox_text(self.gid, self.uid, 'xp'))
        with bot.connect() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) n FROM xp_log WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid)).fetchone()['n'], 2)

    def test_notification_deduplication_and_read_status(self):
        with bot.connect() as db:
            for _ in range(2):
                bot.record_notification(db, self.gid, self.uid, 'streak',
                                        'Deadline', 'Finish your quest', 'weekly-warning:2026-11-16')
        with bot.connect() as db:
            count = db.execute('SELECT COUNT(*) n FROM player_notifications').fetchone()['n']
            self.assertEqual(count, 1)
            db.execute('UPDATE player_notifications SET read_at=? WHERE guild_id=? AND user_id=?',
                       (bot.utc_now().isoformat(), self.gid, self.uid))
        self.assertIn('No notifications', bot.inbox_text(self.gid, self.uid, 'unread'))
        self.assertIn('Deadline', bot.inbox_text(self.gid, self.uid, 'all'))

    def test_reenabling_dms_does_not_replay_older_alerts(self):
        bot.set_bot_dms(self.gid, self.uid, False)
        bot.add_xp(self.gid, self.uid, -25, 'Inactive')
        bot.set_bot_dms(self.gid, self.uid, True)
        with bot.connect() as db:
            row = db.execute('SELECT dm_sent_at FROM player_notifications LIMIT 1').fetchone()
        self.assertIsNotNone(row['dm_sent_at'])

    def test_sunday_warning_is_saved_once_and_respects_pause(self):
        original_gid = bot.GUILD_ID
        bot.GUILD_ID = self.gid
        try:
            with bot.connect() as db:
                db.execute('INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)',
                           (self.gid, self.uid, 50))
                db.execute('INSERT INTO starter_turnins(guild_id,user_id,turned_in_at) VALUES (?,?,?)',
                           (self.gid, self.uid, bot.utc_now().isoformat()))
            # 22 November 2026 is a Sunday.
            with patch.object(bot, 'utc_now', return_value=datetime(2026, 11, 22, 12, tzinfo=timezone.utc)):
                bot.create_streak_warnings()
                bot.create_streak_warnings()
            with bot.connect() as db:
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) n FROM player_notifications WHERE category='streak'"
                ).fetchone()['n'], 1)
        finally:
            bot.GUILD_ID = original_gid
