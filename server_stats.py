"""Public aggregate guild counters; no member details are published."""
import asyncio
import json
from datetime import datetime, timedelta

import discord

CATEGORY = '✦ GUILD STATS ✦'
LABELS = {'members': 'Total Members', 'xp': 'Total XP', 'paragon': 'Paragon Members'}
INTERVAL = timedelta(minutes=10)
_lock = asyncio.Lock()


def load(api):
    with api.connect() as db:
        row = db.execute('SELECT value FROM v12_config WHERE key=?',
                         (f'server-stats-{api.GUILD_ID}',)).fetchone()
    if row is None:
        return {'enabled': False, 'channels': {}}
    return json.loads(row['value'])


def save(api, config):
    with api.connect() as db:
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                   (f'server-stats-{api.GUILD_ID}', json.dumps(config)))


def describe(api):
    config = load(api)
    return ('📊 **Guild Stats**\n'
            f'Updates: **{"enabled" if config.get("enabled") else "paused / not configured"}**\n'
            'Total Members, Total XP and Paragon Members. Bots are excluded; XP is the current '
            'balance of current members. Counters refresh up to every 10 minutes.\n'
            'Create / Enable creates the locked voice channels or repairs the saved setup. '
            'Pause leaves the channels in place.')


def counts(api, guild):
    humans = [m for m in guild.members if not m.bot]
    ids = {m.id for m in humans}
    with api.connect() as db:
        xp = sum(row['xp'] for row in db.execute(
            'SELECT user_id,xp FROM members WHERE guild_id=?', (guild.id,)) if row['user_id'] in ids)
    paragon = sum(any(role.name == 'Paragon' for role in m.roles) for m in humans)
    return {'members': len(humans), 'xp': xp, 'paragon': paragon}


def name(key, value):
    return f'{LABELS[key]}: {value:,}'


async def complete_cache(guild):
    if not guild.chunked:
        await guild.chunk(cache=True)
    return guild.chunked


async def configure(api, guild):
    if guild.id != api.GUILD_ID:
        return 'Wrong server.'
    async with _lock:
        config = load(api)
        category = guild.get_channel(config.get('category', 0))
        if category is not None and not isinstance(category, discord.CategoryChannel):
            return 'Saved stats category is invalid. No channels changed.'
        try:
            if not await complete_cache(guild):
                return 'Member list is incomplete; try again when the bot is ready.'
            if category is None:
                # Only explicit staff setup may create channels. Avoid silently reusing
                # unrelated categories with the same title.
                if any(c.name == CATEGORY for c in guild.categories):
                    return 'A Guild Stats category already exists but is not registered. Contact an admin to reconcile it.'
                category = await guild.create_category(CATEGORY, overwrites={
                    guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
                    guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
                }, reason='GuildKeeper server counters')
                config = {'enabled': False, 'category': category.id, 'channels': {}}
                save(api, config)
            values = counts(api, guild)
            for key in LABELS:
                channel = guild.get_channel(config['channels'].get(key, 0))
                if channel is not None:
                    if not isinstance(channel, discord.VoiceChannel) or channel.category_id != category.id:
                        return 'A saved stats channel is invalid. No unrelated channel was changed.'
                    continue
                # A prior interrupted creation can be adopted only by exact counter prefix.
                matches = [c for c in category.voice_channels if c.name.startswith(LABELS[key] + ': ')]
                if len(matches) > 1:
                    return 'Duplicate stats channels found; remove the duplicate manually before setup.'
                channel = matches[0] if matches else await guild.create_voice_channel(
                    name(key, values[key]), category=category,
                    reason='GuildKeeper server counters')
                config['channels'][key] = channel.id
                save(api, config)
            config['enabled'] = True
            save(api, config)
        except discord.HTTPException:
            return 'Unable to configure stats. Check GuildKeeper Manage Channels permissions and try again.'
    return await refresh(api, guild)


async def pause(api):
    async with _lock:
        config = load(api)
        config['enabled'] = False
        save(api, config)
    return 'Guild Stats updates paused. Existing channels retained.'


async def refresh(api, guild):
    if guild is None or guild.id != api.GUILD_ID:
        return 'Configured server unavailable.'
    async with _lock:
        config = load(api)
        if not config.get('enabled'):
            return 'Guild Stats are not enabled.'
        now = api.utc_now()
        last = config.get('last_update')
        if last and now - datetime.fromisoformat(last) < INTERVAL:
            return 'Guild Stats enabled. Next refresh is within 10 minutes.'
        category = guild.get_channel(config.get('category', 0))
        if not isinstance(category, discord.CategoryChannel):
            return 'Stats category missing; use Create / Enable to repair.'
        if not await complete_cache(guild):
            return 'Member list incomplete; counters unchanged.'
        values = counts(api, guild)
        # Validate every destination before making any edits.
        channels = {key: guild.get_channel(config['channels'].get(key, 0)) for key in LABELS}
        if any(not isinstance(c, discord.VoiceChannel) or c.category_id != category.id
               for c in channels.values()):
            return 'Stats channel missing or moved; use Create / Enable to repair.'
        # Persist the cooldown before edits to avoid rate-limit loops after a partial failure.
        config['last_update'] = now.isoformat()
        save(api, config)
        try:
            for key, channel in channels.items():
                desired = name(key, values[key])
                if channel.name != desired:
                    await channel.edit(name=desired, reason='GuildKeeper stats refresh')
        except discord.HTTPException:
            return 'Stats refresh incomplete; check Manage Channels permission.'
        return 'Guild Stats updated: ' + ', '.join(name(k, v) for k, v in values.items())


async def tick(api):
    if not load(api).get('enabled'):
        return
    result = await refresh(api, api.bot.get_guild(api.GUILD_ID))
    if result.startswith(('Stats category missing', 'Stats channel missing', 'Stats refresh incomplete')):
        raise RuntimeError(result)
