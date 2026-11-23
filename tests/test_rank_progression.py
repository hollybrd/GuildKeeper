"""Regression tests for onboarding and XP rank progression (no Discord connection).

READING GUIDE
-------------
Checks onboarding gates, permanent rank unlocks, XP thresholds, streak requirements and test resets, all offline rather than connecting to Discord.

Each setUp() uses a temporary SQLite database rather than GuildKeeper's
real player database. Assertions compare actual results with expected rules.
"""
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

    def test_paragon_requires_approved_purchase_and_streak(self):
        from unittest.mock import patch
        self.finish_starter()
        with patch.object(bot, 'active_streak', return_value=8):
            self.assertEqual(bot.rank_for(5000,self.guild_id,self.user_id), 'Legend')
            self.assertIn('approved', bot.streak_blocked_rank_notice(self.guild_id,self.user_id,5000))
            with bot.connect() as db:
                db.execute("INSERT INTO purchase_orders (guild_id,user_id,order_reference,status,submitted_at) VALUES (?,?,?,?,?)",
                           (self.guild_id,self.user_id,'test-order','pending',bot.utc_now().isoformat()))
            self.assertEqual(bot.rank_for(5000,self.guild_id,self.user_id), 'Legend')
            with bot.connect() as db:
                db.execute("UPDATE purchase_orders SET status='approved' WHERE guild_id=? AND user_id=?",(self.guild_id,self.user_id))
            self.assertEqual(bot.rank_for(4999,self.guild_id,self.user_id), 'Legend')
            self.assertEqual(bot.rank_for(5000,self.guild_id,self.user_id), 'Paragon')
        with patch.object(bot, 'active_streak', return_value=0):
            self.assertEqual(bot.rank_for(5000,self.guild_id,self.user_id), 'Paragon')

    def test_existing_paragon_unlock_requires_purchase_for_role_and_reward(self):
        self.finish_starter()
        with bot.connect() as db:
            db.execute("INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)",(self.guild_id,self.user_id,5000))
            for rank in ('Legend','Paragon'):
                db.execute("INSERT INTO rank_unlocks VALUES (?,?,?,?)",
                           (self.guild_id,self.user_id,rank,bot.utc_now().isoformat()))
        self.assertEqual(bot.rank_for(5000,self.guild_id,self.user_id),'Legend')
        self.assertEqual(bot.reward_button_state(self.guild_id,self.user_id)['Paragon'],'locked')
        self.assertIn('approved store purchase',bot.reward_eligibility_text(self.guild_id,self.user_id))

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

    def test_admin_testing_reset_starts_at_novice_and_preserves_starter_and_rewards(self):
        self.finish_starter()
        with bot.connect() as db:
            db.execute("INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)",
                       (self.guild_id, self.user_id, 1600))
            db.execute("INSERT INTO starter_claims(guild_id,user_id,kind,claimed_at) VALUES (?,?,?,?)",
                       (self.guild_id, self.user_id, 'intro', bot.utc_now().isoformat()))
            db.execute("INSERT INTO rank_unlocks(guild_id,user_id,rank,unlocked_at) VALUES (?,?,?,?)",
                       (self.guild_id, self.user_id, 'Master', bot.utc_now().isoformat()))
            db.execute("INSERT INTO reward_tickets(guild_id,user_id,rank,status) VALUES (?,?,?,?)",
                       (self.guild_id, self.user_id, 'Master', 'fulfilled'))
            db.execute("INSERT INTO xp_log(guild_id,user_id,amount,reason,created_at) VALUES (?,?,?,?,?)",
                       (self.guild_id, self.user_id, 100, 'test award', bot.utc_now().isoformat()))
        bot.record_highest_rank(self.guild_id, self.user_id, 'Master', 'Novice')
        self.assertEqual(bot.rank_for(1600, self.guild_id, self.user_id), 'Master')

        self.assertEqual(bot.reset_rank_test_data(self.guild_id, self.user_id), 50)
        self.assertEqual(bot.get_xp(self.guild_id, self.user_id), 50)
        self.assertEqual(bot.rank_for(50, self.guild_id, self.user_id), 'Novice')
        self.assertEqual(bot.rank_for(0, self.guild_id, self.user_id), 'Novice')
        with bot.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM starter_turnins WHERE guild_id=? AND user_id=?",
                (self.guild_id, self.user_id)).fetchone()[0], 1)
            claimed = {row['kind'] for row in db.execute(
                'SELECT kind FROM starter_claims WHERE guild_id=? AND user_id=?',
                (self.guild_id, self.user_id))}
            self.assertEqual(claimed, set(bot.STARTER_REWARDS))
            for table in ('rank_unlocks', 'rank_high_watermarks'):
                self.assertEqual(db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE guild_id=? AND user_id=?",
                    (self.guild_id, self.user_id)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM reward_tickets WHERE guild_id=? AND user_id=?",
                (self.guild_id, self.user_id)).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM xp_log WHERE guild_id=? AND user_id=?",
                (self.guild_id, self.user_id)).fetchone()[0], 1)

        # The command seeds Novice as the announcement baseline before
        # the first actual promotion; repeated promos still remain one-time.
        self.assertEqual(bot.record_new_rank_milestones(
            self.guild_id, self.user_id, 'Novice', None), [])
        self.assertEqual(bot.record_new_rank_milestones(
            self.guild_id, self.user_id, 'Apprentice', 'Novice'),
            [('Novice', 'Apprentice')])
        self.assertEqual(bot.record_new_rank_milestones(
            self.guild_id, self.user_id, 'Apprentice', 'Novice'), [])

    def test_admin_testing_reset_is_member_scoped_and_can_skip_starter(self):
        other_id = self.user_id + 1
        with bot.connect() as db:
            db.execute("INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)",
                       (self.guild_id, self.user_id, 450))
            db.execute("INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)",
                       (self.guild_id, other_id, 3000))
            db.execute("INSERT INTO starter_turnins(guild_id,user_id,turned_in_at) VALUES (?,?,?)",
                       (self.guild_id, other_id, bot.utc_now().isoformat()))
        bot.reset_rank_test_data(self.guild_id, self.user_id)
        self.assertEqual(bot.get_xp(self.guild_id, self.user_id), 50)
        self.assertEqual(bot.rank_for(50, self.guild_id, self.user_id), 'Novice')
        self.assertEqual(bot.get_xp(self.guild_id, other_id), 3000)
        self.assertNotEqual(bot.rank_for(3000, self.guild_id, other_id), 'Unranked')

    def test_repeated_test_resets_do_not_double_award_starter_xp(self):
        bot.reset_rank_test_data(self.guild_id, self.user_id)
        bot.reset_rank_test_data(self.guild_id, self.user_id)
        self.assertEqual(bot.get_xp(self.guild_id, self.user_id), 50)
        with bot.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM starter_claims WHERE guild_id=? AND user_id=?",
                (self.guild_id, self.user_id)).fetchone()[0], 5)

    def test_large_award_announces_each_rank_it_passes(self):
        self.finish_starter()
        self.assertEqual(
            bot.record_new_rank_milestones(self.guild_id, self.user_id, 'Novice', None), []
        )
        self.assertEqual(
            bot.record_new_rank_milestones(self.guild_id, self.user_id, 'Adept', 'Novice'),
            [('Novice', 'Apprentice'), ('Apprentice', 'Adept')],
        )
        self.assertEqual(
            bot.record_new_rank_milestones(self.guild_id, self.user_id, 'Master', 'Adept'),
            [('Adept', 'Expert'), ('Expert', 'Master')],
        )
        self.assertEqual(
            bot.record_new_rank_milestones(self.guild_id, self.user_id, 'Adept', 'Master'), []
        )
        self.assertEqual(
            bot.record_new_rank_milestones(self.guild_id, self.user_id, 'Master', 'Adept'), []
        )
        self.assertEqual(
            bot.record_new_rank_milestones(self.guild_id, self.user_id, 'Legend', 'Master'),
            [('Master', 'Legend')],
        )

    def test_rank_milestones_preserved_across_database_reopen(self):
        self.finish_starter()
        self.assertEqual(
            bot.record_new_rank_milestones(self.guild_id, self.user_id, 'Apprentice', 'Novice'),
            [('Novice', 'Apprentice')],
        )
        with bot.connect() as db:
            self.assertEqual(db.execute(
                'SELECT rank FROM rank_high_watermarks WHERE guild_id=? AND user_id=?',
                (self.guild_id, self.user_id),
            ).fetchone()['rank'], 'Apprentice')
        self.assertEqual(
            bot.record_new_rank_milestones(self.guild_id, self.user_id, 'Apprentice', 'Novice'), []
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
