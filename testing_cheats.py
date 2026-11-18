"""Permanent, private GuildKeeper testing shortcuts. Reuses existing commands."""
import discord
import quest_testing
from discord import app_commands

api = None
ACTIONS = {
    'starter_reset': ('Starter Quest Reset', 'admin_starter_reset'),
    'weekly_reset': ('Weekly Quest Reset', 'admin_weekly_reset'),
    'pause_reset': ('Pause History Reset', 'admin_pause_reset'),
    'streak_add': ('Add Streak Weeks', 'admin_streak_add'),
    'streak_remove': ('Remove Credited Streak Weeks', None),
    'complete_quest': ('Complete Quest (Test Only)', None),
    'rank_reset': ('Reset My Rank Test', 'admin_rank_test_reset'),
}
REWARD_RANKS = ('Master', 'Legend', 'Paragon')


def permitted(interaction):
    return (interaction.guild_id == api.GUILD_ID
            and isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild)


async def denied(interaction):
    await interaction.response.send_message(
        'Testing Cheats requires Manage Server permission in GYMGUILD.', ephemeral=True)


class Private(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if not permitted(interaction) or interaction.user.id != self.owner_id:
            await denied(interaction)
            return False
        return True


class TestingCheatsView(discord.ui.View):
    """Persistent entry point; user-specific actions remain private."""
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction):
        if not permitted(interaction):
            await denied(interaction)
            return False
        return True

    @discord.ui.button(label='Add / Remove XP', emoji='⭐', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:xp', row=0)
    async def xp(self, interaction, button):
        await interaction.response.send_message(
            '⭐ Select a member for XP testing. Award or Remove XP requires a reason and confirmation.',
            view=api.admin_hub.PlayerPickerView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='Complete Quest', emoji='✅', style=discord.ButtonStyle.primary,
                       custom_id='guildkeeper:testing:complete_quest', row=0)
    async def complete_quest(self, interaction, button):
        await interaction.response.send_message(
            '🧪 **Complete Quest — Admin Testing Only**\nChoose a player and then a quest category. '
            'You can award one missing objective or all remaining objectives. '
            'This changes real XP and quest progress; no purchase or boost verification is performed.',
            view=CheatMemberPickerView(interaction.user.id, 'complete_quest'), ephemeral=True)

    @discord.ui.button(label='Starter Reset', emoji='🛡️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:starter', row=0)
    async def starter(self, interaction, button):
        await choose_member(interaction, 'starter_reset')

    @discord.ui.button(label='Weekly Reset', emoji='📜', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:weekly', row=1)
    async def weekly(self, interaction, button):
        await choose_member(interaction, 'weekly_reset')

    @discord.ui.button(label='Add / Remove Streak', emoji='🔥', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:streak', row=1)
    async def streak(self, interaction, button):
        await interaction.response.send_message(
            '🔥 **Streak Testing** — Add consecutive weeks or remove previously admin-credited weeks.',
            view=StreakChoiceView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='Pause Reset', emoji='⏸️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:pause', row=1)
    async def pause(self, interaction, button):
        await choose_member(interaction, 'pause_reset')

    @discord.ui.button(label='Rank Test Reset', emoji='🏆', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:rank', row=2)
    async def rank(self, interaction, button):
        await interaction.response.send_message(
            '⚠️ **Reset YOUR rank test to Novice (50 XP)?**\n'
            'This resets your rank progression and one-time rank announcements. '
            'The existing slash command preserves Starter completion, weekly activity, '
            'XP audit history, and reward tickets.',
            view=ConfirmCheat(interaction.user.id, interaction.user.id, 'rank_reset'),
            ephemeral=True)

    @discord.ui.button(label='Reward Reset', emoji='🎟️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:reward', row=2)
    async def reward(self, interaction, button):
        await choose_member(interaction, 'reward_reset')

    @discord.ui.button(label='Milestone Reset', emoji='🏅', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:milestone', row=2)
    async def milestone(self, interaction, button):
        await interaction.response.send_message(
            '🏅 **Milestone Test Reset** — choose Order XP, Boost XP, or both. '
            'This affects YOUR own account only.',
            view=MilestoneTargetView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='All Test Commands', emoji='📋', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:commands', row=3)
    async def commands(self, interaction, button):
        await interaction.response.send_message(embed=test_command_guide(), ephemeral=True)


def test_command_guide():
    guide = discord.Embed(
        title='📋 Testing & Admin Commands',
        description='Backup slash commands organised by purpose. Select a command in Discord’s '
                    '/ menu to fill out its options. **These operate on real Guild data.**',
        colour=0x71A7A2)
    sections = (
        ('⭐ XP & Rank', [
            ('admin_xp_award', 'Award member XP'),
            ('admin_xp_remove', 'Deduct member XP'),
            ('admin_rank_test_reset', 'Reset your rank to Novice'),
            ('admin_sync_ranks', 'Synchronise rank roles')]),
        ('🔥 Streaks & Weekly Quests', [
            ('admin_streak_add', 'Credit streak weeks'),
            ('admin_weekly_reset', 'Reset a member’s weekly quest'),
            ('admin_weekly_settle', 'Process weekly settlement')]),
        ('🛡️ Starter & Milestones', [
            ('admin_starter_reset', 'Reset Starter Quest'),
            ('admin_starter_award', 'Award Starter objective'),
            ('admin_milestone_test_reset', 'Reset your Order / Boost XP'),
            ('admin_pause_reset', 'Clear pause history')]),
        ('🎟️ Rewards & Other', [
            ('admin_reward_reset', 'Reset rank reward request'),
            ('admin_reward_fulfill', 'Fulfil rank reward'),
            ('admin_quest_create', 'Create a legacy quest')]),
    )
    for heading, items in sections:
        guide.add_field(name=heading, value='\\n'.join(
            f'**/{cmd}** — {description}' for cmd, description in items).replace('\\n', '\n'),
            inline=False)
    guide.set_footer(text='Common actions are available through Testing Cheats buttons')
    return guide


def embed():
    e = discord.Embed(
        title='✦ GUILDKEEPER — TESTING CHEATS',
        description='**Staff-only testing controls.** These actions change real member progress '
                    'in this Guild — not a simulation. Choose a shortcut, select a member, '
                    'and confirm before applying a reset. Use a test account where possible.',
        colour=discord.Colour.orange())
    e.add_field(name='XP & Progress', value='Complete Quest (test-only) · Award/remove XP · Starter reset · Weekly reset · Add/remove credited streak weeks', inline=False)
    e.add_field(name='Advanced', value='Pause history reset · Self-only rank/milestone resets · Rank reward reset', inline=False)
    e.add_field(name='Safety', value='All actions require Manage Server. Destructive actions need confirmation. '
                'Existing admin commands are preserved.', inline=False)
    return e


class QuestCategoryView(Private):
    def __init__(self, owner_id, target_id):
        super().__init__(owner_id)
        self.target_id = target_id

    @discord.ui.select(placeholder='Choose quest category', options=[
        discord.SelectOption(label='Weekly Side Quest', value='weekly', emoji='⚔️'),
        discord.SelectOption(label='Starter Quest', value='starter', emoji='🛡️'),
        discord.SelectOption(label='Milestone Quests (TEST ONLY)', value='milestone', emoji='🏆'),
    ])
    async def category(self, interaction, select):
        category = select.values[0]
        await interaction.response.send_message(
            '🧪 **' + category.title() + ' Quest** — choose an objective or complete all missing. '
            'Already completed objectives will not grant XP again.',
            view=QuestObjectiveView(self.owner_id, self.target_id, category), ephemeral=True)


class QuestObjectiveSelect(discord.ui.Select):
    def __init__(self, category):
        choices = quest_testing.options(api, category)
        super().__init__(
            placeholder='Choose objective or all remaining', min_values=1, max_values=1,
            options=[discord.SelectOption(label=label[:100], value=key) for key, label in choices])
        self.category = category

    async def callback(self, interaction):
        if not permitted(interaction) or interaction.user.id != self.view.owner_id:
            return await denied(interaction)
        gid, uid = interaction.guild_id, self.view.target_id
        if self.category == 'weekly' and api.is_paused(gid, uid, api.week_key()):
            return await interaction.response.send_message(
                '⏸️ This player has an active pause. Weekly XP cannot be earned while paused. '
                'Resume XP first using the Pauses panel.', ephemeral=True)
        choice = self.values[0]
        missing, estimated_xp = quest_testing.preview(api, gid, uid, self.category, choice)
        if not missing:
            return await interaction.response.send_message(
                '✅ These objectives are already complete. No XP will be awarded.', ephemeral=True)
        description = {
            'weekly': 'Weekly quest progress and completion bonuses will be credited.',
            'starter': 'Completing all Starter objectives also turns in the quest and grants access.',
            'milestone': ('TEST ONLY: Milestone claims will be marked complete without verifying '
                          'a real store order, boost or streak. This can also award the one-time '
                          'Main Quest completion bonus. Use a test account.'),
        }[self.category]
        await interaction.response.send_message(
            '⚠️ **Confirm ADMIN TEST quest completion**\n'
            f'Member: <@{uid}>\n'
            f'Category: **{self.category.title()}**\n'
            f'New objectives: **{", ".join(missing)}**\n'
            f'Estimated objective + completion XP: **{estimated_xp}**\n'
            + description
            + '\n\n**This modifies real Guild XP and one-time quest claims.**',
            view=ConfirmQuestCompletion(self.view.owner_id, uid, self.category, choice),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class QuestObjectiveView(Private):
    def __init__(self, owner_id, target_id, category):
        super().__init__(owner_id)
        self.target_id = target_id
        self.add_item(QuestObjectiveSelect(category))


class ConfirmQuestCompletion(Private):
    def __init__(self, owner_id, target_id, category, choice):
        super().__init__(owner_id)
        self.target_id, self.category, self.choice = target_id, category, choice
        self.used = False

    @discord.ui.button(label='Confirm Test Completion', emoji='✅', style=discord.ButtonStyle.danger)
    async def complete(self, interaction, button):
        if self.used:
            return await interaction.response.send_message('Already processed.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            member = (interaction.guild.get_member(self.target_id)
                      or await interaction.guild.fetch_member(self.target_id))
            if member.bot:
                raise ValueError('Bot accounts cannot complete test quests')
            result = await quest_testing.apply_to_member(
                api, member, self.category, self.choice, interaction.user.id)
            api.admin_hub.audit(
                interaction.user.id, 'testing_quest_completion',
                f'Category {self.category}; objective {self.choice}; target {self.target_id}',
                self.target_id)
        except (ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            result = '⚠️ Quest completion not applied: ' + str(exc)
        except Exception:
            api.log.exception('Testing Cheats: quest completion failed')
            result = '⚠️ Quest completion failed unexpectedly. Check bot logs before retrying.'
        await interaction.edit_original_response(content=result, view=None)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='Test quest completion cancelled.', view=None)


class StreakChoiceView(Private):
    @discord.ui.button(label='Add Streak Weeks', emoji='➕', style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        await choose_member(interaction, 'streak_add')

    @discord.ui.button(label='Remove Streak Weeks', emoji='➖', style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        await choose_member(interaction, 'streak_remove')


class MilestoneTargetView(Private):
    @discord.ui.select(placeholder='Select milestone reset', options=[
        discord.SelectOption(label='Order XP', value='order'),
        discord.SelectOption(label='Server Boost XP', value='boost'),
        discord.SelectOption(label='Both', value='both'),
    ])
    async def choose(self, interaction, select):
        target = select.values[0]
        await interaction.response.send_message(
            '⚠️ **Reset your own ' + target + ' milestone(s)?**\n'
            'This reverses recorded XP for selected claims and potentially the 500 XP completion bonus. '
            'Order history is archived, not deleted. An active boost needs a new boost event to re-award XP.',
            view=ConfirmCheat(self.owner_id, self.owner_id, 'milestone_reset', rank=target),
            ephemeral=True)


async def choose_member(interaction, action):
    await interaction.response.send_message(
        'Select a member to run **' + ('Reward Reset' if action == 'reward_reset'
                                      else ACTIONS[action][0]) + '**.',
        view=CheatMemberPickerView(interaction.user.id, action), ephemeral=True)


class CheatUserSelect(discord.ui.UserSelect):
    def __init__(self, action):
        super().__init__(placeholder='Choose the testing member', min_values=1, max_values=1)
        self.action = action

    async def callback(self, interaction):
        if not permitted(interaction) or interaction.user.id != self.view.owner_id:
            return await denied(interaction)
        picked = self.values[0]
        try:
            member = interaction.guild.get_member(picked.id) or await interaction.guild.fetch_member(picked.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message('Member unavailable.', ephemeral=True)
        if member.bot:
            return await interaction.response.send_message('Bot accounts are not valid testing targets.', ephemeral=True)
        if self.action == 'complete_quest':
            return await interaction.response.send_message(
                '🧪 **Complete Quest — ' + discord.utils.escape_markdown(member.display_name)
                + '**\nChoose Weekly, Starter or Milestone progress to test.',
                view=QuestCategoryView(self.view.owner_id, member.id), ephemeral=True)
        if self.action in ('streak_add', 'streak_remove'):
            return await interaction.response.send_modal(StreakForm(interaction.user.id, member.id, self.action))
        if self.action == 'reward_reset':
            return await interaction.response.send_message(
                'Choose which rank reward request to reset for this player. '
                'This may delete their existing ticket channel.',
                view=RewardRankPicker(self.view.owner_id, member.id), ephemeral=True)
        title = ACTIONS[self.action][0]
        consequences = {
            'starter_reset': 'Removes Starter claims, may deduct awarded Starter XP, and removes access role.',
            'weekly_reset': 'Clears current weekly activity and completion, deducting related Weekly XP.',
            'pause_reset': 'Deletes all recorded pauses and resumed pauses. Historical streak calculations can change.',
        }
        await interaction.response.send_message(
            '⚠️ **Confirm ' + title + '**\nPlayer: ' + member.mention
            + '\n' + consequences[self.action] + '\n\n**This changes live data and cannot be automatically undone.**',
            view=ConfirmCheat(self.view.owner_id, member.id, self.action),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class CheatMemberPickerView(Private):
    def __init__(self, owner_id, action):
        super().__init__(owner_id)
        self.add_item(CheatUserSelect(action))


class StreakForm(discord.ui.Modal):
    def __init__(self, owner_id, target_id, action='streak_add'):
        super().__init__(title='Add Streak Weeks' if action == 'streak_add'
                         else 'Remove Credited Streak Weeks', timeout=300)
        self.owner_id, self.target_id, self.action = owner_id, target_id, action
        self.weeks = discord.ui.TextInput(label='Weeks (1 to 52)', placeholder='4', max_length=2)
        self.add_item(self.weeks)

    async def on_submit(self, interaction):
        if not permitted(interaction) or interaction.user.id != self.owner_id:
            return await denied(interaction)
        try:
            weeks = int(self.weeks.value.strip())
        except ValueError:
            weeks = 0
        if not 1 <= weeks <= 52:
            return await interaction.response.send_message('Choose between 1 and 52 weeks.', ephemeral=True)
        if self.action == 'streak_add':
            warning = 'This may unlock ranks and award one-time milestone XP.'
        else:
            warning = ('Only the most recent admin-credited weeks are removed. Actual Weekly Quest '
                       'completions remain. Already-awarded milestone XP and permanent rank unlocks '
                       'will NOT be reversed automatically.')
        await interaction.response.send_message(
            '⚠️ **' + ('Add' if self.action == 'streak_add' else 'Remove up to')
            + ' ' + str(weeks) + ' streak week(s)** for <@' + str(self.target_id)
            + '> ?\n' + warning + '\n**Confirm this change to live progress.**',
            view=ConfirmCheat(self.owner_id, self.target_id, self.action, weeks=weeks),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class RewardRankPicker(Private):
    def __init__(self, owner_id, target_id):
        super().__init__(owner_id)
        self.target_id = target_id

    @discord.ui.select(placeholder='Choose reward request to reset', options=[
        discord.SelectOption(label='Master — 10%', value='Master'),
        discord.SelectOption(label='Legend — 15%', value='Legend'),
        discord.SelectOption(label='Paragon — 20%', value='Paragon'),
    ])
    async def select(self, interaction, select):
        rank = select.values[0]
        await interaction.response.send_message(
            '⚠️ Reset **' + rank + '** reward request for <@' + str(self.target_id)
            + '>?\nThe original reward request and ticket channel may be deleted. '
            'Rank unlocks and XP are preserved.',
            view=ConfirmCheat(self.owner_id, self.target_id, 'reward_reset', rank=rank),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def run_existing_command(interaction, action, target_id, weeks=None, rank=None):
    """Run unchanged legacy command implementation; permissions checked first."""
    if action == 'milestone_reset':
        if target_id != interaction.user.id or rank not in ('order', 'boost', 'both'):
            raise ValueError('Milestone reset is self-only')
        args = (interaction, app_commands.Choice(
            name={'order':'Order XP','boost':'Server Boost XP','both':'Both'}[rank],
            value=rank), True)
        command_name = 'admin_milestone_test_reset'
    elif action == 'rank_reset':
        if target_id != interaction.user.id:
            raise ValueError('Rank test reset is self-only')
        args = (interaction, True)
        command_name = 'admin_rank_test_reset'
    else:
        member = interaction.guild.get_member(target_id) or await interaction.guild.fetch_member(target_id)
        if member.bot:
            raise ValueError('Cannot test on a bot account')
        if action == 'reward_reset':
            if rank not in REWARD_RANKS:
                raise ValueError('Invalid reward rank')
            args = (interaction, member, app_commands.Choice(name=rank, value=rank))
            command_name = 'admin_reward_reset'
        elif action in ('streak_add', 'streak_remove'):
            if not weeks or not 1 <= weeks <= 52:
                raise ValueError('Streak weeks must be 1–52')
            if action == 'streak_remove':
                await interaction.response.defer(ephemeral=True)
                gid, uid = interaction.guild_id, member.id
                with api.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    rows = db.execute(
                        'SELECT week_key FROM admin_streak_weeks WHERE guild_id=? AND user_id=? '
                        'ORDER BY week_key DESC LIMIT ?', (gid, uid, weeks)).fetchall()
                    for row in rows:
                        db.execute('DELETE FROM admin_streak_weeks WHERE guild_id=? '
                                   'AND user_id=? AND week_key=?', (gid, uid, row['week_key']))
                await api.update_rank(member, api.get_xp(gid, uid))
                await interaction.followup.send(
                    '🔥 Removed **' + str(len(rows)) + '** admin-credited weeks from '
                    + member.mention + '. Active streak: **' + str(api.active_streak(gid, uid))
                    + '**. Real quest completions, previously earned milestone XP '
                    'and permanent rank unlocks were kept.', ephemeral=True)
                return
            args = (interaction, member, weeks)
            command_name = 'admin_streak_add'
        else:
            args = (interaction, member)
            command_name = ACTIONS[action][1]
    command = api.bot.tree.get_command(command_name)
    if command is None:
        raise ValueError('Missing command: ' + command_name)
    await command.callback(*args)


class ConfirmCheat(Private):
    def __init__(self, owner_id, target_id, action, weeks=None, rank=None):
        super().__init__(owner_id)
        self.target_id, self.action = target_id, action
        self.weeks, self.rank = weeks, rank
        self.used = False

    @discord.ui.button(label='Confirm Test Action', emoji='✅', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.used:
            return await interaction.response.send_message('Already submitted.', ephemeral=True)
        self.used = True
        # The existing callback acknowledges the interaction and reports its own result.
        try:
            await run_existing_command(interaction, self.action, self.target_id,
                                       weeks=self.weeks, rank=self.rank)
            api.admin_hub.audit(interaction.user.id, 'testing_' + self.action,
                                'Target ' + str(self.target_id)
                                + (' weeks=' + str(self.weeks) if self.weeks else '')
                                + (' rank=' + self.rank if self.rank else ''),
                                self.target_id)
        except (ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
            if interaction.response.is_done():
                await interaction.followup.send('⚠️ Test action failed: ' + str(error), ephemeral=True)
            else:
                await interaction.response.send_message('⚠️ Test action failed: ' + str(error), ephemeral=True)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='Testing action cancelled.', view=None)


async def post_testing_panel(channel):
    guild = api.bot.get_guild(api.GUILD_ID)
    if guild is None or not isinstance(channel, discord.TextChannel) or channel.guild.id != api.GUILD_ID:
        return False, 'Choose a Guild text channel.'
    if channel.permissions_for(guild.default_role).view_channel:
        return False, 'The Testing Cheats channel must be hidden from @everyone.'
    if guild.me is None:
        return False, 'GuildKeeper permissions unavailable.'
    perms = channel.permissions_for(guild.me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history):
        return False, 'GuildKeeper lacks posting permissions.'
    key = 'testing-panel-post-' + str(guild.id)
    with api.connect() as db:
        row = db.execute('SELECT value FROM v12_config WHERE key=?', (key,)).fetchone()
    previous, previous_channel = None, None
    if row:
        try:
            old_channel_id, old_message_id = map(int, row['value'].split(':'))
            previous_channel = guild.get_channel(old_channel_id)
            if isinstance(previous_channel, discord.TextChannel):
                previous = await previous_channel.fetch_message(old_message_id)
        except (ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    try:
        if previous and previous_channel.id == channel.id:
            message = previous
            await message.edit(embed=embed(), view=TestingCheatsView())
        else:
            message = await channel.send(embed=embed(), view=TestingCheatsView(),
                                         allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException):
        return False, 'Could not post Testing Cheats. Check channel permissions.'
    with api.connect() as db:
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                   (key, str(channel.id) + ':' + str(message.id)))
    warning = ''
    if previous and previous_channel.id != channel.id:
        try:
            await previous.delete()
        except (discord.Forbidden, discord.HTTPException):
            warning = ' Previous testing panel could not be removed.'
    return True, 'Testing Cheats ready in ' + channel.mention + '.' + warning


def install(module):
    global api
    api = module

    @api.bot.tree.command(name='admin_testing_panel_post',
                          description='Create or move the permanent private Testing Cheats panel')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel='Private text channel for Testing Cheats')
    async def post(interaction: discord.Interaction, channel: discord.TextChannel):
        if not permitted(interaction):
            return await denied(interaction)
        await interaction.response.defer(ephemeral=True)
        ok, message = await post_testing_panel(channel)
        await interaction.followup.send(('✅ ' if ok else '⚠️ ') + message, ephemeral=True)
