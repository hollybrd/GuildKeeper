# GuildKeeper announcement scheduler

**Admin Hub → Announcements**

Use **Compose Announcement** to preview and immediately publish a one-off message in the configured Announcements channel. No pings.

Use **Schedule Message** to select the destination text channel, choose Once/Daily/Weekly/Monthly, and enter a title, body, first UK-local post date/time (YYYY-MM-DD HH:MM), optional HTTPS image URL, and optional GuildBlast mention. The next screen previews the embed and its cadence before you confirm saving. Nothing is posted during preview.

Use **Upcoming Messages** to list saved posts, their next date, recurrence, channel, and GuildBlast setting. Select any item to Preview, Edit, Cancel or Delete. Editing lets you choose a new recurrence and update contents/time/image/ping while retaining its destination channel. Cancel and Delete stop future posts, but do not remove messages already sent.

Use **Automatic Notices** to see the built-in notices:

- **Weekly Side Quest refreshed:** once per quest week, only after the current week's board is successfully recorded as posted. Posts to configured ANNOUNCEMENTS_CHANNEL_ID. No pings.
- **Monthly leaderboard recap:** first day of every UK month, summarizing the previous month's positive net XP gains in the top ten. This is only an announcement: **the existing leaderboard is still lifetime XP, and no XP, ranks or streaks reset**. No pings.

All times are interpreted in Europe/London, with daylight-saving adjustments. Recurring posts keep the same UK wall-clock time where possible. Missed recurring occurrences during downtime are skipped, with at most one late catch-up post on restart; they do not create a flood of backdated messages.

Failed or uncertain sends are held for moderator review rather than blindly retried, to prevent duplicate pings. **Admin Hub → Health Check** reports how many need attention. The scheduler keeps all jobs and dates in the SQLite database across restarts.

Permissions: only Manage Server staff can access scheduler controls. The bot needs View Channel, Send Messages and Embed Links in every destination. GuildBlast mentions require the configured role and mention permission/mentionable role. Everyone/here mentions and arbitrary user pings are not supported.

**Potential future Shopify integration:** the Shopify app connection is separate from this Discord scheduler. Once store integrations are approved, verified shop events (new products, sale starts/ends, release drops) could use this same scheduler/publishing layer, with opt-in announcements. A Discord mini shop could show product cards with links to Shopify checkout; do not accept customer payment data or create a payment processor inside Discord.

**Manual deployment required.** Merging code to GitHub does not deploy GuildKeeper to Oracle.