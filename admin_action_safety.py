"""Read-only impact previews for testing and moderator reset confirmations."""

WEEKLY_REWARDS = {'training1': 50, 'training2': 50, 'gym': 25, 'hobby': 25}


def impact(api, gid, uid, action, rank=None, weeks=None):
    """Return explanatory text and a stable snapshot of affected records.

    Displayed XP deductions are nominal, because existing add_xp clamps XP at zero.
    """
    with api.connect() as db:
        xp = api.get_xp(gid, uid)
        if action == 'starter_reset':
            claims = {r['kind'] for r in db.execute(
                'SELECT kind FROM starter_claims WHERE guild_id=? AND user_id=?',
                (gid, uid))}
            turned_in = bool(db.execute(
                'SELECT 1 FROM starter_turnins WHERE guild_id=? AND user_id=?',
                (gid, uid)).fetchone())
            refund = sum(api.STARTER_REWARDS.get(k, 0) for k in claims)
            key = (tuple(sorted(claims)), turned_in, xp)
            return ('**Starter Quest reset**\n'
                    f'Current progress: **{len(claims)}/5** objectives\n'
                    f'Starter XP to deduct: **up to {refund} XP** (current XP: {xp})\n'
                    'Clears all Starter claims and turn-in, and attempts to remove '
                    'the Guild Member access role.\n'
                    'Weekly quests, other role choices and existing audit records stay intact.', key)
        if action == 'weekly_reset':
            wk = api.week_key()
            rows = db.execute(
                'SELECT kind FROM weekly_actions WHERE guild_id=? AND user_id=? AND week_key=?',
                (gid, uid, wk)).fetchall()
            kinds = {row['kind'] for row in rows}
            completion = bool(db.execute(
                'SELECT 1 FROM weekly_completions WHERE guild_id=? AND user_id=? AND week_key=?',
                (gid, uid, wk)).fetchone())
            refund = sum(WEEKLY_REWARDS.get(k, 0) for k in kinds) + (50 if completion else 0)
            key = (wk, tuple(sorted(kinds)), completion, xp)
            return ('**Current Weekly Quest reset**\n'
                    f'Week: **{wk}** · objectives: **{len(kinds)}/4**\n'
                    f'Completed bonus: **{"Yes" if completion else "No"}**\n'
                    f'Weekly XP to deduct: **up to {refund} XP** (current XP: {xp})\n'
                    'Removes this week’s objectives, completion and message fingerprints. '
                    'Other weeks, pauses, Starter and permanent milestone claims remain.', key)
        if action == 'pause_reset':
            rows = db.execute('SELECT week_key FROM pauses WHERE guild_id=? AND user_id=? '
                              'ORDER BY week_key', (gid, uid)).fetchall()
            resumed = db.execute('SELECT COUNT(*) AS n FROM resumed_pauses '
                                 'WHERE guild_id=? AND user_id=?', (gid, uid)).fetchone()['n']
            keys = tuple(r['week_key'] for r in rows)
            return ('**Pause history reset**\n'
                    f'Pauses to delete: **{len(keys)}** · resumed entries: **{resumed}**\n'
                    'Restores pause allowance. **XP: unchanged.**\n'
                    'Deleting historical pauses may affect streak eligibility.', (keys, resumed))
        if action == 'reward_reset':
            row = db.execute(
                'SELECT id,status,channel_id FROM reward_tickets '
                'WHERE guild_id=? AND user_id=? AND rank=?',
                (gid, uid, rank)).fetchone()
            if not row:
                return (f'**{rank} reward reset**\nNo request exists; nothing to reset.',
                        (rank, None))
            return (f'**{rank} reward reset**\n'
                    f'Request ID: **{row["id"]}** · status: **{row["status"]}**\n'
                    f'Ticket channel: <#{row["channel_id"]}>\n'
                    'Removes the recorded request and attempts to delete its ticket channel. '
                    '**XP, rank unlocks, streak and other rewards remain unchanged.**',
                    (rank, row['id'], row['status'], row['channel_id']))
    return ('Review the action details before confirming.', (action, weeks, rank))
