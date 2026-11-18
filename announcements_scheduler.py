"""GuildKeeper announcement scheduling, UK local recurrence, and automatic notices.

Discord publishing is best-effort. A reserved delivery that has an uncertain
result is never blindly replayed, avoiding accidental duplicate pings.
"""
import calendar
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import discord

api = None
TZ = ZoneInfo("Europe/London")
FREQUENCIES = ("once", "daily", "weekly", "monthly")
FORMAT = "%Y-%m-%d %H:%M"


def initialize():
    with api.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS scheduled_announcements (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
          title TEXT NOT NULL, body TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '',
          recurrence TEXT NOT NULL, next_run_utc TEXT NOT NULL,
          month_day INTEGER NOT NULL, ping_guildblast INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'active',
          created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
          last_posted_at TEXT, last_message_id INTEGER, last_error TEXT
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS automatic_announcements (
          guild_id INTEGER NOT NULL, event_key TEXT NOT NULL,
          status TEXT NOT NULL, channel_id INTEGER NOT NULL,
          message_id INTEGER, error TEXT, created_at TEXT NOT NULL,
          PRIMARY KEY (guild_id, event_key)
        )""")


def parse_local(value):
    try:
        naive = datetime.strptime(value.strip(), FORMAT)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Use YYYY-MM-DD HH:MM in UK time.") from exc
    aware = naive.replace(tzinfo=TZ, fold=0)
    if aware.astimezone(timezone.utc).astimezone(TZ).replace(tzinfo=None) != naive:
        raise ValueError("That local time does not exist because the clocks change.")
    return aware


def recurrence_after(previous, frequency):
    """Preserve UK wall-clock time across seasonal clock changes."""
    if frequency == "once":
        return None
    local = previous.astimezone(TZ)
    naive = local.replace(tzinfo=None)
    if frequency == "daily":
        nxt = naive + timedelta(days=1)
    elif frequency == "weekly":
        nxt = naive + timedelta(days=7)
    elif frequency == "monthly":
        month = naive.month % 12 + 1
        year = naive.year + (naive.month // 12)
        # Store desired day separately so Jan 31 -> Feb 28 -> Mar 31.
        raise ValueError("Monthly recurrence needs a preferred month day.")
    else:
        raise ValueError("Invalid frequency.")
    aware = nxt.replace(tzinfo=TZ, fold=0)
    normalized = aware.astimezone(timezone.utc).astimezone(TZ)
    if normalized.replace(tzinfo=None) != nxt:
        return normalized
    return aware


def next_occurrence(previous, frequency, month_day, now):
    if frequency == "once":
        return None
    local = previous.astimezone(TZ)
    for _ in range(750):
        if frequency == "monthly":
            month = local.month % 12 + 1
            year = local.year + (local.month // 12)
            day = min(month_day, calendar.monthrange(year, month)[1])
            naive = local.replace(tzinfo=None).replace(year=year, month=month, day=day)
            local = naive.replace(tzinfo=TZ, fold=0)
            normalized = local.astimezone(timezone.utc).astimezone(TZ)
            if normalized.replace(tzinfo=None) != naive:
                local = normalized
        else:
            local = recurrence_after(local, frequency)
        if local.astimezone(timezone.utc) > now.astimezone(timezone.utc):
            return local
    raise ValueError("Recurrence could not find next date within two years.")


def validate_image(url):
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or len(url) > 500 or any(c in url for c in "\r\n "):
        raise ValueError("Image must be a valid HTTPS URL (up to 500 characters).")
    return url


def check_channel(guild, channel_id, ping=False):
    channel = guild.get_channel(channel_id) if guild else None
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Choose an existing text channel on this server.")
    if guild.me is None:
        raise ValueError("GuildKeeper cannot inspect its channel permissions.")
    perms = channel.permissions_for(guild.me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        raise ValueError("GuildKeeper needs View Channel, Send Messages and Embed Links.")
    if ping:
        role = guild.get_role(api.GUILD_BLAST_ROLE_ID) if api.GUILD_BLAST_ROLE_ID else None
        if role is None or not (perms.mention_everyone or role.mentionable):
            raise ValueError("Guild Blast role cannot be mentioned in that channel.")
    return channel


def build_data(guild, channel_id, title, body, when, recurrence, image_url="", ping=False, now=None):
    if recurrence not in FREQUENCIES:
        raise ValueError("Choose Once, Daily, Weekly or Monthly.")
    title, body = title.strip(), body.strip()
    if not (1 <= len(title) <= 100 and 1 <= len(body) <= 1500):
        raise ValueError("Title must be 1–100 characters and body 1–1500 characters.")
    if "@everyone" in body.lower() or "@here" in body.lower():
        raise ValueError("@everyone and @here are not supported.")
    image_url = validate_image(image_url)
    scheduled = parse_local(when)
    now = now or api.utc_now()
    scheduled_utc = scheduled.astimezone(timezone.utc)
    if scheduled_utc <= now.astimezone(timezone.utc):
        raise ValueError("Choose a future UK date and time.")
    if scheduled_utc > now + timedelta(days=730):
        raise ValueError("First post must be within the next two years.")
    check_channel(guild, channel_id, ping)
    return dict(channel_id=channel_id, title=discord.utils.escape_mentions(title),
                body=discord.utils.escape_mentions(body), image_url=image_url,
                recurrence=recurrence, next_run_utc=scheduled_utc.isoformat(),
                month_day=scheduled.day, ping_guildblast=int(ping))


def embed(title, body, image_url=""):
    obj = discord.Embed(title=title, description=body, colour=0x71A7A2)
    if image_url:
        obj.set_image(url=image_url)
    return obj


def create(data, actor):
    initialize()
    with api.connect() as db:
        cur = db.execute("""INSERT INTO scheduled_announcements
          (guild_id,channel_id,title,body,image_url,recurrence,next_run_utc,month_day,
           ping_guildblast,status,created_by,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
          (api.GUILD_ID,data["channel_id"],data["title"],data["body"],
           data["image_url"],data["recurrence"],data["next_run_utc"],data["month_day"],
           data["ping_guildblast"],"active",actor,api.utc_now().isoformat()))
        return cur.lastrowid


def get(job_id):
    initialize()
    with api.connect() as db:
        return db.execute("SELECT * FROM scheduled_announcements WHERE guild_id=? AND id=?",
                          (api.GUILD_ID,job_id)).fetchone()


def upcoming(limit=25):
    initialize()
    with api.connect() as db:
        return db.execute("""SELECT * FROM scheduled_announcements WHERE guild_id=?
         AND status!='deleted' ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,
         next_run_utc,id LIMIT ?""", (api.GUILD_ID,limit)).fetchall()


def update(job_id, data):
    initialize()
    with api.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM scheduled_announcements WHERE guild_id=? AND id=?",
                         (api.GUILD_ID,job_id)).fetchone()
        if row is None or row["status"]=="sending":
            raise ValueError("Announcement missing or being sent.")
        db.execute("""UPDATE scheduled_announcements SET
          channel_id=?,title=?,body=?,image_url=?,recurrence=?,next_run_utc=?,
          month_day=?,ping_guildblast=?,status='active',last_error=NULL
          WHERE guild_id=? AND id=?""",
          (data["channel_id"],data["title"],data["body"],data["image_url"],data["recurrence"],
           data["next_run_utc"],data["month_day"],data["ping_guildblast"],
           api.GUILD_ID,job_id))


