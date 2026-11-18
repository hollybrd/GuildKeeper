"""GuildKeeper's permission-checked, persistent, button-driven moderator hub.

Installed by bot.py, which provides the existing XP/quest/reward functions.
No legacy admin slash commands are replaced.
"""
import discord
from discord import app_commands

api = None


def allowed(interaction):
    return (interaction.guild_id == api.GUILD_ID
            and isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild)


def audit(actor, action, detail, target=None):
    with api.connect() as db:
        db.execute(
            'INSERT INTO admin_hub_audit(guild_id,actor_id,target_id,action,details,created_at) '
            'VALUES (?,?,?,?,?,?)',
            (api.GUILD_ID, actor, target, action, detail, api.utc_now().isoformat()))


class PrivateMenu(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if not allowed(interaction) or interaction.user.id != self.owner_id:
            await interaction.response.send_message('This private moderator menu is not available to you.', ephemeral=True)
            return False
        return True


class AdminHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction):
        if not allowed(interaction):
            await interaction.response.send_message(
                'Manage Server permission is required to use the GuildKeeper Admin Hub.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Players', emoji='👥', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:players', row=0)
    async def players(self, interaction, button):
        await interaction.response.send_message(
            '👥 **Manage Players** — choose a member to inspect or adjust their XP.',
            view=PlayerPickerView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='Quests', emoji='📜', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:quests', row=0)
    async def quests(self, interaction, button):
        await interaction.response.send_message(
            '📜 **Manage Quests** — view weekly status or refresh the board.',
            view=QuestMenu(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='Rewards & Orders', emoji='🎟️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:rewards', row=0)
    async def rewards(self, interaction, button):
        await interaction.response.send_message(
            reward_summary(), view=RewardMenu(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='Announcements', emoji='📣', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:announcements', row=1)
    async def announcements(self, interaction, button):
        await interaction.response.send_message(
            '📣 **Announcements** — compose, preview and confirm a one-off announcement. '
            'Mentions and role pings are disabled.', view=AnnouncementMenu(interaction.user.id),
            ephemeral=True)

    @discord.ui.button(label='Audit Log', emoji='📋', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:audit', row=1)
    async def history(self, interaction, button):
        await interaction.response.send_message(
            audit_summary(), view=AuditMenu(interaction.user.id), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Bot Controls', emoji='⚙️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:controls', row=1)
    async def controls(self, interaction, button):
        await interaction.response.send_message(
            health_summary(interaction.guild), view=ControlMenu(interaction.user.id), ephemeral=True)


def hub_embed():
    embed = discord.Embed(
        title='✦ GUILDKEEPER — ADMIN HUB',
        description='Private moderator controls. Choose an area below; sensitive actions require confirmation. '
                    'Existing admin slash commands remain available as backups.',
        colour=0x71A7A2)
    for title, description in (
        ('👥 Players', 'Choose a member, check their progress, award or remove XP.'),
        ('📜 Quests', 'Inspect weekly quests, refresh the weekly board and locate Milestones.'),
        ('🎟️ Rewards & Orders', 'Review outstanding requests in the existing moderator channel.'),
        ('📣 Announcements', 'Compose, preview and publish without pings.'),
        ('📋 Audit Log', 'Inspect Admin Hub changes.'),
        ('⚙️ Bot Controls', 'Channel health, Player Hub refresh, leaderboard and rank syncing.'),
    ):
        embed.add_field(name=title, value=description, inline=False)
    embed.set_footer(text='Only staff with Manage Server permission can open the controls')
    return embed


class MemberSelector(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder='Choose the member to manage', min_values=1, max_values=1)

    async def callback(self, interaction):
        target = self.values[0]
        try:
            member = interaction.guild.get_member(target.id) or await interaction.guild.fetch_member(target.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message('That member is not available in this Guild.', ephemeral=True)
        if member.bot:
            return await interaction.response.send_message('Bot accounts cannot be adjusted.', ephemeral=True)
        await interaction.response.send_message(
            member_summary(member), view=PlayerMenu(interaction.user.id, member.id), ephemeral=True)


class PlayerPickerView(PrivateMenu):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.add_item(MemberSelector())


def member_summary(member):
    gid, uid = member.guild.id, member.id
    xp = api.get_xp(gid, uid)
    return (
        '👤 **Member: ' + discord.utils.escape_markdown(member.display_name) + '**\n'
        + 'XP: **' + str(xp) + '** · Rank: **' + api.rank_for(xp, gid, uid) + '**\n'
        + 'Weekly objectives: **' + str(len(api.weekly_status(gid, uid, api.week_key()))) + '/4**\n'
        + 'Streak: **' + str(api.active_streak(gid, uid)) + ' weeks**\n'
        + 'Paused: **' + ('Yes' if api.is_paused(gid, uid, api.week_key()) else 'No') + '**\n\n'
        + 'Award or remove up to 500 XP with a required reason and second confirmation.')


class PlayerMenu(PrivateMenu):
    def __init__(self, owner_id, target_id):
        super().__init__(owner_id)
        self.target_id = target_id

    @discord.ui.button(label='Award XP', emoji='➕', style=discord.ButtonStyle.success)
    async def award(self, interaction, button):
        await interaction.response.send_modal(XPForm(self.owner_id, self.target_id, 1))

    @discord.ui.button(label='Remove XP', emoji='➖', style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        await interaction.response.send_modal(XPForm(self.owner_id, self.target_id, -1))

    @discord.ui.button(label='Refresh Member', emoji='🔄', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        try:
            member = interaction.guild.get_member(self.target_id) or await interaction.guild.fetch_member(self.target_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message('Member unavailable.', ephemeral=True)
        await interaction.response.edit_message(content=member_summary(member), view=self)

    @discord.ui.button(label='Change Member', emoji='👥', style=discord.ButtonStyle.secondary, row=1)
    async def change(self, interaction, button):
        await interaction.response.edit_message(
            content='👥 Select another member.', view=PlayerPickerView(self.owner_id))


class XPForm(discord.ui.Modal):
    def __init__(self, owner_id, target_id, sign):
        super().__init__(title='Award XP' if sign > 0 else 'Remove XP', timeout=300)
        self.owner_id, self.target_id, self.sign = owner_id, target_id, sign
        self.amount = discord.ui.TextInput(label='Amount (1–500)', max_length=3, placeholder='25')
        self.reason = discord.ui.TextInput(label='Reason', max_length=180,
                                           style=discord.TextStyle.paragraph)
        self.add_item(self.amount)
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        if not allowed(interaction) or interaction.user.id != self.owner_id:
            return await interaction.response.send_message('Not authorised.', ephemeral=True)
        try:
            amount = int(self.amount.value.strip())
        except ValueError:
            amount = 0
        reason = self.reason.value.strip()
        if amount < 1 or amount > 500 or not reason:
            return await interaction.response.send_message(
                'Please provide 1–500 XP and a nonempty reason.', ephemeral=True)
        await interaction.response.send_message(
            '⚠️ **Confirm XP change**\n'
            + 'Member ID: ' + str(self.target_id) + '\n'
            + 'Change: **' + str(self.sign * amount) + ' XP**\n'
            + 'Reason: ' + discord.utils.escape_mentions(reason) + '\n'
            + 'Current balance: ' + str(api.get_xp(interaction.guild_id, self.target_id))
            + ' XP\n\n**This may change the member’s rank.**',
            view=ConfirmXP(self.owner_id, self.target_id, amount * self.sign, reason),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ConfirmXP(PrivateMenu):
    def __init__(self, owner_id, target_id, amount, reason):
        super().__init__(owner_id)
        self.target_id, self.amount, self.reason = target_id, amount, reason
        self.used = False

    @discord.ui.button(label='Confirm XP Change', emoji='✅', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.used:
            return await interaction.response.send_message('Already processed.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(self.target_id) or await interaction.guild.fetch_member(self.target_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await interaction.edit_original_response(content='Member no longer available. Nothing changed.', view=None)
        if member.bot:
            return await interaction.edit_original_response(content='Cannot adjust bot XP.', view=None)
        before = api.get_xp(interaction.guild_id, self.target_id)
        xp = api.add_xp(interaction.guild_id, self.target_id, self.amount,
                        'Moderator ' + str(interaction.user.id) + ': ' + self.reason)
        rank_updated = await api.update_rank(member, xp)
        audit(interaction.user.id, 'xp_change',
              str(self.amount) + ' XP; ' + str(before) + ' to ' + str(xp) + '; ' + self.reason,
              self.target_id)
        await interaction.edit_original_response(
            content='✅ XP updated: **' + str(xp) + '** for ' + member.display_name
                    + ('' if rank_updated else '. ⚠️ Rank role sync needs checking.'), view=None)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='XP change cancelled.', view=None)


class QuestMenu(PrivateMenu):
    @discord.ui.button(label='Weekly Status', emoji='📋', style=discord.ButtonStyle.secondary)
    async def weekly(self, interaction, button):
        channel = interaction.guild.get_channel(api.QUESTS_CHANNEL_ID)
        await interaction.response.edit_message(
            content='📜 **Weekly Quest**\nWeek: ' + api.week_key()
                    + '\nNext reset: <t:' + str(int(api.next_reset().timestamp())) + ':F>'
                    + '\nBoard channel: ' + (channel.mention if channel else 'Not configured'),
            view=self)

    @discord.ui.button(label='Post Weekly Board', emoji='🔄', style=discord.ButtonStyle.primary)
    async def post_weekly(self, interaction, button):
        await interaction.response.send_message(
            '⚠️ This will post a Weekly Side Quest board in the public quests channel. Continue?',
            view=ConfirmWeeklyBoard(self.owner_id), ephemeral=True)

    @discord.ui.button(label='Milestone Board', emoji='🏆', style=discord.ButtonStyle.secondary)
    async def milestone(self, interaction, button):
        await interaction.response.edit_message(
            content='🏆 **Milestone Quests**\n' + api.milestone_board_link(interaction.guild)
                    + '\nExisting approval and milestone buttons remain unchanged.',
            view=self)


class ConfirmWeeklyBoard(PrivateMenu):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.used = False

    @discord.ui.button(label='Confirm Post', emoji='✅', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.used:
            return await interaction.response.send_message('Already processed.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(api.QUESTS_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return await interaction.edit_original_response(content='Quest channel not configured.', view=None)
        await api.post_weekly_board(force=True)
        audit(interaction.user.id, 'weekly_board', 'Requested weekly board in channel ' + str(channel.id))
        await interaction.edit_original_response(content='✅ Weekly board requested in ' + channel.mention, view=None)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='No board posted.', view=None)


def reward_summary():
    with api.connect() as db:
        pending = db.execute(
            "SELECT COUNT(*) AS n FROM purchase_orders WHERE guild_id=? AND status='pending'",
            (api.GUILD_ID,)).fetchone()['n']
        open_tickets = db.execute(
            "SELECT COUNT(*) AS n FROM reward_tickets WHERE guild_id=? AND status NOT IN ('closed','fulfilled')",
            (api.GUILD_ID,)).fetchone()['n']
    channel = ('<#' + str(api.MOD_REVIEW_CHANNEL_ID) + '>' if api.MOD_REVIEW_CHANNEL_ID else 'Not configured')
    return ('🎟️ **Rewards & Orders**\nOrders pending: **' + str(pending)
            + '**\nUnfulfilled reward tickets: **' + str(open_tickets)
            + '**\nModerator review: ' + channel
            + '\n\nUse the existing Approve / Reject and reward ticket buttons '
              'in their respective review channels. Their one-time XP safeguards remain unchanged.')


class RewardMenu(PrivateMenu):
    @discord.ui.button(label='Refresh Requests', emoji='🔄', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        await interaction.response.edit_message(content=reward_summary(), view=self)


class AnnouncementMenu(PrivateMenu):
    @discord.ui.button(label='Compose Announcement', emoji='📝', style=discord.ButtonStyle.primary)
    async def compose(self, interaction, button):
        channel = interaction.guild.get_channel(api.ANNOUNCEMENTS_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                'ANNOUNCEMENTS_CHANNEL_ID is not configured.', ephemeral=True)
        await interaction.response.send_modal(AnnouncementForm(self.owner_id))


class AnnouncementForm(discord.ui.Modal):
    def __init__(self, owner_id):
        super().__init__(title='Draft Guild Announcement', timeout=300)
        self.owner_id = owner_id
        self.heading = discord.ui.TextInput(label='Title', max_length=100)
        self.body = discord.ui.TextInput(label='Announcement', max_length=1500,
                                         style=discord.TextStyle.paragraph)
        self.add_item(self.heading)
        self.add_item(self.body)

    async def on_submit(self, interaction):
        if not allowed(interaction) or interaction.user.id != self.owner_id:
            return await interaction.response.send_message('Not authorised.', ephemeral=True)
        title = self.heading.value.strip()
        body = self.body.value.strip()
        if not title or not body:
            return await interaction.response.send_message('Title and message are required.', ephemeral=True)
        title = discord.utils.escape_mentions(title)
        body = discord.utils.escape_mentions(body)
        await interaction.response.send_message(
            '📣 **Preview**\n' + title + '\n\n' + body
            + '\n\nPublish to announcements? No members or roles will be pinged.',
            view=ConfirmAnnouncement(self.owner_id, title, body),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ConfirmAnnouncement(PrivateMenu):
    def __init__(self, owner_id, title, body):
        super().__init__(owner_id)
        self.heading, self.body, self.used = title, body, False

    @discord.ui.button(label='Publish', emoji='✅', style=discord.ButtonStyle.danger)
    async def publish(self, interaction, button):
        if self.used:
            return await interaction.response.send_message('Already processed.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(api.ANNOUNCEMENTS_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return await interaction.edit_original_response(content='Announcement channel unavailable.', view=None)
        embed = discord.Embed(title=self.heading, description=self.body,
                              colour=0x71A7A2, timestamp=api.utc_now())
        try:
            post = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.edit_original_response(content='Announcement failed; check permissions.', view=None)
        audit(interaction.user.id, 'announcement',
              'Message ' + str(post.id) + ' in ' + str(channel.id) + ': ' + self.heading)
        await interaction.edit_original_response(content='✅ Posted in ' + channel.mention, view=None)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='Announcement cancelled.', view=None)


def audit_summary():
    with api.connect() as db:
        rows = db.execute('SELECT actor_id,target_id,action,details,created_at '
                          'FROM admin_hub_audit WHERE guild_id=? ORDER BY id DESC LIMIT 8',
                          (api.GUILD_ID,)).fetchall()
    if not rows:
        return '📋 **Admin Hub Audit**\nNo Admin Hub changes yet. Legacy command audit history remains available.'
    lines = ['📋 **Recent Admin Hub Changes**']
    for row in rows:
        detail = discord.utils.escape_mentions(row['details'])[:120]
        lines.append(row['created_at'][:16] + ' · actor ' + str(row['actor_id'])
                     + ' · ' + row['action'] + ' · ' + detail)
    return '\n'.join(lines)[:1900]


class AuditMenu(PrivateMenu):
    @discord.ui.button(label='Refresh Audit', emoji='🔄', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        await interaction.response.edit_message(content=audit_summary(), view=self)


def health_summary(guild):
    channels = (('Player Hub', api.PLAYER_STATS_CHANNEL_ID),
                ('Weekly Quests', api.QUESTS_CHANNEL_ID),
                ('Announcements', api.ANNOUNCEMENTS_CHANNEL_ID),
                ('Moderator Review', api.MOD_REVIEW_CHANNEL_ID),
                ('Leaderboard', api.LEADERBOARD_CHANNEL_ID))
    lines = ['⚙️ **GuildKeeper Health**']
    for name, cid in channels:
        channel = guild.get_channel(cid) if cid else None
        lines.append(('✅ ' if isinstance(channel, discord.TextChannel) else '⚠️ ')
                     + name + ': ' + (channel.mention if channel else 'Not configured'))
    return '\n'.join(lines)


class ControlMenu(PrivateMenu):
    @discord.ui.button(label='Refresh Player Hub', emoji='🔄', style=discord.ButtonStyle.primary)
    async def player_hub(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        ok, result = await api.post_player_stats_hub()
        if ok:
            audit(interaction.user.id, 'player_hub_refresh', result)
        await interaction.edit_original_response(content=('✅ ' if ok else '⚠️ ') + result, view=self)

    @discord.ui.button(label='Refresh Leaderboard', emoji='🏆', style=discord.ButtonStyle.secondary)
    async def leaderboard(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        ok, result = await api.update_permanent_leaderboard(force=True)
        if ok:
            audit(interaction.user.id, 'leaderboard_refresh', result)
        await interaction.edit_original_response(content=('✅ ' if ok else '⚠️ ') + result, view=self)

    @discord.ui.button(label='Sync Rank Roles', emoji='🛡️', style=discord.ButtonStyle.secondary)
    async def sync(self, interaction, button):
        await interaction.response.send_message(
            'Sync all member rank roles to their stored XP and unlocks? This does not change XP.',
            view=ConfirmRankSync(self.owner_id), ephemeral=True)

    @discord.ui.button(label='Health Check', emoji='🔍', style=discord.ButtonStyle.secondary, row=1)
    async def health(self, interaction, button):
        await interaction.response.edit_message(content=health_summary(interaction.guild), view=self)


class ConfirmRankSync(PrivateMenu):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.used = False

    @discord.ui.button(label='Confirm Rank Sync', emoji='✅', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.used:
            return await interaction.response.send_message('Already processed.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        success, failed = await api.sync_all_ranks(interaction.guild)
        audit(interaction.user.id, 'rank_sync', str(success) + ' succeeded, ' + str(failed) + ' failed')
        await interaction.edit_original_response(
            content='✅ Rank sync: ' + str(success) + ' succeeded; ' + str(failed) + ' failed.', view=None)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(content='Rank sync cancelled.', view=None)


async def post_admin_hub():
    guild = api.bot.get_guild(api.GUILD_ID)
    channel = guild.get_channel(api.MOD_REVIEW_CHANNEL_ID) if guild and api.MOD_REVIEW_CHANNEL_ID else None
    if not isinstance(channel, discord.TextChannel):
        return False, 'MOD_REVIEW_CHANNEL_ID must be a private text channel.'
    if channel.permissions_for(guild.default_role).view_channel:
        return False, 'Hide the moderator channel from @everyone before posting the Admin Hub.'
    key = 'admin-hub-post-' + str(api.GUILD_ID)
    with api.connect() as db:
        row = db.execute('SELECT value FROM v12_config WHERE key=?', (key,)).fetchone()
    message = None
    if row:
        try:
            message = await channel.fetch_message(int(row['value']))
        except (ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    try:
        if message is None:
            message = await channel.send(embed=hub_embed(), view=AdminHubView(),
                                         allowed_mentions=discord.AllowedMentions.none())
        else:
            await message.edit(embed=hub_embed(), view=AdminHubView())
    except (discord.Forbidden, discord.HTTPException):
        return False, 'Cannot post Admin Hub. Check bot permissions.'
    with api.connect() as db:
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)', (key, str(message.id)))
    return True, 'Admin Hub ready in ' + channel.mention


def install(bot_module):
    """Register the panel, slash entry point, and persistent buttons."""
    global api
    api = bot_module

    @api.bot.tree.command(name='admin_hub_post', description='Post or refresh the private moderator Admin Hub')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_hub_post(interaction: discord.Interaction):
        if not allowed(interaction):
            return await interaction.response.send_message('Manage Server permission required.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ok, result = await post_admin_hub()
        await interaction.followup.send(('✅ ' if ok else '⚠️ ') + result, ephemeral=True)
