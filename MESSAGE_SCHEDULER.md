# Permanent Message Scheduler panel

GuildKeeper now has a staff-only, persistent **Message Scheduler** dashboard.

## Create the dashboard

1. Create or choose a **private staff text channel**, such as `#admin-scheduler`. @everyone must not have View Channel.
2. Use `/admin_scheduler_post` and select that channel.
3. GuildKeeper posts a permanent panel with the timeline, scheduled messages, automations, new message, new automation, and refresh controls. If posted in the same channel again, it edits the saved panel. Posting to a different private channel moves it.
4. Every button checks **Manage Server**, and follow-up menus are ephemeral and locked to the moderator who opened them.
5. The permanent panel is re-registered on restart and included in panel health and repair.

## Scheduled messages

**New Message** opens the existing Scheduler flow: destination channel, one-off / daily / weekly / monthly, title, body, first UK-local time, optional HTTPS image, and optional GuildBlast mention. You must confirm the preview before the job is saved.

**Scheduled Messages** lists saved posts and offers Preview, Edit, Cancel and Delete. Existing posted messages are never deleted when a job is cancelled. Editing a cancelled job can resume it by choosing a future date.

**Posting Timeline** merges upcoming custom posts (and previews of recurring occurrences) with predictable weekly and monthly automation dates. Dates are displayed in Europe/London. Shopify events happen when products change and have no predictable publication time; find these in Automations, not the calendar.

## Event automations

**New Automation** offers four supported event triggers, at most one configured rule per trigger:

| Event | Trigger | Default |
| --- | --- | --- |
| Weekly Side Quest refresh | After the week's quest board is posted | Enabled |
| Monthly leaderboard recap | On the first UK day of the month | Enabled |
| New Shopify product | New online product detected by polling | Follows SHOPIFY_AUTO_ANNOUNCE environment switch (off initially) |
| Shopify sale/price reduction | Reduced product price detected by polling | Follows SHOPIFY_AUTO_ANNOUNCE environment switch (off initially) |

You can choose a text channel and customize the announcement title and opening text. The event-specific text is still added, so quest and leaderboard details are preserved. New rules do not post instantly.

**Automations** lets you inspect all rules and edit text, change destination, pause/resume, or remove a custom rule. Pausing stops future event sends without deleting historic posts. **Deleting a custom rule restores the default**, including its original enabled state. Pause an automation instead of deleting it to ensure it stays disabled.

Event posts use the same once-per-event deduplication record as before. Discord or API failures have to be reviewed in Health Check; they are not blindly retried. The lifetime leaderboard never resets.

## What the dashboard does not do

- It cannot create arbitrary custom program triggers; supported automatic triggers are fixed to the four existing Guildkeeper event types. Custom timed posts use New Message.
- It does not publish to a channel visible to everyone, but the destination of a scheduled announcement may be public.
- A scheduled send timestamp is an estimate, not a guarantee to the exact second. The scheduler checks work roughly once per minute.
- A Shopify catalog event requires access to the store's public catalog feed. The first scan initializes a baseline and does not announce the entire product catalog.

## Testing and deployment

All changes are in `message_scheduler_panel.py` and the existing scheduler modules, covered by `tests/test_message_scheduler_panel.py`. After CI passes and changes are merged, run **Deploy GuildKeeper** manually. The panel is **not** posted automatically on a new deployment, since that could expose admin controls in the wrong channel. Use `/admin_scheduler_post` once to choose the private channel.
