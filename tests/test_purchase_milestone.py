"""First-purchase milestone regression tests using an isolated SQLite database."""
import tempfile
import unittest
from pathlib import Path

import bot


class FirstPurchaseMilestoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = bot.DB
        bot.DB = Path(self.tmp.name) / "test-purchase.db"
        bot.setup_db()
        self.gid, self.uid = 123456, 789

    def tearDown(self):
        bot.DB = self.original_db
        self.tmp.cleanup()

    def test_submit_pending_approve_once_and_preserve_existing_records(self):
        with bot.connect() as db:
            db.execute(
                'INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)',
                (self.gid, self.uid, 150),
            )
            db.execute(
                'INSERT INTO reward_tickets(guild_id,user_id,rank,status) VALUES (?,?,?,?)',
                (self.gid, self.uid, 'Master', 'fulfilled'),
            )
        self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid, '#1234'), 'submitted')
        self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid, '#other'), 'pending')
        self.assertEqual(bot.review_first_purchase_milestone(self.gid, self.uid, True), 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 200)
        self.assertEqual(bot.review_first_purchase_milestone(self.gid, self.uid, True), 'not_pending')
        self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid, '#another'), 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 200)
        with bot.connect() as db:
            row = db.execute(
                'SELECT status,evidence FROM pending_milestones WHERE guild_id=? AND user_id=? AND kind=?',
                (self.gid, self.uid, 'purchase1'),
            ).fetchone()
            self.assertEqual((row['status'], row['evidence']), ('approved', '#1234'))
            claims = db.execute(
                'SELECT COUNT(*) FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind=?',
                (self.gid, self.uid, 'purchase1'),
            ).fetchone()[0]
            self.assertEqual(claims, 1)
            tickets = db.execute(
                'SELECT COUNT(*) FROM reward_tickets WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid),
            ).fetchone()[0]
            self.assertEqual(tickets, 1)

    def test_rejection_and_resubmit(self):
        self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid, 'order-a'), 'submitted')
        self.assertEqual(bot.review_first_purchase_milestone(self.gid, self.uid, False), 'rejected')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)
        self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid, 'order-b'), 'submitted')
        self.assertEqual(bot.review_first_purchase_milestone(self.gid, self.uid, True), 'awarded')
        self.assertEqual(bot.get_xp(self.gid, self.uid), 50)

    def test_never_overwrite_already_claimed_boost_or_purchase(self):
        with bot.connect() as db:
            db.execute(
                'INSERT INTO milestone_claims(guild_id,user_id,kind,claimed_at) VALUES (?,?,?,?)',
                (self.gid, self.uid, 'purchase1', bot.utc_now().isoformat()),
            )
            db.execute(
                'INSERT INTO milestone_claims(guild_id,user_id,kind,claimed_at) VALUES (?,?,?,?)',
                (self.gid, self.uid, 'boost1', bot.utc_now().isoformat()),
            )
        self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid, '#xyz'), 'awarded')
        with bot.connect() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM milestone_claims WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid),
            ).fetchone()[0], 2)

    def test_invalid_reference_and_other_users_untouched(self):
        for reference in ('', ' ', 'x' * 81, 'newline\nvalue'):
            self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid, reference), 'invalid')
        self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid + 1, '#abcd'), 'submitted')
        self.assertEqual(bot.submit_first_purchase_milestone(self.gid, self.uid, '#1234'), 'submitted')
        self.assertEqual(bot.review_first_purchase_milestone(self.gid, self.uid, True), 'awarded')
        with bot.connect() as db:
            self.assertEqual(db.execute(
                "SELECT status FROM pending_milestones WHERE guild_id=? AND user_id=? AND kind=?",
                (self.gid, self.uid + 1, 'purchase1'),
            ).fetchone()['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
