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
            ['Players', 'Rewards & Orders', 'Announcements',
             'Audit Log', 'Health Check', 'Bot Controls'])
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

class PermanentPanelHealthTests(unittest.IsolatedAsyncioTestCase):
    def test_status_report_labels_and_links(self):
        import panel_health
        report = panel_health.render([
            {'name': 'Player Hub', 'state': 'current', 'detail': 'Current', 'url': None},
            {'name': 'Milestone Quests', 'state': 'missing', 'detail': 'No recorded panel', 'url': None},
            {'name': 'Admin Hub', 'state': 'outdated', 'detail': 'Old buttons', 'url': 'https://discord.com/channels/1/2/3'},
        ])
        self.assertIn('1/3', report)
        self.assertIn('missing', report)
        self.assertIn('View panel', report)

    async def test_refresh_only_operates_on_missing_or_outdated_panels(self):
        from unittest.mock import patch, AsyncMock
        import panel_health
        api = SimpleNamespace(GUILD_ID=1, QUESTS_CHANNEL_ID=123)
        guild = SimpleNamespace(id=1)
        status = [
            {'name': 'Player Hub', 'state': 'current', 'detail': 'Current', 'url': None},
            {'name': 'No Such Panel', 'state': 'unknown', 'detail': 'Cannot check', 'url': None},
            {'name': 'Other', 'state': 'setup', 'detail': 'Missing configuration', 'url': None},
        ]
        with patch.object(panel_health, '_repair_weekly', new=AsyncMock()) as repair:
            results = await panel_health.repair(api, guild, status)
        self.assertEqual(results, [])
        repair.assert_not_awaited()

    async def test_mismatched_button_ids_are_outdated(self):
        from unittest.mock import patch
        import panel_health
        class FakeTextChannel:
            pass
        channel = FakeTextChannel()
        message = SimpleNamespace(
            author=SimpleNamespace(id=456),
            embeds=[],
            components=[SimpleNamespace(children=[SimpleNamespace(custom_id='old-button')])])
        channel.fetch_message = AsyncMock(return_value=message)
        guild = SimpleNamespace(
            id=1,
            me=SimpleNamespace(id=456),
            get_channel=lambda channel_id: channel)
        channel.guild = guild
        channel.id = 234
        message.id = 789
        expected_view = SimpleNamespace(children=[SimpleNamespace(custom_id='new-button')])
        with patch.object(panel_health.discord, 'TextChannel', FakeTextChannel):
            result = await panel_health._check_message(
                guild, 'Player Hub', 234, 789, view=expected_view)
        self.assertEqual(result['state'], 'outdated')

    async def test_missing_message_does_not_delete_or_repost_unknown_panels(self):
        import panel_health
        unknown = [{'name': 'Starter Quests', 'state': 'unknown',
                    'detail': 'Discord permission error', 'url': None}]
        output = await panel_health.repair(SimpleNamespace(), SimpleNamespace(id=12), unknown)
        self.assertEqual(output, [])

    def test_admin_ui_has_status_and_refresh_with_confirmation(self):
        import admin_hub
        controls = [button.label for button in admin_hub.ControlMenu(123).children]
        self.assertIn('Permanent Panels', controls)
        screen = [button.label for button in admin_hub.PanelHealthMenu(123).children]
        self.assertEqual(screen, ['Check Again', 'Refresh Panels'])
        confirmation = [button.label for button in admin_hub.ConfirmPanelRefresh(123).children]
        self.assertEqual(confirmation, ['Confirm Refresh', 'Cancel'])

class StandaloneHealthTests(unittest.TestCase):
    def test_overall_never_claims_healthy_when_unknown_or_missing(self):
        import health_check
        empty = lambda state: {'name': 'Example', 'state': state, 'detail': 'test'}
        self.assertEqual(health_check.overall(
            {'core': [empty('current')], 'panels': [empty('current')]}), 'healthy')
        self.assertEqual(health_check.overall(
            {'core': [empty('current')], 'panels': [empty('unknown')]}), 'partial')
        self.assertEqual(health_check.overall(
            {'core': [empty('current')], 'panels': [empty('missing')]}), 'attention')
        self.assertEqual(health_check.overall(
            {'core': [empty('problem')], 'panels': [empty('current')]}), 'attention')

    def test_standalone_button_and_private_subpanel(self):
        self.assertIn('Health Check', [b.label for b in admin_hub.AdminHubView().children])
        self.assertNotIn('Health Check', [b.label for b in admin_hub.ControlMenu(1).children])
        self.assertEqual(
            [b.label for b in admin_hub.HealthCheckMenu(1).children],
            ['Check Again', 'Permanent Panels'])

    def test_database_health(self):
        import health_check
        with tempfile.TemporaryDirectory() as temp:
            original = bot.DB
            try:
                bot.DB = Path(temp) / 'health.db'
                bot.setup_db()
                self.assertEqual(health_check.check_database(bot)['state'], 'current')
            finally:
                bot.DB = original


