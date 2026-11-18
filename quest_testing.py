"""Admin-only quest completion cheats.

Synthetic progress is written to the real GuildKeeper database, always marked for
testing in the audit trail. This module is only called from owner-checked,
Manage-Server-protected ephemeral Testing Cheats views.
"""
from datetime import timedelta

WEEKLY_REWARDS = {'training1': 50, 'training2': 50, 'gym': 25, 'hobby': 25}


def options(api, category):
    if category == 'weekly':
        return [('all', 'All remaining objectives')] + [
            (key, label) for key, label in (
                ('training1', 'First Flame — Training 1'),
                ('training2', 'Fuel the Fire — Training 2'),
                ('gym', 'Flex Text — Gym Chat'),
                ('hobby', 'Skill Share — Hobby Chat'))]
    if category == 'starter':
        return [('all', 'All remaining objectives')] + [
            (kind, {'intro': 'Introduce Yourself',
                    'class': 'Choose a Class',
                    'hobby': 'Choose a Proficiency',
                    'welcome': 'Read Guild Info',
                    'first_steps': 'First Steps'}[kind])
            for kind in api.STARTER_REWARDS]
    if category == 'milestone':
        return [('all', 'All remaining milestones')] + [
            (kind, label) for kind, label, _, _ in api.MILESTONE_QUESTS]
    raise ValueError('Unknown quest category')


def preview(api, gid, uid, category, choice):
    valid = dict(options(api, category))
    if choice not in valid:
        raise ValueError('Invalid quest objective')
    with api.connect() as db:
        if category == 'weekly':
            week = api.week_key()
            done = {r['kind'] for r in db.execute(
                'SELECT kind FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?',
                (gid, uid, week))}
            chosen = list(WEEKLY_REWARDS) if choice == 'all' else [choice]
            missing = [k for k in chosen if k not in done]
            xp = sum(WEEKLY_REWARDS[k] for k in missing)
            if len(done | set(missing)) == 4 and len(done) < 4:
                completed = db.execute(
                    'SELECT 1 FROM weekly_completions WHERE guild_id=? AND user_id=? AND week_key=?',
                    (gid, uid, week)).fetchone()
                if not completed:
                    xp += 50
            return missing, xp
        if category == 'starter':
            done = {r['kind'] for r in db.execute(
                'SELECT kind FROM starter_claims WHERE guild_id=? AND user_id=?',
                (gid, uid))}
            chosen = list(api.STARTER_REWARDS) if choice == 'all' else [choice]
            missing = [k for k in chosen if k not in done]
            return missing, sum(api.STARTER_REWARDS[k] for k in missing)
        done = {r['kind'] for r in db.execute(
            'SELECT kind FROM milestone_claims WHERE guild_id=? AND user_id=?', (gid, uid))}
        chosen = [k for k, *_ in api.MILESTONE_QUESTS] if choice == 'all' else [choice]
        missing = [k for k in chosen if k not in done]
        xp = sum(api.MILESTONE_XP[k] for k in missing)
        if len(set(chosen + list(done)) & set(api.MILESTONE_XP)) == len(api.MILESTONE_QUESTS):
            if 'guild_completion' not in done:
                xp += api.MILESTONE_COMPLETION_BONUS
        return missing, xp


def _synthetic_message_id(db):
    row = db.execute('SELECT MIN(message_id) AS minimum FROM weekly_actions WHERE message_id<0').fetchone()
    minimum = row['minimum']
    return (minimum - 1) if minimum is not None else -1


