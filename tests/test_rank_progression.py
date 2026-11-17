"""Regression tests for onboarding and XP rank progression (no Discord connection)."""
import tempfile
import unittest
from pathlib import Path

import bot


class RankProgressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = bot.DB
        bot.DB = Path(self.tmp.name) / "guildkeeper-test.db"
        bot.setup_db()
        self.guild_id = 12345
        self.user_id = 98765

    def tearDown(self):
        bot.DB = self.original_db
        self.tmp.cleanup()

    def finish_starter(self):
        with bot.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO starter_turnins(guild_id,user_id,turned_in_at) VALUES (?,?,?)",
                (self.guild_id, self.user_id, bot.utc_now().isoformat()),
            )

    def test_unranked_before_starter_turn_in_even_with_xp(self):
        for amount in (0, 49, 50, 75, 275, 300):
            with self.subTest(xp=amount):
                self.assertEqual(
                    bot.rank_for(amount, self.guild_id, self.user_id),
                    "Unranked",
                )

    def test_novice_persists_after_xp_loss(self):
        self.finish_starter()
        for amount in (0, 1, 25, 49, 50, 75, 275, 299):
            with self.subTest(xp=amount):
                self.assertEqual(
                    bot.rank_for(amount, self.guild_id, self.user_id),
                    "Novice",
                )

    def test_new_lower_rank_thresholds(self):
        self.finish_starter()
        expected = [
            (299, "Novice"),
            (300, "Apprentice"),
            (499, "Apprentice"),
            (500, "Adept"),
            (749, "Adept"),
            (750, "Expert"),
            (999, "Expert"),
        ]
        for xp, rank in expected:
            with self.subTest(xp=xp):
                self.assertEqual(bot.rank_for(xp, self.guild_id, self.user_id), rank)

    def test_master_requires_unlock_and_can_be_regained(self):
        self.finish_starter()
        self.assertEqual(bot.rank_for(1000, self.guild_id, self.user_id), "Expert")
        with bot.connect() as db:
            db.execute(
                "INSERT INTO rank_unlocks(guild_id,user_id,rank,unlocked_at) VALUES (?,?,?,?)",
                (self.guild_id, self.user_id, "Master", bot.utc_now().isoformat()),
            )
        self.assertEqual(bot.rank_for(1000, self.guild_id, self.user_id), "Master")
        self.assertEqual(bot.rank_for(0, self.guild_id, self.user_id), "Novice")
        self.assertEqual(bot.rank_for(1000, self.guild_id, self.user_id), "Master")

    def test_streak_notice_only_when_xp_is_enough_but_streak_locked(self):
        self.finish_starter()
        self.assertEqual(
            bot.streak_blocked_rank_notice(self.guild_id, self.user_id, 999), "",
        )
        notice = bot.streak_blocked_rank_notice(self.guild_id, self.user_id, 1000)
        self.assertIn("Master", notice)
        self.assertIn("0/2 weeks", notice)
        self.assertNotIn("Legend", notice)
        self.assertIn("\n", notice)

    def test_streak_notice_multiple_eligible_ranks_and_permanent_unlocks(self):
        self.finish_starter()
        notice = bot.streak_blocked_rank_notice(self.guild_id, self.user_id, 5000)
        for name in ("Master", "Legend", "Paragon"):
            self.assertIn(name, notice)
        with bot.connect() as db:
            db.execute(
                "INSERT INTO rank_unlocks(guild_id,user_id,rank,unlocked_at) VALUES (?,?,?,?)",
                (self.guild_id, self.user_id, "Master", bot.utc_now().isoformat()),
            )
        notice = bot.streak_blocked_rank_notice(self.guild_id, self.user_id, 5000)
        self.assertNotIn("**Master**", notice)
        self.assertIn("**Legend**", notice)

    def test_streak_notice_not_shown_to_unranked_members(self):
        self.assertEqual(
            bot.streak_blocked_rank_notice(self.guild_id, self.user_id, 5000), "",
        )

    def test_promotion_history_never_reannounces_same_rank(self):
        remember = bot.record_highest_rank
        self.assertFalse(remember(self.guild_id, self.user_id, "Novice", None))
        self.assertTrue(remember(self.guild_id, self.user_id, "Apprentice", "Novice"))
        self.assertFalse(remember(self.guild_id, self.user_id, "Novice", "Apprentice"))
        self.assertFalse(remember(self.guild_id, self.user_id, "Apprentice", "Novice"))
        self.assertTrue(remember(self.guild_id, self.user_id, "Adept", "Apprentice"))
        # Restart-safe: the table still contains the highest rank.
        with bot.connect() as db:
            row = db.execute(
                "SELECT rank FROM rank_high_watermarks WHERE guild_id=? AND user_id=?",
                (self.guild_id, self.user_id),
            ).fetchone()
        self.assertEqual(row["rank"], "Adept")


if __name__ == "__main__":
    unittest.main()
