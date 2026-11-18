"""Order XP panel storage and moderator review regression tests.

READING GUIDE
-------------
Tests that purchase requests need a moderator decision, cannot double-award milestone XP, and do not erase other member rewards when reset.

Each setUp() uses a temporary SQLite database rather than GuildKeeper's
real player database. Assertions compare actual results with expected rules.
"""
import tempfile
import unittest
from datetime import timedelta
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

    def test_rejected_order_can_be_resubmitted_and_approved(self):
        status, first = bot.submit_purchase_order(self.gid, self.uid, 'A001')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'A002')[0], 'pending')
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, False)[0], 'rejected')
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'available')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        status, second = bot.submit_purchase_order(self.gid, self.uid, 'A002')
        self.assertEqual(status, 'submitted')
        self.assertNotEqual(second, first)
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, True)[0], 'not_pending')
        self.assertEqual(bot.review_purchase_order(self.gid, second, 1, True)[0], 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'approved')
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'A003')[0], 'approved')

    def test_cancel_pending_then_resubmit_without_claiming_xp(self):
        status, first = bot.submit_purchase_order(self.gid, self.uid, 'A001')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'pending')
        self.assertFalse(bot.OrderXPView(self.uid, self.gid).cancel_request.disabled)
        result, oid, channel_id, message_id = bot.cancel_purchase_order(self.gid, self.uid)
        self.assertEqual((result, oid), ('cancelled', first))
        self.assertIsNone(channel_id)
        self.assertIsNone(message_id)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, True)[0], 'not_pending')
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'available')
        self.assertTrue(bot.OrderXPView(self.uid, self.gid).cancel_request.disabled)
        status, second = bot.submit_purchase_order(self.gid, self.uid, 'A002')
        self.assertEqual(status, 'submitted')
        self.assertNotEqual(second, first)
        self.assertEqual(bot.review_purchase_order(self.gid, second, 1, True)[0], 'awarded')
        self.assertEqual(bot.cancel_purchase_order(self.gid, self.uid)[0], 'approved')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'A003')[0], 'approved')

    def test_cancelled_request_can_retry_same_order_reference(self):
        _, first = bot.submit_purchase_order(self.gid, self.uid, 'A001')
        self.assertEqual(bot.cancel_purchase_order(self.gid, self.uid)[0], 'cancelled')
        status, again = bot.submit_purchase_order(self.gid, self.uid, 'A001')
        self.assertEqual((status, again), ('submitted', first))
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, True)[0], 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)

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
        self.assertIn('first approved order', bot.order_xp_embed(self.gid, self.uid).description)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        bot.submit_purchase_order(self.gid, self.uid, 'xyz')
        self.assertTrue(any('awaiting approval' in field.value for field in bot.order_xp_embed(self.gid, self.uid).fields))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)

    def test_refresh_controls_across_member_progress_panels(self):
        milestone_buttons = {button.label for button in bot.MilestoneQuestsView(self.uid).children}
        self.assertEqual(milestone_buttons, {'Refresh Progress'})
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
            {'Refresh Status', 'Cancel Request'},
        )
        self.assertEqual(
            {item.label for item in bot.MilestoneQuestsView(self.uid).children},
            {'Refresh Progress'},
        )

    def test_prior_milestone_claims_show_actual_historical_awards(self):
        with bot.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertTrue(bot.grant_once(
                db, 'milestone_claims', self.gid, self.uid, 'purchase1', 50))
            self.assertTrue(bot.grant_once(
                db, 'milestone_claims', self.gid, self.uid, 'boost1', 50))
        summary = bot.purchase_order_summary(self.gid, self.uid)
        self.assertTrue(summary['claimed'])
        self.assertEqual(summary['earned_xp'], 50)
        self.assertIn('50 XP granted', bot.order_xp_embed(self.gid, self.uid).fields[2].value)
        self.assertIn('50 XP granted', bot.boost_milestone_message(self.gid, self.uid, True))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 100)

    def test_milestone_progress_links_to_public_submission(self):
        embed = bot.milestone_quests_embed(self.gid, self.uid)
        self.assertIn('main Milestone Quests board', embed.footer.text)

    def test_boost_button_guidance_and_earned_message(self):
        self.assertIn('Boost Server', {
            item.label for item in bot.MilestoneBoardView().children})
        info = bot.boost_milestone_message(self.gid, self.uid, False)
        self.assertIn('Server Boosts', info)
        self.assertIn('100 XP', info)
        self.assertIn('payment', info)
        self.assertNotIn('XP granted!', info)
        with bot.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertTrue(bot.grant_once(
                db, 'milestone_claims', self.gid, self.uid, 'boost1',
                bot.MILESTONE_XP['boost1']))
        thank_you = bot.boost_milestone_message(self.gid, self.uid, True)
        self.assertIn('100 XP granted', thank_you)
        self.assertIn('Thank you', thank_you)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 100)
        self.assertEqual(bot.boost_milestone_message(self.gid, self.uid, False), thank_you)

    def test_cancelled_order_retains_mod_history_and_blocks_old_approval(self):
        status, old = bot.submit_purchase_order(self.gid, self.uid, 'HIST001')
        self.assertEqual(status, 'submitted')
        with bot.connect() as db:
            db.execute(
                'UPDATE purchase_orders SET review_channel_id=?,review_message_id=? WHERE id=?',
                (222, 333, old),
            )
        result, oid, channel, message = bot.cancel_purchase_order(self.gid, self.uid)
        self.assertEqual((result, oid, channel, message), ('cancelled', old, 222, 333))
        self.assertEqual(bot.review_purchase_order(self.gid, old, 44, True)[0], 'not_pending')
        # Never reopen the original review card's ID, even with the same reference.
        self.assertEqual(bot.submit_purchase_order(
            self.gid, self.uid, 'HIST001')[0], 'used_reference')
        status, new = bot.submit_purchase_order(self.gid, self.uid, 'HIST002')
        self.assertEqual(status, 'submitted')
        self.assertNotEqual(new, old)
        self.assertEqual(bot.review_purchase_order(self.gid, new, 44, True)[0], 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)

    def test_invalid_reference_preserves_data(self):
        for invalid in ('', '   ', 'a' * 81, 'bad\nref'):
            self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, invalid), ('invalid', None))
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['orders'], [])

    def test_permanent_milestone_board_and_pending_mod_review_buttons(self):
        public = bot.MilestoneBoardView()
        self.assertTrue(public.is_persistent())
        self.assertEqual(
            {button.label for button in public.children},
            {'My Progress', 'Submit Order XP', 'Boost Server'},
        )
        review = bot.PurchaseReviewView(51)
        self.assertTrue(review.is_persistent())
        self.assertEqual(
            {button.custom_id for button in review.children},
            {'guildkeeper:order:approve:51', 'guildkeeper:order:reject:51'},
        )
        hub = bot.PlayerStatsView()
        self.assertTrue(hub.is_persistent())
        self.assertNotIn('Milestone Quests', {button.label for button in hub.children})
        self.assertEqual(len(hub.children), 9)
        self.assertNotIn('Resume XP', {button.label for button in hub.children})
        self.assertEqual([button.label for button in bot.AllQuestsView(self.uid).children], ['Refresh Quests'])
        self.assertEqual(
            [(button.label, button.row) for button in hub.children],
            [('Profile', None), ('Streak', None), ('Ranks', None),
             ('Pauses', 1),
             ('Inbox', 2), ('Bot DMs', 2), ('Claim Reward', 1),
             ('All Quests', 1), ('Guild Blast', 2)],
        )
        pause_labels = [b.label for b in bot.PausePanelView(self.uid).children]
        self.assertEqual(pause_labels, ['Activate Pause', 'Resume XP', 'Refresh'])
        pause_display = bot.pause_panel_text(self.gid, self.uid)
        self.assertIn('Pauses remaining:', pause_display)
        self.assertIn('No active pause', pause_display)
        self.assertIn('cannot be undone', pause_display)
        self.assertEqual(len(bot.PrivateProgressView(self.uid, 'profile').children), 1)
        self.assertEqual([b.label for b in bot.RankProfileButtonView(self.uid).children], ['View Rank'])
        self.assertLessEqual(len(hub.children), 25)

    def test_one_lifetime_submission_persists_after_restart(self):
        _, first = bot.submit_purchase_order(self.gid, self.uid, 'test-1001')
        self.assertEqual(bot.review_purchase_order(self.gid, first, 42, True)[0], 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)
        bot.setup_db()
        self.assertEqual(bot.submit_purchase_order(self.gid, self.uid, 'test-1002')[0], 'approved')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)

    def test_streak_resets_on_missed_week_and_pause_preserves_count(self):
        from datetime import timedelta
        from unittest.mock import patch
        # Timeline from the user: 1 2 3 complete, 4 missed,
        # 5 complete, 6 paused, 7 complete = 2-week streak.
        now_week = bot.week_start()
        first = now_week - timedelta(weeks=6)
        def key(n):
            return (first + timedelta(weeks=n - 1)).date().isoformat()
        with bot.connect() as db:
            for n in (1, 2, 3, 5, 7):
                db.execute(
                    'INSERT INTO weekly_completions VALUES (?,?,?,?)',
                    (self.gid, self.uid, key(n), bot.utc_now().isoformat()))
            db.execute('INSERT INTO pauses VALUES (?,?,?,?)',
                       (self.gid, self.uid, key(6), bot.utc_now().isoformat()))
        with patch.object(bot, 'week_start', return_value=now_week):
            self.assertEqual(bot.active_streak(self.gid, self.uid), 2)
            self.assertEqual(bot.longest_streak(self.gid, self.uid), 3)
            # Paused week protects, but does not increase count.
            self.assertEqual(bot.award_earned_streak_milestones(self.gid, self.uid), [])
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)

    def test_inactivity_penalty_requires_zero_weekly_objectives(self):
        wk = bot.week_key()
        with bot.connect() as db:
            self.assertTrue(bot.week_needs_inactivity_penalty(
                db, self.gid, self.uid, wk))
            db.execute(
                'INSERT INTO weekly_actions VALUES (?,?,?,?,?,?,?,?)',
                (self.gid, self.uid, wk, 'gym', wk, 901, 'test-gym',
                 bot.utc_now().isoformat()))
            self.assertFalse(bot.week_needs_inactivity_penalty(
                db, self.gid, self.uid, wk))
            # Pressing the Turn In button is not needed to avoid the fine.
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM weekly_completions WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid)).fetchone()[0], 0)
        next_week = (bot.week_start() + timedelta(weeks=1)).date().isoformat()
        with bot.connect() as db:
            db.execute('INSERT INTO pauses VALUES (?,?,?,?)',
                       (self.gid, self.uid, next_week, bot.utc_now().isoformat()))
            self.assertFalse(bot.week_needs_inactivity_penalty(
                db, self.gid, self.uid, next_week))
            db.execute('INSERT INTO resumed_pauses VALUES (?,?,?,?)',
                       (self.gid, self.uid, next_week, bot.utc_now().isoformat()))
            self.assertTrue(bot.week_needs_inactivity_penalty(
                db, self.gid, self.uid, next_week))

    def test_full_quest_completion_counts_without_pressing_turn_in(self):
        # A finished week is stored independently of the Discord Turn In button.
        with bot.connect() as db:
            wk = bot.week_key()
            db.execute(
                'INSERT INTO weekly_completions VALUES (?,?,?,?)',
                (self.gid, self.uid, wk, bot.utc_now().isoformat()))
        self.assertEqual(bot.active_streak(self.gid, self.uid), 1)
        self.assertEqual(bot.award_earned_streak_milestones(self.gid, self.uid), [])
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)

    def test_admin_credited_streak_weeks_unlock_milestone_rewards(self):
        from datetime import timedelta
        start = bot.week_start()
        with bot.connect() as db:
            for i in range(12):
                wk = (start - timedelta(weeks=i)).date().isoformat()
                db.execute('INSERT INTO admin_streak_weeks VALUES (?,?,?,?)',
                           (self.gid, self.uid, wk, bot.utc_now().isoformat()))
        new = bot.award_earned_streak_milestones(self.gid, self.uid)
        self.assertEqual(new, ['streak4', 'streak8', 'streak12'])
        self.assertEqual(bot.get_xp(self.gid, self.uid), 75 + 125 + 175)
        self.assertEqual(bot.award_earned_streak_milestones(self.gid, self.uid), [])
        self.assertEqual(bot.get_xp(self.gid, self.uid), 375)
        self.assertEqual(bot.milestone_progress(self.gid, self.uid)['completed'], 3)

    def test_previous_admin_streak_credits_are_backfilled_and_stay_earned(self):
        from datetime import timedelta
        start = bot.week_start() - timedelta(weeks=13)
        with bot.connect() as db:
            for i in range(8):
                wk = (start - timedelta(weeks=i)).date().isoformat()
                db.execute('INSERT INTO admin_streak_weeks VALUES (?,?,?,?)',
                           (self.gid, self.uid, wk, bot.utc_now().isoformat()))
        self.assertEqual(bot.active_streak(self.gid, self.uid), 0)
        self.assertEqual(bot.backfill_earned_streak_milestones(self.gid), 2)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 200)
        self.assertEqual(bot.backfill_earned_streak_milestones(self.gid), 0)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 200)

    def test_pauses_preserve_but_do_not_add_streak_milestone_weeks(self):
        from datetime import timedelta
        start = bot.week_start()
        with bot.connect() as db:
            for i in (0, 1, 3, 4):
                wk = (start - timedelta(weeks=i)).date().isoformat()
                db.execute('INSERT INTO admin_streak_weeks VALUES (?,?,?,?)',
                           (self.gid, self.uid, wk, bot.utc_now().isoformat()))
            paused = (start - timedelta(weeks=2)).date().isoformat()
            db.execute('INSERT INTO pauses VALUES (?,?,?,?)',
                       (self.gid, self.uid, paused, bot.utc_now().isoformat()))
        self.assertEqual(bot.longest_streak(self.gid, self.uid), 4)
        self.assertEqual(bot.award_earned_streak_milestones(self.gid, self.uid), ['streak4'])
        self.assertEqual(bot.get_xp(self.gid, self.uid), 75)

    def test_reset_does_not_reimport_historical_order_on_restart(self):
        with bot.connect() as db:
            db.execute(
                'INSERT INTO pending_milestones(guild_id,user_id,kind,evidence,status,created_at) '
                "VALUES (?,?, 'purchase1', ?, 'pending', ?)",
                (self.gid, self.uid, '#historic1001', bot.utc_now().isoformat()),
            )
        bot.setup_db()
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'pending')
        self.assertEqual(bot.reset_milestone_test_data(
            self.gid, self.uid, 'order')['orders_archived'], 1)
        bot.setup_db()
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'available')
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['pending'], 0)
        self.assertEqual(bot.submit_purchase_order(
            self.gid, self.uid, '#historic1001')[0], 'submitted')

    def test_admin_reset_order_reverses_xp_preserves_audit_and_can_retest_same_reference(self):
        other = self.uid + 1
        status, order_id = bot.submit_purchase_order(self.gid, self.uid, '#9001')
        self.assertEqual(status, 'submitted')
        self.assertEqual(bot.review_purchase_order(self.gid, order_id, 1, True)[0], 'awarded')
        with bot.connect() as db:
            db.execute('INSERT INTO reward_tickets(guild_id,user_id,rank,status) VALUES (?,?,?,?)',
                       (self.gid, self.uid, 'Master', 'fulfilled'))
            db.execute('INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)',
                       (self.gid, other, 400))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)
        result = bot.reset_milestone_test_data(self.gid, self.uid, 'order')
        self.assertEqual(result['order'], 1)
        self.assertEqual(result['xp_reversed'], 250)
        self.assertEqual(result['orders_archived'], 1)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        self.assertEqual(bot.get_xp(self.gid, other), 400)
        self.assertEqual(bot.order_submission_status(self.gid, self.uid), 'available')
        self.assertEqual(bot.review_purchase_order(self.gid, order_id, 1, True)[0], 'not_pending')
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['approved'], 0)
        with bot.connect() as db:
            old = db.execute('SELECT status,order_reference FROM purchase_orders WHERE id=?',
                             (order_id,)).fetchone()
            self.assertEqual(old['status'], 'test_reset')
            self.assertIn('#9001', old['order_reference'])
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM reward_tickets WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid)).fetchone()[0], 1)
        self.assertEqual(bot.reset_milestone_test_data(self.gid, self.uid, 'order')['xp_reversed'], 0)
        status, new_id = bot.submit_purchase_order(self.gid, self.uid, '#9001')
        self.assertEqual(status, 'submitted')
        self.assertNotEqual(new_id, order_id)
        self.assertEqual(bot.review_purchase_order(self.gid, new_id, 1, True)[0], 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 250)
        # Most recent award, not two historical test cycles.
        self.assertEqual(bot.purchase_order_summary(self.gid, self.uid)['earned_xp'], 250)

    def test_admin_reset_boost_reverses_xp_and_allows_reaward(self):
        with bot.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            bot.grant_once(db, 'milestone_claims', self.gid, self.uid, 'boost1', 50)
        result = bot.reset_milestone_test_data(self.gid, self.uid, 'boost')
        self.assertEqual(result['boost'], 1)
        self.assertEqual(result['xp_reversed'], 50)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        self.assertNotIn('boost1', bot.milestone_progress(self.gid, self.uid)['claimed'])
        with bot.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertTrue(bot.grant_once(db, 'milestone_claims', self.gid, self.uid,
                                           'boost1', bot.MILESTONE_XP['boost1']))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 100)
        self.assertIn('100 XP granted', bot.boost_milestone_message(self.gid, self.uid, True))

    def test_admin_reset_both_reverses_completion_but_preserves_other_milestones(self):
        kinds = dict((kind, xp) for kind, _, xp, _ in bot.MILESTONE_QUESTS)
        with bot.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for kind, amount in kinds.items():
                self.assertTrue(bot.grant_once(db, 'milestone_claims', self.gid, self.uid, kind, amount))
        initial = sum(kinds.values()) + bot.MILESTONE_COMPLETION_BONUS
        self.assertEqual(bot.get_xp(self.gid, self.uid), initial)
        result = bot.reset_milestone_test_data(self.gid, self.uid, 'both')
        self.assertEqual((result['order'], result['boost'], result['completion']), (1, 1, 1))
        self.assertEqual(result['xp_reversed'], 250 + 100 + 500)
        self.assertEqual(bot.get_xp(self.gid, self.uid), initial - 850)
        claimed = bot.milestone_progress(self.gid, self.uid)['claimed']
        self.assertEqual(claimed, set(kinds) - {'purchase1', 'boost1'})
        bot.setup_db()
        self.assertEqual(bot.get_xp(self.gid, self.uid), initial - 850)
        self.assertFalse(bot.milestone_progress(self.gid, self.uid)['bonus_earned'])

    def test_admin_reset_is_scoped_and_does_not_clear_unrelated_order_requests(self):
        other = self.uid + 1
        _, first = bot.submit_purchase_order(self.gid, self.uid, 'self-order')
        _, other_id = bot.submit_purchase_order(self.gid, other, 'other-order')
        result = bot.reset_milestone_test_data(self.gid, self.uid, 'order')
        self.assertEqual(result['orders_archived'], 1)
        self.assertEqual(bot.review_purchase_order(self.gid, first, 1, True)[0], 'not_pending')
        self.assertEqual(bot.order_submission_status(self.gid, other), 'pending')
        self.assertEqual(bot.review_purchase_order(self.gid, other_id, 1, True)[0], 'awarded')
        self.assertEqual(bot.get_xp(self.gid, other), 250)
        with self.assertRaises(ValueError):
            bot.reset_milestone_test_data(self.gid, self.uid, 'all')

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
