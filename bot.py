"""GYMGUILD Guildkeeper V1 — Discord slash-command community bot."""
import asyncio
import logging
import os
import re
import random
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN', '')
GUILD_ID = int(os.getenv('GUILD_ID', '0'))
QUESTS_CHANNEL_ID = int(os.getenv('QUESTS_CHANNEL_ID', '0'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID', '0'))
LEVEL_UP_CHANNEL_ID = int(os.getenv('LEVEL_UP_CHANNEL_ID', '0'))
HQ_CATEGORY_ID = int(os.getenv('HQ_CATEGORY_ID', '0'))
TZ = ZoneInfo(os.getenv('TIMEZONE', 'Europe/London'))
DB = Path(__file__).with_name('guildkeeper.db')
RANKS = [('Novice', 50), ('Apprentice', 300), ('Adept', 500),
         ('Expert', 750), ('Master', 1000), ('Legend', 2500),
         ('Paragon', 5000)]
TRAINING_LOG_CHANNEL_ID = int(os.getenv('TRAINING_LOG_CHANNEL_ID', '0'))
GYM_CATEGORY_ID = int(os.getenv('GYM_CATEGORY_ID', '0'))
HOBBY_CATEGORY_ID = int(os.getenv('HOBBY_CATEGORY_ID', '0'))
REWARD_TICKETS_CATEGORY_ID = int(os.getenv('REWARD_TICKETS_CATEGORY_ID', '0'))
MOD_REVIEW_CHANNEL_ID = int(os.getenv('MOD_REVIEW_CHANNEL_ID', '0'))
MOD_ROLE_ID = int(os.getenv('MOD_ROLE_ID', '0'))
INTRODUCTIONS_CHANNEL_ID = int(os.getenv('INTRODUCTIONS_CHANNEL_ID', '0'))
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID', os.getenv('RULES_CHANNEL_ID', '0')))
WELCOME_MESSAGE_ID = int(os.getenv('WELCOME_MESSAGE_ID', os.getenv('RULES_MESSAGE_ID', '0')))
GET_ROLE_CHANNEL_ID = int(os.getenv('GET_ROLE_CHANNEL_ID', '0'))
SIDE_QUEST_CHANNEL_ID = int(os.getenv('SIDE_QUEST_CHANNEL_ID', os.getenv('QUESTS_CHANNEL_ID', '0')))
STARTER_CHANNEL_ID = int(os.getenv('STARTER_CHANNEL_ID', '0'))
STARTER_ACCESS_ROLE_ID = int(os.getenv('STARTER_ACCESS_ROLE_ID', '0'))
GUILD_BLAST_ROLE_ID = int(os.getenv('GUILD_BLAST_ROLE_ID', '0') or 0)
SPAWNPOINT_CHANNEL_ID = int(os.getenv('SPAWNPOINT_CHANNEL_ID', '0'))
LEADERBOARD_CHANNEL_ID = int(os.getenv('LEADERBOARD_CHANNEL_ID', '0'))
def role_ids(env_name):
    return {int(x.strip()) for x in os.getenv(env_name, '').split(',') if x.strip().isdigit() and int(x.strip()) > 0}
GYM_ROLE_IDS = role_ids('GYM_ROLE_IDS')
HOBBY_ROLE_IDS = role_ids('HOBBY_ROLE_IDS')
# Menu order follows the comma-separated role IDs in .env.
GYM_MENU_EMOJIS = ['🏋️', '🧘', '🏃', '🤺']
HOBBY_MENU_EMOJIS = ['🎮', '🎲', '🎬', '📚']

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('guildkeeper')


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def setup_db():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS rank_unlocks (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, rank TEXT NOT NULL, unlocked_at TEXT NOT NULL, PRIMARY KEY(guild_id,user_id,rank));
        CREATE TABLE IF NOT EXISTS rank_high_watermarks (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, rank TEXT NOT NULL, PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS admin_streak_weeks (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, week_key TEXT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY(guild_id,user_id,week_key));
        CREATE TABLE IF NOT EXISTS members (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          xp INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS quests (id INTEGER PRIMARY KEY AUTOINCREMENT,
          guild_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
          reward INTEGER NOT NULL, ends_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS objectives (id INTEGER PRIMARY KEY AUTOINCREMENT,
          quest_id INTEGER NOT NULL REFERENCES quests(id), title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS completions (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          objective_id INTEGER NOT NULL REFERENCES objectives(id), completed_at TEXT NOT NULL,
          PRIMARY KEY(guild_id,user_id,objective_id));
        CREATE TABLE IF NOT EXISTS claims (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          quest_id INTEGER NOT NULL REFERENCES quests(id), claimed_at TEXT NOT NULL,
          PRIMARY KEY(guild_id,user_id,quest_id));
        CREATE TABLE IF NOT EXISTS xp_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
          guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
          reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v12_config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS weekly_actions (guild_id INTEGER, user_id INTEGER, week_key TEXT,
          kind TEXT, action_day TEXT, message_id INTEGER UNIQUE, fingerprint TEXT,
          created_at TEXT, PRIMARY KEY(guild_id,user_id,week_key,kind));
        CREATE TABLE IF NOT EXISTS weekly_completions (guild_id INTEGER, user_id INTEGER,
          week_key TEXT, completed_at TEXT, PRIMARY KEY(guild_id,user_id,week_key));
        CREATE TABLE IF NOT EXISTS starter_claims (guild_id INTEGER, user_id INTEGER, kind TEXT,
          claimed_at TEXT, PRIMARY KEY(guild_id,user_id,kind));
        CREATE TABLE IF NOT EXISTS milestone_claims (guild_id INTEGER, user_id INTEGER, kind TEXT,
          claimed_at TEXT, PRIMARY KEY(guild_id,user_id,kind));
        CREATE TABLE IF NOT EXISTS pauses (guild_id INTEGER, user_id INTEGER, week_key TEXT,
          created_at TEXT, PRIMARY KEY(guild_id,user_id,week_key));
        CREATE TABLE IF NOT EXISTS resumed_pauses (guild_id INTEGER, user_id INTEGER, week_key TEXT,
          resumed_at TEXT, PRIMARY KEY(guild_id,user_id,week_key));
        CREATE TABLE IF NOT EXISTS weekly_settlements (guild_id INTEGER, user_id INTEGER, week_key TEXT,
          settled_at TEXT, PRIMARY KEY(guild_id,user_id,week_key));
        CREATE TABLE IF NOT EXISTS reward_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT,
          guild_id INTEGER, user_id INTEGER, rank TEXT, channel_id INTEGER,
          status TEXT, requested_at TEXT, fulfilled_at TEXT,
          UNIQUE(guild_id,user_id,rank));
        CREATE TABLE IF NOT EXISTS purchase_orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL, order_reference TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', submitted_at TEXT NOT NULL,
          reviewed_at TEXT, reviewed_by INTEGER,
          UNIQUE(guild_id,order_reference));
        CREATE TABLE IF NOT EXISTS pending_milestones (id INTEGER PRIMARY KEY AUTOINCREMENT,
          guild_id INTEGER, user_id INTEGER, kind TEXT, evidence TEXT,
          status TEXT DEFAULT 'pending', created_at TEXT,
          UNIQUE(guild_id,user_id,kind));
        CREATE TABLE IF NOT EXISTS message_fingerprints (guild_id INTEGER, user_id INTEGER,
          fingerprint TEXT, created_at TEXT, PRIMARY KEY(guild_id,user_id,fingerprint));
        CREATE TABLE IF NOT EXISTS role_menu_posts (
          guild_id INTEGER NOT NULL, menu_type TEXT NOT NULL,
          channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
          PRIMARY KEY(guild_id,menu_type));
        CREATE TABLE IF NOT EXISTS starter_board_posts (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message_id INTEGER);
        CREATE TABLE IF NOT EXISTS starter_turnins (guild_id INTEGER, user_id INTEGER, turned_in_at TEXT, PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS starter_posts (guild_id INTEGER, user_id INTEGER,
          channel_id INTEGER, message_id INTEGER, posted_at TEXT,
          PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS schedule_posts (guild_id INTEGER NOT NULL, post_key TEXT NOT NULL,
          PRIMARY KEY(guild_id,post_key));
        CREATE TABLE IF NOT EXISTS bot_dm_preferences (
          guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
          PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS bot_dm_reminders (
          guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          week_key TEXT NOT NULL, reminder_kind TEXT NOT NULL,
          attempted_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'attempted',
          PRIMARY KEY(guild_id,user_id,week_key,reminder_kind));
        ''')
        # Carry forward any historical first-purchase requests without touching
        # milestone claims, XP balances or existing moderator decisions.
        db.execute(
            'INSERT OR IGNORE INTO purchase_orders(guild_id,user_id,order_reference,status,submitted_at) '
            'SELECT guild_id,user_id,evidence,status,created_at FROM pending_milestones '
            "WHERE kind='purchase1' AND evidence IS NOT NULL AND TRIM(evidence) != '' "
            "AND status IN ('pending','approved','rejected')"
        )
        purchase_columns = {r['name'] for r in db.execute('PRAGMA table_info(purchase_orders)')}
        for col in ('review_channel_id', 'review_message_id'):
            if col not in purchase_columns:
                db.execute(f'ALTER TABLE purchase_orders ADD COLUMN {col} INTEGER')
        # Seed historic high-rank achievements before any new rank is awarded.
        # Keep the highest existing watermark on subsequent bot restarts.
        historic = db.execute(
            'SELECT guild_id, user_id, rank FROM rank_unlocks '
            "WHERE rank IN ('Master', 'Legend', 'Paragon') "
            "ORDER BY CASE rank WHEN 'Paragon' THEN 3 WHEN 'Legend' THEN 2 ELSE 1 END DESC"
        ).fetchall()
        for earned in historic:
            db.execute(
                'INSERT OR IGNORE INTO rank_high_watermarks(guild_id,user_id,rank) VALUES (?,?,?)',
                (earned['guild_id'], earned['user_id'], earned['rank']),
            )
        columns = {r['name'] for r in db.execute('PRAGMA table_info(reward_tickets)')}
        for col in ('closed_at', 'closed_by', 'close_reason'):
            if col not in columns:
                db.execute(f'ALTER TABLE reward_tickets ADD COLUMN {col} TEXT')
        db.execute('INSERT OR IGNORE INTO v12_config(key,value) VALUES (?,?)',('activation_week',week_key()))
        # Backfill the one-time Main Quest Completion reward for members who
        # completed all six milestones before this feature was introduced.
        # Existing milestone claims remain untouched; grant_once is idempotent.
        historic_members = db.execute(
            'SELECT DISTINCT guild_id,user_id FROM milestone_claims'
        ).fetchall()
        for prior in historic_members:
            award_guild_completion_if_ready(db, prior['guild_id'], prior['user_id'])


def migrate_monday_week_keys():
    """One-time non-destructive migration of Sunday-based V1.2 quest week identifiers.

    Existing XP, claims, quest records, and message IDs remain unchanged.
    Run after setup_db(), before Discord tasks start.
    """
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM v12_config WHERE key=?', ('monday_midnight_migrated',)).fetchone():
            return
        for table in ('weekly_actions', 'weekly_completions', 'pauses', 'weekly_settlements'):
            rows = db.execute(f'SELECT DISTINCT week_key FROM {table}').fetchall()
            for row in rows:
                old_key = row['week_key']
                old_day = datetime.fromisoformat(old_key).date()
                # Only legacy Sunday keys are shifted; never rewrite other dates.
                if old_day.weekday() == 6:
                    new_key = (old_day + timedelta(days=1)).isoformat()
                    db.execute(f'UPDATE OR IGNORE {table} SET week_key=? WHERE week_key=?', (new_key, old_key))
        activation = db.execute('SELECT value FROM v12_config WHERE key=?', ('activation_week',)).fetchone()
        if activation and datetime.fromisoformat(activation['value']).weekday() == 6:
            new_activation = (datetime.fromisoformat(activation['value']).date() + timedelta(days=1)).isoformat()
            db.execute('UPDATE v12_config SET value=? WHERE key=?', (new_activation, 'activation_week'))
        db.execute('INSERT INTO v12_config(key,value) VALUES (?,?)', ('monday_midnight_migrated', utc_now().isoformat()))


def utc_now():
    return datetime.now(timezone.utc)


def completed_weeks(guild_id, user_id):
    with connect() as db:
        return db.execute('SELECT COUNT(*) n FROM weekly_completions WHERE guild_id=? AND user_id=?',
                          (guild_id,user_id)).fetchone()['n']


STREAK_REQUIREMENTS = {'Master': 2, 'Legend': 4, 'Paragon': 8}

def bot_dm_enabled(gid: int, uid: int) -> bool:
    """Reminders are enabled by default until explicitly disabled by a member."""
    with connect() as db:
        row = db.execute(
            'SELECT enabled FROM bot_dm_preferences WHERE guild_id=? AND user_id=?',
            (gid, uid),
        ).fetchone()
    return bool(row['enabled']) if row else True


def set_bot_dm_enabled(gid: int, uid: int, enabled: bool) -> None:
    with connect() as db:
        db.execute(
            'INSERT INTO bot_dm_preferences(guild_id,user_id,enabled) VALUES (?,?,?) '
            'ON CONFLICT(guild_id,user_id) DO UPDATE SET enabled=excluded.enabled',
            (gid, uid, int(enabled)),
        )


def bot_dm_preference_text(enabled: bool) -> str:
    if enabled:
        return (
            '🔔 **Bot DMs are ON.** GuildKeeper can privately remind you before '
            'your weekly streak is at risk or you could lose inactivity XP.\n\n'
            'To switch them off, open the **Player Hub → Bot DMs → Turn Off**.'
        )
    return (
        '🔕 **Bot DMs are OFF.** You won’t receive GuildKeeper streak or inactivity '
        'warnings. To turn them on, open the **Player Hub → Bot DMs → Turn On**.'
    )


def streak_weeks(gid, uid):
    with connect() as db:
        completed = {r['week_key'] for r in db.execute('SELECT week_key FROM weekly_completions WHERE guild_id=? AND user_id=?', (gid,uid))}
        completed |= {r['week_key'] for r in db.execute('SELECT week_key FROM admin_streak_weeks WHERE guild_id=? AND user_id=?', (gid,uid))}
        paused = {r['week_key'] for r in db.execute(
            'SELECT p.week_key FROM pauses p WHERE p.guild_id=? AND p.user_id=? '
            'AND NOT EXISTS (SELECT 1 FROM resumed_pauses r '
            'WHERE r.guild_id=p.guild_id AND r.user_id=p.user_id AND r.week_key=p.week_key)',
            (gid, uid))}
    return completed, paused

def active_streak(gid, uid):
    completed, paused = streak_weeks(gid,uid)
    cursor = week_start()
    # An unfinished current week doesn't interrupt the completed streak yet.
    if cursor.date().isoformat() not in completed:
        cursor -= timedelta(days=7)
    count = 0
    for _ in range(156):
        key = cursor.date().isoformat()
        if key in completed:
            count += 1
        elif key not in paused:
            break
        cursor -= timedelta(days=7)
    return count

def unlock_earned_ranks(gid, uid, xp):
    """Persist earned ranks while an active streak qualifies; no rank loss from broken streaks."""
    if gid is None or uid is None:
        return
    current = active_streak(gid, uid)
    with connect() as db:
        for name, threshold in RANKS:
            if name in STREAK_REQUIREMENTS and xp >= threshold and current >= STREAK_REQUIREMENTS[name]:
                db.execute('INSERT OR IGNORE INTO rank_unlocks VALUES (?,?,?,?)', (gid,uid,name,utc_now().isoformat()))

def rank_for(xp, guild_id=None, user_id=None):
    """A completed Starter Quest permanently unlocks Novice regardless of later XP loss."""
    if guild_id is not None and user_id is not None:
        with connect() as db:
            onboarded = db.execute(
                'SELECT 1 FROM starter_turnins WHERE guild_id=? AND user_id=?',
                (guild_id, user_id),
            ).fetchone() is not None
        if not onboarded:
            return 'Unranked'
        unlock_earned_ranks(guild_id, user_id, xp)
        with connect() as db:
            unlocked = {r['rank'] for r in db.execute(
                'SELECT rank FROM rank_unlocks WHERE guild_id=? AND user_id=?',
                (guild_id, user_id),
            )}
    else:
        unlocked = set()
    for name, threshold in reversed(RANKS):
        if xp >= threshold and (name not in STREAK_REQUIREMENTS or name in unlocked):
            return name
    return 'Novice' if guild_id is not None and user_id is not None else 'Unranked'


def streak_blocked_rank_notice(gid: int, uid: int, xp: int) -> str:
    """Private explanation of earned XP ranks still locked by streak requirements."""
    # Refresh rank unlocks before reading permanent unlock history.
    if rank_for(xp, gid, uid) == 'Unranked':
        return ''
    with connect() as db:
        unlocked = {row['rank'] for row in db.execute(
            'SELECT rank FROM rank_unlocks WHERE guild_id=? AND user_id=?',
            (gid, uid),
        )}
    streak = active_streak(gid, uid)
    blocked = [
        (name, required)
        for name, threshold in RANKS
        if name in STREAK_REQUIREMENTS
        and xp >= threshold
        and name not in unlocked
        and (required := STREAK_REQUIREMENTS[name]) > streak
    ]
    if not blocked:
        return ''
    lines = ['🔒 **Streak requirement not yet met**',
             'You have enough XP, but still need an active weekly streak to unlock:']
    for name, required in blocked:
        lines.append(
            f'• **{name}** — **{streak}/{required} weeks** '
            f'(need **{required - streak}** more qualifying week(s)).'
        )
    lines.append(
        'Complete your Weekly Side Quest in consecutive weeks to build your streak. '
        'Paused weeks protect your streak but do not increase it. '
        'Once a rank is unlocked, you won\'t need to repeat its streak requirement.'
    )
    return '\n'.join(lines)


def lifetime_objectives(guild_id: int, user_id: int) -> int:
    """Count recorded Starter and Weekly Side Quest objectives, not bonus rewards.

    Each weekly objective counts once per week, so repeat achievements count.
    Reset objectives are excluded automatically because resets delete their records.
    """
    with connect() as db:
        starter = db.execute(
            'SELECT COUNT(*) AS n FROM starter_claims WHERE guild_id=? AND user_id=?',
            (guild_id, user_id)).fetchone()['n']
        weekly = db.execute(
            "SELECT COUNT(*) AS n FROM weekly_actions WHERE guild_id=? AND user_id=? "
            "AND kind IN ('training1', 'training2', 'gym', 'hobby')",
            (guild_id, user_id)).fetchone()['n']
    return starter + weekly


def get_xp(guild_id, user_id):
    with connect() as db:
        row = db.execute('SELECT xp FROM members WHERE guild_id=? AND user_id=?', (guild_id,user_id)).fetchone()
        return row['xp'] if row else 0


def add_xp(guild_id, user_id, amount, reason, db=None):
    """Use an existing transaction for quest claims to make rewards atomic."""
    if db is None:
        with connect() as conn:
            return add_xp(guild_id, user_id, amount, reason, conn)
    db.execute('INSERT OR IGNORE INTO members(guild_id,user_id,xp) VALUES (?,?,0)', (guild_id,user_id))
    db.execute('UPDATE members SET xp=MAX(0,xp+?) WHERE guild_id=? AND user_id=?',
               (amount,guild_id,user_id))
    db.execute('INSERT INTO xp_log(guild_id,user_id,amount,reason,created_at) VALUES (?,?,?,?,?)',
               (guild_id,user_id,amount,reason,utc_now().isoformat()))
    return db.execute('SELECT xp FROM members WHERE guild_id=? AND user_id=?',
                      (guild_id,user_id)).fetchone()['xp']


intents = discord.Intents.default()
intents.members = True  # Enable Server Members Intent in the Discord Developer Portal
intents.message_content = True  # Enable Message Content Intent for quest tracking
class Guildkeeper(commands.Bot):
    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.add_view(StarterTurnInView())
        self.add_view(WeeklyTurnInView())
        self.add_view(PlayerStatsView())
        self.add_view(MilestoneBoardView())
        self.add_view(RewardTicketAdminView())
        # Register outstanding review buttons after restarts.
        with connect() as db:
            pending_orders = db.execute(
                "SELECT id FROM purchase_orders WHERE guild_id=? AND status='pending'",
                (GUILD_ID,),
            ).fetchall()
        for order in pending_orders:
            self.add_view(PurchaseReviewView(order['id']))
        scheduler.start()

bot = Guildkeeper(command_prefix='!', intents=intents)

async def announce_new_guild_member(member: discord.Member) -> None:
    """Welcome members only after their completed starter quest unlocks the Guild."""
    if not LEVEL_UP_CHANNEL_ID:
        return
    channel = member.guild.get_channel(LEVEL_UP_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        log.warning('LEVEL_UP_CHANNEL_ID is not a text channel in this Guild')
        return
    stats = discord.utils.get(member.guild.text_channels, name='stats-and-streaks')
    stats_link = stats.mention if stats else '#stats-and-streaks'
    embed = discord.Embed(
        title='✦ New Guild Member! ✦',
        description=(
            'You have successfully joined the guild and advanced to Novice! '
            f'All channels are now unlocked. Check the {stats_link} channel any time '
            'to review your XP and progress!\n\n'
            f'✦ Current XP: **{get_xp(member.guild.id, member.id):,}**'
        ),
        colour=discord.Colour.from_rgb(104, 163, 153),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    try:
        await channel.send(
            content=f'🎉 Congratulations {member.mention} — **Novice**!',
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not post new Guild Member announcement for %s', member.id)


async def announce_level_up(member: discord.Member, old_rank: str, new_rank: str, xp: int) -> None:
    """Celebrate genuine promotions, with HQ invitations for Master and above."""
    if not LEVEL_UP_CHANNEL_ID:
        return
    channel = member.guild.get_channel(LEVEL_UP_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        log.warning('LEVEL_UP_CHANNEL_ID is not a text channel in this Guild')
        return
    embed = discord.Embed(
        title='✦ LEVEL UP! ✦',
        description=(
            f'You advanced from **{old_rank}** to **{new_rank}**!\n\n'
            f'✦ Current XP: **{xp:,}**'
        ),
        colour=discord.Colour.from_rgb(104, 163, 153),
    )
    if new_rank in {'Master', 'Legend', 'Paragon'}:
        hq = member.guild.get_channel(HQ_CATEGORY_ID) if HQ_CATEGORY_ID else None
        destination = hq.mention if isinstance(hq, discord.CategoryChannel) else '**✦ GYMGUILD HQ ✦**'
        embed.add_field(
            name='🔐 GYMGUILD HQ UNLOCKED',
            value=(
                f'Your new rank gives you access to {destination}! '
                'Help shape upcoming products through exclusive previews, surveys '
                'and design discussions. HQ access lasts while your current rank '
                'is Master or higher.'
            ),
            inline=False,
        )
    embed.set_thumbnail(url=member.display_avatar.url)
    try:
        await channel.send(
            content=f'🎉 Congratulations {member.mention} — **{new_rank}**!',
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not post level-up for member %s', member.id)


def record_new_rank_milestones(
    gid: int, uid: int, current: str, previous: str | None,
) -> list[tuple[str, str]]:
    """Record the highest ever reached rank and list every newly crossed rank.

    A single large XP award can pass multiple thresholds. Each newly crossed
    rank gets its own first-time announcement, while a later demotion/recovery
    cannot replay already reached milestones.
    """
    rank_order = {name: i for i, (name, _) in enumerate(RANKS)}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(
            'SELECT rank FROM rank_high_watermarks WHERE guild_id=? AND user_id=?',
            (gid, uid),
        ).fetchone()
        baseline = row['rank'] if row else previous
        if previous and (baseline is None or rank_order[previous] > rank_order[baseline]):
            baseline = previous
        old_index = rank_order[baseline] if baseline is not None else rank_order[current]
        new_index = rank_order[current]
        crossed = [
            (RANKS[index - 1][0], RANKS[index][0])
            for index in range(max(old_index + 1, 1), new_index + 1)
        ]
        highest = current if baseline is None or new_index > old_index else baseline
        db.execute(
            'INSERT INTO rank_high_watermarks(guild_id,user_id,rank) VALUES (?,?,?) '
            'ON CONFLICT(guild_id,user_id) DO UPDATE SET rank=excluded.rank',
            (gid, uid, highest),
        )
    return crossed


def record_highest_rank(gid: int, uid: int, current: str, previous: str | None) -> bool:
    """Backward-compatible boolean helper for existing rank regression tests."""
    return bool(record_new_rank_milestones(gid, uid, current, previous))


async def update_rank(member: discord.Member, xp: int, announce: bool = True) -> bool:
    """Sync rank roles and celebrate only new personal-best ranks, never repeats."""
    desired = rank_for(xp, member.guild.id, member.id)
    rank_order = {name: i for i, (name, _) in enumerate(RANKS)}
    rank_names = set(rank_order)
    roles = {role.name: role for role in member.guild.roles if role.name in rank_names}
    member_access = member.guild.get_role(STARTER_ACCESS_ROLE_ID) if STARTER_ACCESS_ROLE_ID else None
    # Starter Quest role is the gate: newcomers remain unranked, even if
    # they have earned XP from starter objectives before turning the quest in.
    qualified = member_access is not None and member_access in member.roles
    target = roles.get(desired) if qualified else None
    if qualified and desired != 'Unranked' and target is None:
        log.warning('Missing Discord rank role %s; create it in Server Settings > Roles', desired)
        return False
    previous = [role.name for role in member.roles if role.name in rank_names]
    old_rank = max(previous, key=rank_order.get) if previous else None
    old = [role for role in member.roles if role.name in rank_names and role != target]
    try:
        # One edit avoids a temporary gap between removing and adding ranks.
        if old or (target is not None and target not in member.roles):
            new_roles = [role for role in member.roles if role not in old]
            if target is not None and target not in new_roles:
                new_roles.append(target)
            await member.edit(roles=new_roles, reason='Guildkeeper XP rank synchronisation')
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not update rank for member %s; check Manage Roles and role order', member.id)
        return False

    # No rank history/announcements until the starter access role is granted.
    if target is None:
        return True
    try:
        new_milestones = record_new_rank_milestones(member.guild.id, member.id, desired, old_rank)
    except sqlite3.Error:
        log.exception('Could not record highest rank for member %s', member.id)
        return True
    if announce:
        for from_rank, to_rank in new_milestones:
            await announce_level_up(member, from_rank, to_rank, xp)
    return True


async def sync_all_ranks(guild: discord.Guild) -> tuple[int, int]:
    """Fetch all members, including those not yet cached, and reconcile ranks."""
    updated = failed = 0
    async for member in guild.fetch_members(limit=None):
        if member.bot:
            continue
        if await update_rank(member, get_xp(guild.id, member.id), announce=False):
            updated += 1
        else:
            failed += 1
    return updated, failed


SPAWNPOINT_GREETINGS = (
    'A new adventurer has entered the realm!',
    'A challenger approaches the Guild gates!',
    'Your adventure begins here, hero!',
    'A fresh character has spawned into the Guild!',
    'The party just gained a new member!',
    'A new legend is loading in!',
    'The Guild welcomes its newest recruit!',
    'A new player has joined the quest!',
    'Another hero has answered the call!',
    'A new chapter begins for our latest adventurer!',
    'The respawn point has a new arrival!',
    'A new ally has entered the arena!',
)


async def send_spawnpoint_welcome(member: discord.Member):
    """Post one public welcome embed, mentioning the joining member."""
    if not SPAWNPOINT_CHANNEL_ID:
        return False
    channel = member.guild.get_channel(SPAWNPOINT_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        log.warning('SPAWNPOINT_CHANNEL_ID does not identify a text channel in %s', member.guild.id)
        return False
    starter = f'<#{STARTER_CHANNEL_ID}>' if STARTER_CHANNEL_ID else '#start-here'
    embed = discord.Embed(
        title='✦ WELCOME TO GYMGUILD ✦',
        description=(f'**{random.choice(SPAWNPOINT_GREETINGS)}**\n\n'
                     f'⚔️ **Begin your Starter Quest**\n'
                     f'Head to {starter} to begin your initial quest. '
                     'Complete all five objectives, then press **Turn In Quest** '
                     'to unlock the rest of the Guild.\n\n'
                     'Complete weekly quests, get rewarded, and level up IRL.'),
        color=0x71A7A2,
    )
    embed.set_footer(text='GYMGUILD ✦ Where culture meets strength')
    try:
        await channel.send(
            content=f'Welcome to the Guild, {member.mention}! {random.choice(("⚔️", "🛡️", "🎮", "🏰", "✨", "🐉", "🏆", "🗡️"))}',
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        return True
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not send spawnpoint welcome for user %s', member.id)
        return False


@bot.tree.command(name='admin_spawnpoint_test', description='Moderator: test the random Spawnpoint welcome embed')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_spawnpoint_test(interaction: discord.Interaction, member: discord.Member | None = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild_id != GUILD_ID:
        return await interaction.followup.send('This command is only available in the configured Guild.', ephemeral=True)
    if not SPAWNPOINT_CHANNEL_ID:
        return await interaction.followup.send('Set SPAWNPOINT_CHANNEL_ID in .env first.', ephemeral=True)
    channel = interaction.guild.get_channel(SPAWNPOINT_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.followup.send('The configured Spawnpoint channel could not be found.', ephemeral=True)
    target = member or interaction.user
    try:
        sent = await send_spawnpoint_welcome(target)
        if not sent:
            return await interaction.followup.send('Could not send the welcome. Check channel permissions and the bot terminal.', ephemeral=True)
    except Exception:
        log.exception('Spawnpoint test failed for member %s', target.id)
        return await interaction.followup.send('The welcome test failed; check the bot terminal.', ephemeral=True)
    await interaction.followup.send(
        f'Welcome test sent to {channel.mention} for {target.mention}. '
        'This is a public test message; it does not reset quests, award XP, or change roles.',
        ephemeral=True,
    )


def guildkeeper_welcome_dm() -> str:
    """One short onboarding DM; never gate access on Discord DM permissions."""
    start_here = f'<#{STARTER_CHANNEL_ID}>' if STARTER_CHANNEL_ID else '#start-here'
    player_hub = (f'<#{PLAYER_STATS_CHANNEL_ID}>' if PLAYER_STATS_CHANNEL_ID
                  else '#stats-and-streaks')
    return (
        '🛡️ **Welcome to GYMGUILD!** ✦\n'
        f'Head to {start_here} and complete your **Starter Quest** to unlock '
        'all Guild channels.\n\n'
        'GuildKeeper reminders are on by default. After completing the Starter Quest, '
        f'you can switch them off in {player_hub} → **Player Hub → Bot DMs → Turn Off**.'
    )


async def send_guildkeeper_welcome_dm(member: discord.Member) -> bool:
    """Welcome new members once per join, without interrupting the public welcome."""
    if member.bot or member.guild.id != GUILD_ID or not bot_dm_enabled(member.guild.id, member.id):
        return False
    try:
        await member.send(guildkeeper_welcome_dm())
        return True
    except (discord.Forbidden, discord.HTTPException):
        log.info('Welcome DM unavailable for user %s', member.id)
        return False


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id == GUILD_ID and not member.bot:
        await send_spawnpoint_welcome(member)
        await send_guildkeeper_welcome_dm(member)
        await update_rank(member, get_xp(member.guild.id, member.id), announce=False)
        current_roles = {r.id for r in member.roles}
        if GYM_ROLE_IDS and current_roles & GYM_ROLE_IDS:
            await award_starter(member,'class')
        if HOBBY_ROLE_IDS and current_roles & HOBBY_ROLE_IDS:
            await award_starter(member,'hobby')


def command_directory_lines(admin_only: bool) -> list[str]:
    """Build command directory from registered slash commands, so descriptions stay current."""
    commands_found = []
    for command in bot.tree.get_commands():
        if not isinstance(command, app_commands.Command):
            continue
        is_admin = command.name.startswith('admin_')
        if is_admin != admin_only:
            continue
        description = command.description or 'No description provided.'
        commands_found.append((command.name, description))
    return [f'**/{name}** — {description}' for name, description in sorted(commands_found)]


async def send_command_directory(interaction: discord.Interaction, admin_only: bool):
    """Show one Discord embed field per command; no newline escape strings."""
    commands_found = []
    for command in bot.tree.get_commands():
        if not isinstance(command, app_commands.Command):
            continue
        if command.name.startswith('admin_') != admin_only:
            continue
        commands_found.append((command.name, command.description or 'No description provided.'))
    commands_found.sort(key=lambda item: item[0])
    title = '✦ GUILDKEEPER — ADMIN COMMANDS' if admin_only else '✦ GUILDKEEPER — MEMBER COMMANDS'
    intro = ('Commands for Guild moderators and administrators.' if admin_only
             else 'Commands available to Guild members.')
    embeds = []
    for start in range(0, len(commands_found), 20):
        page = commands_found[start:start + 20]
        embed = discord.Embed(title=title, description=intro if start == 0 else None, color=0x71A7A2)
        for name, description in page:
            embed.add_field(name=f'/{name}', value=description[:1024], inline=False)
        embed.set_footer(text='GYMGUILD ✦ Where culture meets strength')
        embeds.append(embed)
    if not embeds:
        embeds.append(discord.Embed(title=title, description='No commands found.', color=0x71A7A2))
    await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)


@bot.tree.command(name='commands', description='List all member commands and what they do')
async def member_commands_directory(interaction: discord.Interaction):
    await send_command_directory(interaction, admin_only=False)


@bot.tree.command(name='admin_commands', description='List all administrator commands and what they do')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_commands_directory(interaction: discord.Interaction):
    await send_command_directory(interaction, admin_only=True)


@bot.tree.command(name='admin_sync_ranks', description='Moderator: synchronise all member XP rank roles')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_sync_ranks(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        updated, failed = await sync_all_ranks(interaction.guild)
        await interaction.followup.send(
            f'✦ Rank sync finished. Checked **{updated + failed}** members. '
            f'Successful: **{updated}** · Could not update: **{failed}**.', ephemeral=True)
    except discord.HTTPException:
        log.exception('Rank sync failed')
        await interaction.followup.send('Rank sync failed. Check the bot log and Server Members Intent.', ephemeral=True)


async def reply(interaction: discord.Interaction, content: str, ephemeral=True):
    await interaction.response.send_message(content, ephemeral=ephemeral)


def only_guild(interaction):
    return interaction.guild is not None and interaction.guild.id == GUILD_ID

@bot.tree.command(name='profile', description='See your Guild XP and rank')
@app_commands.guild_only()
async def profile(interaction: discord.Interaction):
    xp = get_xp(interaction.guild_id, interaction.user.id)
    next_rank = next(((name, threshold) for name,threshold in RANKS if threshold>xp), None)
    upcoming = f'Next: **{next_rank[0]}** in **{next_rank[1]-xp} XP**' if next_rank else 'Maximum rank reached ✦'
    streak_notice = streak_blocked_rank_notice(interaction.guild_id, interaction.user.id, xp)
    await reply(interaction, f'✦ **{interaction.user.display_name}**\nRank: **{rank_for(xp, interaction.guild_id, interaction.user.id)}**\nXP: **{xp}**\nLifetime objectives completed: **{lifetime_objectives(interaction.guild_id, interaction.user.id)}**\nLongest streak: **{longest_streak(interaction.guild_id, interaction.user.id)} weeks**\n\n**Rewards**\n{reward_eligibility_text(interaction.guild_id, interaction.user.id)}\n{upcoming}' + ('\n\n' + streak_notice if streak_notice else '') + '\n\n🎟️ To request an unlocked rank reward, visit **#stats-and-streaks** and press **Claim Reward** in the Player Hub.')

@bot.tree.command(name='ranks', description='See Guild XP milestones')
async def ranks(interaction: discord.Interaction):
    xp = get_xp(interaction.guild_id, interaction.user.id)
    streak_notice = streak_blocked_rank_notice(interaction.guild_id, interaction.user.id, xp)
    await reply(interaction, '**GYMGUILD RANKS**\n'+'\n'.join(f'✦ **{name}** — {threshold:,} XP' + (f' + {STREAK_REQUIREMENTS[name]}-week **active** streak to unlock' if name in STREAK_REQUIREMENTS else '') for name,threshold in RANKS) + '\n\n**Rank rules:** Streaks must be active when unlocking Master, Legend or Paragon. Once unlocked, breaking your streak will not demote you; losing XP below a rank threshold can. Regaining XP restores previously unlocked ranks without repeating the streak.\n**Rewards:** Master (10%), Legend (15%) and Paragon (20%) are unlocked permanently when their rank requirements are first met. Each reward can be claimed **once**, even after an XP demotion. Paused weeks preserve streaks but do not increase them.' + ('\n\n' + streak_notice if streak_notice else ''))



@bot.tree.command(name='quest_progress', description='View objectives for a quest')
@app_commands.describe(quest_id='Quest number from /quests')
async def quest_progress(interaction: discord.Interaction, quest_id: int):
    with connect() as db:
        q = db.execute('SELECT * FROM quests WHERE id=? AND guild_id=?', (quest_id,interaction.guild_id)).fetchone()
        if not q:
            return await reply(interaction, 'Quest not found.')
        rows = db.execute('''SELECT o.id,o.title,c.completed_at FROM objectives o LEFT JOIN completions c
            ON c.objective_id=o.id AND c.guild_id=? AND c.user_id=? WHERE o.quest_id=? ORDER BY o.id''',
            (interaction.guild_id,interaction.user.id,quest_id)).fetchall()
        claimed = db.execute('SELECT 1 FROM claims WHERE guild_id=? AND user_id=? AND quest_id=?',
                             (interaction.guild_id,interaction.user.id,quest_id)).fetchone()
    msg = '\n'.join(f'{"✅" if r["completed_at"] else "⬜"} **Objective {r["id"]}:** {r["title"]}' for r in rows)
    status = 'Claimed ✦' if claimed else ('Expired' if q['ends_at']<=utc_now().isoformat() else 'Not yet claimed')
    await reply(interaction, f'**{q["title"]}**\n{msg}\n\nReward: **{q["reward"]} XP** · {status}\nUse `/quest_complete` with an objective number, then `/quest_claim`.', ephemeral=True)

@bot.tree.command(name='quest_complete', description='Legacy quest completion information')
async def quest_complete(interaction: discord.Interaction, objective_id: int):
    await reply(interaction, 'V1.2 requires evidence. Post in #training-log or the Class/Proficiency categories instead. Legacy quests can be administered by moderators.')


@bot.tree.command(name='admin_quest_create', description='Moderator: create a quest with up to 3 objectives')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(title='Quest title', description='Quest description', objective_1='First objective',
                       objective_2='Second objective', objective_3='Optional third objective',
                       reward='XP for completing all objectives', days='Days until deadline')
async def admin_quest_create(interaction: discord.Interaction, title: str, description: str,
                             objective_1: str, objective_2: str, reward: app_commands.Range[int,1,500]=100,
                             days: app_commands.Range[int,1,30]=7, objective_3: str | None=None):
    deadline = (utc_now()+timedelta(days=days)).isoformat()
    with connect() as db:
        cur = db.execute('INSERT INTO quests(guild_id,title,description,reward,ends_at) VALUES (?,?,?,?,?)',
                         (interaction.guild_id,title,description,reward,deadline))
        qid = cur.lastrowid
        for objective in (objective_1,objective_2,objective_3):
            if objective:
                db.execute('INSERT INTO objectives(quest_id,title) VALUES (?,?)',(qid,objective))
    await reply(interaction, f'📜 Quest **#{qid}: {title}** created! Members can use `/quests` and `/quest_progress`.')
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if isinstance(channel,discord.TextChannel) and channel.guild.id==interaction.guild_id:
        await channel.send(f'📜 **NEW GUILD QUEST — #{qid}: {discord.utils.escape_markdown(title)}**\n{discord.utils.escape_mentions(description)}\nReward: **{reward} XP**\nUse `/quest_progress quest_id:{qid}` to begin.', allowed_mentions=discord.AllowedMentions.none())

@bot.tree.command(name='admin_streak_add', description='Moderator: credit consecutive streak weeks to a member (no XP awarded)')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(member='Member to credit', weeks='Number of consecutive completed weeks to credit, ending this week')
async def admin_streak_add(interaction: discord.Interaction, member: discord.Member,
                           weeks: app_commands.Range[int,1,52]):
    if interaction.guild_id != GUILD_ID:
        return await reply(interaction, 'This command is only available in the configured Guild.')
    await interaction.response.defer(ephemeral=True)
    start = week_start()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        added = 0
        for i in range(weeks):
            key = (start - timedelta(weeks=i)).date().isoformat()
            result = db.execute('INSERT OR IGNORE INTO admin_streak_weeks VALUES (?,?,?,?)',
                                (interaction.guild_id,member.id,key,utc_now().isoformat()))
            added += result.rowcount
    milestones = award_earned_streak_milestones(interaction.guild_id, member.id)
    await update_rank(member, get_xp(interaction.guild_id, member.id))
    await interaction.followup.send(
        f'🔥 Credited **{added}** additional streak week(s) to {member.mention} '
        f'(requested {weeks} consecutive weeks, ending this week). '
        f'Active streak: **{active_streak(interaction.guild_id,member.id)}** weeks. '
        f'Rank: **{rank_for(get_xp(interaction.guild_id,member.id),interaction.guild_id,member.id)}**. '
        + (f'Unlocked **{len(milestones)}** streak milestone(s) with their one-time XP rewards.'
           if milestones else 'No new streak milestone rewards were due.'),
        ephemeral=True)

def reset_rank_test_data(gid: int, uid: int) -> int:
    """Reset the member's rank testing progress to permanent Novice.

    Seed completed onboarding for this explicit admin test action so starter
    objectives cannot be claimed a second time. Preserve unrelated history,
    streaks, XP audit records and existing reward tickets.
    """
    novice_xp = dict(RANKS)['Novice']
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO members(guild_id,user_id,xp) VALUES (?,?,0)', (gid, uid))
        db.execute('UPDATE members SET xp=? WHERE guild_id=? AND user_id=?', (novice_xp, gid, uid))
        db.execute(
            'INSERT OR IGNORE INTO starter_turnins(guild_id,user_id,turned_in_at) VALUES (?,?,?)',
            (gid, uid, utc_now().isoformat()),
        )
        for kind in STARTER_REWARDS:
            db.execute(
                'INSERT OR IGNORE INTO starter_claims(guild_id,user_id,kind,claimed_at) VALUES (?,?,?,?)',
                (gid, uid, kind, utc_now().isoformat()),
            )
        db.execute('DELETE FROM rank_unlocks WHERE guild_id=? AND user_id=?', (gid, uid))
        db.execute('DELETE FROM rank_high_watermarks WHERE guild_id=? AND user_id=?', (gid, uid))
    return novice_xp


@bot.tree.command(name='admin_rank_test_reset',
                  description='Admin testing: return to Novice and replay one-time rank-ups')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(confirm='Reset YOUR XP to Novice and replay rank announcements')
async def admin_rank_test_reset(interaction: discord.Interaction, confirm: bool):
    """Self-only test reset, without replaying onboarding or touching other members."""
    if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message('Use this in the GYMGUILD server.', ephemeral=True)
    if not confirm:
        return await interaction.response.send_message(
            'No changes made. Set **confirm: True** to return to **Novice (50 XP)** '
            'and reset your one-time rank announcements. Starter Quest progress, '
            'Guild Member access, weekly activity, XP audit history and reward tickets are preserved.',
            ephemeral=True,
        )
    member = interaction.user
    guild = member.guild
    novice = discord.utils.get(guild.roles, name='Novice')
    access = guild.get_role(STARTER_ACCESS_ROLE_ID) if STARTER_ACCESS_ROLE_ID else None
    if novice is None or access is None:
        return await interaction.response.send_message(
            'Reset cancelled: the Novice or Guild Member role is missing. '
            'Check the Discord roles and STARTER_ACCESS_ROLE_ID.',
            ephemeral=True,
        )
    rank_names = {name for name, _ in RANKS}
    original_roles = list(member.roles)
    new_roles = [role for role in original_roles if role.name not in rank_names]
    for required_role in (access, novice):
        if required_role not in new_roles:
            new_roles.append(required_role)
    await interaction.response.defer(ephemeral=True)
    try:
        await member.edit(
            roles=new_roles, reason='Admin self-reset to Novice for level-up tests',
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not set Novice/Guild Member roles for %s', member.id)
        return await interaction.followup.send(
            'Reset cancelled: GuildKeeper could not assign Novice and Guild Member. '
            'Check Manage Roles and GuildKeeper role hierarchy; your XP and history are unchanged.',
            ephemeral=True,
        )
    try:
        xp = reset_rank_test_data(interaction.guild_id, member.id)
        # Create the Novice baseline without posting a fake promotion.
        # The next higher rank will be a new first-time announcement.
        record_new_rank_milestones(interaction.guild_id, member.id, 'Novice', None)
    except sqlite3.Error:
        log.exception('Could not reset rank test data for %s', member.id)
        try:
            await member.edit(
                roles=original_roles, reason='Restore roles after failed rank test reset',
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception('Could not restore roles after rank test reset failure for %s', member.id)
        return await interaction.followup.send(
            'Database reset failed; the bot attempted to restore your original roles. '
            'Check GuildKeeper logs before trying again.',
            ephemeral=True,
        )
    await interaction.followup.send(
        f'♻️ **Rank testing reset complete!** You are **Novice** with **{xp} XP**.\n\n'
        'You kept **Guild Member** access and do not need to repeat the Starter Quest. '
        'Your first-time rank announcement history and higher-rank unlocks are reset, '
        'so you can test promotions again using **/admin_xp_award**.\n\n'
        '**Preserved:** Starter Quest completion, weekly activities and streaks, '
        'past XP log, other roles and discount reward tickets. '
        'Your XP balance is now 50.',
        ephemeral=True,
    )


@bot.tree.command(name='admin_xp_award', description='Moderator: award bonus XP to a member')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_xp_award(interaction: discord.Interaction, member: discord.Member,
                         amount: app_commands.Range[int,1,500], reason: str):
    xp = add_xp(interaction.guild_id,member.id,amount,f'Moderator {interaction.user.id}: {reason}')
    await update_rank(member,xp)
    await reply(interaction, f'✦ Awarded **{amount} XP** to {member.mention}. New total: **{xp}**.')

@bot.tree.command(name='admin_xp_remove', description='Moderator: remove XP from a member')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_xp_remove(interaction: discord.Interaction, member: discord.Member,
                          amount: app_commands.Range[int,1,500], reason: str):
    xp = add_xp(interaction.guild_id,member.id,-amount,f'Moderator {interaction.user.id}: {reason}')
    await update_rank(member,xp)
    await reply(interaction,f'✦ Removed **{amount} XP** from {member.mention}. New total: **{xp}**.')

# Quest weeks begin Monday 00:00 Europe/London (GMT/BST automatically).
def week_start(dt=None):
    local = (dt or utc_now()).astimezone(TZ)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday, datetime.min.time(), TZ)


def week_key(dt=None):
    return week_start(dt).date().isoformat()


def next_reset(dt=None):
    return datetime.combine(week_start(dt).date() + timedelta(days=7), datetime.min.time(), TZ)


def cycle_day(dt=None):
    return (dt or utc_now()).astimezone(TZ).date().isoformat()


def is_paused(gid, uid, wk):
    with connect() as db:
        return bool(db.execute('''SELECT 1 FROM pauses p WHERE p.guild_id=? AND p.user_id=? AND p.week_key=?
            AND NOT EXISTS (SELECT 1 FROM resumed_pauses r WHERE r.guild_id=p.guild_id
            AND r.user_id=p.user_id AND r.week_key=p.week_key)''', (gid,uid,wk)).fetchone())


def weekly_status(gid, uid, wk):
    with connect() as db:
        kinds = {r['kind'] for r in db.execute('SELECT kind FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?',(gid,uid,wk))}
    return kinds


MILESTONE_QUESTS = (
    ('first_week', 'First Weekly Quest', 25, 'Complete your first Weekly Side Quest'),
    ('streak4', 'Four-Week Streak', 75, 'Complete 4 consecutive weekly quests'),
    ('streak8', 'Eight-Week Streak', 125, 'Complete 8 consecutive weekly quests'),
    ('streak12', 'Twelve-Week Streak', 175, 'Complete 12 consecutive weekly quests'),
    ('boost1', 'Guild Booster', 100, 'Boost the GYMGUILD Discord server once'),
    ('purchase1', 'First Purchase', 250, 'Have your first GYMGUILD order approved'),
)
MILESTONE_XP = {kind: xp for kind, _, xp, _ in MILESTONE_QUESTS}
MILESTONE_EMOJI = {
    'first_week': '🎯', 'streak4': '🔥', 'streak8': '⚡',
    'streak12': '🏆', 'boost1': '💎', 'purchase1': '🛍️',
}
MILESTONE_COMPLETION_BONUS = 500


def milestone_progress(gid: int, uid: int) -> dict:
    with connect() as db:
        claimed = {r['kind'] for r in db.execute(
            'SELECT kind FROM milestone_claims WHERE guild_id=? AND user_id=?',
            (gid, uid),
        )}
        completed_weeks = db.execute(
            'SELECT COUNT(*) FROM weekly_completions WHERE guild_id=? AND user_id=?',
            (gid, uid),
        ).fetchone()[0]
    return {
        'claimed': claimed,
        'completed': sum(kind in claimed for kind, *_ in MILESTONE_QUESTS),
        'total': len(MILESTONE_QUESTS),
        'weekly_completions': completed_weeks,
        'streak': active_streak(gid, uid),
        'bonus_earned': 'guild_completion' in claimed,
    }


def milestone_quests_embed(gid: int, uid: int) -> discord.Embed:
    progress = milestone_progress(gid, uid)
    orders = purchase_order_summary(gid, uid)
    embed = discord.Embed(
        title='✦ GYMGUILD — MILESTONE QUESTS ✦',
        description=(
            f'**Completed: {progress["completed"]}/{progress["total"]}** permanent milestones\n'
            f'Complete them all to receive a one-time **+{MILESTONE_COMPLETION_BONUS} XP** Main Quest Completion bonus! '
            'Your milestones remain unlocked even if you lose XP or your streak later breaks.'
        ),
        colour=discord.Colour.from_rgb(104, 163, 153),
    )
    for kind, label, xp, description in MILESTONE_QUESTS:
        achieved = kind in progress['claimed']
        detail = ''
        if kind == 'first_week' and not achieved:
            detail = f' ({progress["weekly_completions"]}/1 completed weeks)'
        elif kind.startswith('streak') and not achieved:
            weeks = int(kind.removeprefix('streak'))
            detail = f' (active streak: {progress["streak"]}/{weeks} weeks)'
        elif kind == 'purchase1':
            detail = (f' ({orders["pending"]} pending review)'
                      if orders['pending'] else
                      f' ({orders["approved"]} approved orders)')
        embed.add_field(
            name=f'{"✅" if achieved else "⬜"} {MILESTONE_EMOJI[kind]} {label} — {xp} XP',
            value=description + detail + '\n\u200b',
            inline=False,
        )
    embed.add_field(
        name='🏆 Main Quest Completion — +500 XP',
        value='✅ Claimed permanently!' if progress['bonus_earned']
              else f'🔒 {progress["completed"]}/{progress["total"]} milestones completed',
        inline=False,
    )
    embed.set_footer(text='Submit orders through the main Milestone Quests board.')
    return embed


def award_guild_completion_if_ready(db, gid: int, uid: int) -> bool:
    """Grant the six-milestone completion reward once, in the existing transaction."""
    kinds = {row['kind'] for row in db.execute(
        'SELECT kind FROM milestone_claims WHERE guild_id=? AND user_id=?',
        (gid, uid),
    )}
    if all(kind in kinds for kind, *_ in MILESTONE_QUESTS):
        return grant_once(
            db, 'milestone_claims', gid, uid,
            'guild_completion', MILESTONE_COMPLETION_BONUS,
        )
    return False


def grant_once(db, table, gid, uid, kind, amount):
    cur = db.execute(f'INSERT OR IGNORE INTO {table}(guild_id,user_id,kind,claimed_at) VALUES (?,?,?,?)',
                     (gid,uid,kind,utc_now().isoformat()))
    if cur.rowcount:
        add_xp(gid,uid,amount,kind,db)
        if table == 'milestone_claims' and kind != 'guild_completion':
            award_guild_completion_if_ready(db, gid, uid)
        return True
    return False


def reset_milestone_test_data(gid: int, uid: int, target: str) -> dict:
    """Undo this member's current test milestone rewards without touching others.

    Preserve order and XP audit history. Old review cards become inert because
    the order rows no longer have 'pending' status. The one-time completion
    bonus is also reversed if a prerequisite milestone is reset.
    """
    if target not in ('order', 'boost', 'both'):
        raise ValueError('Choose order, boost or both')
    kinds = (('purchase1', 'boost1') if target == 'both' else
             ('purchase1',) if target == 'order' else ('boost1',))
    result = {'order': 0, 'boost': 0, 'completion': 0, 'xp_reversed': 0, 'orders_archived': 0}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = {row['kind'] for row in db.execute(
            "SELECT kind FROM milestone_claims WHERE guild_id=? AND user_id=?",
            (gid, uid),
        )}
        to_reset = [kind for kind in kinds if kind in existing]
        if to_reset and 'guild_completion' in existing:
            to_reset.append('guild_completion')
        for kind in to_reset:
            # The claim is unique but the XP log is append-only across earlier
            # admin resets, so reverse only its most recent matching award.
            previous = db.execute(
                "SELECT amount FROM xp_log WHERE guild_id=? AND user_id=? "
                "AND reason=? AND amount>0 ORDER BY id DESC LIMIT 1",
                (gid, uid, kind),
            ).fetchone()
            amount = previous['amount'] if previous else 0
            db.execute(
                "DELETE FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind=?",
                (gid, uid, kind),
            )
            if amount:
                add_xp(gid, uid, -amount, f'Admin milestone test reset: {kind}', db)
                result['xp_reversed'] += amount
            if kind == 'purchase1':
                result['order'] = 1
            elif kind == 'boost1':
                result['boost'] = 1
            else:
                result['completion'] = 1
        if 'purchase1' in kinds:
            # Prevent legacy migration on the next startup from resurrecting
            # imported pending requests under their original order reference.
            db.execute(
                "UPDATE pending_milestones SET status='test_reset' "
                "WHERE guild_id=? AND user_id=? AND kind='purchase1'",
                (gid, uid),
            )
            # Do not destroy verification or moderator history. Archiving
            # every prior row prevents a stale approval button from granting XP.
            result['orders_archived'] = db.execute(
                "UPDATE purchase_orders SET status='test_reset', "
                "order_reference='[test reset ' || id || '] ' || order_reference "
                "WHERE guild_id=? AND user_id=? AND status IN ('pending','approved','rejected','cancelled')",
                (gid, uid),
            ).rowcount
    return result


@bot.tree.command(
    name='admin_milestone_test_reset',
    description='Admin test: reset your own Order XP, Server Boost XP, or both',
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
@app_commands.describe(
    target='Choose which of YOUR milestone awards to reset',
    confirm='Must be True to reset your milestone claim and reverse its XP',
)
@app_commands.choices(target=[
    app_commands.Choice(name='Order XP', value='order'),
    app_commands.Choice(name='Server Boost XP', value='boost'),
    app_commands.Choice(name='Both', value='both'),
])
async def admin_milestone_test_reset(
    interaction: discord.Interaction, target: app_commands.Choice[str], confirm: bool,
):
    if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            'This command is only available inside GYMGUILD.', ephemeral=True)
    if not confirm:
        return await interaction.response.send_message(
            'No changes made. Set **confirm: True** to reset **your own** selected '
            'milestone claim(s), reverse their recorded XP and (if applicable) '
            'the +500 XP Main Quest Completion bonus. For Order XP, old '
            'submission records are archived, not deleted; other progress stays intact. '
            'For Boost XP, an already-active boost must end before a new boost '
            'event can automatically award the milestone again.',
            ephemeral=True,
        )
    await interaction.response.defer(ephemeral=True)
    try:
        result = reset_milestone_test_data(
            interaction.guild_id, interaction.user.id, target.value)
    except sqlite3.Error:
        log.exception('Could not reset milestone test data for member %s', interaction.user.id)
        return await interaction.followup.send(
            'Reset failed: database unchanged by the unsuccessful transaction.',
            ephemeral=True,
        )
    await update_rank(interaction.user, get_xp(interaction.guild_id, interaction.user.id))
    await interaction.followup.send(
        f'🧪 **Milestone test reset: {target.name}**\n'
        f'Order claim cleared: **{"Yes" if result["order"] else "No claim found"}**\n'
        f'Boost claim cleared: **{"Yes" if result["boost"] else "No claim found"}**\n'
        f'Main Quest Completion claim reversed: **{"Yes" if result["completion"] else "Not needed"}**\n'
        f'Historical order requests archived: **{result["orders_archived"]}**\n'
        f'XP reversed: **{result["xp_reversed"]}**\n\n'
        'New Order XP requests can now be tested through Milestone Quests. '
        'To re-test automatic boost XP, Discord must emit a new boost-start '
        'event (an existing active boost does not trigger it).',
        ephemeral=True,
    )


# Quest auto-completion is exclusively for the invoking server admin's test account.
# Synthetic awards are clearly marked in the XP audit; real members still need
# their usual evidence, Shopify verification, and Discord boost event.
WEEKLY_TEST_XP = {'training1': 50, 'training2': 50, 'gym': 25, 'hobby': 25}


def admin_complete_weekly_test(gid: int, uid: int) -> dict:
    """Fill only missing current-week objectives and auto-turn in exactly once."""
    wk = week_key()
    awarded = 0
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute(
            "SELECT 1 FROM pauses p WHERE p.guild_id=? AND p.user_id=? AND p.week_key=? "
            "AND NOT EXISTS (SELECT 1 FROM resumed_pauses r WHERE r.guild_id=p.guild_id "
            "AND r.user_id=p.user_id AND r.week_key=p.week_key)",
            (gid, uid, wk),
        ).fetchone():
            return {'paused': True, 'objectives': 0, 'completion': False, 'streak_awards': []}
        for idx, (kind, xp) in enumerate(WEEKLY_TEST_XP.items()):
            exists = db.execute(
                'SELECT 1 FROM weekly_actions WHERE guild_id=? AND user_id=? '
                'AND week_key=? AND kind=?', (gid, uid, wk, kind),
            ).fetchone()
            if exists:
                continue
            # Generate unique negative placeholder message IDs; no Discord proof
            # message is fabricated. Never collide with a real message ID.
            least = db.execute(
                'SELECT MIN(message_id) AS lowest FROM weekly_actions'
            ).fetchone()['lowest']
            fake_id = min(0, least or 0) - 1
            action_day = (datetime.fromisoformat(wk).date()
                          + timedelta(days=idx)).isoformat()
            db.execute(
                'INSERT INTO weekly_actions VALUES (?,?,?,?,?,?,?,?)',
                (gid, uid, wk, kind, action_day, fake_id,
                 f'admin-test:{gid}:{uid}:{wk}:{kind}', utc_now().isoformat()),
            )
            add_xp(gid, uid, xp, f'Weekly {wk} {kind}', db)
            awarded += 1
            grant_once(db, 'starter_claims', gid, uid, 'first_steps',
                       STARTER_REWARDS['first_steps'])
        kinds = {r['kind'] for r in db.execute(
            'SELECT kind FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?',
            (gid, uid, wk),
        )}
        complete = False
        if set(WEEKLY_TEST_XP).issubset(kinds):
            complete = db.execute(
                'INSERT OR IGNORE INTO weekly_completions VALUES (?,?,?,?)',
                (gid, uid, wk, utc_now().isoformat()),
            ).rowcount > 0
            if complete:
                add_xp(gid, uid, 50, 'Weekly completion ' + wk, db)
                grant_once(db, 'milestone_claims', gid, uid, 'first_week',
                           MILESTONE_XP['first_week'])
    streak_awards = award_earned_streak_milestones(gid, uid) if complete else []
    return {'paused': False, 'objectives': awarded,
            'completion': complete, 'streak_awards': streak_awards}


def admin_complete_starter_test(gid: int, uid: int) -> dict:
    """Grant missing starter rewards without replaying a completed starter quest."""
    added = 0
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for kind, xp in STARTER_REWARDS.items():
            added += int(grant_once(db, 'starter_claims', gid, uid, kind, xp))
        turnin = db.execute(
            'INSERT OR IGNORE INTO starter_turnins VALUES (?,?,?)',
            (gid, uid, utc_now().isoformat()),
        ).rowcount > 0
    return {'objectives': added, 'turnin': turnin}


def admin_complete_milestones_test(gid: int, uid: int) -> dict:
    """Synthetic milestone claims for admin testing, including a labelled order.

    This deliberately does not assert a real boost occurred, nor modify weekly
    completion history. A pending genuine order review must be handled first.
    """
    awarded = []
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute(
            "SELECT 1 FROM purchase_orders WHERE guild_id=? AND user_id=? "
            "AND status='pending' LIMIT 1", (gid, uid),
        ).fetchone():
            return {'blocked': True, 'awarded': [], 'completion': False}
        old_completion = db.execute(
            "SELECT 1 FROM milestone_claims WHERE guild_id=? AND user_id=? "
            "AND kind='guild_completion'", (gid, uid),
        ).fetchone() is not None
        # Insert a synthetic, clearly labelled approved order only for the
        # admin account when awarding its first purchase milestone.
        has_purchase = db.execute(
            "SELECT 1 FROM milestone_claims WHERE guild_id=? AND user_id=? "
            "AND kind='purchase1'", (gid, uid),
        ).fetchone() is not None
        if not has_purchase:
            label = f'ADMIN-TEST-{gid}-{uid}-{int(utc_now().timestamp() * 1000000)}'
            db.execute(
                "INSERT INTO purchase_orders "
                "(guild_id,user_id,order_reference,status,submitted_at,reviewed_at,reviewed_by) "
                "VALUES (?,?,?,'approved',?,?,?)",
                (gid, uid, label, utc_now().isoformat(), utc_now().isoformat(), uid),
            )
        for kind, label, xp, description in MILESTONE_QUESTS:
            if grant_once(db, 'milestone_claims', gid, uid, kind, xp):
                awarded.append(kind)
        completion = not old_completion and db.execute(
            "SELECT 1 FROM milestone_claims WHERE guild_id=? AND user_id=? "
            "AND kind='guild_completion'", (gid, uid),
        ).fetchone() is not None
    return {'blocked': False, 'awarded': awarded, 'completion': completion}


def is_admin_quest_test_member(interaction: discord.Interaction) -> bool:
    return (interaction.guild_id == GUILD_ID and
            isinstance(interaction.user, discord.Member) and
            interaction.user.guild_permissions.manage_guild)


@bot.tree.command(name='admin_weekly_complete',
                  description='Admin test: complete all your current weekly objectives')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def admin_weekly_complete(interaction: discord.Interaction, confirm: bool):
    if not is_admin_quest_test_member(interaction):
        return await interaction.response.send_message('Admins only, in GYMGUILD.', ephemeral=True)
    if not confirm:
        return await interaction.response.send_message(
            'Nothing changed. Set **confirm: True** to fill YOUR missing weekly objectives '
            'with synthetic test records. This awards normal objective/completion XP '
            'and can advance streaks. Existing weekly progress is preserved.',
            ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    outcome = admin_complete_weekly_test(interaction.guild_id, interaction.user.id)
    if outcome['paused']:
        return await interaction.followup.send(
            '⏸️ This week is paused. Resume XP first before test-completing it.',
            ephemeral=True)
    await update_rank(interaction.user, get_xp(interaction.guild_id, interaction.user.id))
    await interaction.followup.send(
        f'🧪 Weekly quest test complete: **{outcome["objectives"]}** new objectives; '
        f'weekly completion **{"recorded" if outcome["completion"] else "already recorded"}**. '
        f'🔥 Current streak: **{active_streak(interaction.guild_id, interaction.user.id)}** weeks. '
        f'New streak milestones: **{len(outcome["streak_awards"])}**.',
        ephemeral=True)


@bot.tree.command(name='admin_starter_complete',
                  description='Admin test: complete your starter quest and unlock Guild access')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def admin_starter_complete(interaction: discord.Interaction, confirm: bool):
    if not is_admin_quest_test_member(interaction):
        return await interaction.response.send_message('Admins only, in GYMGUILD.', ephemeral=True)
    if not confirm:
        return await interaction.response.send_message(
            'Nothing changed. Set **confirm: True** to award YOUR missing Starter Quest XP, '
            'mark the quest turned in, and grant the configured Guild Member access role.',
            ephemeral=True)
    role = interaction.guild.get_role(STARTER_ACCESS_ROLE_ID) if STARTER_ACCESS_ROLE_ID else None
    if role is None:
        return await interaction.response.send_message(
            'Starter role is not configured; no XP or progress was changed.', ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    if role not in interaction.user.roles:
        try:
            await interaction.user.add_roles(role, reason='Admin self-test Starter Quest completion')
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.followup.send(
                'Cannot assign the Guild Member role; no Starter Quest progress was changed.',
                ephemeral=True)
    result = admin_complete_starter_test(interaction.guild_id, interaction.user.id)
    await update_rank(interaction.user, get_xp(interaction.guild_id, interaction.user.id),
                      announce=False)
    await interaction.followup.send(
        f'🧪 Starter Quest complete: **{result["objectives"]}** newly awarded objectives '
        f'and **{"new" if result["turnin"] else "existing"}** turn-in. '
        'Guild Member access has been granted.',
        ephemeral=True)


@bot.tree.command(name='admin_milestones_complete',
                  description='Admin test: complete all six of your milestone quests')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def admin_milestones_complete(interaction: discord.Interaction, confirm: bool):
    if not is_admin_quest_test_member(interaction):
        return await interaction.response.send_message('Admins only, in GYMGUILD.', ephemeral=True)
    if not confirm:
        return await interaction.response.send_message(
            'Nothing changed. Set **confirm: True** to test-award YOUR six milestone '
            'objectives and any unclaimed +500 XP Main Quest bonus. This simulates '
            'a verified purchase and server boost; it does NOT make a real purchase '
            'or boost the server. An existing pending order must be resolved first.',
            ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    result = admin_complete_milestones_test(interaction.guild_id, interaction.user.id)
    if result['blocked']:
        return await interaction.followup.send(
            'You have a pending Order XP review. Cancel or resolve it first; nothing changed.',
            ephemeral=True)
    await update_rank(interaction.user, get_xp(interaction.guild_id, interaction.user.id))
    await interaction.followup.send(
        f'🧪 Milestones test complete: **{len(result["awarded"])}** new milestones; '
        f'Main Quest completion **{"awarded" if result["completion"] else "already claimed"}**. '
        'Synthetic purchase and boost achievements are for testing only.',
        ephemeral=True)


def longest_streak(gid: int, uid: int) -> int:
    """Longest run of completed weeks, including admin credits; pauses preserve runs."""
    completed, paused = streak_weeks(gid, uid)
    if not completed:
        return 0
    dates = sorted(datetime.fromisoformat(key).date() for key in completed | paused)
    cursor, end = dates[0], dates[-1]
    best = current = 0
    while cursor <= end:
        key = cursor.isoformat()
        if key in completed:
            current += 1
            best = max(best, current)
        elif key not in paused:
            current = 0
        cursor += timedelta(days=7)
    return best


STREAK_MILESTONE_THRESHOLDS = (
    (4, 'streak4'),
    (8, 'streak8'),
    (12, 'streak12'),
)


def award_earned_streak_milestones(gid: int, uid: int) -> list[str]:
    """Recognize consecutive credited or completed weeks, exactly once.

    Admin-added streak weeks count as completions, just like the rank streak
    display. Historical consecutive records count even after a streak breaks.
    Pauses protect a run without increasing its length.
    """
    longest = longest_streak(gid, uid)
    awarded = []
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for needed, kind in STREAK_MILESTONE_THRESHOLDS:
            if longest >= needed and grant_once(
                db, 'milestone_claims', gid, uid, kind, MILESTONE_XP[kind]
            ):
                awarded.append(kind)
    return awarded


def backfill_earned_streak_milestones(gid: int) -> int:
    """Repair earlier admin streak credits without touching member records."""
    with connect() as db:
        members = db.execute(
            'SELECT user_id FROM ('
            'SELECT user_id FROM admin_streak_weeks WHERE guild_id=? '
            'UNION SELECT user_id FROM weekly_completions WHERE guild_id=?'
            ') ORDER BY user_id',
            (gid, gid),
        ).fetchall()
    return sum(len(award_earned_streak_milestones(gid, row['user_id']))
               for row in members)


def reward_eligibility_text(gid: int, uid: int) -> str:
    xp = get_xp(gid, uid)
    # Refresh unlocks while the current active streak is qualifying.
    rank_for(xp, gid, uid)
    with connect() as db:
        unlocked = {row['rank'] for row in db.execute(
            'SELECT rank FROM rank_unlocks WHERE guild_id=? AND user_id=?', (gid, uid))}
        requests = {row['rank']: row['status'] for row in db.execute(
            'SELECT rank,status FROM reward_tickets WHERE guild_id=? AND user_id=?', (gid, uid))}
    lines = []
    for rank, discount in REWARD_RANKS.items():
        if rank in requests:
            status = 'Already requested (' + requests[rank] + ')'
        elif rank in unlocked:
            status = 'Eligible — unclaimed'
        else:
            status = 'Locked — requires ' + str(STREAK_REQUIREMENTS[rank]) + '-week active streak and ' + str(dict(RANKS)[rank]) + ' XP'
        lines.append(f'• **{rank} ({discount}% off):** {status}')
    return '\n'.join(lines)


def streak_length(gid, uid):
    completed, paused = streak_weeks(gid, uid)
    day = week_start() - timedelta(days=7)
    count = 0
    for _ in range(156):
        key = day.date().isoformat()
        if key in completed:
            count += 1
        elif key not in paused:
            break
        day -= timedelta(days=7)
    return count


async def credit_action(message, kind, xp, fingerprint):
    gid,uid = message.guild.id,message.author.id
    wk = week_key(message.created_at)
    if is_paused(gid,uid,wk):
        return
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM message_fingerprints WHERE guild_id=? AND user_id=? AND fingerprint=?',
                      (gid,uid,fingerprint)).fetchone():
            return
        if kind == 'training':
            days = {r['action_day'] for r in db.execute('SELECT action_day FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=? AND kind IN ("training1","training2")',(gid,uid,wk))}
            day = cycle_day(message.created_at)
            if day in days:
                return
            kind = 'training1' if not days else 'training2'
        else:
            day = cycle_day(message.created_at)
        cur = db.execute('INSERT OR IGNORE INTO weekly_actions VALUES (?,?,?,?,?,?,?,?)',
                         (gid,uid,wk,kind,day,message.id,fingerprint,utc_now().isoformat()))
        if not cur.rowcount:
            return
        db.execute('INSERT OR IGNORE INTO message_fingerprints VALUES (?,?,?,?)',
                   (gid,uid,fingerprint,utc_now().isoformat()))
        add_xp(gid,uid,xp,'Weekly '+wk+' '+kind,db)
        grant_once(db,'starter_claims',gid,uid,'first_steps',10)
        kinds = {r['kind'] for r in db.execute('SELECT kind FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?',(gid,uid,wk))}
        completed_now = False
        if len(kinds) == 4:
            finished = db.execute('INSERT OR IGNORE INTO weekly_completions VALUES (?,?,?,?)',
                                  (gid,uid,wk,utc_now().isoformat()))
            if finished.rowcount:
                add_xp(gid,uid,50,'Weekly completion '+wk,db)
                grant_once(db,'milestone_claims',gid,uid,'first_week',MILESTONE_XP['first_week'])
                n = db.execute('SELECT COUNT(*) n FROM weekly_completions WHERE guild_id=? AND user_id=?',(gid,uid)).fetchone()['n']
                # A full set is automatically turned in: the button simply confirms completion.
                completed_now = True
    if completed_now:
        # After committing the weekly completion, use the same streak rules
        # as rank progression (including admin credits and protected pauses).
        award_earned_streak_milestones(gid, uid)
    await update_rank(message.author,get_xp(gid,uid))
    try:
        await message.add_reaction('✦')
    except discord.HTTPException:
        pass


@bot.event
async def on_message(message: discord.Message):
    if not message.guild or message.guild.id != GUILD_ID or message.author.bot or not isinstance(message.author,discord.Member):
        return
    if not message.content:
        return
    normalized = ' '.join(message.content.lower().split())
    # No link-only, trivial, or repetitive messages. This is anti-spam, not proof of exercise.
    if len(normalized) < 25 or len(normalized.split()) < 5 or re.fullmatch(r'https?://\S+',normalized):
        return
    if INTRODUCTIONS_CHANNEL_ID and message.channel.id == INTRODUCTIONS_CHANNEL_ID:
        await award_starter(message.author,'intro')
    fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
    channel = message.channel
    if channel.id == TRAINING_LOG_CHANNEL_ID and TRAINING_LOG_CHANNEL_ID:
        # Both training objectives require a real Discord attachment as evidence.
        # Links or text-only claims do not qualify; preserve the existing meaningful caption rule.
        if not message.attachments:
            return
        await credit_action(message,'training',50,fingerprint)
    elif channel.category_id == GYM_CATEGORY_ID and GYM_CATEGORY_ID:
        await credit_action(message,'gym',25,fingerprint)
    elif channel.category_id == HOBBY_CATEGORY_ID and HOBBY_CATEGORY_ID:
        await credit_action(message,'hobby',25,fingerprint)


def week_needs_inactivity_penalty(db, gid: int, uid: int, wk: str) -> bool:
    """A week is inactive only if NONE of its four objectives was recorded."""
    paused = db.execute(
        'SELECT 1 FROM pauses p WHERE p.guild_id=? AND p.user_id=? AND p.week_key=? '
        'AND NOT EXISTS (SELECT 1 FROM resumed_pauses r '
        'WHERE r.guild_id=p.guild_id AND r.user_id=p.user_id AND r.week_key=p.week_key)',
        (gid, uid, wk),
    ).fetchone()
    if paused:
        return False
    return db.execute(
        "SELECT 1 FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=? "
        "AND kind IN ('training1','training2','gym','hobby') LIMIT 1",
        (gid, uid, wk),
    ).fetchone() is None


async def settle_previous_weeks():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    current = week_start()
    with connect() as db:
        activation = db.execute('SELECT value FROM v12_config WHERE key=?',('activation_week',)).fetchone()
        activation_week = datetime.fromisoformat(activation['value']).date() if activation else current.date()
        people = [r['user_id'] for r in db.execute('SELECT user_id FROM members WHERE guild_id=?',(GUILD_ID,))]
    for uid in people:
        with connect() as db:
            first = db.execute('SELECT MIN(created_at) first FROM xp_log WHERE guild_id=? AND user_id=?',(GUILD_ID,uid)).fetchone()['first']
        if not first:
            continue
        first_week = week_start(datetime.fromisoformat(first))
        cursor = max(first_week, week_start(datetime.combine(activation_week, datetime.min.time(), TZ)))
        changed = False
        while cursor < current:
            wk = cursor.date().isoformat()
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                inserted = db.execute('INSERT OR IGNORE INTO weekly_settlements VALUES (?,?,?,?)',(GUILD_ID,uid,wk,utc_now().isoformat()))
                if inserted.rowcount:
                    if week_needs_inactivity_penalty(db, GUILD_ID, uid, wk):
                        add_xp(GUILD_ID,uid,-25,'Inactive week '+wk,db)
                        changed = True
                    # Streak milestones are claimed when week 4/8/12 completes.
                    # The startup backfill handles historical missing claims.
            cursor += timedelta(days=7)
        if changed:
            member = guild.get_member(uid)
            if member:
                await update_rank(member,get_xp(GUILD_ID,uid))


WEEKLY_OBJECTIVES = (
    ('training1', '⚔️ First Flame — 50 XP'),
    ('training2', '🏃 Fuel the Fire — 50 XP'),
    ('gym', '🏋️ Flex Text — 25 XP'),
    ('hobby', '🎮 Skill Share — 25 XP'),
)


def weekly_streak_with_current(gid, uid, wk):
    return active_streak(gid, uid)


class WeeklyTurnInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='View Progress', style=discord.ButtonStyle.secondary,
                       emoji='📋', custom_id='guildkeeper:weekly:progress')
    async def view_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('This quest belongs to another server.', ephemeral=True)
        await interaction.response.send_message(
            weekly_progress_text(interaction.guild_id, interaction.user.id),
            view=PrivateProgressView(interaction.user.id, 'weekly'), ephemeral=True)

    @discord.ui.button(label='Turn In Quest', style=discord.ButtonStyle.success,
                       custom_id='guildkeeper:weekly:turn_in', emoji='⚔️')
    async def turn_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('This quest belongs to another server.', ephemeral=True)
        wk = week_key()
        kinds = weekly_status(interaction.guild_id, interaction.user.id, wk)
        if len(kinds) < 4:
            await interaction.response.send_message(
                f'You have completed **{len(kinds)}/4** objectives. '
                'Use `/weekly_progress` to see what remains.',
                ephemeral=True)
            return
        # Completing all four tasks already auto-recorded the turn-in and
        # credited XP; the button gives confirmation without rewarding twice.
        with connect() as db:
            recorded = db.execute(
                'SELECT 1 FROM weekly_completions WHERE guild_id=? AND user_id=? AND week_key=?',
                (interaction.guild_id, interaction.user.id, wk),
            ).fetchone() is not None
        if not recorded:
            return await interaction.response.send_message(
                'All four objectives are recorded, but the weekly completion is still '
                'being processed. Refresh your progress and try again.', ephemeral=True)
        streak = weekly_streak_with_current(interaction.guild_id, interaction.user.id, wk)
        await interaction.response.send_message(
            '🏆 **Congratulations! Weekly Side Quest complete!** '
            'All four objectives have been **automatically turned in**, and your '
            'weekly completion bonus has already been awarded. '
            f'🔥 **Your current streak: {streak} week(s)!** '
            'You do not need to press Turn In to keep your streak.',
            ephemeral=True)


def weekly_progress_text(gid: int, uid: int) -> str:
    kinds = weekly_status(gid, uid, week_key())
    lines = [f'{"✅" if key in kinds else "⬜"} {name}' for key, name in WEEKLY_OBJECTIVES]
    complete = len(kinds) == 4
    return ('⚔️ **Your Weekly Side Quest**\n' + '\n'.join(lines) +
            f'\n**{len(kinds)}/4 objectives completed.**\n' +
            ('🏆 **Automatically turned in!** Your completed week counts toward your streak.'
             if complete else 'Complete all four to turn in automatically — no button required.'))


@bot.tree.command(name='weekly_progress',description='See your four weekly quest objectives')
async def weekly_progress(interaction: discord.Interaction):
    await reply(interaction, weekly_progress_text(interaction.guild_id, interaction.user.id))


@bot.tree.command(name='pause',description='Use one pause week for the current or next cycle')
async def pause(interaction:discord.Interaction,next_week:bool=False):
    start=week_start()+(timedelta(days=7) if next_week else timedelta())
    wk=start.date().isoformat()
    # 2 pauses in a rolling 13-week window, based on target week.
    cutoff=(start-timedelta(weeks=13)).date().isoformat()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        used=db.execute('SELECT COUNT(*) n FROM pauses WHERE guild_id=? AND user_id=? AND week_key>? AND week_key<=?',(interaction.guild_id,interaction.user.id,cutoff,wk)).fetchone()['n']
        exists=db.execute('SELECT 1 FROM pauses WHERE guild_id=? AND user_id=? AND week_key=?',(interaction.guild_id,interaction.user.id,wk)).fetchone()
        if exists: return await reply(interaction,'You have already paused that week.')
        if used>=2: return await reply(interaction,'You have used both pauses in the rolling 13-week allowance.')
        db.execute('INSERT INTO pauses VALUES (?,?,?,?)',(interaction.guild_id,interaction.user.id,wk,utc_now().isoformat()))
    await reply(interaction,f'⏸️ Pause set for quest week **{wk}**. No inactivity penalty or streak loss for that week. Use `/pause_status` to check your allowance.')


@bot.tree.command(name='pause_status',description='Check your pauses and upcoming quest reset')
async def pause_status(interaction:discord.Interaction):
    wk=week_key(); cutoff=(week_start()-timedelta(weeks=13)).date().isoformat()
    with connect() as db:
        rows=db.execute('SELECT week_key FROM pauses WHERE guild_id=? AND user_id=? AND week_key>? ORDER BY week_key',(interaction.guild_id,interaction.user.id,cutoff)).fetchall()
    await reply(interaction,f'⏸️ **Pause status**\nUsed in rolling 13-week window: **{len(rows)}/2**\nPaused weeks: '+(', '.join(r['week_key'] for r in rows) or 'None')+f'\nNext weekly reset: <t:{int(next_reset().timestamp())}:F>\nAllowance restores as old pause weeks fall outside the 13-week window.')


@bot.tree.command(name='streak',description='See your consecutive weekly completions')
async def streak(interaction:discord.Interaction):
    await reply(interaction,f'🔥 Completed weekly trials: **{completed_weeks(interaction.guild_id,interaction.user.id)}**\nCurrent streak (including this week if complete): **{active_streak(interaction.guild_id,interaction.user.id)}**. Paused weeks preserve but do not increase it.')




STARTER_REWARDS = {'intro': 10, 'class': 10, 'hobby': 10, 'welcome': 10, 'first_steps': 10}

def starter_embed(member=None):
    embed = discord.Embed(
        title='🛡️ GYMGUILD ✦ STARTER QUEST',
        description='Welcome to the Guild! Complete these **five one-time objectives**, then press **Turn In Quest** to unlock the Guild channels.',
        colour=discord.Colour.from_rgb(104, 163, 153))
    intro = f'<#{INTRODUCTIONS_CHANNEL_ID}>' if INTRODUCTIONS_CHANNEL_ID else '#introductions'
    get_role = f'<#{GET_ROLE_CHANNEL_ID}>' if GET_ROLE_CHANNEL_ID else '#get-role'
    welcome = f'<#{WELCOME_CHANNEL_ID}>' if WELCOME_CHANNEL_ID else '#welcome'
    side_quest = f'<#{SIDE_QUEST_CHANNEL_ID}>' if SIDE_QUEST_CHANNEL_ID else '#side-quest'
    msg_link = (f'https://discord.com/channels/{GUILD_ID}/{WELCOME_CHANNEL_ID}/{WELCOME_MESSAGE_ID}'
                if WELCOME_CHANNEL_ID and WELCOME_MESSAGE_ID else None)
    embed.add_field(name='📜 1 · Guild Info — 10 XP',
                    value=(f'React with any emoji to [this message]({msg_link}) in {welcome}.' if msg_link
                           else f'React with any emoji to the designated message in {welcome}.'), inline=False)
    embed.add_field(name='\u200b', value='\u200b', inline=False)
    embed.add_field(name='🏋️ 2 · Choose Your Class — 10 XP',
                    value=f'Choose Warrior, Ranger, Mage or Paladin. This can be done in {get_role}.', inline=False)
    embed.add_field(name='\u200b', value='\u200b', inline=False)
    embed.add_field(name='🎮 3 · Choose Your Proficiency — 10 XP',
                    value=f'Choose at least one Proficiency. This can be done in {get_role}.', inline=False)
    embed.add_field(name='\u200b', value='\u200b', inline=False)
    embed.add_field(name='👋 4 · Introduce Yourself — 10 XP',
                    value=f'Say hello in {intro} (at least 25 characters and 5 words).', inline=False)
    embed.add_field(name='\u200b', value='\u200b', inline=False)
    embed.add_field(name='👟 5 · First Steps — 10 XP',
                    value=f'Complete any 1 of the 4 Weekly Side Quest objectives in {side_quest}.', inline=False)
    embed.add_field(name='━━━━━━━━━━━━━━━━━━━━', value='​', inline=False)
    embed.add_field(name='Total starter reward', value='**50 XP**', inline=False)
    embed.set_footer(text='Guildkeeper Starter Quest · Complete 5/5, then Turn In Quest · /starter_progress')
    return embed

async def award_starter(member, kind):
    if member.bot or member.guild.id != GUILD_ID or kind not in STARTER_REWARDS:
        return False
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        granted = grant_once(db,'starter_claims',member.guild.id,member.id,kind,STARTER_REWARDS[kind])
    if granted:
        await update_rank(member,get_xp(member.guild.id,member.id))
    return granted

async def set_guild_blast(member: discord.Member, enable: bool) -> str:
    """Opt-in announcement pings; never manage other roles."""
    role = member.guild.get_role(GUILD_BLAST_ROLE_ID) if GUILD_BLAST_ROLE_ID else None
    me = member.guild.me
    if role is None:
        return '⚠️ Guild Blast is not configured yet. Please contact an admin.'
    if role.is_default() or role.managed or me is None or role >= me.top_role:
        return '⚠️ Guildkeeper cannot manage the Guild Blast role. Ask an admin to check role permissions and hierarchy.'
    if (role in member.roles) == enable:
        return ('🔔 Guild Blast notifications are already **enabled**.' if enable else
                '🔕 Guild Blast notifications are already **disabled**.')
    try:
        if enable:
            await member.add_roles(role, reason='Member opted in to Guild Blast notifications')
            return '🔔 **Guild Blast enabled!** You can now be mentioned in Guild announcements. You can disable it anytime in #player-stats.'
        await member.remove_roles(role, reason='Member opted out of Guild Blast notifications')
        return '🔕 **Guild Blast disabled.** You will no longer receive Guild Blast role mentions. You can enable it anytime in #player-stats.'
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Failed to update Guild Blast for member %s', member.id)
        return '⚠️ Could not update Guild Blast. Please ask an admin to check Guildkeeper’s Manage Roles permission.'


class GuildBlastWelcomeView(discord.ui.View):
    """Private optional opt-in offered after completing the Starter Quest."""
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('This choice belongs to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Yes, Enable Guild Blast', emoji='🔔', style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id != GUILD_ID:
            return await interaction.response.edit_message(content='This option is only available in the Guild.', view=None)
        await interaction.response.defer()
        result = await set_guild_blast(interaction.user, True)
        await interaction.edit_original_response(content=result, view=None)

    @discord.ui.button(label='No Thanks', emoji='🔕', style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content='No problem! Guild Blast is optional. You can enable or disable it anytime in #player-stats.', view=None)


class StarterTurnInView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='View Progress', style=discord.ButtonStyle.secondary,
                       emoji='📜', custom_id='guildkeeper:starter:progress')
    async def view_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('This quest belongs to another server.', ephemeral=True)
        await interaction.response.send_message(
            starter_progress_text(interaction.guild_id, interaction.user.id),
            view=PrivateProgressView(interaction.user.id, 'starter'), ephemeral=True)

    @discord.ui.button(label='Turn In Quest', style=discord.ButtonStyle.success,
                       emoji='⚔️', custom_id='guildkeeper:starter:turn_in')
    async def turn_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message('Use this button in the GYMGUILD server.', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user
        with connect() as db:
            claimed = {r['kind'] for r in db.execute(
                'SELECT kind FROM starter_claims WHERE guild_id=? AND user_id=?',
                (GUILD_ID, member.id))}
        if not set(STARTER_REWARDS).issubset(claimed):
            return await interaction.followup.send(
                'Complete all objectives to unlock all channels. '
                f'You have completed **{len(set(STARTER_REWARDS) & claimed)}/5** objectives. '
                'Use `/starter_progress` to see what remains.', ephemeral=True)
        role = interaction.guild.get_role(STARTER_ACCESS_ROLE_ID) if STARTER_ACCESS_ROLE_ID else None
        if role is None:
            return await interaction.followup.send(
                'Your quest is complete, but the access role is not configured. '
                'Please contact an admin.', ephemeral=True)
        newly_granted = role not in member.roles
        if newly_granted:
            try:
                await member.add_roles(role, reason='Completed and turned in all Starter Quest objectives')
            except (discord.Forbidden, discord.HTTPException):
                log.exception('Failed to grant Starter Quest access role to %s', member.id)
                return await interaction.followup.send(
                    'Your quest is complete, but I could not unlock your channels. '
                    'Please contact an admin.', ephemeral=True)
        # Persist onboarding before rank sync, so Novice stays unlocked even
        # if weekly inactivity later reduces XP below the 50 XP threshold.
        with connect() as db:
            inserted = db.execute('INSERT OR IGNORE INTO starter_turnins VALUES (?,?,?)',
                                  (GUILD_ID, member.id, utc_now().isoformat())).rowcount > 0
        await update_rank(member, get_xp(GUILD_ID, member.id), announce=False)
        # Only the original successful starter turn-in announces Novice;
        # role restoration, duplicate clicks and old completed quests stay quiet.
        if newly_granted and inserted:
            await announce_new_guild_member(member)
        await interaction.followup.send(
            '🎉 **Welcome to the Guild!** All five Starter Quest objectives are complete, and all channels are unlocked.\n\n'
            '🔔 **Would you like to enable Guild Blast?** This optional role is used by admins and the Guildkeeper app in #announcements. '
            'You can turn Guild Blast on or off anytime in **#player-stats**.',
            view=GuildBlastWelcomeView(member.id), ephemeral=True)


@bot.event
async def on_member_update(before:discord.Member,after:discord.Member):
    if after.guild.id != GUILD_ID or after.bot:
        return
    before_ids={r.id for r in before.roles}
    after_ids={r.id for r in after.roles}
    added=after_ids-before_ids
    if GYM_ROLE_IDS and added & GYM_ROLE_IDS:
        await award_starter(after,'class')
    if HOBBY_ROLE_IDS and added & HOBBY_ROLE_IDS:
        await award_starter(after,'hobby')
    if not before.premium_since and after.premium_since:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ok=grant_once(db,'milestone_claims',after.guild.id,after.id,'boost1',MILESTONE_XP['boost1'])
        if ok:
            await update_rank(after,get_xp(after.guild.id,after.id))

@bot.event
async def on_raw_reaction_add(payload:discord.RawReactionActionEvent):
    await process_role_reaction(payload, adding=True)
    if (payload.guild_id != GUILD_ID or not WELCOME_MESSAGE_ID or
            payload.message_id != WELCOME_MESSAGE_ID or payload.channel_id != WELCOME_CHANNEL_ID):
        return
    # Any emoji qualifies. The target is a specific welcome message, not any message in the channel.
    guild = bot.get_guild(GUILD_ID)
    if guild is None or payload.user_id == bot.user.id:
        return
    member = payload.member or guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except (discord.NotFound,discord.Forbidden,discord.HTTPException):
            return
    await award_starter(member,'welcome')



def configured_menu_emojis(menu_type: str) -> list[str]:
    """Custom emoji entries use Discord's <:name:id> or <a:name:id> format."""
    env_key = 'CLASS_MENU_EMOJIS' if menu_type == 'gym' else 'PROFICIENCY_MENU_EMOJIS'
    raw = os.getenv(env_key, '').strip()
    if not raw:
        return GYM_MENU_EMOJIS if menu_type == 'gym' else HOBBY_MENU_EMOJIS
    emojis = [item.strip() for item in raw.split(',')]
    if not all(emojis) or len(set(emojis)) != len(emojis):
        raise ValueError(f'{env_key} must contain unique, nonempty comma-separated emojis.')
    for emoji in emojis:
        if emoji.startswith('<') and not re.fullmatch(r'<a?:[A-Za-z0-9_]{2,32}:[0-9]{15,25}>', emoji):
            raise ValueError(f'Invalid custom emoji {emoji!r} in {env_key}. Use <:name:id> or <a:name:id>.')
    return emojis


def role_menu_mapping(menu_type: str) -> dict[str, int]:
    """Only explicitly allowlisted gym/hobby roles; never XP ranks."""
    ids = sorted(GYM_ROLE_IDS if menu_type == 'gym' else HOBBY_ROLE_IDS)
    # The explicit ordered variables allow admins to control which role gets which emoji.
    ordered = os.getenv('GYM_MENU_ROLE_IDS' if menu_type == 'gym' else 'HOBBY_MENU_ROLE_IDS', '')
    if ordered.strip():
        ids = [int(x.strip()) for x in ordered.split(',') if x.strip().isdigit()]
    allowed = GYM_ROLE_IDS if menu_type == 'gym' else HOBBY_ROLE_IDS
    emojis = configured_menu_emojis(menu_type)
    if len(ids) != len(set(ids)) or len(ids) != len(emojis) or not set(ids).issubset(allowed):
        raise ValueError(f'Invalid {menu_type} menu configuration: provide exactly one emoji per role, with unique valid role IDs.')
    return dict(zip(emojis, ids))


def role_menu_lookup(message_id: int, channel_id: int):
    with connect() as db:
        row = db.execute('SELECT menu_type FROM role_menu_posts WHERE guild_id=? AND message_id=? AND channel_id=?',
                         (GUILD_ID, message_id, channel_id)).fetchone()
    return row['menu_type'] if row else None


async def process_role_reaction(payload: discord.RawReactionActionEvent, adding: bool):
    if payload.guild_id != GUILD_ID or bot.user is None or payload.user_id == bot.user.id:
        return
    menu_type = role_menu_lookup(payload.message_id, payload.channel_id)
    if not menu_type:
        return
    try:
        mapping = role_menu_mapping(menu_type)
    except ValueError:
        log.exception('Invalid role menu configuration')
        return
    role_id = mapping.get(str(payload.emoji))
    if role_id is None:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    member = guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    if member.bot:
        return
    role = guild.get_role(role_id)
    if (role is None or role.is_default() or role.managed or role.name in {name for name, _ in RANKS}
            or role >= guild.me.top_role):
        log.warning('Refusing to manage invalid or protected menu role ID %s', role_id)
        return
    try:
        if adding and role not in member.roles:
            if menu_type == 'gym':
                previous = [r for r in member.roles if r.id in GYM_ROLE_IDS and r.id != role_id]
                if previous:
                    await member.remove_roles(*previous, reason='Choose one Class')
                    channel = guild.get_channel(payload.channel_id)
                    if isinstance(channel, discord.TextChannel):
                        try:
                            menu_message = await channel.fetch_message(payload.message_id)
                            for emoji, previous_id in mapping.items():
                                if previous_id in {r.id for r in previous}:
                                    await menu_message.remove_reaction(emoji, member)
                        except (discord.Forbidden, discord.HTTPException):
                            log.warning('Could not clear old Class menu reactions for %s', member.id)
            await member.add_roles(role, reason='Guildkeeper self-service role menu')
            if menu_type == 'gym':
                await award_starter(member, 'class')
            else:
                await award_starter(member, 'hobby')
        elif not adding and role in member.roles:
            await member.remove_roles(role, reason='Guildkeeper self-service role menu')
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not change menu role; check Guildkeeper role hierarchy and Manage Roles')


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    await process_role_reaction(payload, adding=False)


@bot.tree.command(name='admin_role_menus', description='Moderator: post separate Class and Proficiency reaction role menus')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_role_menus(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    channel = bot.get_channel(GET_ROLE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild_id:
        await interaction.followup.send('Set GET_ROLE_CHANNEL_ID to your #get-role text channel first.', ephemeral=True)
        return
    if not GYM_ROLE_IDS or not HOBBY_ROLE_IDS:
        await interaction.followup.send('Set both GYM_ROLE_IDS and HOBBY_ROLE_IDS in .env first.', ephemeral=True)
        return
    try:
        mappings = {kind: role_menu_mapping(kind) for kind in ('gym', 'hobby')}
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    try:
        urls = []
        for kind in ('gym', 'hobby'):
            urls.append(await publish_role_menu(interaction.guild, channel, kind, mappings[kind]))
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not publish role menus')
        await interaction.followup.send('Could not post both menus. Check permissions and bot role hierarchy.', ephemeral=True)
        return
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    await interaction.followup.send('Class and Proficiency menus published in ' + channel.mention + ': ' + ' · '.join(urls), ephemeral=True)


async def publish_role_menu(guild: discord.Guild, channel: discord.TextChannel, kind: str, mapping: dict[str, int]) -> str:
    """Post one self-service menu; only configured non-rank roles can be offered."""
    if not mapping or any(
        guild.get_role(role_id) is None
        or guild.get_role(role_id).name in {name for name, _ in RANKS}
        or guild.get_role(role_id).managed
        or guild.get_role(role_id).is_default()
        or guild.get_role(role_id) >= guild.me.top_role
        for role_id in mapping.values()
    ):
        raise ValueError('Invalid Class/Proficiency roles. XP ranks and roles above Guildkeeper cannot be offered.')
    heading = '⚔️ CHOOSE YOUR CLASS' if kind == 'gym' else '🔮 CHOOSE YOUR PROFICIENCY'
    desc = ('React to choose your training class. Selecting a different class replaces the previous one.'
            if kind == 'gym' else 'React to select as many proficiencies as you like. Remove a reaction to unselect.')
    lines = [f'{emoji} — <@&{role_id}>' for emoji, role_id in mapping.items()]
    embed = discord.Embed(title=heading, description=desc + '\n\n' + '\n\n'.join(lines), color=0x71A7A2)
    embed.set_footer(text='Class & Proficiency roles only · XP ranks are earned automatically')
    msg = await channel.send(embed=embed)
    try:
        for emoji in mapping:
            await msg.add_reaction(emoji)
    except (discord.HTTPException, discord.InvalidArgument) as error:
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
        raise ValueError('Could not add a configured emoji reaction. Make sure Guildkeeper can use the custom emoji (and external emojis if needed).') from error
    with connect() as db:
        db.execute('INSERT INTO role_menu_posts(guild_id,menu_type,channel_id,message_id) VALUES (?,?,?,?) '
                   'ON CONFLICT(guild_id,menu_type) DO UPDATE SET channel_id=excluded.channel_id,message_id=excluded.message_id',
                   (GUILD_ID, kind, channel.id, msg.id))
    return msg.jump_url


async def publish_single_role_menu(interaction: discord.Interaction, kind: str):
    await interaction.response.defer(ephemeral=True)
    channel = bot.get_channel(GET_ROLE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild_id:
        await interaction.followup.send('Set GET_ROLE_CHANNEL_ID to your #get-role channel first.', ephemeral=True)
        return
    try:
        mapping = role_menu_mapping(kind)
        url = await publish_role_menu(interaction.guild, channel, kind, mapping)
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not publish %s role menu', kind)
        await interaction.followup.send('Could not post the menu. Check bot permissions and role hierarchy.', ephemeral=True)
        return
    await interaction.followup.send(f'Menu posted in {channel.mention}: {url}', ephemeral=True)


@bot.tree.command(name='admin_class_menu', description='Moderator: post or refresh the Class reaction menu')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_class_menu(interaction: discord.Interaction):
    await publish_single_role_menu(interaction, 'gym')


@bot.tree.command(name='admin_proficiency_menu', description='Moderator: post or refresh the Proficiency reaction menu')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_proficiency_menu(interaction: discord.Interaction):
    await publish_single_role_menu(interaction, 'hobby')


def starter_progress_text(gid: int, uid: int) -> str:
    with connect() as db:
        claimed = {r['kind'] for r in db.execute(
            'SELECT kind FROM starter_claims WHERE guild_id=? AND user_id=?', (gid, uid))}
    labels = [('welcome', 'Guild Info'), ('class', 'Class'), ('hobby', 'Proficiency'),
              ('intro', 'Introduction'), ('first_steps', 'First Steps')]
    lines = [f'{"✅" if key in claimed else "⬜"} {label} — {STARTER_REWARDS[key]} XP'
             for key, label in labels]
    return '🛡️ **Your Starter Quest**\n' + '\n'.join(lines) + f'\n**{len(set(STARTER_REWARDS) & claimed)}/5 objectives completed.**'


@bot.tree.command(name='starter_progress',description='See your one-time Starter Quest progress')
async def starter_progress(interaction:discord.Interaction):
    await reply(interaction, starter_progress_text(interaction.guild_id, interaction.user.id))

@bot.tree.command(name='admin_starter_post',description='Moderator: post or refresh the permanent Starter Quest board')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_starter_post(interaction:discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    channel = interaction.guild.get_channel(STARTER_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.followup.send('Configure STARTER_CHANNEL_ID to your #start-here text channel.', ephemeral=True)
    if not STARTER_ACCESS_ROLE_ID or not interaction.guild.get_role(STARTER_ACCESS_ROLE_ID):
        return await interaction.followup.send('Configure STARTER_ACCESS_ROLE_ID to an existing Guild Member role.', ephemeral=True)
    with connect() as db:
        old = db.execute('SELECT channel_id,message_id FROM starter_board_posts WHERE guild_id=?',
                         (GUILD_ID,)).fetchone()
    if old:
        previous_channel = interaction.guild.get_channel(old['channel_id'])
        if isinstance(previous_channel, discord.TextChannel):
            try:
                previous_message = await previous_channel.fetch_message(old['message_id'])
                if previous_channel.id == channel.id:
                    await previous_message.edit(embed=starter_embed(), view=StarterTurnInView())
                    return await interaction.followup.send('Starter Quest board refreshed.', ephemeral=True)
                await previous_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning('Could not edit or remove previous Starter Quest board')
    try:
        msg = await channel.send(embed=starter_embed(), view=StarterTurnInView())
    except (discord.Forbidden, discord.HTTPException):
        return await interaction.followup.send('Could not post the Starter Quest board. Check bot channel permissions.', ephemeral=True)
    with connect() as db:
        db.execute('INSERT INTO starter_board_posts VALUES (?,?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,message_id=excluded.message_id',
                   (GUILD_ID, channel.id, msg.id))
    await interaction.followup.send('Permanent Starter Quest board posted. Pin it in #start-here.', ephemeral=True)

@bot.tree.command(name='admin_starter_reset', description='Moderator: reset a member Starter Quest for testing')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_starter_reset(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    gid, uid = interaction.guild_id, member.id
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        claimed = {r['kind'] for r in db.execute(
            'SELECT kind FROM starter_claims WHERE guild_id=? AND user_id=?', (gid, uid))}
        refunded = sum(STARTER_REWARDS[k] for k in claimed if k in STARTER_REWARDS)
        db.execute('DELETE FROM starter_claims WHERE guild_id=? AND user_id=?', (gid, uid))
        db.execute('DELETE FROM starter_turnins WHERE guild_id=? AND user_id=?', (gid, uid))
        if refunded:
            add_xp(gid, uid, -refunded, f'Admin Starter Quest reset by {interaction.user.id}', db)
    role = interaction.guild.get_role(STARTER_ACCESS_ROLE_ID) if STARTER_ACCESS_ROLE_ID else None
    role_removed = False
    role_error = False
    if role is not None and role in member.roles:
        try:
            await member.remove_roles(role, reason=f'Starter Quest test reset by {interaction.user.id}')
            role_removed = True
        except (discord.Forbidden, discord.HTTPException):
            role_error = True
            log.exception('Could not remove Starter access role from %s', uid)
    await update_rank(member, get_xp(gid, uid))
    note = ('Could not remove the access role; check bot role hierarchy and Manage Roles permission.'
            if role_error else 'Access role removed.' if role_removed else 'Member did not have the access role.')
    await interaction.followup.send(
        f'♻️ Reset Starter Quest for {member.mention}: **0/5** objectives. '
        f'Removed **{refunded} Starter XP** and cleared their turn-in. {note}\n'
        'Their weekly activity history and selected Class/Proficiency roles were kept. '
        'They must perform new qualifying actions to reclaim Starter XP.',
        ephemeral=True)


@bot.tree.command(name='starter',description='View how to complete your one-time Starter Quest')
async def starter(interaction:discord.Interaction):
    await interaction.response.send_message(embed=starter_embed(interaction.user),ephemeral=True)

@bot.tree.command(name='admin_starter_award',description='Moderator: verify and award a one-time starter quest')
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(action=[
    app_commands.Choice(name='Introduction',value='intro'),
    app_commands.Choice(name='Class',value='class'),
    app_commands.Choice(name='Proficiency',value='hobby'),
    app_commands.Choice(name='Guild Info reaction',value='welcome'),
    app_commands.Choice(name='First Steps (one weekly objective)',value='first_steps')])
async def admin_starter_award(interaction:discord.Interaction,member:discord.Member,action:app_commands.Choice[str]):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        ok=grant_once(db,'starter_claims',interaction.guild_id,member.id,action.value,STARTER_REWARDS[action.value])
    if ok:
        await update_rank(member,get_xp(interaction.guild_id,member.id))
    await reply(interaction,f'{"Awarded" if ok else "Already claimed"}: {action.name} for {member.mention}.')


def purchase_order_summary(gid: int, uid: int) -> dict:
    """Count orders recorded through GuildKeeper, not all Shopify orders."""
    with connect() as db:
        records = db.execute(
            'SELECT id,order_reference,status FROM purchase_orders '
            'WHERE guild_id=? AND user_id=? ORDER BY id DESC',
            (gid, uid),
        ).fetchall()
        earned = db.execute(
            "SELECT 1 FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind='purchase1'",
            (gid, uid),
        ).fetchone() is not None
        actual = db.execute(
            "SELECT amount FROM xp_log "
            "WHERE guild_id=? AND user_id=? AND reason='purchase1' AND amount>0 "
            "ORDER BY id DESC LIMIT 1",
            (gid, uid),
        ).fetchone()
        actual = actual['amount'] if actual else 0
    return {
        'approved': sum(row['status'] == 'approved' for row in records),
        'pending': sum(row['status'] == 'pending' for row in records),
        'rejected': sum(row['status'] == 'rejected' for row in records),
        'claimed': earned,
        'earned_xp': (actual or 0) if earned else 0,
        'potential_xp': MILESTONE_XP['purchase1'] if not earned and any(row['status'] == 'pending' for row in records) else 0,
        'orders': [dict(row) for row in records[:8]],
    }


def order_xp_embed(gid: int, uid: int) -> discord.Embed:
    summary = purchase_order_summary(gid, uid)
    embed = discord.Embed(
        title='✦ GYMGUILD — ORDER XP',
        description=('Submit your GYMGUILD Shopify order number for moderator review. '
                     f'Your **first approved order** can earn **{MILESTONE_XP["purchase1"]} XP** '
                     'when approved. Rejected or cancelled requests can be retried until approved.'),
        colour=discord.Colour.from_rgb(104, 163, 153),
    )
    embed.add_field(name='✅ Approved orders', value=str(summary['approved']), inline=True)
    embed.add_field(name='⏳ Awaiting review', value=str(summary['pending']), inline=True)
    earned_label = (
        f"{summary['earned_xp']} XP granted" if summary['earned_xp'] else
        'Previously claimed' if summary['claimed'] else
        '0 XP earned'
    )
    embed.add_field(name='✦ Order XP earned', value=earned_label, inline=True)
    embed.add_field(
        name='🎯 Potential XP',
        value=(f"+{summary['potential_xp']} XP awaiting approval"
               if summary['potential_xp'] else
               'First-order reward already claimed' if summary['claimed']
               else f'+{MILESTONE_XP["purchase1"]} XP available when your first order is approved'),
        inline=False,
    )
    if summary['orders']:
        status_icons = {'approved': '✅', 'pending': '⏳', 'rejected': '❌', 'cancelled': '🚫', 'test_reset': '🧪'}
        lines = [
            f"{status_icons.get(order['status'], '•')} "
            f"`{discord.utils.escape_markdown(order['order_reference'])}` — "
            f"{order['status'].title()}"
            for order in summary['orders']
        ]
        embed.add_field(name='Your submitted orders (latest 8)', value='\n'.join(lines), inline=False)
    else:
        embed.add_field(name='Your submitted orders', value='No orders submitted yet.', inline=False)
    embed.set_footer(text='Only GuildKeeper submissions are counted — not all Shopify orders.')
    return embed


def order_submission_status(gid: int, uid: int) -> str:
    """Only a granted purchase milestone permanently closes submissions."""
    with connect() as db:
        if db.execute(
            "SELECT 1 FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind='purchase1'",
            (gid, uid),
        ).fetchone():
            return 'approved'
        pending = db.execute(
            "SELECT 1 FROM purchase_orders WHERE guild_id=? AND user_id=? AND status='pending'",
            (gid, uid),
        ).fetchone()
        if pending:
            return 'pending'
    return 'available'


def submit_purchase_order(gid: int, uid: int, reference: str) -> tuple[str, int | None]:
    """One open request per member. Rejected/cancelled requests can be retried.

    All checks and writes run in one immediate transaction; an approved
    milestone prevents new requests even if earlier records exist.
    """
    reference = reference.strip()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        claim = db.execute(
            "SELECT 1 FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind='purchase1'",
            (gid, uid),
        ).fetchone()
        if claim:
            return 'approved', None
        pending = db.execute(
            "SELECT id FROM purchase_orders WHERE guild_id=? AND user_id=? AND status='pending'",
            (gid, uid),
        ).fetchone()
        if pending:
            return 'pending', pending['id']
        if not reference or len(reference) > 80 or any(ord(char) < 32 for char in reference):
            return 'invalid', None
        row = db.execute(
            'SELECT id,user_id,status,review_message_id FROM purchase_orders '
            'WHERE guild_id=? AND order_reference=?',
            (gid, reference),
        ).fetchone()
        if row:
            if row['user_id'] != uid:
                return 'duplicate', None
            if row['status'] not in ('rejected', 'cancelled'):
                return row['status'], row['id']
            # A previous moderator card can still be clicked after cancellation.
            # Never reuse that card's order ID: submit a different reference.
            if row['review_message_id'] is not None:
                return 'used_reference', row['id']
            # Without an old moderator card, reopening the row is safe.
            db.execute(
                "UPDATE purchase_orders SET status='pending',submitted_at=?,"
                "reviewed_at=NULL,reviewed_by=NULL,review_channel_id=NULL,review_message_id=NULL "
                "WHERE id=?",
                (utc_now().isoformat(), row['id']),
            )
            return 'submitted', row['id']
        cur = db.execute(
            "INSERT INTO purchase_orders(guild_id,user_id,order_reference,status,submitted_at) "
            "VALUES (?,?,?,'pending',?)",
            (gid, uid, reference, utc_now().isoformat()),
        )
        return 'submitted', cur.lastrowid


def cancel_purchase_order(gid: int, uid: int) -> tuple[str, int | None, int | None, int | None]:
    """Withdraw ONLY the caller's pending request, atomically and without XP effects."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        claimed = db.execute(
            "SELECT 1 FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind='purchase1'",
            (gid, uid),
        ).fetchone()
        if claimed:
            return 'approved', None, None, None
        row = db.execute(
            "SELECT id,review_channel_id,review_message_id FROM purchase_orders "
            "WHERE guild_id=? AND user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (gid, uid),
        ).fetchone()
        if row is None:
            return 'not_pending', None, None, None
        db.execute(
            "UPDATE purchase_orders SET status='cancelled',reviewed_at=?,reviewed_by=? "
            "WHERE guild_id=? AND user_id=? AND id=? AND status='pending'",
            (utc_now().isoformat(), uid, gid, uid, row['id']),
        )
        return 'cancelled', row['id'], row['review_channel_id'], row['review_message_id']


def submit_first_purchase_milestone(gid: int, uid: int, reference: str) -> str:
    """Compatibility wrapper for older slash-command tests."""
    status, _ = submit_purchase_order(gid, uid, reference)
    return 'awarded' if status == 'approved' else status


def review_purchase_order(gid: int, order_id: int, approver_id: int, approve: bool) -> tuple[str, int | None]:
    """Resolve an individual pending order transactionally; only the first grants XP."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(
            'SELECT user_id,status FROM purchase_orders WHERE guild_id=? AND id=?',
            (gid, order_id),
        ).fetchone()
        if row is None or row['status'] != 'pending':
            return 'not_pending', None
        db.execute(
            'UPDATE purchase_orders SET status=?,reviewed_at=?,reviewed_by=? WHERE id=?',
            ('approved' if approve else 'rejected', utc_now().isoformat(), approver_id, order_id),
        )
        if approve:
            awarded = grant_once(db, 'milestone_claims', gid, row['user_id'], 'purchase1', MILESTONE_XP['purchase1'])
            return ('awarded' if awarded else 'approved_no_xp'), row['user_id']
    return 'rejected', row['user_id']


def review_first_purchase_milestone(gid: int, uid: int, approve: bool) -> str:
    """Compatibility with the existing moderator command and stored requests."""
    with connect() as db:
        row = db.execute(
            "SELECT id FROM purchase_orders WHERE guild_id=? AND user_id=? "
            "AND status='pending' ORDER BY id LIMIT 1",
            (gid, uid),
        ).fetchone()
    if row is None:
        return 'not_pending'
    result, _ = review_purchase_order(gid, row['id'], 0, approve)
    return 'already_awarded' if result == 'approved_no_xp' else result


async def post_purchase_review(guild: discord.Guild, user_id: int, order_id: int) -> bool:
    channel = guild.get_channel(MOD_REVIEW_CHANNEL_ID) if MOD_REVIEW_CHANNEL_ID else None
    if not isinstance(channel, discord.TextChannel):
        log.warning('Purchase order pending but MOD_REVIEW_CHANNEL_ID is missing/invalid')
        return False
    with connect() as db:
        row = db.execute(
            'SELECT order_reference FROM purchase_orders WHERE guild_id=? AND id=?',
            (guild.id, order_id),
        ).fetchone()
    if row is None:
        return False
    reference = discord.utils.escape_mentions(
        discord.utils.escape_markdown(row['order_reference']))
    embed = discord.Embed(
        title='🛍️ ORDER XP — REVIEW REQUIRED',
        description=(f'Member: <@{user_id}>\nOrder reference: **{reference}**\n'
                     'Check this order in Shopify before approving.\n'
                     f'**First approved order: +{MILESTONE_XP["purchase1"]} XP. Subsequent orders: +0 XP.**'),
        colour=discord.Colour.from_rgb(104, 163, 153),
    )
    try:
        posted = await channel.send(
            embed=embed, view=PurchaseReviewView(order_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        with connect() as db:
            db.execute(
                "UPDATE purchase_orders SET review_channel_id=?,review_message_id=? "
                "WHERE guild_id=? AND id=? AND status='pending'",
                (channel.id, posted.id, guild.id, order_id),
            )
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not post order XP review request %s', order_id)
        return False
    return True


async def submit_order_from_interaction(interaction: discord.Interaction, reference: str) -> None:
    if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message('Use this in the GYMGUILD server.', ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    status, order_id = submit_purchase_order(interaction.guild_id, interaction.user.id, reference)
    if status == 'submitted':
        notified = await post_purchase_review(interaction.guild, interaction.user.id, order_id)
        message = ('✅ **Order submitted for review.** '
                   + ('A moderator will verify it in Shopify.' if notified else
                      'Your submission is saved, but the moderator review channel needs configuring.'))
    elif status == 'pending':
        message = '⏳ Your Order XP request is pending. Cancel it from the private status panel to submit another.'
    elif status == 'approved':
        message = '✅ Order XP granted — milestone objective complete.'
    elif status == 'rejected':
        message = '❌ Your request was rejected. You may submit a new order number.'
    elif status == 'used_reference':
        message = ('❌ This order reference was already submitted and reviewed or cancelled. '
                   'Please enter a different reference or contact a moderator.')
    elif status == 'duplicate':
        message = '❌ That order reference is already registered to another Guild member. Contact a moderator.'
    else:
        message = '❌ Enter a valid Shopify order reference (1–80 characters, no private details).'
    await interaction.followup.send(
        message, embed=order_xp_embed(interaction.guild_id, interaction.user.id),
        view=OrderXPView(interaction.user.id, interaction.guild_id), ephemeral=True,
    )


def milestone_board_embed() -> discord.Embed:
    embed = discord.Embed(
        title='✦ GYMGUILD — MILESTONE QUESTS ✦',
        description=(
            'Complete special Guild achievements to earn permanent one-time XP rewards. '
            'Select **My Progress** to view what you have unlocked privately, '
            'or **Submit Order XP** to send your one-time Shopify order request for approval.\n\n'
            '**All six milestones unlock a one-time +500 XP Main Quest Completion bonus!**'
        ),
        colour=discord.Colour.from_rgb(104, 163, 153),
    )
    for kind, name, xp, description in MILESTONE_QUESTS:
        embed.add_field(
            name=f'{MILESTONE_EMOJI[kind]} {name} — {xp} XP',
            value=description + '\n\u200b',
            inline=False,
        )
    embed.set_footer(text='GYMGUILD ✦ Where culture meets strength')
    return embed


def boost_milestone_message(gid: int, uid: int, currently_boosting: bool) -> str:
    """Provide directions without attempting to open Discord's native purchase UI."""
    with connect() as db:
        awarded = db.execute(
            "SELECT 1 FROM milestone_claims WHERE guild_id=? AND user_id=? AND kind='boost1'",
            (gid, uid),
        ).fetchone() is not None
    if awarded:
        with connect() as db:
            total = db.execute(
                "SELECT amount FROM xp_log "
                "WHERE guild_id=? AND user_id=? AND reason='boost1' AND amount>0 "
                "ORDER BY id DESC LIMIT 1",
                (gid, uid),
            ).fetchone()
        amount = f'{total["amount"]} XP granted' if total else 'XP reward claimed'
        return (
            f'💎 **Boost milestone complete — {amount}!** '
            'Thank you for supporting GYMGUILD and helping our community grow. ✦'
        )
    if currently_boosting:
        return (
            '💎 **Thanks for boosting GYMGUILD!** Your boost is active, but the '
            'milestone reward has not been recorded yet. Please ask a moderator '
            'to check your reward before boosting again.'
        )
    return (
        '💎 **Boost GYMGUILD**\n'
        'Discord does not allow bots to open the native Server Boost checkout window.\n\n'
        '**To boost this server:**\n'
        '1. Click **GYMGUILD✦** at the top-left of the channel list.\n'
        '2. Select **Server Boost** (or open **Server Boosts** in the sidebar).\n'
        '3. Choose **Boost This Server** and follow Discord’s confirmation steps.\n\n'
        f'Once Discord confirms your first boost, GuildKeeper awards **{MILESTONE_XP["boost1"]} XP** automatically. '
        'Boosting may use an existing Nitro boost or require payment; check Discord’s price before confirming.'
    )


class MilestoneBoardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='My Progress', emoji='🏆', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:milestones:progress')
    async def my_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('Only available in GYMGUILD.', ephemeral=True)
        await interaction.response.send_message(
            embed=milestone_quests_embed(interaction.guild_id, interaction.user.id),
            view=MilestoneQuestsView(interaction.user.id), ephemeral=True,
        )

    @discord.ui.button(label='Submit Order XP', emoji='🛍️', style=discord.ButtonStyle.primary,
                       custom_id='guildkeeper:milestones:submit_order')
    async def submit_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('Only available in GYMGUILD.', ephemeral=True)
        await open_order_submission(interaction)

    @discord.ui.button(label='Boost Server', emoji='💎', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:milestones:boost_server')
    async def boost_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                'Only available in GYMGUILD.', ephemeral=True)
        await interaction.response.send_message(
            boost_milestone_message(
                interaction.guild_id, interaction.user.id,
                interaction.user.premium_since is not None,
            ), ephemeral=True,
        )


@bot.tree.command(
    name='admin_milestone_post',
    description='Moderator: post or refresh the permanent Milestone Quests panel',
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_milestone_post(
    interaction: discord.Interaction, channel: discord.TextChannel,
):
    if interaction.guild_id != GUILD_ID or channel.guild.id != GUILD_ID:
        return await interaction.response.send_message(
            'Choose a text channel in GYMGUILD.', ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    config_key = f'milestone-board-{GUILD_ID}'
    with connect() as db:
        row = db.execute(
            'SELECT value FROM v12_config WHERE key=?', (config_key,),
        ).fetchone()
    previous = None
    previous_channel = None
    if row:
        try:
            channel_id, message_id = map(int, row['value'].split(':'))
            previous_channel = interaction.guild.get_channel(channel_id)
            if isinstance(previous_channel, discord.TextChannel):
                previous = await previous_channel.fetch_message(message_id)
        except (ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
            previous = None
    try:
        if previous is not None and previous_channel.id == channel.id:
            await previous.edit(embed=milestone_board_embed(), view=MilestoneBoardView())
            message = previous
        else:
            message = await channel.send(
                embed=milestone_board_embed(), view=MilestoneBoardView())
            if previous is not None:
                try:
                    await previous.delete()
                except (discord.Forbidden, discord.HTTPException):
                    log.warning('Old milestone board could not be deleted')
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Failed to post milestone board')
        return await interaction.followup.send(
            'Could not post the board. Check View Channel, Send Messages, '
            'Embed Links and Manage Messages permissions.', ephemeral=True)
    with connect() as db:
        db.execute(
            'INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
            (config_key, f'{channel.id}:{message.id}'),
        )
    await interaction.followup.send(
        f'✅ Milestone Quests panel posted or refreshed in {channel.mention}.', ephemeral=True,
    )


class MilestoneQuestsView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                'This Milestone Quests panel belongs to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Refresh Progress', emoji='🔄', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=milestone_quests_embed(interaction.guild_id, interaction.user.id),
            view=self,
        )


async def open_order_submission(interaction: discord.Interaction) -> None:
    """Show the order modal only before a member makes their lifetime request."""
    if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            'Only available in GYMGUILD.', ephemeral=True)
    status = order_submission_status(interaction.guild_id, interaction.user.id)
    if status == 'approved':
        return await interaction.response.send_message(
            '✅ **Order XP granted — milestone objective complete.**',
            ephemeral=True,
        )
    if status == 'pending':
        return await interaction.response.send_message(
            '⏳ Your Order XP request is awaiting moderator approval. '
            'Use Cancel Request below to withdraw it and submit a replacement.',
            embed=order_xp_embed(interaction.guild_id, interaction.user.id),
            view=OrderXPView(interaction.user.id, interaction.guild_id),
            ephemeral=True,
        )
    await interaction.response.send_modal(OrderReferenceModal())


class OrderReferenceModal(discord.ui.Modal, title='Submit a GYMGUILD order'):
    order_reference = discord.ui.TextInput(
        label='Shopify order number', placeholder='e.g. #1001',
        min_length=1, max_length=80, required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await submit_order_from_interaction(interaction, str(self.order_reference))


class OrderXPView(discord.ui.View):
    """Private request-status view; cancel is available only while pending."""
    def __init__(self, user_id: int, guild_id: int | None = None):
        super().__init__(timeout=300)
        self.user_id = user_id
        if guild_id is not None:
            self.update_controls(guild_id)

    def update_controls(self, guild_id: int):
        self.cancel_request.disabled = order_submission_status(guild_id, self.user_id) != 'pending'

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('This order panel belongs to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Refresh Status', emoji='🔄', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.update_controls(interaction.guild_id)
        await interaction.response.edit_message(
            content='✦ Your latest order XP status',
            embed=order_xp_embed(interaction.guild_id, interaction.user.id), view=self,
        )

    @discord.ui.button(label='Cancel Request', emoji='✖️', style=discord.ButtonStyle.danger)
    async def cancel_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('Only available in GYMGUILD.', ephemeral=True)
        result, order_id, channel_id, message_id = cancel_purchase_order(
            interaction.guild_id, interaction.user.id)
        if result == 'cancelled':
            await mark_cancelled_mod_review(interaction.guild, channel_id, message_id, order_id)
            notice = ('🚫 **Order XP request cancelled.** No XP awarded. '
                      'You can submit a replacement through the public Milestone Quests panel.')
        elif result == 'approved':
            notice = '✅ Order XP granted — milestone objective complete. Approved orders cannot be cancelled.'
        else:
            notice = 'There is no pending Order XP request to cancel.'
        self.update_controls(interaction.guild_id)
        await interaction.response.edit_message(
            content=notice, embed=order_xp_embed(interaction.guild_id, interaction.user.id),
            view=self,
        )


async def mark_cancelled_mod_review(guild, channel_id, message_id, order_id):
    """Retire a cancelled order review card; DB state is already authoritative."""
    if channel_id is None or message_id is None:
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        msg = await channel.fetch_message(message_id)
        embed = msg.embeds[0].copy() if msg.embeds else None
        if embed:
            embed.add_field(name='Decision', value='🚫 Cancelled by member — no XP awarded.', inline=False)
        await msg.edit(embed=embed, view=None)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        log.warning('Could not retire cancelled order review card %s', order_id)


class PurchaseReviewView(discord.ui.View):
    def __init__(self, order_id: int):
        super().__init__(timeout=None)
        self.order_id = order_id
        # Stable custom IDs make pending review buttons persistent across restarts.
        self.approve.custom_id = f'guildkeeper:order:approve:{order_id}'
        self.reject.custom_id = f'guildkeeper:order:reject:{order_id}'

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != GUILD_ID:
            await interaction.response.send_message('Not available in this server.', ephemeral=True)
            return False
        if not is_ticket_mod(interaction):
            await interaction.response.send_message('Only moderators can review orders.', ephemeral=True)
            return False
        return True

    async def resolve(self, interaction: discord.Interaction, approved: bool):
        await interaction.response.defer(ephemeral=True)
        result, uid = review_purchase_order(
            interaction.guild_id, self.order_id, interaction.user.id, approved)
        if result == 'not_pending':
            return await interaction.followup.send(
                'This order was already reviewed or no longer exists.', ephemeral=True)
        member = interaction.guild.get_member(uid)
        if result == 'awarded' and member:
            await update_rank(member, get_xp(interaction.guild_id, uid))
        try:
            original = interaction.message
            if original is not None:
                embed = original.embeds[0] if original.embeds else None
                if embed:
                    embed = embed.copy()
                    embed.add_field(
                        name='Decision', value=(
                            f'{"✅ Approved" if approved else "❌ Rejected"} by {interaction.user.mention} '
                            f'({"+" + str(MILESTONE_XP["purchase1"]) + " XP" if result == "awarded" else "0 XP"})'
                        ), inline=False,
                    )
                await original.edit(embed=embed, view=None)
        except (discord.Forbidden, discord.HTTPException):
            log.warning('Could not update reviewed purchase message %s', self.order_id)
        await interaction.followup.send(
            f'{"✅ Approved" if approved else "❌ Rejected"} order for <@{uid}>. '
            f'{str(MILESTONE_XP["purchase1"]) + " XP awarded." if result == "awarded" else "No extra XP awarded."}',
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label='Approve', emoji='✅', style=discord.ButtonStyle.success,
                       custom_id='guildkeeper:order:approve')
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, True)

    @discord.ui.button(label='Reject', emoji='❌', style=discord.ButtonStyle.danger,
                       custom_id='guildkeeper:order:reject')
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, False)


@bot.tree.command(name='milestone_purchase', description='Submit your GYMGUILD order number for review')
@app_commands.guild_only()
async def milestone_purchase(interaction: discord.Interaction, order_reference: str):
    await submit_order_from_interaction(interaction, order_reference)


@bot.tree.command(name='admin_milestone_pending', description='Moderator: privately list orders awaiting review')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_milestone_pending(interaction: discord.Interaction):
    if interaction.guild_id != GUILD_ID:
        return await interaction.response.send_message('This command belongs to GYMGUILD.', ephemeral=True)
    with connect() as db:
        rows = db.execute(
            "SELECT id,user_id,order_reference FROM purchase_orders "
            "WHERE guild_id=? AND status='pending' ORDER BY id LIMIT 15",
            (interaction.guild_id,),
        ).fetchall()
    if not rows:
        return await interaction.response.send_message('✅ No pending orders.', ephemeral=True)
    lines = [
        f'<@{row["user_id"]}> — `{discord.utils.escape_markdown(row["order_reference"])}` '
        f'(order #{row["id"]})'
        for row in rows
    ]
    await interaction.response.send_message(
        '🛍️ **Pending order reviews**\n' + '\n'.join(lines)
        + '\nUse the buttons in the moderator review channel, or /admin_milestone_review.',
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name='admin_milestone_review',description='Moderator: approve or reject the oldest pending order')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_milestone_review(interaction: discord.Interaction, member: discord.Member, approve: bool):
    if interaction.guild_id != GUILD_ID:
        return await reply(interaction, 'This command belongs to GYMGUILD.')
    result = review_first_purchase_milestone(interaction.guild_id, member.id, approve)
    if result == 'not_pending':
        return await reply(interaction, 'No pending order for this member.')
    if result == 'awarded':
        await update_rank(member, get_xp(interaction.guild_id, member.id))
        return await reply(interaction, f'✅ First order approved: **+{MILESTONE_XP["purchase1"]} XP** for {member.mention}.')
    if result == 'already_awarded':
        return await reply(interaction, f'✅ Additional order approved for {member.mention}; no further XP (one-time reward).')
    return await reply(interaction, f'❌ Order rejected for {member.mention}. They may resubmit.')


REWARD_RANKS={'Master':10,'Legend':15,'Paragon':20}

async def create_reward_ticket(interaction: discord.Interaction, rank: str):
    if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message('Use this in the GYMGUILD server.', ephemeral=True)
    if rank not in REWARD_RANKS:
        return await interaction.response.send_message('Unknown reward.', ephemeral=True)
    if not REWARD_TICKETS_CATEGORY_ID or not MOD_ROLE_ID:
        return await interaction.response.send_message('Reward ticket category and moderator role are not configured.', ephemeral=True)
    # Reward eligibility is permanent once unlocked, even after an XP demotion.
    rank_for(get_xp(interaction.guild_id, interaction.user.id), interaction.guild_id, interaction.user.id)
    with connect() as db:
        unlocked = db.execute('SELECT 1 FROM rank_unlocks WHERE guild_id=? AND user_id=? AND rank=?',
                              (interaction.guild_id, interaction.user.id, rank)).fetchone()
    if not unlocked:
        return await interaction.response.send_message(
            f'You must first unlock **{rank}** with its XP and active-streak requirements to claim this reward.', ephemeral=True)
    guild = interaction.guild
    category = guild.get_channel(REWARD_TICKETS_CATEGORY_ID)
    modrole = guild.get_role(MOD_ROLE_ID)
    if not isinstance(category, discord.CategoryChannel) or modrole is None:
        return await interaction.response.send_message('Ticket configuration is invalid. Contact an administrator.', ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    with connect() as db:
        existing = db.execute('SELECT channel_id,status FROM reward_tickets WHERE guild_id=? AND user_id=? AND rank=?',
                              (guild.id, interaction.user.id, rank)).fetchone()
    if existing:
        return await interaction.followup.send(
            f'This **{rank}** reward was already requested ({existing["status"]}). '
            'Each rank discount can only be requested once.', ephemeral=True)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        modrole: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    try:
        channel = await guild.create_text_channel(
            f'r-{rank.lower()}-{interaction.user.id % 100000:05d}', category=category,
            overwrites=overwrites, reason='Private Guildkeeper reward request')
    except discord.HTTPException:
        log.exception('Could not create reward channel')
        return await interaction.followup.send('Could not create a private ticket. Check bot permissions.', ephemeral=True)
    try:
        with connect() as db:
            db.execute('INSERT INTO reward_tickets(guild_id,user_id,rank,channel_id,status,requested_at) VALUES (?,?,?,?,?,?)',
                       (guild.id, interaction.user.id, rank, channel.id, 'open', utc_now().isoformat()))
    except sqlite3.IntegrityError:
        await channel.delete(reason='Duplicate reward ticket request')
        return await interaction.followup.send('You already requested this reward.', ephemeral=True)
    except sqlite3.Error:
        log.exception('Could not save reward ticket')
        await channel.delete(reason='Failed to save reward ticket')
        return await interaction.followup.send('Unable to save the ticket. Please try again.', ephemeral=True)
    await channel.send(
        f'✦ **{rank} reward — {REWARD_RANKS[rank]}% off**\n'
        f'Requested by {interaction.user.mention}.\n\n'
        'An admin will generate a one-use Shopify discount code and share it privately here. '
        'A moderator can mark the reward fulfilled using the panel below. '
        'Tickets close automatically after 7 days.',
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        view=RewardTicketAdminView())
    await interaction.followup.send(f'🎟️ Your private reward ticket is ready: {channel.mention}', ephemeral=True)



class RewardChoiceView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('This reward menu belongs to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Master · 10%', style=discord.ButtonStyle.secondary)
    async def master(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_reward_ticket(interaction, 'Master')

    @discord.ui.button(label='Legend · 15%', style=discord.ButtonStyle.secondary)
    async def legend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_reward_ticket(interaction, 'Legend')

    @discord.ui.button(label='Paragon · 20%', style=discord.ButtonStyle.secondary)
    async def paragon(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_reward_ticket(interaction, 'Paragon')

    @discord.ui.button(label='Refresh Rewards', emoji='🔄', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('Only available in GYMGUILD.', ephemeral=True)
        await interaction.response.edit_message(
            content=guild_rewards_progress_text(interaction.guild_id, interaction.user.id),
            view=self,
        )


def is_ticket_mod(interaction: discord.Interaction) -> bool:
    member = interaction.user
    return isinstance(member, discord.Member) and (
        member.guild_permissions.manage_guild or
        (MOD_ROLE_ID and any(role.id == MOD_ROLE_ID for role in member.roles)))


def reward_ticket_row(guild_id: int, channel_id: int):
    with connect() as db:
        return db.execute('SELECT * FROM reward_tickets WHERE guild_id=? AND channel_id=?',
                          (guild_id, channel_id)).fetchone()


async def log_reward_ticket(guild: discord.Guild, row, actor: str, reason: str, automatic=False):
    channel = guild.get_channel(MOD_REVIEW_CHANNEL_ID) if MOD_REVIEW_CHANNEL_ID else None
    if not isinstance(channel, discord.TextChannel):
        log.warning('Reward ticket closed without mod log: configure MOD_REVIEW_CHANNEL_ID')
        return False
    embed = discord.Embed(title='✦ Reward Ticket Closed', colour=discord.Colour.from_rgb(104, 163, 153),
                          timestamp=utc_now())
    embed.add_field(name='Ticket', value=f'`{row["channel_id"]}` · {row["rank"]}', inline=False)
    embed.add_field(name='Member', value=f'<@{row["user_id"]}> (`{row["user_id"]}`)', inline=False)
    embed.add_field(name='Reward', value=f'{REWARD_RANKS.get(row["rank"], 0)}% off', inline=True)
    embed.add_field(name='Fulfilled', value='Yes' if row['fulfilled_at'] else 'No', inline=True)
    embed.add_field(name='Closed by', value='Automatic expiry' if automatic else actor, inline=False)
    embed.add_field(name='Reason', value=reason[:1024], inline=False)
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except discord.HTTPException:
        log.exception('Failed to log closed reward ticket %s', row['channel_id'])
        return False


async def close_reward_ticket(guild: discord.Guild, channel_id: int, actor: str,
                              reason: str, automatic=False):
    # Record closure atomically before deleting the private channel.
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM reward_tickets WHERE guild_id=? AND channel_id=?',
                         (guild.id, channel_id)).fetchone()
        if not row or row['status'] == 'closed':
            return False, 'Ticket already closed or not found.'
        db.execute('UPDATE reward_tickets SET status="closed",closed_at=?,closed_by=?,close_reason=? '
                   'WHERE id=?', (utc_now().isoformat(), actor, reason, row['id']))
    logged = await log_reward_ticket(guild, row, actor, reason, automatic)
    channel = guild.get_channel(channel_id)
    if channel is not None:
        try:
            await channel.delete(reason=f'Reward ticket closed: {reason[:350]}')
        except discord.HTTPException:
            log.exception('Could not delete closed reward ticket channel %s', channel_id)
            return True, 'Closed in the database, but could not delete the channel. Check Manage Channels.'
    return True, 'Ticket closed.' + ('' if logged else ' Warning: moderator log could not be posted.')


class RewardCloseReasonModal(discord.ui.Modal, title='Close reward ticket'):
    reason = discord.ui.TextInput(label='Reason for closing', style=discord.TextStyle.paragraph,
                                  placeholder='e.g. Discount code delivered and confirmed',
                                  min_length=3, max_length=500, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_ticket_mod(interaction):
            return await interaction.response.send_message('Only moderators can close tickets.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        success, message = await close_reward_ticket(interaction.guild, interaction.channel_id,
                                                      str(interaction.user.id), str(self.reason.value))
        await interaction.followup.send(('✅ ' if success else '⚠️ ') + message, ephemeral=True)


class RewardCloseConfirmView(discord.ui.View):
    def __init__(self, mod_id: int):
        super().__init__(timeout=120)
        self.mod_id = mod_id

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.mod_id or not is_ticket_mod(interaction):
            await interaction.response.send_message('This confirmation is for the requesting moderator.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Yes, Close Ticket', style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RewardCloseReasonModal())

    @discord.ui.button(label='No, Keep Open', style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content='Ticket remains open.', view=None)


class RewardTicketAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction):
        if not is_ticket_mod(interaction):
            await interaction.response.send_message('Only moderators can manage reward tickets.', ephemeral=True)
            return False
        if not reward_ticket_row(interaction.guild_id, interaction.channel_id):
            await interaction.response.send_message('This is not a registered reward ticket.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Fulfil Reward', emoji='🎁', style=discord.ButtonStyle.success,
                       custom_id='guildkeeper:reward:fulfil')
    async def fulfil(self, interaction: discord.Interaction, button: discord.ui.Button):
        with connect() as db:
            cur = db.execute('UPDATE reward_tickets SET status="fulfilled",fulfilled_at=? '
                             'WHERE guild_id=? AND channel_id=? AND status="open"',
                             (utc_now().isoformat(), interaction.guild_id, interaction.channel_id))
        if not cur.rowcount:
            return await interaction.response.send_message('This ticket is already fulfilled or closed.', ephemeral=True)
        await interaction.response.send_message('🎁 Reward marked as fulfilled. **Close ticket?**',
                                                view=RewardCloseConfirmView(interaction.user.id), ephemeral=True)
        await interaction.channel.send(f'✅ Reward fulfilled by {interaction.user.mention}.',
                                       allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Close Ticket', emoji='🔒', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:reward:close')
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = reward_ticket_row(interaction.guild_id, interaction.channel_id)
        if row['status'] == 'closed':
            return await interaction.response.send_message('This ticket is already closed.', ephemeral=True)
        await interaction.response.send_message('🔒 **Close ticket?** A reason is required.',
                                                view=RewardCloseConfirmView(interaction.user.id), ephemeral=True)


@bot.tree.command(name='admin_reward_fulfill', description='Moderator: mark a private reward ticket fulfilled')
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_reward_fulfill(interaction: discord.Interaction):
    with connect() as db:
        cur = db.execute('UPDATE reward_tickets SET status="fulfilled",fulfilled_at=? '
                         'WHERE guild_id=? AND channel_id=? AND status="open"',
                         (utc_now().isoformat(), interaction.guild_id, interaction.channel_id))
    if cur.rowcount:
        await interaction.response.send_message('🎁 Reward fulfilled. **Close ticket?**',
                                                view=RewardCloseConfirmView(interaction.user.id), ephemeral=True)
    else:
        await interaction.response.send_message('No open unfulfilled reward ticket found here.', ephemeral=True)


async def expire_reward_tickets():
    cutoff = (utc_now() - timedelta(days=7)).isoformat()
    with connect() as db:
        rows = db.execute('SELECT channel_id FROM reward_tickets WHERE guild_id=? '
                          'AND status IN ("open","fulfilled") AND requested_at<=?',
                          (GUILD_ID, cutoff)).fetchall()
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    for row in rows:
        try:
            await close_reward_ticket(guild, row['channel_id'], 'automatic',
                                      'Automatically closed after 7 days.', automatic=True)
        except Exception:
            log.exception('Automatic reward ticket expiry failed for %s', row['channel_id'])


@bot.tree.command(name='admin_reward_reset', description='Admin: reset one member rank reward so it can be requested again')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(member='Member whose reward request will be reset', rank='Reward rank to reset')
@app_commands.choices(rank=[
    app_commands.Choice(name='Master — 10% off', value='Master'),
    app_commands.Choice(name='Legend — 15% off', value='Legend'),
    app_commands.Choice(name='Paragon — 20% off', value='Paragon'),
])
async def admin_reward_reset(interaction: discord.Interaction, member: discord.Member,
                             rank: app_commands.Choice[str]):
    if interaction.guild_id != GUILD_ID:
        return await interaction.response.send_message('Use this in the GYMGUILD server.', ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        ticket = db.execute(
            'SELECT * FROM reward_tickets WHERE guild_id=? AND user_id=? AND rank=?',
            (interaction.guild_id, member.id, rank.value)).fetchone()
        if ticket:
            db.execute('DELETE FROM reward_tickets WHERE id=?', (ticket['id'],))
    if not ticket:
        return await interaction.followup.send(
            f'No **{rank.value}** reward request was recorded for {member.mention}; nothing changed. '
            'Their permanent rank unlocks were not changed.', ephemeral=True)
    # Removing the request makes the already-unlocked reward available again.
    # The unlock itself, XP, streak, and other rewards are intentionally untouched.
    deletion = 'No active ticket channel remained.'
    channel = interaction.guild.get_channel(ticket['channel_id'])
    if channel is not None:
        try:
            await channel.delete(reason=f'Reward reset by {interaction.user} for {member} ({rank.value})')
            deletion = 'The existing ticket channel was deleted.'
        except discord.HTTPException:
            log.exception('Failed to delete reward ticket channel on admin reset')
            deletion = 'WARNING: The existing ticket channel could not be deleted; remove it manually.'
    log_channel = interaction.guild.get_channel(MOD_REVIEW_CHANNEL_ID) if MOD_REVIEW_CHANNEL_ID else None
    if isinstance(log_channel, discord.TextChannel):
        embed = discord.Embed(title='✦ Reward Request Reset', colour=discord.Colour.orange(), timestamp=utc_now())
        embed.add_field(name='Member', value=f'{member.mention} (`{member.id}`)', inline=False)
        embed.add_field(name='Reward', value=f'{rank.value} — {REWARD_RANKS[rank.value]}% off', inline=True)
        embed.add_field(name='Previous status', value=ticket['status'], inline=True)
        embed.add_field(name='Reset by', value=interaction.user.mention, inline=False)
        embed.add_field(name='Ticket channel', value=f'`{ticket["channel_id"]}`', inline=False)
        try:
            await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception('Failed to post reward reset audit log')
            deletion += ' WARNING: Could not post moderator log.'
    else:
        deletion += ' WARNING: Moderator log channel is not configured.'
    await interaction.followup.send(
        f'♻️ Reset **{rank.value}** reward request for {member.mention}. '
        'They can request it again from **#stats-and-streaks** if previously unlocked. '
        f'{deletion} XP, streaks, rank unlocks and other rewards were unchanged.', ephemeral=True)


@bot.tree.command(name='admin_pause_reset', description='Admin: clear all recorded pauses for a member')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_pause_reset(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        count = db.execute('SELECT COUNT(*) AS n FROM pauses WHERE guild_id=? AND user_id=?',
                           (interaction.guild_id, member.id)).fetchone()['n']
        db.execute('DELETE FROM pauses WHERE guild_id=? AND user_id=?',
                   (interaction.guild_id, member.id))
        db.execute('DELETE FROM resumed_pauses WHERE guild_id=? AND user_id=?',
                   (interaction.guild_id, member.id))
    await interaction.followup.send(
        f'♻️ Cleared **{count}** recorded pause(s) for {member.mention}. '
        'Their pause allowance is reset to 2/2. Previous weekly quest progress and XP were not changed. '
        'Be aware that removing historical pauses can change how their streak is calculated.',
        ephemeral=True)


@bot.tree.command(name='admin_weekly_reset', description='Moderator: reset a member current Weekly Side Quest for testing')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_weekly_reset(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    gid, uid, wk = interaction.guild_id, member.id, week_key()
    rewards = {'training1': 50, 'training2': 50, 'gym': 25, 'hobby': 25}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        actions = db.execute(
            'SELECT kind, fingerprint FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?',
            (gid, uid, wk)).fetchall()
        completed = bool(db.execute(
            'SELECT 1 FROM weekly_completions WHERE guild_id=? AND user_id=? AND week_key=?',
            (gid, uid, wk)).fetchone())
        refunded = sum(rewards.get(row['kind'], 0) for row in actions) + (50 if completed else 0)
        for row in actions:
            db.execute('DELETE FROM message_fingerprints WHERE guild_id=? AND user_id=? AND fingerprint=?',
                       (gid, uid, row['fingerprint']))
        db.execute('DELETE FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?', (gid, uid, wk))
        db.execute('DELETE FROM weekly_completions WHERE guild_id=? AND user_id=? AND week_key=?', (gid, uid, wk))
        if refunded:
            add_xp(gid, uid, -refunded, f'Admin Weekly Side Quest reset {wk} by {interaction.user.id}', db)
    await update_rank(member, get_xp(gid, uid))
    await interaction.followup.send(
        f'♻️ Reset {member.mention} Weekly Side Quest for **{wk}** to **0/4**. '
        f'Removed **{refunded} XP** from this week’s four objectives and completion bonus. '
        'Starter Quest claims, one-time milestones, previous weeks and pause history remain unchanged. '
        'New qualifying posts are required to earn progress again.',
        ephemeral=True)


@bot.tree.command(name='admin_weekly_settle',description='Moderator: process missed-week penalties and streak milestones')
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_weekly_settle(interaction:discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await settle_previous_weeks()
    await interaction.followup.send('Weekly settlements processed.',ephemeral=True)


def weekly_board_embed():
    """The same four objectives that the message tracker awards XP for."""
    reset = next_reset()
    embed = discord.Embed(
        title='✦ GYMGUILD — WEEKLY SIDE QUEST',
        description='A new quest week has begun! Complete all four objectives to earn **200 XP**.',
        colour=discord.Colour.from_rgb(104, 163, 153),
    )
    embed.add_field(name='⚔️ 1 · First Flame — 50 XP', value='Post a training activity in <#%s> **with an image or file attached as evidence**.' % TRAINING_LOG_CHANNEL_ID, inline=False)
    embed.add_field(name='\u200b', value='\u200b', inline=False)
    embed.add_field(name='🏃 2 · Fuel the Fire — 50 XP', value='Post a second training activity in <#%s> **with an image or file attached as evidence**, on a **different UK calendar day**.' % TRAINING_LOG_CHANNEL_ID, inline=False)
    embed.add_field(name='\u200b', value='\u200b', inline=False)
    embed.add_field(name='🏋️ 3 · Flex Text — 25 XP', value='Post a message in any channel within ✦ THE FORGE ✦.', inline=False)
    embed.add_field(name='\u200b', value='\u200b', inline=False)
    embed.add_field(name='🎮 4 · Skill Share — 25 XP', value='Post a message in any channel within ✦ OFF THE CLOCK ✦.', inline=False)
    embed.add_field(name='​', value='​', inline=False)
    embed.add_field(name='🏆 Completion bonus — 50 XP', value='Complete all four objectives to claim the bonus automatically.', inline=False)
    embed.add_field(name='━━━━━━━━━━━━━━━━━━━━', value='**Your progress & information**', inline=False)
    embed.add_field(name='Track your progress', value='Complete all four objectives to **automatically turn in your weekly quest** and advance your streak. The **Turn In Quest** button confirms your completion and shows your streak. Use **View Progress**, `/weekly_progress`, `/streak` or `/pause_status`. Messages must be meaningful (at least 25 characters and 5 words).', inline=False)
    embed.add_field(name='Next reset', value=f'<t:{int(reset.timestamp())}:F> · <t:{int(reset.timestamp())}:R>', inline=False)
    embed.set_footer(text=f'Guildkeeper Weekly Side Quest • {week_key()}')
    return embed


async def post_weekly_board(force=False):
    """Post at most one board per quest week, including after bot downtime."""
    wk = week_key()
    post_key = 'weekly-board-' + wk
    with connect() as db:
        if not force and db.execute('SELECT 1 FROM schedule_posts WHERE guild_id=? AND post_key=?', (GUILD_ID,post_key)).fetchone():
            return
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if not isinstance(channel,discord.TextChannel) or channel.guild.id != GUILD_ID:
        return
    try:
        message = await channel.send(embed=weekly_board_embed(), view=WeeklyTurnInView())
    except discord.HTTPException:
        log.exception('Unable to post weekly quest board; will retry')
        return
    # Record only after the post succeeds. This also prevents duplicates on restart.
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO schedule_posts VALUES (?,?)',(GUILD_ID,post_key))
    # Pinning is optional: the bot needs Manage Messages for it.
    try:
        for pinned in await channel.pins():
            if pinned.id == message.id or pinned.author.id != bot.user.id:
                continue
            if pinned.embeds and (pinned.embeds[0].footer.text or '').startswith(('Guildkeeper Weekly Trial', 'Guildkeeper Weekly Side Quest')):
                await pinned.unpin(reason='Replace previous Guildkeeper weekly quest board')
        await message.pin(reason='Current Guildkeeper weekly quest board')
    except (discord.Forbidden, discord.HTTPException):
        log.warning('Weekly quest board posted but could not update pins (Manage Messages permission required)')


@bot.tree.command(name='admin_weekly_post', description='Moderator: post or refresh this week’s quest board')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_weekly_post(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    channel = bot.get_channel(QUESTS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
        await interaction.followup.send('Quest channel not found. Check QUESTS_CHANNEL_ID in .env.', ephemeral=True)
        return
    try:
        await post_weekly_board(force=True)
    except discord.HTTPException:
        log.exception('Manual weekly board posting failed')
        await interaction.followup.send('Unable to post the board. Check bot channel permissions.', ephemeral=True)
        return
    await interaction.followup.send(f'Weekly quest board refresh requested in {channel.mention}.', ephemeral=True)


def leaderboard_embed():
    with connect() as db:
        rows = db.execute(
            'SELECT user_id, xp FROM members WHERE guild_id=? ORDER BY xp DESC, user_id LIMIT 10',
            (GUILD_ID,)).fetchall()
    embed = discord.Embed(
        title='🏆 GYMGUILD — LEADERBOARD',
        description='Top 10 Guild members ranked by total XP.',
        color=0x71A7A2)
    if rows:
        for position, row in enumerate(rows, 1):
            embed.add_field(
                name=f'#{position} — {row["xp"]:,} XP',
                value=f'<@{row["user_id"]}>', inline=False)
    else:
        embed.add_field(name='No rankings yet', value='Earn XP by completing quests!', inline=False)
    embed.set_footer(text='GYMGUILD ✦ Updated daily at midnight UK time')
    return embed


async def update_permanent_leaderboard(force=False):
    """Edit the saved message in place; create one only if it does not exist."""
    if not LEADERBOARD_CHANNEL_ID:
        return False, 'Set LEADERBOARD_CHANNEL_ID in .env first.'
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
        return False, 'Leaderboard channel not found or inaccessible.'
    today = datetime.now(TZ).date().isoformat()
    with connect() as db:
        saved = db.execute(
            "SELECT value FROM v12_config WHERE key=?", (f'leaderboard-post-{GUILD_ID}',)).fetchone()
        last = db.execute(
            "SELECT value FROM v12_config WHERE key=?", (f'leaderboard-date-{GUILD_ID}',)).fetchone()
    if not force and not saved:
        return False, 'Leaderboard has not been posted by an admin yet.'
    if not force and last and last['value'] == today and saved:
        return True, 'Leaderboard is already current.'
    message = None
    if saved:
        try:
            message = await channel.fetch_message(int(saved['value']))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            pass
    embed = leaderboard_embed()
    try:
        if message:
            await message.edit(embed=embed)
        else:
            message = await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not publish Guild leaderboard')
        return False, 'Could not publish leaderboard. Check View Channel, Send Messages and Embed Links permissions.'
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                   (f'leaderboard-post-{GUILD_ID}', str(message.id)))
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                   (f'leaderboard-date-{GUILD_ID}', today))
    return True, f'Leaderboard updated in {channel.mention}.'


PLAYER_STATS_CHANNEL_ID = int(os.getenv('PLAYER_STATS_CHANNEL_ID', '0') or 0)


def player_stats_embed():
    embed = discord.Embed(
        title='✦ GYMGUILD — PLAYER HUB',
        description=('Welcome to your Guildkeeper player hub! Select a button below '
                     'to privately view your stats, streak and ranks, manage pauses, and choose Guild Blast notifications.'),
        color=0x71A7A2)
    embed.add_field(name='👤 Profile', value='Your XP, rank, and next milestone', inline=False)
    embed.add_field(name='🔥 Streak', value='Your completed weeks and current streak', inline=False)
    embed.add_field(name='🏆 Ranks', value='All Guild XP ranks and requirements', inline=False)
    embed.add_field(name='⏸️ Activate Pause', value='Use a pause for the current weekly quest (2 per rolling 13 weeks)', inline=False)
    embed.add_field(name='▶️ Resume XP', value='End an active pause early and resume earning XP; the pause is not refunded', inline=False)
    embed.add_field(name='🔔 Guild Blast', value='Opt in or out of Guild announcement mentions at any time', inline=False)
    embed.add_field(name='🎟️ Claim Reward', value='See eligible one-time rank rewards and open a private ticket with admins', inline=False)
    embed.add_field(name='🏆 Milestone Quests', value='See your milestones and progress, and submit Shopify orders', inline=False)
    embed.set_footer(text='GYMGUILD ✦ Where culture meets strength')
    return embed


def profile_progress_text(gid: int, uid: int, display_name: str) -> str:
    xp = get_xp(gid, uid)
    next_rank = next(((name, threshold) for name, threshold in RANKS if threshold > xp), None)
    upcoming = (f'Next: **{next_rank[0]}** in **{next_rank[1]-xp} XP**'
                if next_rank else 'Maximum rank reached ✦')
    notice = streak_blocked_rank_notice(gid, uid, xp)
    return (
        f'✦ **{display_name}** — Rank: **{rank_for(xp, gid, uid)}** · XP: **{xp}**\n'
        f'🎯 Lifetime objectives completed: **{lifetime_objectives(gid, uid)}**\n'
        f'Longest streak: **{longest_streak(gid, uid)} weeks**\n\n'
        f'**Rewards**\n{reward_eligibility_text(gid, uid)}\n{upcoming}'
        + ('\n\n' + notice if notice else '')
    )


def streak_progress_text(gid: int, uid: int) -> str:
    return (
        f'🔥 **Your Weekly Streak** — Completed weeks: **{completed_weeks(gid, uid)}** '
        f'· Current streak: **{active_streak(gid, uid)}**. '
        'Paused weeks preserve but do not increase your streak.'
    )


def ranks_progress_embed(gid: int, uid: int) -> discord.Embed:
    embed = discord.Embed(title='✦ GYMGUILD — RANKS', color=0x71A7A2)
    for name, threshold in RANKS:
        requirement = (f'{threshold:,} XP + **{STREAK_REQUIREMENTS[name]}-week active streak**'
                       if name in STREAK_REQUIREMENTS else f'{threshold:,} XP')
        reward = f' · **{REWARD_RANKS[name]}% reward**' if name in REWARD_RANKS else ''
        embed.add_field(name=f'✦ {name}', value=requirement + reward, inline=False)
    embed.add_field(name='🔒 Rank & reward rules', value=(
        'Streak requirements must be met **while your streak is active**. '
        'Paused weeks protect a streak but do not add to it.\n\n'
        'Unlocked ranks survive broken streaks. Only losing XP below a rank threshold can demote you; '
        'earning the XP back restores that rank without repeating the streak.\n\n'
        '**Each rank reward unlocks permanently and can be claimed once**, '
        'even if you later drop to a lower rank.'), inline=False)
    notice = streak_blocked_rank_notice(gid, uid, get_xp(gid, uid))
    if notice:
        embed.add_field(name='🔒 Your rank progress', value=notice, inline=False)
    embed.set_footer(text='GYMGUILD ✦ Where culture meets strength')
    return embed


def guild_rewards_progress_text(gid: int, uid: int) -> str:
    return (
        '🎟️ **Guild Rewards**\n\n' + reward_eligibility_text(gid, uid)
        + '\n\nSelect an **eligible, unclaimed** rank reward below to open '
          'a private ticket with Guild admins. Rank rewards are one-time Shopify '
          'discount codes, fulfilled manually by an admin.'
    )


def private_progress_content(kind: str, interaction: discord.Interaction) -> dict:
    gid, uid = interaction.guild_id, interaction.user.id
    if kind == 'profile':
        return {'content': profile_progress_text(gid, uid, interaction.user.display_name)}
    if kind == 'streak':
        return {'content': streak_progress_text(gid, uid)}
    if kind == 'ranks':
        return {'embed': ranks_progress_embed(gid, uid)}
    if kind == 'weekly':
        return {'content': weekly_progress_text(gid, uid)}
    if kind == 'starter':
        return {'content': starter_progress_text(gid, uid)}
    raise ValueError(f'Unknown progress view: {kind}')


class PrivateProgressView(discord.ui.View):
    """Owner-only, short-lived refresh control for personal progress responses."""
    def __init__(self, user_id: int, kind: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.kind = kind

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                'This progress panel belongs to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Refresh Progress', emoji='🔄', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message(
                'This panel is only available in GYMGUILD.', ephemeral=True)
        data = private_progress_content(self.kind, interaction)
        await interaction.response.edit_message(
            content=data.get('content'), embed=data.get('embed'), view=self,
        )


class PlayerStatsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Profile', emoji='👤', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:stats:profile')
    async def profile_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            **private_progress_content('profile', interaction),
            view=PrivateProgressView(interaction.user.id, 'profile'), ephemeral=True)

    @discord.ui.button(label='Streak', emoji='🔥', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:stats:streak')
    async def streak_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            **private_progress_content('streak', interaction),
            view=PrivateProgressView(interaction.user.id, 'streak'), ephemeral=True)

    @discord.ui.button(label='Ranks', emoji='🏆', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:stats:ranks')
    async def ranks_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            **private_progress_content('ranks', interaction),
            view=PrivateProgressView(interaction.user.id, 'ranks'), ephemeral=True)

    @discord.ui.button(label='Activate Pause', emoji='⏸️', style=discord.ButtonStyle.primary,
                       custom_id='guildkeeper:stats:activate_pause', row=1)
    async def activate_pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('This button is not available here.', ephemeral=True)
        await interaction.response.send_message(
            '⏸️ **Are you sure you want to activate a pause?** '
            'This uses one of your two pauses in a rolling 13-week period for the current week.\n'
            + pause_allowance_text(interaction.guild_id, interaction.user.id),
            view=ConfirmPauseView(interaction.user.id), ephemeral=True)


    @discord.ui.button(label='Resume XP', emoji='▶️', style=discord.ButtonStyle.success,
                       custom_id='guildkeeper:stats:resume_xp', row=1)
    async def resume_xp_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('This button is not available here.', ephemeral=True)
        if not is_paused(interaction.guild_id, interaction.user.id, week_key()):
            return await interaction.response.send_message(
                '▶️ You do not currently have an active pause. XP is already enabled.\n'
                + pause_allowance_text(interaction.guild_id, interaction.user.id), ephemeral=True)
        await interaction.response.send_message(
            '▶️ **Resume earning XP?** Your pause will end immediately and you can earn weekly XP again. '
            '**This will NOT refund your pause** — it still counts toward your 2 pauses per rolling 13 weeks. '
            'The week will remain recorded as a pause for streak protection.\n'
            + pause_allowance_text(interaction.guild_id, interaction.user.id),
            view=ConfirmResumeView(interaction.user.id), ephemeral=True)


    @discord.ui.button(label='Guild Blast', emoji='🔔', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:stats:guild_blast', row=1)
    async def guild_blast_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message('This button is only available in the Guild.', ephemeral=True)
        role = interaction.guild.get_role(GUILD_BLAST_ROLE_ID) if GUILD_BLAST_ROLE_ID else None
        if role is None:
            return await interaction.response.send_message('Guild Blast is not configured. Ask an admin to set GUILD_BLAST_ROLE_ID.', ephemeral=True)
        enabled = role in interaction.user.roles
        await interaction.response.send_message(
            f'🔔 **Guild Blast** — Currently **{"enabled" if enabled else "disabled"}**.\n'
            'Guild Blast is an optional role used for announcements by admins and Guildkeeper. '
            'Choose below to change your preference.',
            view=GuildBlastToggleView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='Bot DMs', emoji='✉️', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:stats:bot_dms', row=1)
    async def bot_dms_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                'This option is available only in GYMGUILD.', ephemeral=True)
        enabled = bot_dm_enabled(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            bot_dm_preference_text(enabled),
            view=BotDMToggleView(interaction.user.id), ephemeral=True,
        )

    @discord.ui.button(label='Milestone Quests', emoji='🏆', style=discord.ButtonStyle.secondary,
                       custom_id='guildkeeper:stats:milestone_quests', row=2)
    async def milestone_quests_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('Only available in GYMGUILD.', ephemeral=True)
        await interaction.response.send_message(
            embed=milestone_quests_embed(interaction.guild_id, interaction.user.id),
            view=MilestoneQuestsView(interaction.user.id), ephemeral=True,
        )

    @discord.ui.button(label='Claim Reward', emoji='🎟️', style=discord.ButtonStyle.primary,
                       custom_id='guildkeeper:stats:reward_ticket', row=1)
    async def reward_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('This button is not available here.', ephemeral=True)
        await interaction.response.send_message(
            guild_rewards_progress_text(interaction.guild_id, interaction.user.id),
            view=RewardChoiceView(interaction.user.id), ephemeral=True)


class BotDMToggleView(discord.ui.View):
    """A private user-owned menu; only its owner can change their reminders."""
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                'These notification settings belong to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Turn On', emoji='🔔', style=discord.ButtonStyle.success)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('Only available in GYMGUILD.', ephemeral=True)
        set_bot_dm_enabled(interaction.guild_id, interaction.user.id, True)
        await interaction.response.edit_message(
            content=bot_dm_preference_text(True), view=self)

    @discord.ui.button(label='Turn Off', emoji='🔕', style=discord.ButtonStyle.secondary)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.send_message('Only available in GYMGUILD.', ephemeral=True)
        set_bot_dm_enabled(interaction.guild_id, interaction.user.id, False)
        await interaction.response.edit_message(
            content=bot_dm_preference_text(False), view=self)


class GuildBlastToggleView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('This menu belongs to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Enable Guild Blast', emoji='🔔', style=discord.ButtonStyle.success)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id != GUILD_ID:
            return await interaction.response.edit_message(content='Unavailable outside the Guild.', view=None)
        await interaction.response.defer()
        result = await set_guild_blast(interaction.user, True)
        await interaction.edit_original_response(content=result, view=None)

    @discord.ui.button(label='Disable Guild Blast', emoji='🔕', style=discord.ButtonStyle.secondary)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id != GUILD_ID:
            return await interaction.response.edit_message(content='Unavailable outside the Guild.', view=None)
        await interaction.response.defer()
        result = await set_guild_blast(interaction.user, False)
        await interaction.edit_original_response(content=result, view=None)


def pause_allowance_text(gid: int, uid: int) -> str:
    """Remaining pauses in the current rolling 13-week window."""
    cutoff = (week_start() - timedelta(weeks=13)).date().isoformat()
    with connect() as db:
        used = db.execute(
            'SELECT COUNT(*) n FROM pauses WHERE guild_id=? AND user_id=? AND week_key>? AND week_key<=?',
            (gid, uid, cutoff, week_key())
        ).fetchone()['n']
    return f'**Pauses remaining: {max(0, 2-used)}/2.**'


class ConfirmResumeView(discord.ui.View):
    """Private confirmation to resume XP while retaining a spent pause."""
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('This confirmation belongs to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Yes, Resume XP', emoji='▶️', style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.edit_message(content='This button is not available here.', view=None)
        wk = week_key()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exists = db.execute('SELECT 1 FROM pauses WHERE guild_id=? AND user_id=? AND week_key=?',
                                (interaction.guild_id, interaction.user.id, wk)).fetchone()
            if not exists:
                result = '▶️ No active pause found. XP is already enabled.'
            else:
                cur = db.execute('INSERT OR IGNORE INTO resumed_pauses VALUES (?,?,?,?)',
                                 (interaction.guild_id, interaction.user.id, wk, utc_now().isoformat()))
                result = ('✅ **XP resumed!** Your pause is no longer active and you can earn XP again. '
                          '**Your pause has not been refunded** and still counts toward your 2-in-13-week allowance.'
                          if cur.rowcount else '▶️ XP has already been resumed for this week. Your pause remains spent.')
                result += '\n' + pause_allowance_text(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(content=result, view=None)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content='No changes made. Your pause remains active.', view=None)


class ConfirmPauseView(discord.ui.View):
    """Short-lived private confirmation; only the requesting member may confirm."""
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('This confirmation belongs to another member.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Yes, Activate Pause', emoji='⏸️', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            return await interaction.response.edit_message(content='This button is not available here.', view=None)
        start = week_start()
        wk = start.date().isoformat()
        cutoff = (start - timedelta(weeks=13)).date().isoformat()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute(
                'SELECT COUNT(*) n FROM pauses WHERE guild_id=? AND user_id=? AND week_key>? AND week_key<=?',
                (interaction.guild_id, interaction.user.id, cutoff, wk)
            ).fetchone()['n']
            exists = db.execute(
                'SELECT 1 FROM pauses WHERE guild_id=? AND user_id=? AND week_key=?',
                (interaction.guild_id, interaction.user.id, wk)
            ).fetchone()
            if exists:
                result = '⏸️ You already have a pause active for this week. No additional pause was used.'
            elif used >= 2:
                result = '⏸️ You have used both pauses in your rolling 13-week allowance. No pause was activated.'
            else:
                db.execute('INSERT INTO pauses VALUES (?,?,?,?)',
                           (interaction.guild_id, interaction.user.id, wk, utc_now().isoformat()))
                result = (f'✅ **Pause activated!** Your Weekly Side Quest for **{wk}** is paused. '
                          'Your streak is protected and you will not receive an inactivity penalty this week. '
                          'Your pause ends at the next weekly reset.')
        result += '\n' + pause_allowance_text(interaction.guild_id, interaction.user.id)
        await interaction.response.edit_message(content=result, view=None)

    @discord.ui.button(label='Cancel', emoji='✖️', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content='Pause cancelled. No pause was used.', view=None)


async def post_player_stats_hub():
    if not PLAYER_STATS_CHANNEL_ID:
        return False, 'Set PLAYER_STATS_CHANNEL_ID in your .env file first.'
    channel = bot.get_channel(PLAYER_STATS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != GUILD_ID:
        return False, 'Player stats channel not found. Check PLAYER_STATS_CHANNEL_ID.'
    with connect() as db:
        row = db.execute('SELECT value FROM v12_config WHERE key=?',
                         (f'player-stats-post-{GUILD_ID}',)).fetchone()
    message = None
    if row:
        try:
            message = await channel.fetch_message(int(row['value']))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            message = None
    try:
        if message:
            await message.edit(embed=player_stats_embed(), view=PlayerStatsView())
        else:
            message = await channel.send(embed=player_stats_embed(), view=PlayerStatsView())
    except (discord.Forbidden, discord.HTTPException):
        log.exception('Could not post player stats hub')
        return False, 'Could not post. Check Guildkeeper channel permissions.'
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                   (f'player-stats-post-{GUILD_ID}', str(message.id)))
    return True, f'Player Hub is ready in {channel.mention}.'


@bot.tree.command(name='admin_player_stats_post', description='Admin: create or refresh the permanent Player Hub buttons')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_player_stats_post(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    ok, result = await post_player_stats_hub()
    await interaction.followup.send(('✅ ' if ok else '⚠️ ') + result, ephemeral=True)


@bot.tree.command(name='admin_leaderboard_post', description='Admin: create or refresh the permanent leaderboard')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_leaderboard_post(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    ok, message = await update_permanent_leaderboard(force=True)
    await interaction.followup.send(('✅ ' if ok else '⚠️ ') + message, ephemeral=True)


@bot.tree.command(name='admin_leaderboard_update', description='Admin: manually refresh the leaderboard rankings')
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_leaderboard_update(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    ok, message = await update_permanent_leaderboard(force=True)
    await interaction.followup.send(('✅ ' if ok else '⚠️ ') + message, ephemeral=True)


BOT_DM_FOOTER = (
    'Manage these reminders anytime: open the GYMGUILD Player Hub → Bot DMs → Turn Off.'
)


def bot_dm_reminder_kind(now: datetime | None = None) -> str | None:
    """Saturday / Sunday from 09:00 UK local time, respecting BST/GMT."""
    local = (now or utc_now()).astimezone(TZ)
    if local.hour < 9:
        return None
    return {5: 'streak', 6: 'xp'}.get(local.weekday())


def bot_dm_reminder_message(kind: str, streak: int, objectives: int) -> str:
    if kind == 'streak':
        return (
            '🔥 **Your GYMGUILD streak is at risk!**\n'
            f'You currently have a **{streak}-week streak**, but this week’s '
            f'Side Quest is only **{objectives}/4 complete**. The next quest '
            'reset is **Monday at 00:00 UK time**. Complete all four objectives '
            'before then or activate an available pause to protect your streak.\n\n'
            + BOT_DM_FOOTER
        )
    if kind == 'xp':
        return (
            '⚠️ **GYMGUILD XP reminder**\n'
            'You have **0/4 Weekly Side Quest objectives** recorded this week. '
            'At the **Monday 00:00 UK reset**, an unpaused week with zero '
            'qualifying objectives incurs a **25 XP inactivity deduction** '
            '(your total cannot drop below 0). Completing **any one** qualifying '
            'objective or activating a pause avoids that deduction.\n\n'
            + BOT_DM_FOOTER
        )
    raise ValueError('Unsupported reminder kind')


def bot_dm_reminder_candidates(gid: int, now: datetime | None = None) -> list[dict]:
    """Return only members currently eligible for the daily reminder.

    This is a read-only selection pass; the send path rechecks preferences
    and uses a unique DB key to prevent duplicate messages after restarts.
    """
    kind = bot_dm_reminder_kind(now)
    if kind is None:
        return []
    wk = week_key(now)
    with connect() as db:
        user_ids = [row['user_id'] for row in db.execute(
            'SELECT user_id FROM members WHERE guild_id=?', (gid,))]
    eligible = []
    for uid in user_ids:
        if not bot_dm_enabled(gid, uid) or is_paused(gid, uid, wk):
            continue
        objectives = len(weekly_status(gid, uid, wk) & set(WEEKLY_TEST_XP))
        if kind == 'streak':
            prior_streak = active_streak(gid, uid)
            if objectives >= 4 or prior_streak <= 0:
                continue
        else:
            if objectives != 0 or get_xp(gid, uid) <= 0:
                continue
            prior_streak = 0
        eligible.append({
            'guild_id': gid, 'user_id': uid, 'week_key': wk,
            'kind': kind, 'streak': prior_streak, 'objectives': objectives,
        })
    return eligible


def claim_bot_dm_reminder(gid: int, uid: int, wk: str, kind: str) -> bool:
    """Atomically claim one reminder; opt-out and completion rechecked."""
    if kind not in ('xp', 'streak'):
        return False
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        pref = db.execute(
            'SELECT enabled FROM bot_dm_preferences WHERE guild_id=? AND user_id=?',
            (gid, uid),
        ).fetchone()
        if pref is not None and not pref['enabled']:
            return False
        paused = db.execute(
            'SELECT 1 FROM pauses p WHERE p.guild_id=? AND p.user_id=? AND p.week_key=? '
            'AND NOT EXISTS (SELECT 1 FROM resumed_pauses r '
            'WHERE r.guild_id=p.guild_id AND r.user_id=p.user_id AND r.week_key=p.week_key)',
            (gid, uid, wk),
        ).fetchone()
        if paused:
            return False
        objectives = db.execute(
            'SELECT COUNT(*) AS n FROM weekly_actions WHERE guild_id=? AND user_id=? '
            'AND week_key=? AND kind IN (\'training1\',\'training2\',\'gym\',\'hobby\')',
            (gid, uid, wk),
        ).fetchone()['n']
        if kind == 'xp' and objectives:
            return False
        if kind == 'streak' and objectives >= 4:
            return False
        return bool(db.execute(
            'INSERT OR IGNORE INTO bot_dm_reminders '
            '(guild_id,user_id,week_key,reminder_kind,attempted_at,status) '
            "VALUES (?,?,?,?,?,'attempted')",
            (gid, uid, wk, kind, utc_now().isoformat()),
        ).rowcount)


async def send_weekly_bot_dm_reminders(now: datetime | None = None) -> dict:
    """No public announcements or @mentions; ignore Discord-disabled DMs."""
    kind = bot_dm_reminder_kind(now)
    stats = {'kind': kind, 'sent': 0, 'blocked': 0}
    if kind is None:
        return stats
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return stats
    for candidate in bot_dm_reminder_candidates(GUILD_ID, now):
        member = guild.get_member(candidate['user_id'])
        if member is None or member.bot:
            continue
        if not claim_bot_dm_reminder(
            GUILD_ID, member.id, candidate['week_key'], candidate['kind']
        ):
            continue
        status = 'sent'
        try:
            await member.send(bot_dm_reminder_message(
                candidate['kind'], candidate['streak'], candidate['objectives']))
            stats['sent'] += 1
        except (discord.Forbidden, discord.HTTPException):
            status = 'blocked'
            stats['blocked'] += 1
            log.info('GuildKeeper reminder DM unavailable for user %s', member.id)
        with connect() as db:
            db.execute(
                'UPDATE bot_dm_reminders SET status=? WHERE guild_id=? '
                'AND user_id=? AND week_key=? AND reminder_kind=?',
                (status, GUILD_ID, member.id, candidate['week_key'], candidate['kind']),
            )
    return stats


@tasks.loop(minutes=1)
async def scheduler():
    await settle_previous_weeks()
    await post_weekly_board()
    await update_permanent_leaderboard()
    await expire_reward_tickets()
    await send_weekly_bot_dm_reminders()

@scheduler.before_loop
async def before_scheduler():
    await bot.wait_until_ready()

startup_synced = False

@bot.event
async def on_ready():
    global startup_synced
    log.info('Guildkeeper online as %s | Guild ID %s', bot.user, GUILD_ID)
    if startup_synced:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        log.warning('Configured guild %s not found; skipping rank sync', GUILD_ID)
        return
    startup_synced = True
    try:
        awarded = backfill_earned_streak_milestones(GUILD_ID)
        log.info('Startup backfilled %s missed streak milestones', awarded)
        success, failed = await sync_all_ranks(guild)
        log.info('Startup rank sync complete: %s successful, %s failed', success, failed)
        await settle_previous_weeks()
    except (discord.HTTPException, discord.Forbidden):
        log.exception('Startup rank sync failed; verify Server Members Intent and permissions')


@bot.tree.error
async def on_app_command_error(interaction, error):
    if isinstance(error,app_commands.MissingPermissions):
        message = 'You need Manage Server permission for this command.'
    else:
        log.error('Command failed: %s',error,exc_info=error)
        message = 'Something went wrong. Please try again or contact a moderator.'
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.NotFound:
        # Discord interaction token expired before the bot could acknowledge it.
        log.warning('Could not reply to expired interaction for %s', interaction.command)
    except discord.HTTPException:
        log.exception('Could not deliver application command error response')

if __name__=='__main__':
    if not TOKEN or TOKEN=='PASTE_YOUR_BOT_TOKEN_HERE' or not GUILD_ID:
        raise SystemExit('Set DISCORD_TOKEN and GUILD_ID in your .env file first.')
    setup_db()
    migrate_monday_week_keys()
    bot.run(TOKEN)
