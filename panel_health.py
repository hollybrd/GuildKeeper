"""Read-only live health checks for GuildKeeper's permanent Discord panels.

Never writes or repairs panels. Uses saved message IDs where available; weekly
quest boards are discovered from the configured channel for the current week.
"""
import discord


def _same_embed(actual, expected):
    """Compare authored panel content, ignoring Discord's extra metadata."""
    if actual is None:
        return False
    for field in ('title', 'description', 'color'):
        if getattr(actual, field, None) != getattr(expected, field, None):
            return False
    actual_footer = actual.footer.text if actual.footer else None
    expected_footer = expected.footer.text if expected.footer else None
    if actual_footer != expected_footer:
        return False
    return [(f.name, f.value, f.inline) for f in actual.fields] == [
        (f.name, f.value, f.inline) for f in expected.fields]


def _button_ids(message):
    ids = []
    for row in message.components:
        for component in getattr(row, 'children', ()):
            custom_id = getattr(component, 'custom_id', None)
            if custom_id:
                ids.append(custom_id)
    return ids


def _expected_buttons(view):
    return [item.custom_id for item in view.children if item.custom_id]


def _status(label, state, reason, channel=None, message=None):
    return {
        'name': label, 'state': state, 'detail': reason,
        'url': (f'https://discord.com/channels/{channel.guild.id}/{channel.id}/{message.id}'
                if channel is not None and message is not None else None),
    }


async def _check_message(guild, label, channel_id, message_id, embed=None, view=None,
                         role_emoji=None, expected_title=None, required_fragments=None):
    if not channel_id:
        return _status(label, 'setup', 'Channel is not configured')
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return _status(label, 'setup', 'Channel is unavailable to GuildKeeper')
    if not message_id:
        return _status(label, 'missing', 'No posted panel is recorded')
    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        return _status(label, 'missing', 'Previously posted message was deleted')
    except (discord.Forbidden, discord.HTTPException):
        return _status(label, 'unknown', 'GuildKeeper cannot inspect the message')
    if not guild.me or message.author.id != guild.me.id:
        return _status(label, 'outdated', 'Saved message was not posted by this bot', channel, message)
    if embed is not None and (
        not message.embeds or not _same_embed(message.embeds[0], embed)
    ):
        return _status(label, 'outdated', 'Panel content differs from the current version', channel, message)
    if expected_title and (not message.embeds or message.embeds[0].title != expected_title):
        return _status(label, 'outdated', 'Panel title differs from the current version', channel, message)
    if required_fragments and (not message.embeds or any(
        fragment not in (message.embeds[0].description or '')
        for fragment in required_fragments
    )):
        return _status(label, 'outdated', 'Role choice labels differ from configured roles', channel, message)
    if view is not None and sorted(_button_ids(message)) != sorted(_expected_buttons(view)):
        return _status(label, 'outdated', 'Panel buttons differ from the current version', channel, message)
    if role_emoji is not None:
        actual_reactions = {str(reaction.emoji) for reaction in message.reactions}
        if not set(role_emoji).issubset(actual_reactions):
            return _status(label, 'outdated', 'Role-choice reactions are missing', channel, message)
    return _status(label, 'current', 'Current', channel, message)


def _saved_config(api, key):
    with api.connect() as db:
        row = db.execute('SELECT value FROM v12_config WHERE key=?', (key,)).fetchone()
    return row['value'] if row else None


def _parse_location(value, default_channel):
    if not value:
        return default_channel, None
    try:
        bits = [int(v) for v in value.split(':')]
        if len(bits) == 2:
            return bits[0], bits[1]
        if len(bits) == 1:
            return default_channel, bits[0]
    except (ValueError, TypeError):
        pass
    return default_channel, None