def change_status(job_id, new_status):
    if new_status not in ("cancelled","deleted"):
        raise ValueError("Can only cancel or delete scheduled announcements.")
    initialize()
    with api.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT status FROM scheduled_announcements WHERE guild_id=? AND id=?",
                       (api.GUILD_ID,job_id)).fetchone()
        if row is None or row["status"]=="sending":
            raise ValueError("Announcement missing or being sent.")
        db.execute("UPDATE scheduled_announcements SET status=? WHERE guild_id=? AND id=?",
                   (new_status,api.GUILD_ID,job_id))


async def dispatch_one(job_id, now=None):
    initialize()
    now=now or api.utc_now()
    with api.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("""SELECT * FROM scheduled_announcements WHERE guild_id=? AND id=?
                       AND status='active' AND next_run_utc<=?""",
                       (api.GUILD_ID,job_id,now.isoformat())).fetchone()
        if row is None:
            return False
        db.execute("UPDATE scheduled_announcements SET status='sending' WHERE guild_id=? AND id=?",
                   (api.GUILD_ID,job_id))
    message_id=None
    error=None
    try:
        guild=api.bot.get_guild(api.GUILD_ID)
        channel=check_channel(guild,row["channel_id"],bool(row["ping_guildblast"]))
        role=guild.get_role(api.GUILD_BLAST_ROLE_ID) if row["ping_guildblast"] else None
        message=await channel.send(
            content=role.mention if role else None,
            embed=embed(row["title"],row["body"],row["image_url"]),
            allowed_mentions=discord.AllowedMentions(everyone=False,users=False,
                roles=[role] if role else False,replied_user=False))
        message_id=message.id
    except (discord.HTTPException,ValueError) as exc:
        error=type(exc).__name__+": "+str(exc)[:180]
    future=next_occurrence(datetime.fromisoformat(row["next_run_utc"]).astimezone(TZ),
                           row["recurrence"],row["month_day"],now) if message_id else None
    status="active" if future else "sent" if message_id else "error"
    next_run=(future.astimezone(timezone.utc).isoformat() if future else row["next_run_utc"])
    with api.connect() as db:
        db.execute("""UPDATE scheduled_announcements SET status=?,next_run_utc=?,
          last_posted_at=?,last_message_id=?,last_error=? WHERE guild_id=? AND id=?""",
          (status,next_run,now.isoformat() if message_id else row["last_posted_at"],
           message_id,error,api.GUILD_ID,job_id))
    if error:
        api.log.error("Scheduled post #%s needs review: %s",job_id,error)
    return bool(message_id)


