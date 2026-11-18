"""Read-only GuildKeeper diagnostics shown from the standalone Admin Hub Health Check.

This is a point-in-time check, not a guarantee that all future events succeed.
"""
import discord
import panel_health
import admin_maintenance

CHANNELS = (
    ('Player Hub', 'PLAYER_STATS_CHANNEL_ID', 'send'),
    ('Weekly Quests', 'QUESTS_CHANNEL_ID', 'send'),
    ('Starter Quests', 'STARTER_CHANNEL_ID', 'send'),
    ('Training Log', 'TRAINING_LOG_CHANNEL_ID', 'read'),
    ('Role Selection', 'GET_ROLE_CHANNEL_ID', 'react'),
    ('Announcements', 'ANNOUNCEMENTS_CHANNEL_ID', 'send'),
    ('Leaderboard', 'LEADERBOARD_CHANNEL_ID', 'send'),
    ('Mod Review', 'MOD_REVIEW_CHANNEL_ID', 'send'),
)

ROLE_CHECKS = (
    ('Starter Access Role', 'STARTER_ACCESS_ROLE_ID'),
    ('Guild Blast Role', 'GUILD_BLAST_ROLE_ID'),
)


def status(label, state, message):
    return {'name': label, 'state': state, 'detail': message}


def check_database(api):
    try:
        with api.connect() as db:
            result = db.execute('PRAGMA quick_check').fetchone()
            if not result or result[0] != 'ok':
                return status('Database', 'problem', 'SQLite integrity check failed')
            expected = ('v12_config', 'members', 'weekly_actions', 'pauses',
                        'admin_hub_audit', 'player_notifications')
            placeholders = ','.join('?' for _ in expected)
            rows = db.execute(
                'SELECT name FROM sqlite_master WHERE type=? AND name IN ('
                + placeholders + ')', ('table', *expected)).fetchall()
            found = {row['name'] for row in rows}
        missing = set(expected) - found
        if missing:
            return status('Database', 'problem',
                          'Required tables missing: ' + ', '.join(sorted(missing)))
        return status('Database', 'current', 'Connected · SQLite quick_check passed')
    except Exception as exc:
        return status('Database', 'problem', type(exc).__name__ + ' while querying database')


def check_runtime(api, guild):
    checks = []
    if guild is None or not api.bot.is_ready():
        checks.append(status('Discord connection', 'problem',
                             'Bot is not ready in the configured Guild'))
    else:
        checks.append(status('Discord connection', 'current', 'Connected to configured Guild'))
    latency = getattr(api.bot, 'latency', None)
    if isinstance(latency, (float, int)) and 0 <= latency < float('inf'):
        if latency > 2:
            checks.append(status('Gateway latency', 'warning',
                                 f'{latency * 1000:.0f} ms — elevated'))
        else:
            checks.append(status('Gateway latency', 'current', f'{latency * 1000:.0f} ms'))
    else:
        checks.append(status('Gateway latency', 'unknown', 'No gateway measurement available'))
    try:
        if api.scheduler.failed():
            checks.append(status('Scheduled jobs', 'problem',
                                 'Background scheduler crashed or stopped with an exception'))
        elif not api.scheduler.is_running():
            checks.append(status('Scheduled jobs', 'problem',
                                 'Background scheduler is not running'))
        else:
            checks.append(status('Scheduled jobs', 'current', 'Scheduler loop is running'))
    except Exception as exc:
        checks.append(status('Scheduled jobs', 'unknown',
                             'Could not inspect scheduler: ' + type(exc).__name__))
    try:
        statuses = admin_maintenance.job_statuses(api)
        expected = ('weekly_settlement', 'streak_warnings', 'notifications',
                    'weekly_board', 'leaderboard', 'reward_ticket_expiry')
        now = api.utc_now()
        for name in expected:
            record = statuses.get(name)
            if record is None:
                checks.append(status('Job: ' + name, 'unknown', 'No recorded run yet'))
                continue
            stamp = api.datetime.fromisoformat(record['timestamp']) if hasattr(api, 'datetime') else None
            if not record.get('success'):
                checks.append(status('Job: ' + name, 'problem',
                                     'Latest run failed: ' + str(record.get('error') or 'Unknown error')))
            elif stamp is not None and (now - stamp).total_seconds() > 300:
                checks.append(status('Job: ' + name, 'warning', 'Last recorded run is more than 5 minutes old'))
            else:
                checks.append(status('Job: ' + name, 'current', 'Last run succeeded'))
    except Exception as exc:
        checks.append(status('Scheduled job details', 'unknown',
                             'Could not inspect job history: ' + type(exc).__name__))
    return checks


