"""Staff-confirmed, single-use Shopify reward codes for private GuildKeeper tickets.

READING GUIDE
-------------
This module is the safety boundary between Discord reward tickets and live
Shopify discounts. A moderator confirms issuance; the bot first reserves a code
in SQLite, creates it once in Shopify, then posts it only to the private ticket.
An interrupted API response is NOT retried automatically to avoid duplicate codes.
"""
import asyncio
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation

import aiohttp

import discord

import shopify_client as shopify


# Only server moderators with Manage Server may issue these live rewards.
def permitted(api,interaction):
    return (interaction.guild_id==api.GUILD_ID and
            isinstance(interaction.user,discord.Member) and
            interaction.user.guild_permissions.manage_guild)


# A previously reserved or delivered code belongs to one specific ticket.
def get_ticket_code(api,ticket_id):
    with api.connect() as db:
        return db.execute(
            "SELECT * FROM shopify_reward_codes WHERE guild_id=? AND reward_id=?",
            (api.GUILD_ID,ticket_id)).fetchone()


# Serialize staff actions so two confirmations cannot issue or deliver concurrently.
_reward_lock = asyncio.Lock()


def diagnostic(exc):
    if isinstance(exc, (shopify.ShopifyError, shopify.ShopifyUnavailable)):
        return str(exc)[:300]
    return "Shopify connection interrupted or timed out."


def match_discount(api, ticket, node):
    """Only recover an existing code with expected rank, usage and valid expiry."""
    discount = node.get("codeDiscount") or {}
    try:
        return (bool(node.get("id"))
                and discount.get("__typename") == "DiscountCodeBasic"
                and discount.get("title") == f"GYMGUILD GuildKeeper {ticket['rank']} #{ticket['id']}"
                and discount.get("status") == "ACTIVE"
                and discount.get("usageLimit") == 1
                and discount.get("asyncUsageCount") == 0
                and datetime.fromisoformat(discount["endsAt"]) > api.utc_now()
                and Decimal(str(discount["customerGets"]["value"]["percentage"]))
                    == Decimal(api.REWARD_RANKS[ticket["rank"]]) / 100)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False


async def check_status(api, interaction, ticket_id):
    """Read-only lookup; no code is created or delivered by this button."""
    async with _reward_lock:
        with api.connect() as db:
            ticket = db.execute("SELECT * FROM reward_tickets WHERE id=? AND guild_id=?",
                                (ticket_id, api.GUILD_ID)).fetchone()
        if not ticket or ticket["channel_id"] != interaction.channel_id or ticket["status"] == "closed":
            return "Reward ticket unavailable."
        saved = get_ticket_code(api, ticket_id)
        if saved is None:
            return "No code has been reserved yet."
        try:
            node = await shopify.lookup_discount(saved["code"])
        except (shopify.ShopifyError, shopify.ShopifyUnavailable, TimeoutError, aiohttp.ClientError) as exc:
            return "Shopify status check failed: " + diagnostic(exc)
        if node is None:
            return "No matching code found in Shopify. No changes made. If the ticket is open, you can confirm a retry using the same reserved code."
        if match_discount(api, ticket, node):
            return "Shopify confirmed the matching unused, active discount. Confirm to recover and deliver it without creating another."
        return "Shopify found the code, but its settings or status do not match an unused active reward. Review it manually; no changes made."


# Reservation -> Shopify creation -> private delivery -> ticket fulfilment, with no blind retry.
async def issue_or_deliver(api,interaction,ticket_id,retry=False):
    async with _reward_lock:
        return await _issue_or_deliver(api,interaction,ticket_id,retry)


