"""Regression tests for the private GuildKeeper Admin Hub."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
import admin_hub


class AdminHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB
        bot.DB = Path(self.temp.name) / 'admin-hub.db'
        bot.setup_db()

    def tearDown(self):
        bot.DB = self.old_db
        self.temp.cleanup()

    def test_persistent_admin_panel_has_six_actions(self):
        view = admin_hub.AdminHubView()
        self.assertTrue(view.is_persistent())
        self.assertEqual(
            [item.label for item in view.children],
            ['Players', 'Quests', 'Rewards & Orders',
             'Announcements', 'Audit Log', 'Bot Controls'])
        self.assertEqual([item.row for item in view.children], [0,0,0,1,1,1])

    def test_admin_only_permission_policy(self):
        def interaction(gid, manage):
            return SimpleNamespace(
                guild_id=gid,
                user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=manage)))
        # User must be an actual Discord Member, not a forged user object.
        self.assertFalse(admin_hub.allowed(interaction(bot.GUILD_ID, True)))
        self.assertFalse(admin_hub.allowed(interaction(bot.GUILD_ID, False)))
        self.assertFalse(admin_hub.allowed(interaction(999999, True)))

    def test_audit_entries_are_persistent(self):
        admin_hub.audit(101, 'xp_change', '25 XP correction', 202)
        with bot.connect() as db:
            entry = db.execute('SELECT * FROM admin_hub_audit').fetchone()
        self.assertEqual(entry['actor_id'], 101)
        self.assertEqual(entry['target_id'], 202)
        self.assertEqual(entry['action'], 'xp_change')
        self.assertIn('25 XP', admin_hub.audit_summary())

    def test_player_modal_and_confirmation_are_separate(self):
        form = admin_hub.XPForm(11, 22, -1)
        self.assertEqual(len(form.children), 2)
        self.assertEqual({x.label for x in form.children}, {'Amount (1–500)', 'Reason'})
        confirmation = admin_hub.ConfirmXP(11, 22, -25, 'Correction')
        self.assertEqual([x.label for x in confirmation.children],
                         ['Confirm XP Change', 'Cancel'])
        self.assertEqual(bot.get_xp(bot.GUILD_ID, 22), 0)

    def test_rewards_and_health_are_read_only(self):
        self.assertIn('Orders pending:', admin_hub.reward_summary())
        view = admin_hub.RewardMenu(11)
        self.assertEqual([x.label for x in view.children], ['Refresh Requests'])
        with bot.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) n FROM xp_log').fetchone()['n'], 0)


class PermissionChecks(unittest.IsolatedAsyncioTestCase):
    async def test_every_public_hub_button_is_permission_guarded(self):
        view = admin_hub.AdminHubView()
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(guild_id=bot.GUILD_ID,
                                      user=SimpleNamespace(
                                          guild_permissions=SimpleNamespace(manage_guild=False)),
                                      response=response)
        self.assertFalse(await view.interaction_check(interaction))
        response.send_message.assert_awaited_once()
        self.assertEqual(response.send_message.await_args.kwargs['ephemeral'], True)
