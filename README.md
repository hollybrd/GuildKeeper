# GYMGUILD ✦ GuildKeeper  -  Start Here

GuildKeeper is the Discord community bot for GYMGUILD. It runs continuously on an Oracle server, remembers member progress in **SQLite**, and interacts with Discord and (optionally) Shopify.

**This repository is now annotated in plain English.** Each Python module opens with a **READING GUIDE**, and important functions have comments explaining why they exist. You can read the files online without installing anything.

## A simple picture of how it works

```text
DISCORD MEMBER                   GUILDKEEPER                      STORAGE / EXTERNAL
joins / posts / clicks           bot.py receives event             SQLite guildkeeper.db
       |                         |                                |
       +------------------------>+--> permission / quest checks --> XP, claims, ranks
                                 |                                |
                                 +--> Discord response / role / DM+
                                 |
STAFF BUTTON / COMMAND --------->+--> admin_hub.py / testing_cheats.py
                                 |       +--> audit + confirmations
                                 |
SHOP BUTTON -------------------->+--> shopify_discord.py
                                         +--> shopify_client.py ---> Shopify APIs
                                         +--> shopify_rewards.py --> reward ticket

EVERY MINUTE: bot.py scheduler --> announcement / notification / maintenance jobs
```

### Suggested reading order

| Start with | What it teaches you |
| --- | --- |
| **1. This README** | Understand what files exist and how they work together. |
| **2. `bot.py`** | The Discord events, database, quests, XP, ranks, streaks and scheduled work. |
| **3. `admin_hub.py`** | How staff buttons and private confirmations work. |
| **4. `testing_cheats.py` + `quest_testing.py`** | How you safely simulate progress and reset test data. |
| **5. `test_runner.py`** | How manual Discord tests save pass/fail reports. |
| **6. `announcements_scheduler.py` + `announcement_ui.py`** | How UK-time scheduled messages are stored, dispatched and edited. |
| **7. `shopify_discord.py` + `shopify_client.py`** | The shop buttons, public catalog and Shopify API calls. |
| **8. `shopify_rewards.py` + `shopify_events.py`** | Single-use discount codes and optional product announcements. |
| **9. `health_check.py` + `panel_health.py` + `admin_maintenance.py`** | How GuildKeeper notices and repairs out-of-date panels. |
| **10. `tests/`** | Examples that show which behaviours are expected and protected. |
| **11. `.github/workflows/`** | How GitHub verifies code and deploys it to Oracle. |

### Core concepts explained

**Discord event:** Something Discord tells the bot happened, such as a member joining, a message being posted or a button being clicked.

**Slash command:** A `/command` that calls a registered Python function. Buttons are a friendlier interface to the same underlying work.

**View:** A `discord.ui.View` object defining the buttons or selectors beneath a message. Permanent views have stable identifiers and are re-registered when the bot starts.

**Ephemeral message:** A private Discord response visible only to the member or moderator who clicked a control.

**SQLite database:** The file `guildkeeper.db` stores XP, quests, orders, tickets, reminders and configuration. It must be preserved during deployments. The repository contains SQL definitions, not members' real production database.

**Transaction:** A group of database operations intended to succeed or fail together, reducing half-applied rewards or resets.

**Idempotent:** Safe to repeat without awarding the same benefit twice. See `grant_once()` in `bot.py`, unique keys in the database and scheduler event reservations.

**Scheduler:** The background loop in `bot.py` checks due work once per minute. Scheduled announcements are stored in SQLite; they are *not* hundreds of separate Python timers.

**Environment variable:** A private configuration value loaded from Oracle's `.env`, such as a Discord channel ID or Shopify credential. Do not commit passwords, app secrets, access tokens or `.env` to GitHub.

## Follow a member action end-to-end

### Example A  -  a member posts eligible training proof

1. `bot.py → on_message()` receives the message.
2. The post is classified according to the rules and channel.
3. `credit_action()` checks whether this objective was already earned, whether a pause applies, and whether the second training proof is on another UK calendar day.
4. A successful action is saved in `weekly_actions`; XP changes are logged.
5. The player sees updated progress in the Player Hub and possibly an Inbox event.
6. When all four objectives are done, the weekly bonus is awarded once.

### Example B  -  a moderator adjusts XP

1. `admin_hub.py → AdminHubView` opens a staff-only panel.
2. The moderator selects a member and enters an amount and reason.
3. A confirmation shows the current and projected XP balance.
4. The confirmed change calls the shared `bot.py` XP logic and updates Discord rank roles.
5. `admin_hub_audit` remembers who made the change and why.

### Example C  -  a Shopify reward is claimed

1. A player unlocks an eligible rank and opens their private reward ticket.
2. A moderator clicks **Generate Shopify Code**, then confirms.
3. `shopify_rewards.py` reserves a unique code in SQLite before making the API call.
4. `shopify_client.py` asks Shopify to create a one-use discount.
5. The code is delivered in the existing private ticket and the reward is recorded as fulfilled.
6. If Shopify's outcome is uncertain, GuildKeeper does **not** silently create a second code.

### Example D  -  the bot restarts

1. The bot reads its environment and connects to Discord.
2. Registered permanent `discord.ui.View` classes allow existing buttons to respond.
3. Startup code synchronizes roles and asks `admin_maintenance.py` to inspect saved panel messages.
4. Known stale panels may be edited/repaired; uncertain ones are flagged rather than duplicated.
5. The once-per-minute scheduler resumes due tasks from the persisted SQLite records.

## How code changes reach Discord

```text
Edit file on a feature branch
  ↓
Open GitHub pull request
  ↓
GitHub Actions runs syntax and unit tests
  ↓
Merge into main
  ↓
Manually run Deploy GuildKeeper workflow
  ↓
Oracle backs up the database and configuration
  ↓
Oracle installs new code and restarts the bot
  ↓
Check Admin Hub → Health Check and test with a regular Discord member
```

**Merging does not automatically deploy.** The deployment workflow has a manual trigger and a rollback process. Never upload a fresh/empty `guildkeeper.db` over the production copy.

## Types of comments you will see

- **READING GUIDE** at the top of a module: why the file exists and how it fits in.
- **Comments above functions**: what the function is responsible for or why a safety check matters.
- **Existing docstrings**: more precise contracts for functions and arguments.
- **Tests**: executable examples of what should happen, using temporary databases rather than real accounts.

Comments explain the *current implementation*. They do not mean a feature has passed a live Discord or Shopify integration test.

## Other guides

- [Live Discord testing](LIVE_TESTING.md)  -  how to test with a separate ordinary account.
- [Announcements](ANNOUNCEMENTS.md)  -  automatic and manually scheduled posts.
- [Shopify integration](SHOPIFY_DISCORD.md)  -  catalog, order checks, reward codes, and secrets.

If you want to understand a particular button, search GitHub for its label or `custom_id`, follow its callback, and look for the helper it calls in `bot.py`.