def check_channels(api, guild):
    if guild is None:
        return [status('Channel configuration', 'unknown',
                       'Guild is unavailable; cannot check channel permissions')]
    me = guild.me
    if me is None:
        return [status('Channel configuration', 'unknown',
                       'GuildKeeper member permissions are unavailable')]
    checks = []
    for label, attribute, mode in CHANNELS:
        cid = getattr(api, attribute, 0)
        if not cid:
            checks.append(status(label, 'problem', 'Channel ID not configured'))
            continue
        channel = guild.get_channel(cid)
        if not isinstance(channel, discord.TextChannel):
            checks.append(status(label, 'problem', 'Configured text channel not found'))
            continue
        perms = channel.permissions_for(me)
        required = ['view_channel', 'read_message_history']
        if mode in ('send', 'react'):
            required.extend(['send_messages', 'embed_links'])
        if mode == 'react':
            required.append('add_reactions')
        missing = [permission for permission in required if not getattr(perms, permission, False)]
        if missing:
            checks.append(status(label, 'problem', 'Missing permissions: ' + ', '.join(missing)))
        else:
            checks.append(status(label, 'current', 'Channel accessible · permissions OK'))
    return checks


def check_roles(api, guild):
    if guild is None or guild.me is None:
        return [status('Role configuration', 'unknown', 'Could not inspect Guild roles')]
    checks = []
    for label, attr in ROLE_CHECKS:
        role_id = getattr(api, attr, 0)
        if not role_id:
            checks.append(status(label, 'problem', 'Role ID not configured'))
            continue
        role = guild.get_role(role_id)
        if role is None:
            checks.append(status(label, 'problem', 'Role not found'))
        elif role >= guild.me.top_role:
            checks.append(status(label, 'problem', 'Role is not below GuildKeeper in hierarchy'))
        else:
            checks.append(status(label, 'current', 'Role found · manageable'))
    for key, label in [('gym', 'Class Roles'), ('hobby', 'Proficiency Roles')]:
        try:
            mapping = api.role_menu_mapping(key)
            missing = [rid for rid in mapping.values() if guild.get_role(rid) is None]
            checks.append(status(label, 'problem' if missing else 'current',
                                 'Some configured roles are missing' if missing else 'Role mapping configured'))
        except (ValueError, KeyError, TypeError) as exc:
            checks.append(status(label, 'problem', 'Invalid role configuration: ' + type(exc).__name__))
    return checks


