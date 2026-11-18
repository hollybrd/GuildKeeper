"""Permanent, private GuildKeeper testing shortcuts. Reuses existing commands."""
import discord
from discord import app_commands

api = None
ACTIONS = {
    'starter_reset': ('Starter Quest Reset', 'admin_starter_reset'),
    'weekly_reset': ('Weekly Quest Reset', 'admin_weekly_reset'),
    'pause_reset': ('Pause History Reset', 'admin_pause_reset'),
    'streak_add': ('Add Streak Weeks', 'admin_streak_add'),
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

    @discord.ui.button(label='Starter Reset', emoji='🛡️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:starter', row=0)
    async def starter(self, interaction, button):
        await choose_member(interaction, 'starter_reset')

    @discord.ui.button(label='Weekly Reset', emoji='📜', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:weekly', row=0)
    async def weekly(self, interaction, button):
        await choose_member(interaction, 'weekly_reset')

    @discord.ui.button(label='Add Streak', emoji='🔥', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:streak', row=1)
    async def streak(self, interaction, button):
        await choose_member(interaction, 'streak_add')

    @discord.ui.button(label='Pause Reset', emoji='⏸️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:pause', row=1)
    async def pause(self, interaction, button):
        await choose_member(interaction, 'pause_reset')

    @discord.ui.button(label='Rank Test Reset', emoji='🏆', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:rank', row=1)
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

    @discord.ui.button(label='All Test Commands', emoji='📋', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:testing:commands', row=2)
    async def commands(self, interaction, button):
        await interaction.response.send_message(
            '**Other administrator / testing commands**\n'
            '/admin_rank_test_reset · /admin_streak_add · /admin_xp_award · /admin_xp_remove\n'
            '/admin_starter_reset · /admin_starter_award · /admin_weekly_reset\n'
            '/admin_pause_reset · /admin_reward_reset · /admin_reward_fulfill\n'
            '/admin_weekly_settle · /admin_sync_ranks · /admin_quest_create\n'
            'Use the buttons above for common tests; existing slash commands remain available '
            'for less-frequent operations.', ephemeral=True)


def embed():
    e = discord.Embed(
        title='✦ GUILDKEEPER — TESTING CHEATS',
        description='**Staff-only testing controls.** These actions change real member progress '
                    'in this Guild — not a simulation. Choose a shortcut, select a member, '
                    'and confirm before applying a reset. Use a test account where possible.',
        colour=discord.Colour.orange())
    e.add_field(name='XP & Progress', value='Award/remove XP · Starter reset · Weekly reset · Add streak weeks', inline=False)
    e.add_field(name='Advanced', value='Pause history reset · Self-only rank test reset · Rank reward reset', inline=False)
    e.add_field(name='Safety', value='All actions require Manage Server. Destructive actions need confirmation. '
                'Existing admin commands are preserved.', inline=False)
    return e


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
        if self.action == 'streak_add':
            return await interaction.response.send_modal(StreakForm(interaction.user.id, member.id))
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
    def __init__(self, owner_id, target_id):
        super().__init__(title='Credit Test Streak Weeks', timeout=300)
        self.owner_id, self.target_id = owner_id, target_id
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
        await interaction.response.send_message(
            '⚠️ Credit **' + str(weeks) + ' consecutive weeks** to <@'
            + str(self.target_id) + '>?\nThis can unlock ranks and award one-time milestone XP.',
            view=ConfirmCheat(self.owner_id, self.target_id, 'streak_add', weeks=weeks),
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
    if action == 'rank_reset':
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
        elif action == 'streak_add':
            if not weeks or not 1 <= weeks <= 52:
                raise ValueError('Streak weeks must be 1–52')
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
