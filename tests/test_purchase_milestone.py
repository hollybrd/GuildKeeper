"""Order XP panel storage and moderator review regression tests."""
import tempfile
import unittest
from pathlib import Path

import bot


class PurchaseMilestoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = bot.DB
        bot.DB = Path(self.tmp.name) / "purchase-test.db"
        bot.setup_db()
        self.gid, self.uid = 123456, 789

    def tearDown(self):
        bot.DB = self.original_db
        self.tmp.cleanup()

    def test_first_approved_order_awards_50_and_preserves_rewards(self):
        with bot.connect() as db:
            db.execute(
                'INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)',
                (self.gid, self.uid, 150),
            )
            db.execute(
                'INSERT INTO reward_tickets(guild_id,user_id,rank,status) VALUES (?,?,?,?)',
                (self.gid, self.uid, 'Master', 'fulfilled'),
            )
        status, order_id = bot.submit_purchase_order(self.gid, self.uid, '#1001')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, '#1001')[0], 'pending')
        self.assertEqual(bot.review_purchase_order(self.gid, order_id, 22, True), ('awarded', self.uid))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 200)
        self.assertEqual(bot.review_purchase_order(self.gid, order_id, 22, True)[0], 'not_pending')
        summary = bot.purchase_order_summary(self.gid, self.uid)
        self.assertEqual(summary['approved'], 1)
        self.assertEqual(summary['earned_xp'], 50)
        self.assertEqual(summary['pending'], 0)
        self.assertEqual(summary['potential_xp'], 0)
        with bot.connect() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM reward_tickets WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid),
            ).fetchone()[0], 1)

    def test_future_orders_tracked_but_first_purchase_reward_once(self):
        status, first = bot.submit_purchase_order(self.gid, self.uid, '#1001')
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, True)[0], 'awarded')
        status, second = bot.submit_purchase_order(self.gid, self.uid, '#1002')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.review_purchase_order(self.gid, second, 1, True)[0], 'approved_no_xp')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 50)
        summary = bot.purchase_order_summary(self.gid, self.uid)
        self.assertEqual(summary['approved'], 2)
        self.assertEqual(summary['earned_xp'], 50)
        with bot.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind='purchase1'",
                (self.gid, self.uid),
            ).fetchone()[0], 1)

    def test_pending_status_and_rejection_resubmission(self):
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['approved'], 0)
        status, first = bot.submit_purchase_order(self.gid, self.uid, 'A001')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'A002')[0], 'pending')
        summary = bot.purchase_order_summary(self.gid, self.uid)
        self.assertEqual(summary['pending'], 1)
        self.assertEqual(summary['potential_xp'], 50)
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, False)[0], 'rejected')
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['rejected'], 1)
        status, again = bot.submit_purchase_order(self.gid, self.uid, 'A001')
        self.assertEqual((status, again), ('submitted', first))
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, True)[0], 'awarded')

    def test_orders_scoped_and_order_reuse_blocked(self):
        status, first = bot.submit_purchase_order(self.gid, self.uid, 'XYZ')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid + 1, 'XYZ')[0], 'duplicate')
        status, second = bot.submit_purchase_order(self.gid, self.uid + 1, 'XYZ2')
        self.assertEqual(status, 'submitted')
        bot.review_purchase_order(self.gid, first, 1, True)
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid + 1)['pending'], 1)
        self.assertEqual(bot.get_xp(self.gid, self.uid + 1), 0)
        self.assertEqual(bot.review_purchase_order(self.gid, second, 1, False)[0], 'rejected')

    def test_guild_completion_bonus_500_xp_exactly_once(self):
        kinds = [kind for kind, *_ in bot.MILESTONE_QUESTS]
        with bot.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for kind in kinds[:-1]:
                self.assertTrue(bot.grant_once(db, 'milestone_claims', self.gid, self.uid, kind, 1))
        self.assertFalse(bot.milestone_progress(self.gid, self.uid)['bonus_earned'])
        with bot.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertTrue(bot.grant_once(
                db, 'milestone_claims', self.gid, self.uid, kinds[-1], 50))
            self.assertFalse(bot.grant_once(
                db, 'milestone_claims', self.gid, self.uid, kinds[-1], 50))
        self.assertTrue(bot.milestone_progress(self.gid, self.uid)['bonus_earned'])
        self.assertEqual(bot.milestone_progress(self.gid, self.uid)['completed'], 6)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 555)
        with bot.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind='guild_completion'",
                (self.gid, self.uid),
            ).fetchone()[0], 1)

    def test_milestone_panel_and_reward_summary_do_not_give_unearned_xp(self):
        self.assertIn('Completed: 0/6', bot.milestone_quests_embed(self.gid, self.uid).description)
        self.assertIn('Locked', bot.order_rewards_text(self.gid, self.uid))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        bot.submit_purchase_order(self.gid, self.uid, 'xyz')
        self.assertIn('awaiting approval', bot.order_rewards_text(self.gid, self.uid))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)

    def test_invalid_reference_preserves_data(self):
        for invalid in ('', '   ', 'a' * 81, 'bad\nref'):
            self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, invalid), ('invalid', None))
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['orders'], [])

    def test_legacy_pending_purchase_is_migrated_without_awarding_xp(self):
        with bot.connect() as db:
            db.execute(
                'INSERT INTO pending_milestones(guild_id,user_id,kind,evidence,status,created_at) '
                "VALUES (?,?, 'purchase1', ?, 'pending', ?)",
                (self.gid, self.uid, '#legacy', bot.utc_now().isoformat()),
            )
        bot.setup_db()
        summary = bot.purchase_order_summary(self.gid, self.uid)
        self.assertEqual(summary['pending'], 1)
        self.assertEqual(summary['earned_xp'], 0)
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, '#legacy')[0], 'pending')
        bot.setup_db()
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['pending'], 1)


if __name__ == '__main__':
    unittest.main()