async def run(api, guild):
    """Do not mutate Discord messages, guild data, or persistent bot state."""
    core = check_runtime(api, guild) + [check_database(api)]
    core += check_channels(api, guild)
    core += check_roles(api, guild)
    configured = api.shopify_client.admin_configured()
    core.append(status(
        'Shopify store integration', 'current' if configured else 'warning',
        'Admin API credentials configured for private order verification and reward codes'
        if configured else 'Public shop browsing available; Admin API not configured '
        '(order verification and reward codes disabled)'))
    core.append(status(
        'Shopify store notices', 'current',
        'Store announcements opted in (poll every 30 minutes)'
        if api.shopify_events.enabled() else 'Off until SHOPIFY_AUTO_ANNOUNCE=1'))
    try:
        problems = api.announcements_scheduler.failed_count()
        core.append(status('Announcement deliveries',
                           'problem' if problems else 'current',
                           f'{problems} failed or uncertain sends requiring review' if problems
                           else 'No failed or uncertain scheduled notices'))
    except Exception as exc:
        core.append(status('Announcement deliveries', 'unknown',
                           'Cannot inspect scheduled notices: ' + type(exc).__name__))
    try:
        last = admin_maintenance.last_report(api)
        if not last:
            core.append(status('Startup panel maintenance', 'unknown',
                               'No automatic startup check has completed yet'))
        elif last.get('errors') or last.get('remaining'):
            core.append(status('Startup panel maintenance', 'warning',
                               str(last.get('updated', 0)) + ' panels updated; '
                               + str(len(last.get('remaining', []))) + ' unresolved; '
                               + str(len(last.get('errors', []))) + ' skipped/failed'))
        else:
            core.append(status('Startup panel maintenance', 'current',
                               'Latest startup check completed without detected issues'))
    except Exception as exc:
        core.append(status('Startup panel maintenance', 'unknown',
                           'Cannot read maintenance record: ' + type(exc).__name__))
    try:
        panels = await panel_health.snapshot(api, guild)
    except Exception as exc:
        panels = [status('Permanent panels', 'unknown',
                         'Could not inspect panels: ' + type(exc).__name__)]
    return {'core': core, 'panels': panels}


def overall(report):
    states = {item['state'] for item in report['core'] + report['panels']}
    if 'problem' in states or 'missing' in states or 'outdated' in states or 'setup' in states:
        return 'attention'
    if 'unknown' in states or 'warning' in states:
        return 'partial'
    return 'healthy'


def render(report):
    flag = overall(report)
    header = {
        'healthy': '✅ No issues detected in the checks performed',
        'partial': '❔ Some checks were inconclusive',
        'attention': '⚠️ GuildKeeper needs attention',
    }[flag]
    embed = discord.Embed(
        title='✦ GUILDKEEPER — HEALTH CHECK',
        description=(header + '\nPoint-in-time diagnostics. A successful check cannot prove '
                     'that every command, DM, reward, or future scheduled job will succeed.'),
        colour=(discord.Colour.green() if flag == 'healthy'
                else discord.Colour.orange()))

    panels = report['panels']
    panel_symbols = {'current': '✅', 'outdated': '🟠', 'missing': '❌',
                     'setup': '⚠️', 'unknown': '❔'}
    panel_lines = [f'{panel_symbols.get(item["state"], "❔")} '
                   f'**{item["name"]}** — {item["detail"]}' for item in panels]
    embed.add_field(name=f'Permanent panels ({sum(p["state"] == "current" for p in panels)}/{len(panels)} current)',
                    value='\n'.join(panel_lines)[:1024] or 'No panels could be inspected',
                    inline=False)

    runtime_names = {'Discord connection', 'Gateway latency', 'Scheduled jobs', 'Database', 'Startup panel maintenance', 'Announcement deliveries',
                     'Shopify store integration', 'Shopify store notices'}
    groups = [('Runtime & database', [c for c in report['core'] if c['name'] in runtime_names]),
              ('Scheduled task details', [c for c in report['core'] if c['name'].startswith('Job: ')]),
              ('Channel permissions & roles', [c for c in report['core']
                if c['name'] not in runtime_names and not c['name'].startswith('Job: ')])]
    symbols = {'current': '✅', 'problem': '❌', 'warning': '🟠', 'unknown': '❔'}
    for label, checks in groups:
        lines = [f'{symbols.get(c["state"], "❔")} **{c["name"]}:** {c["detail"]}'
                 for c in checks]
        if label == 'Channel permissions & roles':
            healthy = sum(c['state'] == 'current' for c in checks)
            embed.add_field(name=f'{label} ({healthy}/{len(checks)} passing)',
                            value='\n'.join(lines)[:1024] or 'No checks', inline=False)
        else:
            embed.add_field(name=label, value='\n'.join(lines)[:1024], inline=False)
    embed.set_footer(text='Read-only • Checks are point-in-time • Recheck or repair panels from Admin Hub')
    return embed
