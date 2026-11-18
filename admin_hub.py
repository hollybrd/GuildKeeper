"""GuildKeeper's permission-checked, persistent, button-driven moderator hub.

Installed by bot.py, which provides the existing XP/quest/reward functions.
No legacy admin slash commands are replaced.
"""
import discord
import panel_health
import health_check
import announcement_ui
import announcements_scheduler
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

    @discord.ui.button(label='Rewards & Orders', emoji='🎟️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:rewards', row=0)
    async def rewards(self, interaction, button):
        await interaction.response.send_message(
            reward_summary(), view=RewardMenu(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='Announcements', emoji='📣', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:announcements', row=0)
    async def announcements(self, interaction, button):
        await interaction.response.send_message(
            '📣 **Announcements** — post now, schedule one-off/recurring messages, '
            'and manage automatic notifications.', view=AnnouncementMenu(interaction.user.id),
            ephemeral=True)

    @discord.ui.button(label='Audit Log', emoji='📋', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:adminhub:audit', row=1)
    async def history(self, interaction, button):
        await interaction.response.send_message(
            audit_summary(), view=AuditMenu(interaction.user.id), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Health Check', emoji='🩺', style=discord.ButtonStyle.primary,
                       custom_id='guildkeeper:adminhub:health', row=1)
    async def health_check(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        report = await health_check.run(api, interaction.guild)
        await interaction.followup.send(
            embed=health_check.render(report), view=HealthCheckMenu(interaction.user.id),
            ephemeral=True)

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
        ('🎟️ Rewards & Orders', 'Review outstanding requests in the existing moderator channel.'),
        ('📣 Announcements', 'Post now, schedule recurring messages, and manage upcoming posts.'),
        ('📋 Audit Log', 'Inspect Admin Hub changes.'),
        ('⚙️ Bot Controls', 'Refresh panels, leaderboard and rank roles.'),
        ('🩺 Health Check', 'Check panels, runtime, database, permissions and configuration.'),
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
        current_xp = api.get_xp(interaction.guild_id, self.target_id)
        signed = amount * self.sign
        projected = max(0, current_xp + signed)
        await interaction.response.send_message(
            '⚠️ **Confirm XP change**\n'
            + 'Member ID: ' + str(self.target_id) + '\n'
            + 'Requested change: **' + str(signed) + ' XP**\n'
            + 'Actual change: **' + str(projected - current_xp) + ' XP**\n'
            + 'Balance: **' + str(current_xp) + ' → ' + str(projected) + ' XP**\n'
            + 'Reason: ' + discord.utils.escape_mentions(reason)
            + '\n\n**This may change the member’s rank.**',
            view=ConfirmXP(self.owner_id, self.target_id, signed, reason, current_xp),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ConfirmXP(PrivateMenu):
    def __init__(self, owner_id, target_id, amount, reason, expected_xp=None):
        super().__init__(owner_id)
        self.target_id, self.amount, self.reason = target_id, amount, reason
        self.expected_xp = expected_xp
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
        if self.expected_xp is not None and before != self.expected_xp:
            return await interaction.edit_original_response(
                content='⚠️ Member XP changed since your preview. No change made. '
                        'Reopen the action to confirm the new balance.', view=None)
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
    @discord.ui.button(label='Schedule Message', emoji='📅', style=discord.ButtonStyle.primary)
    async def schedule(self, interaction, button):
        await interaction.response.send_message(
            '📅 **Schedule an announcement** — choose its destination channel first.',
            view=announcement_ui.ChooseChannel(self.owner_id), ephemeral=True)

    @discord.ui.button(label='Upcoming Messages', emoji='📋', style=discord.ButtonStyle.secondary)
    async def upcoming(self, interaction, button):
        await interaction.response.send_message(
            announcement_ui.listing(), view=announcement_ui.UpcomingMenu(self.owner_id),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Automatic Notices', emoji='⚙️', style=discord.ButtonStyle.secondary)
    async def automatic(self, interaction, button):
        await interaction.response.send_message(
            announcement_ui.automatic_summary(), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Compose Announcement', emoji='📝', style=discord.ButtonStyle.secondary)
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


def audit_summary(category='all'):
    """Recent changes, with actor, target, timestamp and moderator-provided reason."""
    if category not in ('all', 'xp', 'quest', 'pause', 'reward'):
        category = 'all'
    with api.connect() as db:
        rows = db.execute('SELECT actor_id,target_id,action,details,created_at '
                          'FROM admin_hub_audit WHERE guild_id=? ORDER BY id DESC LIMIT 120',
                          (api.GUILD_ID,)).fetchall()
    if category != 'all':
        def matches(row):
            action = row['action'].lower()
            if category == 'xp':
                return 'xp' in action
            if category == 'quest':
                return any(part in action for part in ('quest', 'starter', 'weekly', 'milestone'))
            if category == 'pause':
                return 'pause' in action
            return any(part in action for part in ('reward', 'order'))
        rows = [row for row in rows if matches(row)]
    if not rows:
        return '📋 **Admin Audit — ' + category.title() + '**\nNo matching recorded Admin Hub changes yet.'
    lines = ['📋 **Admin Audit — ' + category.title() + '** (latest 10)']
    for row in rows[:10]:
        detail = discord.utils.escape_mentions(row['details'])[:160]
        target = (' · target ' + str(row['target_id'])) if row['target_id'] else ''
        lines.append('**' + row['created_at'][:16].replace('T', ' ') + ' UTC**'
                     + ' · moderator ' + str(row['actor_id']) + target
                     + '\n**' + row['action'].replace('_', ' ').title() + '** — ' + detail)
    return '\n'.join(lines)[:1900]


class AuditMenu(PrivateMenu):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.category = 'all'

    async def show(self, interaction, category):
        self.category = category
        await interaction.response.edit_message(
            content=audit_summary(category), view=self,
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='All', style=discord.ButtonStyle.secondary)
    async def all(self, interaction, button):
        await self.show(interaction, 'all')

    @discord.ui.button(label='XP', style=discord.ButtonStyle.secondary)
    async def xp(self, interaction, button):
        await self.show(interaction, 'xp')

    @discord.ui.button(label='Quests', style=discord.ButtonStyle.secondary)
    async def quests(self, interaction, button):
        await self.show(interaction, 'quest')

    @discord.ui.button(label='Pauses', style=discord.ButtonStyle.secondary)
    async def pauses(self, interaction, button):
        await self.show(interaction, 'pause')

    @discord.ui.button(label='Rewards', style=discord.ButtonStyle.secondary)
    async def rewards(self, interaction, button):
        await self.show(interaction, 'reward')

    @discord.ui.button(label='Refresh Audit', emoji='🔄', style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, interaction, button):
        await self.show(interaction, self.category)


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

    @discord.ui.button(label='Permanent Panels', emoji='🛡️', style=discord.ButtonStyle.primary, row=1)
    async def permanent_panels(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        checks = await panel_health.snapshot(api, interaction.guild)
        await interaction.followup.send(
            panel_health.render(checks), view=PanelHealthMenu(self.owner_id), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

class HealthCheckMenu(PrivateMenu):
    @discord.ui.button(label='Check Again', emoji='🔄', style=discord.ButtonStyle.primary)
    async def check(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        report = await health_check.run(api, interaction.guild)
        await interaction.edit_original_response(embed=health_check.render(report), view=self)

    @discord.ui.button(label='Permanent Panels', emoji='🛡️', style=discord.ButtonStyle.secondary)
    async def panels(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        checks = await panel_health.snapshot(api, interaction.guild)
        await interaction.followup.send(
            panel_health.render(checks), view=PanelHealthMenu(self.owner_id),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class PanelHealthMenu(PrivateMenu):
    @discord.ui.button(label='Check Again', emoji='🔍', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        checks = await panel_health.snapshot(api, interaction.guild)
        await interaction.edit_original_response(content=panel_health.render(checks), view=self)

    @discord.ui.button(label='Refresh Panels', emoji='🔄', style=discord.ButtonStyle.primary)
    async def repair(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        checks = await panel_health.snapshot(api, interaction.guild)
        needs_refresh = [item for item in checks if item['state'] in ('missing', 'outdated')]
        if not needs_refresh:
            return await interaction.followup.send(
                '✅ No verifiably outdated or missing panels to refresh. '
                'Unknown/unconfigured panels are never changed automatically.', ephemeral=True)
        names = '\n'.join('• ' + item['name'] for item in needs_refresh)
        await interaction.followup.send(
            '⚠️ **Refresh these permanent panels?**\n'
            + names + '\n\nExisting messages will be **edited in place** where possible; '
            'only missing panels will be posted again. '
            'Panels that cannot be verified will be skipped. '
            '**No existing panel is deleted unless relocating a role menu.**',
            view=ConfirmPanelRefresh(self.owner_id), ephemeral=True)


class ConfirmPanelRefresh(PrivateMenu):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.used = False

    @discord.ui.button(label='Confirm Refresh', emoji='✅', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.used:
            return await interaction.response.send_message('Already processed.', ephemeral=True)
        self.used = True
        await interaction.response.defer(ephemeral=True)
        checks = await panel_health.snapshot(api, interaction.guild)
        results = await panel_health.repair(api, interaction.guild, checks)
        latest = await panel_health.snapshot(api, interaction.guild)
        ok_count = sum(ok for ok, _ in results)
        details = '\n'.join(('✅ ' if ok else '⚠️ ') + info for ok, info in results)
        if not results:
            details = 'No missing or outdated panels remained.'
        audit(interaction.user.id, 'refresh_permanent_panels',
              str(ok_count) + '/' + str(len(results)) + ' panel updates completed')
        await interaction.edit_original_response(
            content=('🔄 **Permanent Panel Refresh**\n'
                     + details + '\n\n' + panel_health.render(latest))[:3950],
            view=None, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.used = True
        await interaction.response.edit_message(
            content='Panel refresh cancelled. Nothing changed.', view=None)


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


async def post_admin_hub(channel: discord.TextChannel):
    """Create or relocate the Admin Hub to any private Guild text channel."""
    guild = api.bot.get_guild(api.GUILD_ID)
    if guild is None or not isinstance(channel, discord.TextChannel) or channel.guild.id != api.GUILD_ID:
        return False, 'Select a text channel inside the configured GYMGUILD server.'
    if channel.permissions_for(guild.default_role).view_channel:
        return False, 'The selected channel is visible to @everyone. Make it private before posting the Admin Hub.'
    me = guild.me
    if me is None:
        return False, 'GuildKeeper could not check its permissions for that channel.'
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history):
        return False, 'GuildKeeper needs View Channel, Send Messages, Embed Links and Read Message History in this channel.'

    key = 'admin-hub-post-' + str(api.GUILD_ID)
    with api.connect() as db:
        row = db.execute('SELECT value FROM v12_config WHERE key=?', (key,)).fetchone()

    previous = None
    previous_channel = None
    if row:
        try:
            saved = row['value'].split(':')
            if len(saved) == 2:
                old_channel_id, old_message_id = map(int, saved)
                previous_channel = guild.get_channel(old_channel_id)
            else:
                # Backward compatibility with the earlier single-message-ID format.
                old_message_id = int(saved[0])
                previous_channel = guild.get_channel(api.MOD_REVIEW_CHANNEL_ID)
            if isinstance(previous_channel, discord.TextChannel):
                previous = await previous_channel.fetch_message(old_message_id)
        except (ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
            previous = None

    try:
        if previous is not None and previous_channel.id == channel.id:
            await previous.edit(embed=hub_embed(), view=AdminHubView())
            message = previous
        else:
            message = await channel.send(embed=hub_embed(), view=AdminHubView(),
                                         allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException):
        return False, 'Unable to post in the selected channel. Please check bot permissions.'

    with api.connect() as db:
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                   (key, str(channel.id) + ':' + str(message.id)))

    note = ''
    if previous is not None and previous_channel.id != channel.id:
        try:
            await previous.delete()
        except (discord.Forbidden, discord.HTTPException):
            note = ' ⚠️ The old Admin Hub could not be deleted. Remove the previous message manually.'
    return True, 'Admin Hub ready in ' + channel.mention + '.' + note


def install(bot_module):
    """Register the panel, slash entry point, and persistent buttons."""
    global api
    api = bot_module

    @api.bot.tree.command(name='admin_hub_post', description='Post or refresh the private moderator Admin Hub')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel='Private text channel where the Admin Hub should appear')
    async def admin_hub_post(interaction: discord.Interaction, channel: discord.TextChannel):
        if not allowed(interaction):
            return await interaction.response.send_message('Manage Server permission required.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ok, result = await post_admin_hub(channel)
        await interaction.followup.send(('✅ ' if ok else '⚠️ ') + result, ephemeral=True)
