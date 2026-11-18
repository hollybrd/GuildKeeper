"""Guided GuildKeeper Discord acceptance testing.

Staff select an existing non-bot test member. Diagnostics only READ player
progress, roles and panel state. The checklist records staff observations;
it never simulates real Discord interactions or mutates quest/XP state.


READING GUIDE
-------------
The Test Runner is a staff-guided checklist, not a Discord account simulator.
It reads the real member's progress for diagnostics and stores manual test notes
in a separate SQLite table. It never changes XP, quests, roles or rewards.
"""
import sqlite3
import discord
import panel_health

api = None

TEST_CASES = (
    ('welcome_dm', 'Welcome DM', 'Tester joins the Guild. Verify the short welcome DM links to onboarding and explains how to disable bot DMs.'),
    ('starter_board', 'Starter board', 'Tester can open #start-here and see the Starter Quest board and Turn In Quest button.'),
    ('starter_steps', 'Starter objectives', 'Complete each of the five genuine onboarding actions and check the progress panel.'),
    ('starter_access', 'Starter access unlock', 'Turn in Starter Quest; verify Guild Member role and unlocked channels, without admin overrides.'),
    ('player_hub', 'Player Hub navigation', 'Verify Profile, Streak, Ranks, Pauses, Inbox, Bot DMs, All Quests and Guild Blast work privately.'),
    ('training_first', 'First training proof', 'Upload evidence in training log; verify first 50 XP is recorded.'),
    ('training_second', 'Second training day', 'Upload evidence on a DIFFERENT day; verify second 50 XP is recorded.'),
    ('gym_hobby', 'Gym and hobby posts', 'Post qualifying gym and hobby contributions; each should grant 25 XP once.'),
    ('weekly_bonus', 'Weekly Quest bonus', 'Finish all four objectives; verify one 50 XP bonus and weekly completion.'),
    ('duplicate_proof', 'Duplicate prevention', 'Try repeating the same qualifying post; verify no duplicate XP.'),
    ('pause', 'Pause and resume', 'On a reset/test week, activate a pause and verify Weekly XP is blocked; resume and check state.'),
    ('rank_roles', 'Rank roles', 'Use Testing Cheats XP adjustment; verify correct role/rank and progression notification.'),
    ('streak', 'Streak handling', 'Use Add / Remove Streak on the tester; verify displayed active streak and milestones.'),
    ('dm_inbox', 'Inbox and DM preference', 'Toggle Bot DMs, trigger a notification, check Inbox and opt-out behavior.'),
    ('rewards', 'Reward claim state', 'Verify a locked reward cannot be claimed and an eligible reward produces one ticket.'),
    ('milestones', 'Milestone test-only flow', 'Use admin-only Complete Quest for Milestones; verify one-time XP and clear test-only labeling.'),
    ('admin_audit', 'Admin confirmation and audit', 'Test a reset with reason, inspect impact preview, then confirm and check who/when/target/reason in Audit Log.'),
    ('panel_recovery', 'Panel refresh and restart', 'Run Health Check, verify panel health, restart bot, and confirm existing buttons still respond.'),
)
TEST_KEYS = {key for key, _, _ in TEST_CASES}
STATUS = {'passed', 'failed', 'skipped'}