async def _weekly_board(api, guild):
    label = 'Weekly Side Quest'
    channel = guild.get_channel(api.QUESTS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return _status(label, 'setup', 'Weekly quest channel is not configured')
    expected = api.weekly_board_embed()
    if guild.me is None:
        return _status(label, 'unknown', 'GuildKeeper bot identity unavailable')
    try:
        async for message in channel.history(limit=100):
            if not message.embeds or message.author.id != guild.me.id:
                continue
            embed = message.embeds[0]
            if embed.title != expected.title:
                continue
            if _same_embed(embed, expected) and sorted(_button_ids(message)) == sorted(
                _expected_buttons(api.WeeklyTurnInView())):
                return _status(label, 'current', 'Current weekly board', channel, message)
            footer = embed.footer.text or ''
            if footer.endswith(api.week_key()):
                return _status(label, 'outdated', 'Current week board needs refreshing', channel, message)
    except (discord.Forbidden, discord.HTTPException):
        return _status(label, 'unknown', 'Cannot inspect quest channel history')
    return _status(label, 'missing', 'No current-week board found in recent messages')


async def snapshot(api, guild):
    """Inspect the live Discord messages, not merely stored post dates."""
    if guild is None:
        return [_status('Permanent Panels', 'unknown', 'Configured Guild is unavailable')]

    gid = guild.id
    checks = []
    admin_channel, admin_message = _parse_location(
        _saved_config(api, f'admin-hub-post-{gid}'), api.MOD_REVIEW_CHANNEL_ID)
    checks.append(await _check_message(
        guild, 'Admin Hub', admin_channel, admin_message,
        embed=api.admin_hub.hub_embed(), view=api.admin_hub.AdminHubView()))

    checks.append(await _check_message(
        guild, 'Player Hub', api.PLAYER_STATS_CHANNEL_ID,
        _parse_location(_saved_config(api, f'player-stats-post-{gid}'),
                        api.PLAYER_STATS_CHANNEL_ID)[1],
        embed=api.player_stats_embed(), view=api.PlayerStatsView()))

    checks.append(await _weekly_board(api, guild))

    with api.connect() as db:
        starter = db.execute(
            'SELECT channel_id,message_id FROM starter_board_posts WHERE guild_id=?', (gid,)
        ).fetchone()
        roles = {
            row['menu_type']: row for row in db.execute(
                'SELECT menu_type,channel_id,message_id FROM role_menu_posts WHERE guild_id=?',
                (gid,))}
    checks.append(await _check_message(
        guild, 'Starter Quests', starter['channel_id'] if starter else api.STARTER_CHANNEL_ID,
        starter['message_id'] if starter else None,
        embed=api.starter_embed(), view=api.StarterTurnInView()))

    milestone_channel, milestone_message = _parse_location(
        _saved_config(api, f'milestone-board-{gid}'), None)
    checks.append(await _check_message(
        guild, 'Milestone Quests', milestone_channel, milestone_message,
        embed=api.milestone_board_embed(), view=api.MilestoneBoardView()))

    leaderboard_id = _parse_location(
        _saved_config(api, f'leaderboard-post-{gid}'), api.LEADERBOARD_CHANNEL_ID)[1]
    leaderboard = await _check_message(
        guild, 'Leaderboard', api.LEADERBOARD_CHANNEL_ID, leaderboard_id,
        expected_title='🏆 GYMGUILD — LEADERBOARD')
    if leaderboard['state'] == 'current':
        # Leaderboard fields are live data and expected to change; the bot refreshes
        # it daily. A recorded refresh date is the accurate freshness criterion.
        last = _saved_config(api, f'leaderboard-date-{gid}')
        today = api.utc_now().astimezone(api.TZ).date().isoformat()
        if last != today:
            leaderboard['state'] = 'outdated'
            leaderboard['detail'] = 'Leaderboard has not refreshed today'
    checks.append(leaderboard)

    for kind, label in [('gym', 'Class Role Menu'), ('hobby', 'Proficiency Role Menu')]:
        row = roles.get(kind)
        try:
            mapping = api.role_menu_mapping(kind)
            emoji = mapping.keys()
        except ValueError:
            checks.append(_status(label, 'setup', 'Role configuration is incomplete'))
            continue
        result = await _check_message(
            guild, label, row['channel_id'] if row else api.GET_ROLE_CHANNEL_ID,
            row['message_id'] if row else None, role_emoji=emoji,
            expected_title=('⚔️ CHOOSE YOUR CLASS' if kind == 'gym' else '🔮 CHOOSE YOUR PROFICIENCY'),
            required_fragments=[f'<@&{rid}>' for rid in mapping.values()])
        checks.append(result)
    return checks


def render(states):
    icons = {'current': '✅', 'outdated': '🟠', 'missing': '❌',
             'setup': '⚙️', 'unknown': '❔'}
    counts = {state: sum(item['state'] == state for item in states)
              for state in icons}
    lines = [
        '🛡️ **GUILDKEEPER — PERMANENT PANEL HEALTH**',
        f'**{counts["current"]}/{len(states)}** confirmed current · '
        f'**{counts["outdated"]}** outdated · **{counts["missing"]}** missing',
        '',
    ]
    for item in states:
        line = f'{icons[item["state"]]} **{item["name"]}** — {item["detail"]}'
        if item['url']:
            line += f'\n    [View panel]({item["url"]})'
        lines.append(line)
    lines.append('\nChecks inspect live Discord messages. Unknown means verification failed, not that the panel is outdated.')
    return '\n'.join(lines)[:3900]