async def _issue_or_deliver(api,interaction,ticket_id,retry):
    """Never mint a second code for the same ticket, even after an uncertain response."""
    with api.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        ticket=db.execute("SELECT * FROM reward_tickets WHERE id=? AND guild_id=?",
                          (ticket_id,api.GUILD_ID)).fetchone()
        if ticket is None or ticket["channel_id"]!=interaction.channel_id or ticket["status"]=="closed":
            return "⚠️ Reward ticket is unavailable."
        if ticket["rank"] not in api.REWARD_RANKS:
            return "⚠️ Unknown reward rank."
        saved=db.execute("SELECT * FROM shopify_reward_codes WHERE guild_id=? AND reward_id=?",
                         (api.GUILD_ID,ticket_id)).fetchone()
        if saved is None:
            if ticket["status"]!="open":
                return "⚠️ This ticket was already fulfilled or closed; no new code was created."
            if not shopify.admin_configured():
                return "⚠️ Shopify API credentials are not configured. No code was created."
            code=shopify.new_code(ticket["rank"],ticket_id)
            now=api.utc_now()
            db.execute("""INSERT INTO shopify_reward_codes
              (guild_id,reward_id,code,status,created_at)
              VALUES (?,?,?,'reserved',?)""",
              (api.GUILD_ID,ticket_id,code,now.isoformat()))
            status="reserved"
        else:
            code=saved["code"]
            status=saved["status"]
            now=api.utc_now()

    if status in ("reserved","uncertain"):
        if saved is not None and not retry:
            return ("⚠️ A discount creation attempt is uncertain. Reopen Generate Shopify Code "
                    "to check Shopify status or confirm a retry. No second code was generated.")
        if ticket["status"] != "open":
            return "⚠️ Ticket is no longer open; no code was created."
        if saved is not None:
            try:
                node = await shopify.lookup_discount(code)
            except (shopify.ShopifyError,shopify.ShopifyUnavailable,TimeoutError,aiohttp.ClientError) as exc:
                return "⚠️ Status check failed; retry blocked. " + diagnostic(exc)
            if node is not None:
                if not match_discount(api,ticket,node):
                    return "⚠️ Existing Shopify discount does not match an unused active reward. Manual review required."
                shopify_gid = node["id"]
            else:
                shopify_gid = None
        else:
            shopify_gid = None
        if shopify_gid is None:
            try:
                # Shopify requires unique discount codes. Reuse the exact reservation,
                # even if a previous create was successful but not yet visible.
                shopify_gid=await shopify.create_discount(ticket["rank"],ticket_id,code,now)
            except (shopify.ShopifyError,shopify.ShopifyUnavailable,TimeoutError,aiohttp.ClientError) as exc:
                with api.connect() as db:
                    db.execute("""UPDATE shopify_reward_codes SET status='uncertain'
                      WHERE guild_id=? AND reward_id=? AND status IN ('reserved','uncertain')""",
                      (api.GUILD_ID,ticket_id))
                api.log.warning("Shopify code creation uncertain for ticket %s: %s",ticket_id,diagnostic(exc))
                return ("⚠️ Shopify did not confirm that the discount was created. "
                        "The reservation remains locked against duplicates. "
                        "Admin diagnostic: " + diagnostic(exc))
        with api.connect() as db:
            db.execute("""UPDATE shopify_reward_codes SET status='created',discount_gid=?
              WHERE guild_id=? AND reward_id=? AND status IN ('reserved','uncertain')""",
              (shopify_gid,api.GUILD_ID,ticket_id))
    elif status=="delivered":
        return "✅ This reward code was already delivered in the private ticket."
    elif status!="created":
        return "⚠️ This code is awaiting staff reconciliation in Shopify."

    # Recheck ticket after the network request; a moderator may have closed it.
    with api.connect() as db:
        current = db.execute("SELECT status,channel_id FROM reward_tickets WHERE id=? AND guild_id=?",
                             (ticket_id,api.GUILD_ID)).fetchone()
    if not current or current["status"] != "open" or current["channel_id"] != interaction.channel_id:
        return "⚠️ Ticket no longer open; saved discount requires staff review."
    # It is deliberately delivered only in the existing private reward ticket,
    # not by potentially blocked DMs or a public channel.
    channel=interaction.channel
    try:
        await channel.send(
            f"🎟️ **GYMGUILD {ticket['rank']} Reward  -  {api.REWARD_RANKS[ticket['rank']]}% off**\n"
            f"Your single-use Shopify code: **\`{code}\`**\n"
            "Use it at https://gymguild.co.uk during checkout. "
            "**Valid for 7 days after creation**, one order only. "
            "Do not share your private reward code.",
            allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        return ("⚠️ Shopify created the code, but GuildKeeper could not post it in this ticket. "
                "The existing code is saved and can be delivered again without creating another.")

    with api.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""UPDATE shopify_reward_codes SET status='delivered',delivered_at=?
          WHERE guild_id=? AND reward_id=? AND status='created'""",
          (api.utc_now().isoformat(),api.GUILD_ID,ticket_id))
        db.execute("""UPDATE reward_tickets SET status='fulfilled',fulfilled_at=?
          WHERE id=? AND guild_id=? AND status='open'""",
          (api.utc_now().isoformat(),ticket_id,api.GUILD_ID))
    api.admin_hub.audit(interaction.user.id,"shopify_reward_code_issued",
                        f"Reward ticket #{ticket_id}, rank {ticket['rank']}; one-use code delivered",
                        ticket["user_id"])
    return "✅ Single-use code created in Shopify and delivered to this private ticket."


# A second click protects against accidentally issuing real discounts.
class ConfirmShopifyReward(discord.ui.View):
    def __init__(self,api,moderator_id,ticket_id,retry=False):
        super().__init__(timeout=180)
        self.api=api
        self.moderator_id=moderator_id
        self.ticket_id=ticket_id
        self.used=False
        self.retry=retry
        if retry:
            self.confirm.label='Confirm Recovery / Retry'

    async def interaction_check(self,interaction):
        if not permitted(self.api,interaction) or interaction.user.id!=self.moderator_id:
            await interaction.response.send_message(
                "Only the requesting Manage Server moderator can approve this code.",
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm and Issue Code",emoji="✅",style=discord.ButtonStyle.danger)
    async def confirm(self,interaction,button):
        if self.used:
            return await interaction.response.send_message("Already submitted.",ephemeral=True)
        self.used=True
        await interaction.response.defer(ephemeral=True)
        result=await issue_or_deliver(self.api,interaction,self.ticket_id,retry=self.retry)
        await interaction.followup.send(result,ephemeral=True)

    @discord.ui.button(label="Check Shopify Status",emoji="🔍",style=discord.ButtonStyle.secondary)
    async def status(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        result=await check_status(self.api,interaction,self.ticket_id)
        await interaction.followup.send(result,ephemeral=True)

    @discord.ui.button(label="Cancel",emoji="✖️",style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button):
        self.used=True
        await interaction.response.edit_message(content="No Shopify code created.",view=None)


# Check ticket ownership, rank and credentials; show the moderator a confirmation.
async def request_code(api,interaction):
    if not permitted(api,interaction):
        return await interaction.response.send_message(
            "Manage Server permission is required to issue store discounts.",ephemeral=True)
    with api.connect() as db:
        ticket=db.execute(
            "SELECT * FROM reward_tickets WHERE guild_id=? AND channel_id=?",
            (api.GUILD_ID,interaction.channel_id)).fetchone()
    if ticket is None or ticket["status"]=="closed":
        return await interaction.response.send_message(
            "No eligible open reward ticket was found here.",ephemeral=True)
    if ticket["status"]!="open" and get_ticket_code(api,ticket["id"]) is None:
        return await interaction.response.send_message(
            "This reward was already fulfilled by a moderator; a second code cannot be issued.",
            ephemeral=True)
    if not shopify.admin_configured():
        return await interaction.response.send_message(
            "⚠️ Shopify Admin API isn't configured. No discount can be issued yet. "
            "Set Shopify domain and Client ID/Secret with read_discounts and write_discounts permissions.",
            ephemeral=True)
    percentage=api.REWARD_RANKS.get(ticket["rank"])
    if not percentage:
        return await interaction.response.send_message("Invalid reward rank.",ephemeral=True)
    saved = get_ticket_code(api,ticket["id"])
    retry = saved is not None and saved["status"] in ("reserved","uncertain")
    await interaction.response.send_message(
        f"🎟️ **Issue Shopify Reward Code?**\n"
        f"Ticket: **#{ticket['id']}** · Member: <@{ticket['user_id']}>\n"
        f"Reward: **{ticket['rank']}  -  {percentage}% off**\n"
        "Shopify will create a **single-use**, **7-day** discount for the store. "
        "It will be posted privately in this ticket, logged, and marked fulfilled "
        "only after delivery.\n"
        "**Only confirm after reviewing this member's eligibility.**" +
        ("\nA previous attempt was uncertain. Check status first; confirmation will check again and reuse the same reserved code." if retry else ""),
        view=ConfirmShopifyReward(api,interaction.user.id,ticket["id"],retry=retry),
        ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