async def run_custom_due(now=None):
    initialize()
    now=now or api.utc_now()
    with api.connect() as db:
        rows=db.execute("""SELECT id FROM scheduled_announcements WHERE guild_id=?
          AND status='active' AND next_run_utc<=? ORDER BY next_run_utc LIMIT 12""",
          (api.GUILD_ID,now.isoformat())).fetchall()
    outcomes=[]
    for row in rows:
        outcomes.append(await dispatch_one(row["id"],now))
    return outcomes


def monthly_top_10(gid, start, end):
    """Monthly snapshot of XP earned; lifetime XP is never reset."""
    with api.connect() as db:
        rows=db.execute("""SELECT user_id,SUM(amount) AS total FROM xp_log
          WHERE guild_id=? AND created_at>=? AND created_at<? GROUP BY user_id
          ORDER BY total DESC,user_id LIMIT 10""",
          (gid,start.isoformat(),end.isoformat())).fetchall()
    return [(r["user_id"],r["total"]) for r in rows if r["total"] > 0]


async def automatic_event(key, title, body, channel_id):
    """Exactly one attempt per event; uncertain sends require manual attention."""
    initialize()
    now=api.utc_now()
    with api.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior=db.execute("SELECT status FROM automatic_announcements WHERE guild_id=? AND event_key=?",
                         (api.GUILD_ID,key)).fetchone()
        if prior:
            return prior["status"]=="sent"
        db.execute("""INSERT INTO automatic_announcements
          (guild_id,event_key,status,channel_id,created_at) VALUES (?,?,?,?,?)""",
          (api.GUILD_ID,key,"sending",channel_id,now.isoformat()))
    mid=None
    err=None
    try:
        guild=api.bot.get_guild(api.GUILD_ID)
        channel=check_channel(guild,channel_id)
        msg=await channel.send(embed=embed(title,body),
                               allowed_mentions=discord.AllowedMentions.none())
        mid=msg.id
    except (discord.HTTPException,ValueError) as exc:
        err=type(exc).__name__+": "+str(exc)[:180]
    with api.connect() as db:
        db.execute("""UPDATE automatic_announcements SET status=?,message_id=?,error=?
          WHERE guild_id=? AND event_key=?""",
          ("sent" if mid else "error",mid,err,api.GUILD_ID,key))
    if err:
        api.log.error("Automatic notice %s needs review: %s",key,err)
    return bool(mid)


async def run_automatic(now=None):
    """Board notice only after weekly board exists; monthly leaderboard is a recap, NOT a reset."""
    initialize()
    now=now or api.utc_now()
    guild=api.bot.get_guild(api.GUILD_ID)
    if guild is None or not api.ANNOUNCEMENTS_CHANNEL_ID:
        return
    wk=api.week_key(now)
    with api.connect() as db:
        exists=db.execute("SELECT 1 FROM schedule_posts WHERE guild_id=? AND post_key=?",
                          (api.GUILD_ID,"weekly-board-"+wk)).fetchone()
    if exists:
        quest_channel=guild.get_channel(api.QUESTS_CHANNEL_ID)
        quest_link=quest_channel.mention if quest_channel else "the Side Quest channel"
        await automatic_event("weekly-refresh-"+wk,"⚔️ Weekly Side Quests refreshed",
            "A new week's objectives are ready in "+quest_link+
            ". Complete the four objectives to earn XP, build your streak and progress your rank.",
            api.ANNOUNCEMENTS_CHANNEL_ID)
    local=now.astimezone(TZ)
    # Monthly leaderboard RECAP on the 1st; no deletion, reset, or change to lifetime XP.
    if local.day==1:
        last_end=local.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        last_start=(last_end-timedelta(days=1)).replace(day=1)
        rows=monthly_top_10(api.GUILD_ID,last_start.astimezone(timezone.utc),
                            last_end.astimezone(timezone.utc))
        lines=[f"**#{i}** <@{uid}> — **{xp:,} XP**" for i,(uid,xp) in enumerate(rows,1)]
        content=("Top XP earned last month:\n"+"\n".join(lines) if lines else
                 "No positive XP gains were recorded last month.")
        content+="\n\n**Lifetime XP and ranks do not reset.**"
        await automatic_event("monthly-leaderboard-"+last_end.strftime("%Y-%m"),
            "🏆 Monthly leaderboard recap",content,api.ANNOUNCEMENTS_CHANNEL_ID)


def failed_count():
    initialize()
    with api.connect() as db:
        manual=db.execute("SELECT COUNT(*) AS n FROM scheduled_announcements WHERE guild_id=? "
                          "AND status IN ('error','sending')",(api.GUILD_ID,)).fetchone()["n"]
        auto=db.execute("SELECT COUNT(*) AS n FROM automatic_announcements WHERE guild_id=? "
                        "AND status IN ('error','sending')",(api.GUILD_ID,)).fetchone()["n"]
    return manual+auto


async def tick():
    await run_custom_due()
    await run_automatic()


def install(module):
    global api
    api=module
    initialize()
