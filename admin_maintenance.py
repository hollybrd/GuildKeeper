"""Safe startup reconciliation for GuildKeeper's permanent Discord panels.

Never auto-publishes a panel without a known configured channel/location.
Unknown or inaccessible messages are not replaced, to avoid duplicates.
All decisions are recorded for the private Admin Hub Health Check.
"""
import json
import logging
from datetime import datetime, timezone

import panel_health

log = logging.getLogger('guildkeeper.maintenance')


def _record(api, data):
    with api.connect() as db:
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                   (f'automatic-maintenance-{api.GUILD_ID}', json.dumps(data)))


def last_report(api):
    with api.connect() as db:
        row = db.execute('SELECT value FROM v12_config WHERE key=?',
                         (f'automatic-maintenance-{api.GUILD_ID}',)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row['value'])
    except (ValueError, TypeError):
        return None



def record_job(api, name, success, error=None):
    """Last status for each scheduler task, retained across restarts."""
    key = f'scheduled-job-{api.GUILD_ID}-{name}'
    record = {
        'timestamp': api.utc_now().isoformat(),
        'success': bool(success),
        'error': (str(error)[:200] if error is not None else None),
    }
    with api.connect() as db:
        db.execute('INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)',
                   (key, json.dumps(record)))


def job_statuses(api):
    with api.connect() as db:
        rows = db.execute('SELECT key,value FROM v12_config WHERE key LIKE ?',
                          (f'scheduled-job-{api.GUILD_ID}-%',)).fetchall()
    statuses = {}
    prefix = f'scheduled-job-{api.GUILD_ID}-'
    for row in rows:
        try:
            statuses[row['key'][len(prefix):]] = json.loads(row['value'])
        except (ValueError, TypeError):
            pass
    return statuses



def needs_periodic_check(api, hours=6):
    """Avoid running the expensive live panel scan every scheduler minute."""
    previous = last_report(api)
    if previous is None:
        return True
    try:
        previous_time = datetime.fromisoformat(previous['timestamp'])
        return (api.utc_now() - previous_time).total_seconds() >= hours * 3600
    except (ValueError, KeyError, TypeError):
        return True


async def reconcile(api, guild):
    """Check live message embeds/buttons and repair verifiably stale/missing panels."""
    timestamp = api.utc_now().isoformat()
    try:
        before = await panel_health.snapshot(api, guild)
    except Exception as exc:
        log.exception('Startup panel scan failed')
        report = {'timestamp': timestamp, 'attempted': 0, 'updated': 0,
                  'errors': ['Panel scan failed: ' + type(exc).__name__],
                  'remaining': ['Unverified (scan failed)']}
        _record(api, report)
        return report

    # Only repair panels with an existing known destination; a new Admin Hub,
    # Testing Cheats, and Milestone board need moderator-chosen locations.
    known = []
    skipped = []
    for item in before:
        if item['state'] not in ('missing', 'outdated'):
            continue
        if item['name'] in ('Admin Hub', 'Testing Cheats', 'Milestone Quests') and not item['url']:
            key = {
                'Admin Hub': f'admin-hub-post-{guild.id}',
                'Testing Cheats': f'testing-panel-post-{guild.id}',
                'Milestone Quests': f'milestone-board-{guild.id}',
            }[item['name']]
            if not panel_health._saved_config(api, key):
                skipped.append(item['name'] + ': no saved location')
                continue
        known.append(item)

    results = []
    if known:
        try:
            results = await panel_health.repair(api, guild, known)
        except Exception as exc:
            log.exception('Startup panel repair failed')
            results = [(False, 'Panel repair stopped: ' + type(exc).__name__)]

    try:
        after = await panel_health.snapshot(api, guild)
        remaining = [entry['name'] + ': ' + entry['state']
                     for entry in after if entry['state'] != 'current']
    except Exception as exc:
        log.exception('Startup panel recheck failed')
        remaining = ['Panel recheck failed: ' + type(exc).__name__]

    report = {
        'timestamp': timestamp,
        'attempted': len(results),
        'updated': sum(bool(ok) for ok, _ in results),
        'errors': [msg for ok, msg in results if not ok] + skipped,
        'remaining': remaining,
    }
    _record(api, report)
    if results:
        try:
            actor = getattr(getattr(api.bot, 'user', None), 'id', None) or 0
            api.admin_hub.audit(
                actor, 'automatic_panel_maintenance',
                f"Automatic check: {report['updated']}/{report['attempted']} repaired; "
                + '; '.join(msg for _, msg in results)[:500])
        except Exception:
            log.exception('Could not append automatic maintenance audit entry')
    log.info('Startup panel maintenance: %s updated/%s attempts; %s unresolved',
             report['updated'], report['attempted'], len(remaining))
    return report