def complete(api, gid, uid, category, choice, actor_id):
    """Transactionally complete only missing objectives, preserving idempotency.

    Caller must verify staff authorization and subsequently sync rank / access role.
    Returns (objective_names, base_xp_including_weekly_bonus).
    """
    if choice not in dict(options(api, category)):
        raise ValueError('Invalid quest objective')
    if category == 'weekly' and api.is_paused(gid, uid, api.week_key()):
        raise ValueError('Weekly XP is paused. Resume XP in Pauses before using this cheat.')
    with api.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if category == 'weekly':
            wk = api.week_key()
            done = {r['kind'] for r in db.execute(
                'SELECT kind FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?',
                (gid, uid, wk))}
            chosen = list(WEEKLY_REWARDS) if choice == 'all' else [choice]
            missing = [k for k in chosen if k not in done]
            total = 0
            base_day = api.week_start().astimezone(api.TZ).date()
            for kind in missing:
                synthetic_id = _synthetic_message_id(db)
                day = (base_day + timedelta(days=1 if kind == 'training2' else 0)).isoformat()
                fingerprint = f'admin-test:{gid}:{uid}:{wk}:{kind}'
                db.execute('INSERT INTO weekly_actions VALUES (?,?,?,?,?,?,?,?)',
                           (gid, uid, wk, kind, day, synthetic_id, fingerprint,
                            api.utc_now().isoformat()))
                db.execute('INSERT OR IGNORE INTO message_fingerprints VALUES (?,?,?,?)',
                           (gid, uid, fingerprint, api.utc_now().isoformat()))
                api.add_xp(gid, uid, WEEKLY_REWARDS[kind],
                           f'ADMIN TEST weekly {wk} {kind} by {actor_id}', db)
                total += WEEKLY_REWARDS[kind]
                api.grant_once(db, 'starter_claims', gid, uid, 'first_steps',
                               api.STARTER_REWARDS['first_steps'])
            all_done = done | set(missing)
            completed_now = False
            if len(all_done) == 4 and missing:
                inserted = db.execute(
                    'INSERT OR IGNORE INTO weekly_completions VALUES (?,?,?,?)',
                    (gid, uid, wk, api.utc_now().isoformat()))
                if inserted.rowcount:
                    api.add_xp(gid, uid, 50, f'ADMIN TEST weekly completion {wk} by {actor_id}', db)
                    api.grant_once(db, 'milestone_claims', gid, uid, 'first_week',
                                   api.MILESTONE_XP['first_week'])
                    total += 50
                    completed_now = True
            result = (missing, total, completed_now)
        elif category == 'starter':
            done = {r['kind'] for r in db.execute(
                'SELECT kind FROM starter_claims WHERE guild_id=? AND user_id=?',
                (gid, uid))}
            chosen = list(api.STARTER_REWARDS) if choice == 'all' else [choice]
            missing = [k for k in chosen if k not in done]
            for kind in missing:
                api.grant_once(db, 'starter_claims', gid, uid, kind, api.STARTER_REWARDS[kind])
            # A full Starter cheat also turns in the quest, as the public Turn In button does.
            if set(api.STARTER_REWARDS).issubset(done | set(missing)):
                db.execute('INSERT OR IGNORE INTO starter_turnins VALUES (?,?,?)',
                           (gid, uid, api.utc_now().isoformat()))
            result = (missing, sum(api.STARTER_REWARDS[k] for k in missing), False)
        else:
            done = {r['kind'] for r in db.execute(
                'SELECT kind FROM milestone_claims WHERE guild_id=? AND user_id=?',
                (gid, uid))}
            chosen = [k for k, *_ in api.MILESTONE_QUESTS] if choice == 'all' else [choice]
            missing = [k for k in chosen if k not in done]
            for kind in missing:
                api.grant_once(db, 'milestone_claims', gid, uid, kind, api.MILESTONE_XP[kind])
            result = (missing, sum(api.MILESTONE_XP[k] for k in missing), False)
    if category == 'weekly' and result[2]:
        api.award_earned_streak_milestones(gid, uid)
    return result


async def apply_to_member(api, member, category, choice, actor_id):
    gid, uid = member.guild.id, member.id
    missing, xp, completed_week = complete(api, gid, uid, category, choice, actor_id)
    role_note = ''
    if category == 'starter':
        with api.connect() as db:
            onboarded = db.execute(
                'SELECT 1 FROM starter_turnins WHERE guild_id=? AND user_id=?',
                (gid, uid)).fetchone() is not None
        if onboarded:
            role = member.guild.get_role(api.STARTER_ACCESS_ROLE_ID)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason='Admin test: complete Starter Quest')
                except Exception:
                    role_note = '\n⚠️ Starter access role could not be granted; check Manage Roles.'
            elif role is None:
                role_note = '\n⚠️ Starter access role is not configured.'
    await api.update_rank(member, api.get_xp(gid, uid))
    note = (f'✅ **ADMIN TEST — {category.title()} Quest**\n'
            f'New objectives completed: **{len(missing)}**'
            + (f' ({", ".join(missing)})' if missing else ' — already completed')
            + f'\nObjective and weekly-completion XP: **{xp}**.'
            + '\nAdditional automatic Starter/Milestone bonuses may also have been granted.'
            + role_note)
    return note
