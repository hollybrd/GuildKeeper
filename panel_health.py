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



async def _repair_saved_embed(api, guild, label, channel_id, message_id,
                              expected_embed, expected_view, update_record):
    """Edit an existing panel first; only post a replacement when absent."""
    channel = guild.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return False, f'{label}: configured channel is unavailable'
    message = None
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            message = None
        except (discord.Forbidden, discord.HTTPException):
            return False, f'{label}: cannot inspect existing message; skipped to avoid duplicates'
    try:
        if message is None:
            message = await channel.send(embed=expected_embed, view=expected_view,
                                         allowed_mentions=discord.AllowedMentions.none())
        else:
            await message.edit(embed=expected_embed, view=expected_view)
    except (discord.Forbidden, discord.HTTPException):
        return False, f'{label}: failed to edit/post; check channel permissions'
    update_record(channel.id, message.id)
    return True, f'{label}: {"created" if not message_id else "updated or replaced"}'


async def _repair_role_menu(api, guild, kind):
    label = 'Class Role Menu' if kind == 'gym' else 'Proficiency Role Menu'
    channel = guild.get_channel(api.GET_ROLE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return False, f'{label}: role-menu channel not configured'
    try:
        mapping = api.role_menu_mapping(kind)
    except ValueError as error:
        return False, f'{label}: {error}'
    with api.connect() as db:
        row = db.execute('SELECT channel_id,message_id FROM role_menu_posts '
                         'WHERE guild_id=? AND menu_type=?', (guild.id, kind)).fetchone()
    old_channel = guild.get_channel(row['channel_id']) if row else None
    message = None
    if row and isinstance(old_channel, discord.TextChannel):
        try:
            message = await old_channel.fetch_message(row['message_id'])
        except discord.NotFound:
            message = None
        except (discord.Forbidden, discord.HTTPException):
            return False, f'{label}: cannot inspect previous menu; skipped'
    if message is not None and old_channel.id == channel.id:
        title = '⚔️ CHOOSE YOUR CLASS' if kind == 'gym' else '🔮 CHOOSE YOUR PROFICIENCY'
        description = (
            'React to choose your training class. Selecting a different class replaces the previous one.'
            if kind == 'gym' else
            'React to select as many proficiencies as you like. Remove a reaction to unselect.')
        lines = [f'{emoji} — <@&{role_id}>' for emoji, role_id in mapping.items()]
        embed = discord.Embed(title=title, description=description + '\n\n' + '\n\n'.join(lines),
                              color=0x71A7A2)
        embed.set_footer(text='Class & Proficiency roles only · XP ranks are earned automatically')
        try:
            await message.edit(embed=embed)
            old_emoji = {str(r.emoji) for r in message.reactions}
            expected_emoji = set(mapping)
            # Preserve member choices for unchanged emoji; clear obsolete only.
            for emoji in old_emoji - expected_emoji:
                await message.clear_reaction(emoji)
            for emoji in expected_emoji - old_emoji:
                await message.add_reaction(emoji)
        except (discord.Forbidden, discord.HTTPException, discord.InvalidArgument):
            return False, f'{label}: could not refresh reactions; check Manage Messages and Add Reactions'
        return True, f'{label}: updated in place'
    try:
        await api.publish_role_menu(guild, channel, kind, mapping)
    except (ValueError, discord.Forbidden, discord.HTTPException) as error:
        return False, f'{label}: {error}'
    if message is not None:
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            return True, f'{label}: new panel posted; previous panel could not be removed'
    return True, f'{label}: created'


async def _repair_weekly(api, guild):
    channel = guild.get_channel(api.QUESTS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return False, 'Weekly Side Quest: channel not configured'
    expected = api.weekly_board_embed()
    candidate = None
    try:
        async for message in channel.history(limit=100):
            if message.author.id != guild.me.id or not message.embeds:
                continue
            if message.embeds[0].title != expected.title:
                continue
            if (message.embeds[0].footer.text or '').endswith(api.week_key()):
                candidate = message
                break
    except (discord.Forbidden, discord.HTTPException):
        return False, 'Weekly Side Quest: cannot inspect recent messages; skipped'
    if candidate is not None:
        try:
            await candidate.edit(embed=expected, view=api.WeeklyTurnInView())
        except (discord.Forbidden, discord.HTTPException):
            return False, 'Weekly Side Quest: unable to edit existing board'
        return True, 'Weekly Side Quest: updated existing board'
    # Existing scheduler also uses this path; force avoids a stale schedule_posts flag.
    await api.post_weekly_board(force=True)
    # Verify a board appeared rather than assuming that the void-return function succeeded.
    result = await _weekly_board(api, guild)
    return (result['state'] == 'current',
            'Weekly Side Quest: ' + ('new board posted' if result['state'] == 'current'
                                    else 'unable to verify newly posted board'))


async def repair(api, guild, states):
    """Repair only verifiably missing/outdated panels; never touch unknown/setup."""
    gid = guild.id
    results = []
    for entry in states:
        if entry['state'] not in {'missing', 'outdated'}:
            continue
        name = entry['name']
        try:
            if name == 'Admin Hub':
                channel_id, _ = _parse_location(
                    _saved_config(api, f'admin-hub-post-{gid}'), api.MOD_REVIEW_CHANNEL_ID)
                channel = guild.get_channel(channel_id) if channel_id else None
                if isinstance(channel, discord.TextChannel):
                    ok, info = await api.admin_hub.post_admin_hub(channel)
                else:
                    ok, info = False, 'Admin Hub: original channel unavailable; skipped'
            elif name == 'Player Hub':
                ok, info = await api.post_player_stats_hub()
            elif name == 'Leaderboard':
                ok, info = await api.update_permanent_leaderboard(force=True)
            elif name == 'Weekly Side Quest':
                ok, info = await _repair_weekly(api, guild)
            elif name == 'Starter Quests':
                with api.connect() as db:
                    saved = db.execute('SELECT channel_id,message_id FROM starter_board_posts '
                                       'WHERE guild_id=?', (gid,)).fetchone()
                if not api.STARTER_ACCESS_ROLE_ID or not guild.get_role(api.STARTER_ACCESS_ROLE_ID):
                    ok, info = False, 'Starter Quests: missing configured access role'
                else:
                    def save_starter(cid, mid):
                        with api.connect() as db:
                            db.execute('INSERT INTO starter_board_posts(guild_id,channel_id,message_id) '
                                       'VALUES (?,?,?) ON CONFLICT(guild_id) DO UPDATE SET '
                                       'channel_id=excluded.channel_id,message_id=excluded.message_id',
                                       (gid, cid, mid))
                    ok, info = await _repair_saved_embed(
                        api, guild, name, saved['channel_id'] if saved else api.STARTER_CHANNEL_ID,
                        saved['message_id'] if saved else None,
                        api.starter_embed(), api.StarterTurnInView(), save_starter)
            elif name == 'Milestone Quests':
                channel_id, message_id = _parse_location(
                    _saved_config(api, f'milestone-board-{gid}'), None)
                def save_milestone(cid, mid):
                    with api.connect() as db:
                        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                                   (f'milestone-board-{gid}', f'{cid}:{mid}'))
                ok, info = await _repair_saved_embed(
                    api, guild, name, channel_id, message_id,
                    api.milestone_board_embed(), api.MilestoneBoardView(), save_milestone)
            elif name == 'Class Role Menu':
                ok, info = await _repair_role_menu(api, guild, 'gym')
            elif name == 'Proficiency Role Menu':
                ok, info = await _repair_role_menu(api, guild, 'hobby')
            else:
                ok, info = False, f'{name}: no automatic refresh configured'
        except (discord.Forbidden, discord.HTTPException, ValueError) as exc:
            ok, info = False, f'{name}: {type(exc).__name__} — refresh failed'
        results.append((ok, info))
    return results


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
