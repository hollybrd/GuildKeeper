"""Regression tests for the private GuildKeeper Admin Hub."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import admin_hub
import discord


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

class FakeChannel:
    pass


class AdminHubChannelSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB
        self.old_gid = bot.GUILD_ID
        bot.GUILD_ID = 123456
        bot.DB = Path(self.tmp.name) / 'hub-channel.db'
        bot.setup_db()

    def tearDown(self):
        bot.DB = self.old_db
        bot.GUILD_ID = self.old_gid
        self.tmp.cleanup()

    def make_channel(self, channel_id=345, public=False):
        guild = SimpleNamespace(
            id=bot.GUILD_ID,
            default_role=SimpleNamespace(),
            me=SimpleNamespace(),
            get_channel=lambda cid: self.channel if cid == channel_id else None,
        )
        channel = FakeChannel()
        channel.id, channel.guild, channel.mention = channel_id, guild, '<#345>'
        channel.permissions_for = lambda role: SimpleNamespace(
            view_channel=public if role is guild.default_role else True,
            send_messages=True, embed_links=True, read_message_history=True)
        channel.send = AsyncMock(return_value=SimpleNamespace(id=678, edit=AsyncMock(), delete=AsyncMock()))
        channel.fetch_message = AsyncMock()
        self.channel = channel
        return guild, channel

    async def test_public_channel_is_rejected_without_posting(self):
        guild, channel = self.make_channel(public=True)
        with patch.object(discord, 'TextChannel', FakeChannel), patch.object(bot.bot, 'get_guild', return_value=guild):
            ok, message = await admin_hub.post_admin_hub(channel)
        self.assertFalse(ok)
        self.assertIn('visible to @everyone', message)
        channel.send.assert_not_awaited()

    async def test_selected_private_channel_is_saved(self):
        guild, channel = self.make_channel()
        with patch.object(discord, 'TextChannel', FakeChannel), patch.object(bot.bot, 'get_guild', return_value=guild):
            ok, message = await admin_hub.post_admin_hub(channel)
        self.assertTrue(ok, message)
        channel.send.assert_awaited_once()
        with bot.connect() as db:
            saved = db.execute('SELECT value FROM v12_config WHERE key=?',
                               ('admin-hub-post-' + str(bot.GUILD_ID),)).fetchone()['value']
        self.assertEqual(saved, '345:678')

    async def test_moving_hub_deletes_previous_message(self):
        guild, channel = self.make_channel()
        with bot.connect() as db:
            db.execute('INSERT INTO v12_config(key,value) VALUES (?,?)',
                       ('admin-hub-post-' + str(bot.GUILD_ID), '345:555'))
        old_message = SimpleNamespace(id=555, edit=AsyncMock(), delete=AsyncMock())
        channel.fetch_message = AsyncMock(return_value=old_message)
        # Same channel: edit existing message rather than posting duplicates.
        with patch.object(discord, 'TextChannel', FakeChannel), patch.object(bot.bot, 'get_guild', return_value=guild):
            ok, message = await admin_hub.post_admin_hub(channel)
        self.assertTrue(ok, message)
        old_message.edit.assert_awaited_once()
        channel.send.assert_not_awaited()
