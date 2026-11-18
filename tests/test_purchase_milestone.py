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
        self.assertEqual(bot.get_xp(self.gid, self.uid), 400)
        self.assertEqual(bot.review_purchase_order(self.gid, order_id, 22, True)[0], 'not_pending')
        summary = bot.purchase_order_summary(self.gid, self.uid)
        self.assertEqual(summary['approved'], 1)
        self.assertEqual(summary['earned_xp'], 250)
        self.assertEqual(summary['pending'], 0)
        self.assertEqual(summary['potential_xp'], 0)
        with bot.connect() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM reward_tickets WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid),
            ).fetchone()[0], 1)

    def test_one_lifetime_submission_after_approval(self):
        status, first = bot.submit_purchase_order(self.gid, self.uid, '#1001')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, True)[0], 'awarded')
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, '#1002')[0], 'approved')
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'approved')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['approved'], 1)
        with bot.connect() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM purchase_orders WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid),
            ).fetchone()[0], 1)

    def test_one_lifetime_submission_after_rejection(self):
        status, first = bot.submit_purchase_order(self.gid, self.uid, 'A001')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'A002')[0], 'pending')
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, False)[0], 'rejected')
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'rejected')
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'A002'), ('rejected', first))
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'A001'), ('rejected', first))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['rejected'], 1)

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

    def test_legacy_six_milestone_history_gets_one_completion_bonus(self):
        with bot.connect() as db:
            for kind, *_ in bot.MILESTONE_QUESTS:
                db.execute(
                    'INSERT INTO milestone_claims(guild_id,user_id,kind,claimed_at) VALUES (?,?,?,?)',
                    (self.gid, self.uid, kind, bot.utc_now().isoformat()),
                )
        bot.setup_db()
        self.assertEqual(bot.get_xp(self.gid, self.uid), 500)
        self.assertTrue(bot.milestone_progress(self.gid, self.uid)['bonus_earned'])
        bot.setup_db()
        self.assertEqual(bot.get_xp(self.gid, self.uid), 500)

    def test_milestone_panel_and_reward_summary_do_not_give_unearned_xp(self):
        self.assertIn('Completed: 0/6', bot.milestone_quests_embed(self.gid, self.uid).description)
        self.assertIn('single lifetime Order XP request', bot.order_xp_embed(self.gid, self.uid).description)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        bot.submit_purchase_order(self.gid, self.uid, 'xyz')
        self.assertTrue(any('awaiting approval' in field.value for field in bot.order_xp_embed(self.gid, self.uid).fields))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)

    def test_refresh_controls_across_member_progress_panels(self):
        milestone_buttons = {button.label for button in bot.MilestoneQuestsView(self.uid).children}
        self.assertEqual(milestone_buttons, {'Refresh Progress', 'Cancel'})
        self.assertNotIn('Submit Order', milestone_buttons)
        self.assertIn('Submit Order XP', {button.label for button in bot.MilestoneBoardView().children})
        for kind in ('starter', 'weekly', 'profile', 'ranks', 'streak'):
            view = bot.PrivateProgressView(self.uid, kind)
            self.assertEqual([button.label for button in view.children], ['Refresh Progress'])
            self.assertEqual(view.user_id, self.uid)
        reward_buttons = {button.label for button in bot.RewardChoiceView(self.uid).children}
        self.assertIn('Refresh Rewards', reward_buttons)

    def test_milestone_rewards_and_cancel_controls_match_display(self):
        rewards = {kind: xp for kind, _, xp, _ in bot.MILESTONE_QUESTS}
        self.assertEqual(rewards, {
            'first_week': 25, 'streak4': 75, 'streak8': 125,
            'streak12': 175, 'boost1': 100, 'purchase1': 250,
        })
        self.assertGreater(rewards['purchase1'], max(
            amount for key, amount in rewards.items() if key != 'purchase1'))
        panel = bot.milestone_board_embed()
        self.assertEqual(panel.title, '✦ GYMGUILD — MILESTONE QUESTS ✦')
        self.assertTrue(panel.fields[0].name.startswith('🎯'))
        self.assertTrue(panel.fields[-1].name.startswith('🛍️'))
        self.assertTrue(all('\n' in field.value for field in panel.fields))
        progress = bot.milestone_quests_embed(self.gid, self.uid)
        self.assertEqual(progress.title, panel.title)
        self.assertTrue(any('250 XP' in field.name for field in progress.fields))
        self.assertEqual(
            {item.label for item in bot.OrderXPView(self.uid).children},
            {'Submit Order', 'Refresh Status', 'Cancel'},
        )
        self.assertEqual(
            {item.label for item in bot.MilestoneQuestsView(self.uid).children},
            {'Refresh Progress', 'Cancel'},
        )

    def test_invalid_reference_preserves_data(self):
        for invalid in ('', '   ', 'a' * 81, 'bad\nref'):
            self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, invalid), ('invalid', None))
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['orders'], [])

    def test_permanent_milestone_board_and_pending_mod_review_buttons(self):
        public = bot.MilestoneBoardView()
        self.assertTrue(public.is_persistent())
        self.assertEqual(
            {button.label for button in public.children},
            {'My Progress', 'Submit Order XP'},
        )
        review = bot.PurchaseReviewView(51)
        self.assertTrue(review.is_persistent())
        self.assertEqual(
            {button.custom_id for button in review.children},
            {'guildkeeper:order:approve:51', 'guildkeeper:order:reject:51'},
        )
        hub = bot.PlayerStatsView()
        self.assertTrue(hub.is_persistent())
        self.assertIn('Milestone Quests', {button.label for button in hub.children})
        self.assertLessEqual(len(hub.children), 25)

    def test_one_lifetime_submission_persists_after_restart(self):
        _, first = bot.submit_purchase_order(self.gid, self.uid, 'test-1001')
        self.assertEqual(bot.review_purchase_order(self.gid, first, 42, True)[0], 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)
        bot.setup_db()
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'test-1002')[0], 'approved')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)

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
