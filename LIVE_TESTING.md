# GuildKeeper live testing — operator guide

**Purpose:** Validate the real Discord experience using a separate, ordinary Discord account. The Test Runner does not impersonate that account, send messages as it, award XP, or automatically mark tests passed. It reads GuildKeeper records and saves moderator-observed pass/fail decisions.

## Before the first test

1. Use a second, ordinary Discord account without Manage Server permission or staff roles.
2. Join the GYMGUILD server as that account. Never share credentials, session cookies, passwords or bot tokens.
3. In the private GuildKeeper Testing Cheats panel, click **Test Runner**. If the panel does not exist, use /admin_testing_panel_post from your admin account and choose a private channel.
4. Select the separate account. This opens a private test dashboard. Results persist across bot restarts.
5. Use **Read-Only Checks** to inspect current player XP, quests, streak, pauses, claimed milestones and DM preferences. These are diagnostics, NOT automatic pass results.
6. Use **Live Checklist** to select a test, perform its Discord interactions with the second account, then mark Passed, Report Failure (with a note), or Skip. Review with **Test Report**.

## Suggested testing order

- **Onboarding:** Welcome DM, Starter board, five genuine Starter steps, turn-in and role/channel unlock.
- **Player Hub:** Profile, Streak, Ranks, Pauses, Inbox, Bot DMs, All Quests and Guild Blast.
- **Weekly Quest:** Training proof, second different-day training proof, gym/hobby posts, completion bonus, no double award.
- **Progression:** Pause and Resume XP; XP/rank-up; streak credit; notification preferences.
- **Rewards and milestones:** Locked/eligible rewards, one-ticket limits, test-only Milestone simulation (without spending money).
- **Admin and maintenance:** Test reset confirmations and audit history, Health Check, and button persistence after a restart.

Second training-day testing requires a different actual day. Skip it until the next day rather than attempting to bypass anti-duplicate rules.

## Safety

- The Test Runner itself only reads member data and writes separate test-result records. It never changes XP, roles, quests, pauses, or reward tickets.
- **Testing Cheats** actions outside Test Runner do change LIVE data. Use a designated tester and review the confirmation.
- A selected test account must be a non-bot account without Manage Server permission, ensuring realistic player permissions.
- Discord DMs, buttons, roles, Shopify validation and boost events cannot be verified without live account actions. Do not make a real purchase or boost just for testing.
- Reporting a test as Passed is a moderator assertion, not proof from an automated agent. Failures are saved with reason, actor and timestamp.
- Test reports can be revisited after restarts; retesting a scenario updates its recorded status.

## Automated regression tests

GitHub Actions executes Python unit tests on every pull request. They cover database/rules and Test Runner persistence; they do NOT log in to Discord or impersonate a member.

## After testing

Open **Test Report** and review passed, failed, skipped and not-yet-tested cases. Share descriptions and optional screenshots of failures to support debugging. Do not send passwords or tokens.