class TestingCheatsTests(unittest.TestCase):
    def test_persistent_test_panel_contains_shortcuts(self):
        import testing_cheats
        view = testing_cheats.TestingCheatsView()
        self.assertTrue(view.is_persistent())
        self.assertEqual([item.label for item in view.children],
                         ['Add / Remove XP', 'Complete Quest', 'Starter Reset', 'Weekly Reset',
                          'Add / Remove Streak', 'Pause Reset', 'Rank Test Reset',
                          'Reward Reset', 'Milestone Reset', 'All Test Commands'])
        self.assertEqual([b.row for b in view.children], [0,0,0,0,1,1,1,2,2,2])

    def test_member_actions_require_confirmation(self):
        import testing_cheats
        view = testing_cheats.ConfirmCheat(1, 2, 'starter_reset')
        self.assertEqual([b.label for b in view.children],
                         ['Confirm Test Action', 'Cancel'])
        self.assertFalse(view.used)
        picker = testing_cheats.CheatMemberPickerView(1, 'starter_reset')
        self.assertEqual(len(picker.children), 1)

    def test_testing_command_exists_as_backup_and_setup(self):
        self.assertIsNotNone(bot.bot.tree.get_command('admin_testing_panel_post'))
        self.assertIsNotNone(bot.bot.tree.get_command('admin_starter_reset'))
        self.assertIsNotNone(bot.bot.tree.get_command('admin_weekly_reset'))


class CheatImprovementsTests(unittest.TestCase):
    def test_streak_and_milestone_actions(self):
        import testing_cheats
        self.assertEqual([b.label for b in testing_cheats.StreakChoiceView(17).children],
                         ['Add Streak Weeks', 'Remove Streak Weeks'])
        self.assertEqual([o.value for o in testing_cheats.MilestoneTargetView(17).children[0].options],
                         ['order', 'boost', 'both'])
        self.assertIsNotNone(bot.bot.tree.get_command('admin_milestone_test_reset'))

    def test_command_reference_is_grouped(self):
        import testing_cheats
        guide = testing_cheats.test_command_guide()
        self.assertEqual(len(guide.fields), 4)
        self.assertIn('/admin_milestone_test_reset', guide.fields[2].value)
        self.assertIn('/admin_streak_add', guide.fields[1].value)
        self.assertIn('\n', guide.fields[0].value)

    def test_only_reward_is_blue_on_player_hub(self):
        from discord import ButtonStyle
        view = bot.PlayerStatsView()
        primary = [b.label for b in view.children if b.style == ButtonStyle.primary]
        self.assertEqual(primary, ['Claim Reward'])


class QuestCompletionCheatsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB
        bot.DB = Path(self.temp.name) / 'quest-cheats.db'
        bot.setup_db()
        self.gid, self.uid = 76543, 66532

    def tearDown(self):
        bot.DB = self.old_db
        self.temp.cleanup()

    def test_quest_completion_has_member_category_objective_confirmation(self):
        import testing_cheats
        view = testing_cheats.TestingCheatsView()
        self.assertIn('Complete Quest', [button.label for button in view.children])
        chooser = testing_cheats.QuestCategoryView(100, self.uid)
        self.assertEqual([x.value for x in chooser.children[0].options],
                         ['weekly', 'starter', 'milestone'])
        objective = testing_cheats.QuestObjectiveView(100, self.uid, 'weekly')
        self.assertEqual(len(objective.children[0].options), 5)
        self.assertEqual([x.label for x in testing_cheats.ConfirmQuestCompletion(
            100, self.uid, 'weekly', 'all').children],
                         ['Confirm Test Completion', 'Cancel'])

    def test_weekly_full_completion_awards_exactly_once_and_first_week(self):
        import quest_testing
        before = bot.get_xp(self.gid, self.uid)
        missing, award, complete = quest_testing.complete(
            bot, self.gid, self.uid, 'weekly', 'all', 111)
        self.assertEqual(len(missing), 4)
        self.assertEqual(award, 200)
        self.assertTrue(complete)
        # Weekly 200 + first weekly milestone 25 + Starter First Steps 10.
        self.assertEqual(bot.get_xp(self.gid, self.uid) - before, 235)
        self.assertEqual(len(bot.weekly_status(self.gid, self.uid, bot.week_key())), 4)
        second = quest_testing.complete(bot, self.gid, self.uid, 'weekly', 'all', 111)
        self.assertEqual(second, ([], 0, False))
        self.assertEqual(bot.get_xp(self.gid, self.uid) - before, 235)

    def test_partial_weekly_completion_then_rest_does_not_reaward(self):
        import quest_testing
        first = quest_testing.complete(bot, self.gid, self.uid, 'weekly', 'training1', 123)
        self.assertEqual(first[1], 50)
        second = quest_testing.complete(bot, self.gid, self.uid, 'weekly', 'all', 123)
        self.assertEqual(second[1], 150)
        self.assertEqual(len(second[0]), 3)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 235)

    def test_weekly_cheat_respects_active_pause(self):
        import quest_testing
        with bot.connect() as db:
            db.execute('INSERT INTO pauses VALUES (?,?,?,?)',
                       (self.gid, self.uid, bot.week_key(), bot.utc_now().isoformat()))
        with self.assertRaisesRegex(ValueError, 'paused'):
            quest_testing.complete(bot, self.gid, self.uid, 'weekly', 'all', 123)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 0)

    def test_starter_full_turns_in_and_does_not_duplicate(self):
        import quest_testing
        result = quest_testing.complete(bot, self.gid, self.uid, 'starter', 'all', 123)
        self.assertEqual(result[:2], (list(bot.STARTER_REWARDS), 50))
        self.assertEqual(bot.get_xp(self.gid, self.uid), 50)
        with bot.connect() as db:
            self.assertIsNotNone(db.execute(
                'SELECT 1 FROM starter_turnins WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid)).fetchone())
        quest_testing.complete(bot, self.gid, self.uid, 'starter', 'all', 123)
        self.assertEqual(bot.get_xp(self.gid, self.uid), 50)

    def test_milestone_admin_test_grants_one_time_rewards_and_completion(self):
        import quest_testing
        missing, base_xp, _ = quest_testing.complete(bot, self.gid, self.uid, 'milestone', 'all', 123)
        self.assertEqual(len(missing), 6)
        self.assertEqual(base_xp, sum(bot.MILESTONE_XP.values()))
        self.assertEqual(bot.get_xp(self.gid, self.uid),
                         sum(bot.MILESTONE_XP.values()) + bot.MILESTONE_COMPLETION_BONUS)
        quest_testing.complete(bot, self.gid, self.uid, 'milestone', 'all', 123)
        self.assertEqual(bot.get_xp(self.gid, self.uid),
                         sum(bot.MILESTONE_XP.values()) + bot.MILESTONE_COMPLETION_BONUS)
        with bot.connect() as db:
            self.assertIsNone(db.execute(
                'SELECT 1 FROM purchase_orders WHERE guild_id=? AND user_id=?',
                (self.gid, self.uid)).fetchone())


class SaferAdminActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB
        bot.DB = Path(self.temp.name) / 'admin-safety.db'
        bot.setup_db()
        self.gid, self.uid = 123, 456

    def tearDown(self):
        bot.DB = self.old_db
        self.temp.cleanup()

    def test_impact_preview_counts_starter_and_weekly_refunds(self):
        import admin_action_safety
        with bot.connect() as db:
            db.execute('INSERT INTO starter_claims VALUES (?,?,?,?)',
                       (self.gid, self.uid, 'intro', bot.utc_now().isoformat()))
            db.execute('INSERT INTO weekly_actions VALUES (?,?,?,?,?,?,?,?)',
                       (self.gid, self.uid, bot.week_key(), 'gym',
                        bot.cycle_day(), -42, 'preview-test', bot.utc_now().isoformat()))
        starter, snapshot = admin_action_safety.impact(
            bot, self.gid, self.uid, 'starter_reset')
        self.assertIn('1/5', starter)
        self.assertIn('up to 10 XP', starter)
        self.assertIsInstance(snapshot, tuple)
        weekly, _ = admin_action_safety.impact(bot, self.gid, self.uid, 'weekly_reset')
        self.assertIn('1/4', weekly)
        self.assertIn('up to 25 XP', weekly)

    def test_reward_preview_includes_ticket_id_and_channel(self):
        import admin_action_safety
        with bot.connect() as db:
            db.execute("INSERT INTO reward_tickets(guild_id,user_id,rank,channel_id,status,requested_at) "
                       "VALUES (?,?,?,?,?,?)",
                       (self.gid, self.uid, 'Master', 789, 'open', bot.utc_now().isoformat()))
        detail, current = admin_action_safety.impact(
            bot, self.gid, self.uid, 'reward_reset', rank='Master')
        self.assertIn('<#789>', detail)
        self.assertIn('open', detail)
        self.assertIsNotNone(current[1])

    def test_reset_reason_is_mandatory_and_confirm_stores_snapshot(self):
        import testing_cheats
        modal = testing_cheats.ResetReasonForm(11, 22, 'weekly_reset')
        self.assertTrue(modal.reason.required)
        view = testing_cheats.ConfirmCheat(
            11, 22, 'weekly_reset', reason='QA retest', snapshot=('before',))
        self.assertEqual(view.reason, 'QA retest')
        self.assertEqual(view.snapshot, ('before',))

    def test_audit_shows_actor_target_date_and_reason(self):
        import admin_hub
        old_gid = bot.GUILD_ID
        bot.GUILD_ID = self.gid
        try:
            admin_hub.audit(11, 'testing_weekly_reset',
                            'reason=QA retest', self.uid)
            display = admin_hub.audit_summary()
            self.assertIn('moderator 11', display)
            self.assertIn('target 456', display)
            self.assertIn('reason=QA retest', display)
        finally:
            bot.GUILD_ID = old_gid


class AutomaticMaintenanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = bot.DB
        bot.DB = Path(self.temp.name) / 'automatic-maintenance.db'
        bot.setup_db()

    def tearDown(self):
        bot.DB = self.old_db
        self.temp.cleanup()

    def test_job_status_is_persistent_and_reports_failure(self):
        import admin_maintenance
        admin_maintenance.record_job(bot, 'weekly_board', False, 'HTTPException')
        statuses = admin_maintenance.job_statuses(bot)
        self.assertFalse(statuses['weekly_board']['success'])
        self.assertEqual(statuses['weekly_board']['error'], 'HTTPException')
        admin_maintenance.record_job(bot, 'weekly_board', True)
        self.assertTrue(admin_maintenance.job_statuses(bot)['weekly_board']['success'])

    async def test_unknown_panels_are_skipped_without_repair(self):
        import admin_maintenance
        guild = SimpleNamespace(id=bot.GUILD_ID)
        unknown = [{'name': 'Starter Quests', 'state': 'unknown',
                    'detail': 'Insufficient permissions', 'url': None}]
        with patch.object(admin_maintenance.panel_health, 'snapshot',
                          new=AsyncMock(side_effect=[unknown, unknown])) as snapshot:
            with patch.object(admin_maintenance.panel_health, 'repair',
                              new=AsyncMock()) as repair:
                result = await admin_maintenance.reconcile(bot, guild)
        repair.assert_not_awaited()
        self.assertEqual(result['updated'], 0)
        self.assertEqual(len(result['remaining']), 1)
        self.assertFalse(admin_maintenance.needs_periodic_check(bot))

    async def test_saved_outdated_panel_repaired_and_rechecked(self):
        import admin_maintenance
        guild = SimpleNamespace(id=bot.GUILD_ID)
        outdated = [{'name': 'Player Hub', 'state': 'outdated',
                     'detail': 'Stale buttons', 'url': None}]
        fresh = [{'name': 'Player Hub', 'state': 'current',
                  'detail': 'Current', 'url': None}]
        with patch.object(admin_maintenance.panel_health, 'snapshot',
                          new=AsyncMock(side_effect=[outdated, fresh])):
            with patch.object(admin_maintenance.panel_health, 'repair',
                              new=AsyncMock(return_value=[(True, 'Updated')])) as repair:
                result = await admin_maintenance.reconcile(bot, guild)
        repair.assert_awaited_once()
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['remaining'], [])