# Create the QA-results table independently of member progression tables.
def ensure_table():
    with api.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS test_runner_results (
          guild_id INTEGER NOT NULL, tester_id INTEGER NOT NULL,
          case_key TEXT NOT NULL, status TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '', actor_id INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(guild_id, tester_id, case_key)
        )''')


# Keep one status per scenario and tester, with moderator attribution.
def save_result(gid, uid, case_key, status, note, actor_id):
    if case_key not in TEST_KEYS or status not in STATUS:
        raise ValueError('Invalid test case or status')
    if not actor_id:
        raise ValueError('Testing requires a moderator')
    ensure_table()
    with api.connect() as db:
        db.execute('''INSERT INTO test_runner_results
          (guild_id,tester_id,case_key,status,note,actor_id,updated_at)
          VALUES (?,?,?,?,?,?,?)
          ON CONFLICT(guild_id,tester_id,case_key) DO UPDATE SET
          status=excluded.status,note=excluded.note,
          actor_id=excluded.actor_id,updated_at=excluded.updated_at''',
          (gid, uid, case_key, status, note[:500], actor_id, api.utc_now().isoformat()))


# Load saved results from previous runs/restarts.
def results(gid, uid):
    ensure_table()
    with api.connect() as db:
        rows = db.execute(
            'SELECT case_key,status,note,actor_id,updated_at FROM test_runner_results '
            'WHERE guild_id=? AND tester_id=?', (gid, uid)).fetchall()
    return {row['case_key']: dict(row) for row in rows}


# Display what was passed, failed, skipped or has not yet been tested.
def progress_text(gid, uid, *, detailed=True):
    records = results(gid, uid)
    counts = {state: sum(x['status'] == state for x in records.values()) for state in STATUS}
    pending = len(TEST_CASES) - len(records)
    lines = [
        f'🧪 **GUILDKEEPER — LIVE TEST RUNNER**',
        f'Test member: <@{uid}> · **{counts["passed"]}/{len(TEST_CASES)} passed** · '
        f'❌ {counts["failed"]} failed · ⏭️ {counts["skipped"]} skipped · ⬜ {pending} untested',
        'These outcomes are **manually recorded by staff**, not automatically verified.',
        '',
    ]
    if detailed:
        icons = {'passed': '✅', 'failed': '❌', 'skipped': '⏭️'}
        for key, name, _ in TEST_CASES:
            record = records.get(key)
            lines.append(f'{icons.get(record["status"], "⬜") if record else "⬜"} {name}')
    return '\n'.join(lines)[:1950]


# Read a tester's current GuildKeeper state without changing it.
def snapshot(gid, uid):
    """Read-only diagnostics; claims do not prove a human clicked the right UI."""
    with api.connect() as db:
        starter = {row['kind'] for row in db.execute(
            'SELECT kind FROM starter_claims WHERE guild_id=? AND user_id=?',
            (gid, uid))}
        turned_in = db.execute(
            'SELECT 1 FROM starter_turnins WHERE guild_id=? AND user_id=?',
            (gid, uid)).fetchone() is not None
        week = api.week_key()
        weekly = {row['kind'] for row in db.execute(
            'SELECT kind FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?',
            (gid, uid, week))}
        week_complete = db.execute(
            'SELECT 1 FROM weekly_completions WHERE guild_id=? AND user_id=? AND week_key=?',
            (gid, uid, week)).fetchone() is not None
        milestone_claims = db.execute(
            'SELECT COUNT(*) AS n FROM milestone_claims WHERE guild_id=? AND user_id=? '
            "AND kind!='guild_completion'", (gid, uid)).fetchone()['n']
        pauses = db.execute(
            'SELECT COUNT(*) AS n FROM pauses WHERE guild_id=? AND user_id=?',
            (gid, uid)).fetchone()['n']
        tickets = db.execute(
            'SELECT COUNT(*) AS n FROM reward_tickets WHERE guild_id=? AND user_id=?',
            (gid, uid)).fetchone()['n']
    warnings = []
    if week_complete and len(weekly) != 4:
        warnings.append('Weekly completion exists but fewer than four current objectives are recorded')
    if turned_in and not set(api.STARTER_REWARDS).issubset(starter):
        warnings.append('Starter turn-in exists but Starter objective claims are incomplete')
    return {
        'xp': api.get_xp(gid, uid),
        'starter': len(starter),
        'starter_total': len(api.STARTER_REWARDS),
        'onboarded': turned_in,
        'week': week,
        'weekly': sorted(weekly),
        'weekly_complete': week_complete,
        'streak': api.active_streak(gid, uid),
        'milestones': milestone_claims,
        'pause_records': pauses,
        'active_pause': api.is_paused(gid, uid, week),
        'reward_tickets': tickets,
        'dms_enabled': api.bot_dms_enabled(gid, uid),
        'warnings': warnings,
    }


# Present database data clearly, but do not claim it proves a button works.
def snapshot_embed(gid, uid):
    info = snapshot(gid, uid)
    e = discord.Embed(
        title='🧪 Test Member — Read-Only Diagnostics',
        description=f'<@{uid}> · GuildKeeper database state (not a simulated test pass)',
        colour=0x71A7A2)
    e.add_field(name='XP / Streak', value=f'{info["xp"]} XP · {info["streak"]} weeks', inline=True)
    e.add_field(name='Starter', value=f'{info["starter"]}/{info["starter_total"]} · '
                + ('Turned in' if info['onboarded'] else 'Not turned in'), inline=True)
    e.add_field(name='Weekly Quest', value=f'{len(info["weekly"])}/4 · '
                + ('Completed' if info['weekly_complete'] else 'In progress')
                + f'\nWeek: {info["week"]}', inline=False)
    e.add_field(name='Progress', value=f'{info["milestones"]} milestone claims · '
                f'{info["pause_records"]} recorded pauses · '
                f'{info["reward_tickets"]} reward requests', inline=False)
    e.add_field(name='Preferences', value=('Bot DMs on' if info['dms_enabled']
                else 'Bot DMs off') + ' · ' + ('Pause active' if info['active_pause'] else 'No active pause'),
                inline=False)
    if info['warnings']:
        e.add_field(name='Potential inconsistencies', value='\n'.join(info['warnings'])[:1024],
                    inline=False)
    e.set_footer(text='Only reads records; use the live checklist to verify Discord interactions')
    return e


# Restrict test reports to Manage Server users and the owner of the private menu.
def permitted(interaction, owner_id):
    return (interaction.guild_id == api.GUILD_ID
            and interaction.user.id == owner_id
            and api.testing_cheats.permitted(interaction))


class Private(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if not permitted(interaction, self.owner_id):
            await interaction.response.send_message('Not authorized to view this test run.', ephemeral=True)
            return False
        return True


# Require a normal, non-admin account for authentic permission testing.
class TestMemberSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder='Select your separate test member account',
                         min_values=1, max_values=1)

    async def callback(self, interaction):
        view = self.view
        if not permitted(interaction, view.owner_id):
            return await interaction.response.send_message('Not authorized.', ephemeral=True)
        selected = self.values[0]
        try:
            member = interaction.guild.get_member(selected.id) or await interaction.guild.fetch_member(selected.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message('Test member not available in server.', ephemeral=True)
        if member.bot or member.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                'Choose a normal, non-admin Discord account to test real member permissions.', ephemeral=True)
        await interaction.response.send_message(
            progress_text(interaction.guild_id, member.id),
            view=RunnerMenu(view.owner_id, member.id), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())


class TestMemberPicker(Private):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.add_item(TestMemberSelect())


# Offer the checklist, current database state and saved report.
class RunnerMenu(Private):
    def __init__(self, owner_id, tester_id):
        super().__init__(owner_id)
        self.tester_id = tester_id

    @discord.ui.button(label='Live Checklist', emoji='📋', style=discord.ButtonStyle.primary)
    async def checklist(self, interaction, button):
        await interaction.response.send_message(
            'Choose a test below. Perform it using the non-admin account, then mark the result.',
            view=ScenarioPicker(self.owner_id, self.tester_id), ephemeral=True)

    @discord.ui.button(label='Read-Only Checks', emoji='🔍', style=discord.ButtonStyle.secondary)
    async def diagnostic(self, interaction, button):
        await interaction.response.send_message(
            embed=snapshot_embed(interaction.guild_id, self.tester_id),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Test Report', emoji='📊', style=discord.ButtonStyle.secondary)
    async def report(self, interaction, button):
        await interaction.response.send_message(
            progress_text(interaction.guild_id, self.tester_id),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


# Show exactly what the tester should do before a moderator records a result.
class ScenarioSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='Select one live testing scenario',
                         min_values=1, max_values=1,
                         options=[discord.SelectOption(label=name[:100], value=key)
                                  for key, name, _ in TEST_CASES])

    async def callback(self, interaction):
        view = self.view
        if not permitted(interaction, view.owner_id):
            return await interaction.response.send_message('Not authorized.', ephemeral=True)
        key = self.values[0]
        if key not in TEST_KEYS:
            return await interaction.response.send_message('Invalid scenario.', ephemeral=True)
        title, instructions = next((name, desc) for x, name, desc in TEST_CASES if x == key)
        previous = results(interaction.guild_id, view.tester_id).get(key)
        history = (f'\nPrevious result: {previous["status"]} — {previous["note"]}' if previous else '')
        await interaction.response.send_message(
            f'🧪 **{title}**\nTester: <@{view.tester_id}>\n\n'
            + instructions + history
            + '\n\nRun this test with the separate Discord account, then record your observation.',
            view=ScenarioResultMenu(view.owner_id, view.tester_id, key),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ScenarioPicker(Private):
    def __init__(self, owner_id, tester_id):
        super().__init__(owner_id)
        self.tester_id = tester_id
        self.add_item(ScenarioSelect())


# Collect evidence from a failed live test instead of simply marking it red.
class FailureNote(discord.ui.Modal):
    def __init__(self, owner_id, tester_id, key):
        super().__init__(title='Record Test Failure', timeout=300)
        self.owner_id, self.tester_id, self.key = owner_id, tester_id, key
        self.note = discord.ui.TextInput(
            label='What happened? (required)', style=discord.TextStyle.paragraph,
            max_length=400, required=True,
            placeholder='Expected 50 XP, received 0 XP after uploading an image')
        self.add_item(self.note)

    async def on_submit(self, interaction):
        if not permitted(interaction, self.owner_id):
            return await interaction.response.send_message('Not authorized.', ephemeral=True)
        reason = self.note.value.strip()
        if not reason:
            return await interaction.response.send_message('A failure description is required.', ephemeral=True)
        save_result(interaction.guild_id, self.tester_id, self.key,
                    'failed', reason, interaction.user.id)
        api.admin_hub.audit(interaction.user.id, 'live_test_failed',
                            f'Test {self.key} for tester {self.tester_id}: {reason}',
                            self.tester_id)
        await interaction.response.send_message(
            '❌ Failure recorded.\n\n' + progress_text(interaction.guild_id, self.tester_id),
            view=RunnerMenu(self.owner_id, self.tester_id),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


# Record observed outcomes; this does not simulate game actions.
class ScenarioResultMenu(Private):
    def __init__(self, owner_id, tester_id, key):
        super().__init__(owner_id)
        self.tester_id, self.key = tester_id, key

    @discord.ui.button(label='Mark Passed', emoji='✅', style=discord.ButtonStyle.success)
    async def passed(self, interaction, button):
        save_result(interaction.guild_id, self.tester_id, self.key,
                    'passed', 'Verified manually by moderator', interaction.user.id)
        await interaction.response.edit_message(
            content='✅ Passed recorded.\n\n' + progress_text(interaction.guild_id, self.tester_id),
            view=RunnerMenu(self.owner_id, self.tester_id),
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Report Failure', emoji='❌', style=discord.ButtonStyle.danger)
    async def failed(self, interaction, button):
        await interaction.response.send_modal(
            FailureNote(self.owner_id, self.tester_id, self.key))

    @discord.ui.button(label='Skip', emoji='⏭️', style=discord.ButtonStyle.secondary)
    async def skipped(self, interaction, button):
        save_result(interaction.guild_id, self.tester_id, self.key,
                    'skipped', 'Not tested this run', interaction.user.id)
        await interaction.response.edit_message(
            content='⏭️ Skipped recorded.\n\n' + progress_text(interaction.guild_id, self.tester_id),
            view=RunnerMenu(self.owner_id, self.tester_id),
            allowed_mentions=discord.AllowedMentions.none())


def install(module):
    global api
    api = module

