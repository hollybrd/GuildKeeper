"""Tests for GuildKeeper's non-destructive guided Discord acceptance runner.

READING GUIDE
-------------
Tests the staff-guided acceptance checklist itself: read-only player snapshots, permission checks and saved human-reported results.

Each setUp() uses a temporary SQLite database rather than GuildKeeper's
real player database. Assertions compare actual results with expected rules.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import discord
import test_runner
import testing_cheats


class TestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_db = bot.DB
        bot.DB = Path(self.temporary.name) / 'acceptance.db'
        bot.setup_db()
        self.gid, self.tester_id, self.admin_id = 67890, 98765, 11111

    def tearDown(self):
        bot.DB = self.original_db
        self.temporary.cleanup()

    def test_testing_panel_exposes_persistent_staff_only_runner(self):
        view = testing_cheats.TestingCheatsView()
        runner = next(item for item in view.children if item.label == 'Test Runner')
        self.assertEqual(runner.custom_id, 'guildkeeper:testing:runner')
        self.assertTrue(view.is_persistent())
        self.assertEqual(len(test_runner.TEST_CASES), 18)
        self.assertEqual(len(test_runner.ScenarioPicker(self.admin_id, self.tester_id).children[0].options), 18)

    def test_results_persist_and_are_isolated_per_tester(self):
        test_runner.save_result(self.gid, self.tester_id, 'welcome_dm',
                                'passed', 'Observed the DM', self.admin_id)
        test_runner.save_result(self.gid, self.tester_id, 'training_first',
                                'failed', 'Expected 50 XP, got zero', self.admin_id)
        first = test_runner.results(self.gid, self.tester_id)
        self.assertEqual(first['welcome_dm']['status'], 'passed')
        self.assertEqual(first['training_first']['note'], 'Expected 50 XP, got zero')
        self.assertEqual(test_runner.results(self.gid, self.tester_id + 1), {})
        self.assertEqual(test_runner.results(self.gid + 1, self.tester_id), {})
        # No duplicate rows on re-evaluation; old failures can be marked fixed.
        test_runner.save_result(self.gid, self.tester_id, 'training_first',
                                'passed', 'Retested', self.admin_id)
        self.assertEqual(test_runner.results(self.gid, self.tester_id)['training_first']['status'],
                         'passed')
        self.assertIn('2/18 passed', test_runner.progress_text(self.gid, self.tester_id))

    def test_invalid_result_cannot_be_stored(self):
        with self.assertRaises(ValueError):
            test_runner.save_result(self.gid, self.tester_id, 'unlisted',
                                    'passed', 'Invalid', self.admin_id)
        with self.assertRaises(ValueError):
            test_runner.save_result(self.gid, self.tester_id, 'welcome_dm',
                                    'unverified', 'Invalid', self.admin_id)
        with self.assertRaises(ValueError):
            test_runner.save_result(self.gid, self.tester_id, 'welcome_dm',
                                    'passed', 'Invalid', 0)

    def test_read_only_snapshot_does_not_mutate_member_progress(self):
        bot.add_xp(self.gid, self.tester_id, 35, 'Seed XP for test')
        before = bot.get_xp(self.gid, self.tester_id)
        first = test_runner.snapshot(self.gid, self.tester_id)
        second = test_runner.snapshot(self.gid, self.tester_id)
        self.assertEqual(first, second)
        self.assertEqual(bot.get_xp(self.gid, self.tester_id), before)
        self.assertEqual(first['weekly'], [])
        self.assertFalse(first['weekly_complete'])
        self.assertFalse(first['onboarded'])
        self.assertEqual(first['starter'], 0)
        self.assertEqual(first['xp'], 35)
        self.assertFalse(test_runner.results(self.gid, self.tester_id))

    def test_inconsistencies_are_flagged_not_silently_passed(self):
        with bot.connect() as db:
            db.execute('INSERT INTO weekly_completions VALUES (?,?,?,?)',
                       (self.gid, self.tester_id, bot.week_key(), bot.utc_now().isoformat()))
            db.execute('INSERT INTO starter_turnins VALUES (?,?,?)',
                       (self.gid, self.tester_id, bot.utc_now().isoformat()))
        snap = test_runner.snapshot(self.gid, self.tester_id)
        self.assertEqual(len(snap['warnings']), 2)
        embed = test_runner.snapshot_embed(self.gid, self.tester_id)
        self.assertTrue(any(f.name == 'Potential inconsistencies' for f in embed.fields))
        self.assertEqual(test_runner.results(self.gid, self.tester_id), {})

    def test_report_marks_manual_not_automated_success(self):
        empty = test_runner.progress_text(self.gid, self.tester_id)
        self.assertIn('0/18 passed', empty)
        self.assertIn('manually recorded', empty)
        self.assertNotIn('✅ Welcome DM', empty)
        runner = test_runner.RunnerMenu(self.admin_id, self.tester_id)
        self.assertEqual([button.label for button in runner.children],
                         ['Live Checklist', 'Read-Only Checks', 'Test Report'])


class TestRunnerAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unauthorized_interactions_are_blocked(self):
        original = bot.GUILD_ID
        bot.GUILD_ID = 123
        try:
            view = test_runner.RunnerMenu(900, 901)
            member = SimpleNamespace(id=901)
            interaction = SimpleNamespace(
                guild_id=123, user=member,
                response=SimpleNamespace(send_message=AsyncMock()))
            allowed = await view.interaction_check(interaction)
            self.assertFalse(allowed)
            interaction.response.send_message.assert_awaited_once()
        finally:
            bot.GUILD_ID = original

    def test_normal_member_selection_requires_no_manage_server(self):
        self.assertEqual(len(test_runner.TestMemberPicker(100).children), 1)
