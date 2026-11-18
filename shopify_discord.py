"""Discord-facing GYMGUILD Shopify shop and safe order/reward workflows."""
import sqlite3
from datetime import datetime
from urllib.parse import quote

import discord
from discord import app_commands

import shopify_client as shopify

api = None


def ensure_tables():
    with api.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS shopify_order_verifications (
          guild_id INTEGER NOT NULL, purchase_id INTEGER NOT NULL UNIQUE,
          order_gid TEXT NOT NULL, verified_at TEXT NOT NULL,
          PRIMARY KEY (guild_id,order_gid)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS shopify_reward_codes (
          guild_id INTEGER NOT NULL, reward_id INTEGER NOT NULL,
          code TEXT NOT NULL, discount_gid TEXT,
          status TEXT NOT NULL, created_at TEXT NOT NULL,
          delivered_at TEXT,
          PRIMARY KEY (guild_id,reward_id)
        )""")


def shop_embed():
    e=discord.Embed(
        title="✦ GYMGUILD — THE GUILD SHOP",
        description="Where culture meets strength.\n\n"
                    "**Browse the latest GYMGUILD kit** without leaving Discord. "
                    "Choose a product to see its price and design, then shop securely at "
                    "**gymguild.co.uk**.\n\n"
                    "Already ordered? Use **Verify Order** to submit your order for "
                    "the First Purchase milestone review.",
        colour=0x71A7A2)
    e.add_field(name="Guild rewards",value="Master 10% · Legend 15% · Paragon 20%\n"
                "Request rewards through the Player Hub when unlocked.",inline=False)
    e.set_footer(text="All prices and checkout are provided by Shopify • no payment details in Discord")
    return e


def price_label(item):
    result=f'£{item["price"]:.2f}'
    if item["sale"] and item["compare_at"]:
        result+=f' (was £{item["compare_at"]:.2f})'
    return result


def product_embed(item):
    e=discord.Embed(title=item["title"],
        description=f'**{price_label(item)}**\n\n'
                    f'[View on GYMGUILD]({item["url"]})\n'
                    'Size, availability and final price are confirmed at checkout.',
        colour=0x71A7A2,url=item["url"])
    if item["image"]:
        e.set_image(url=item["image"])
    return e


class ProductDetail(discord.ui.View):
    def __init__(self,url):
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="View Product on Shopify",emoji="🛍️",url=url))


class ProductPick(discord.ui.Select):
    def __init__(self,items):
        super().__init__(placeholder="Choose a product",max_values=1,min_values=1,
                         options=[discord.SelectOption(
                             label=x["title"][:95],description=price_label(x)[:100],
                             value=str(i)) for i,x in enumerate(items[:25])])
        self.items=items[:25]

    async def callback(self,interaction):
        item=self.items[int(self.values[0])]
        await interaction.response.send_message(
            embed=product_embed(item),view=ProductDetail(item["url"]),ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())


class ProductMenu(discord.ui.View):
    def __init__(self,items):
        super().__init__(timeout=180)
        self.add_item(ProductPick(items))


async def browse(interaction,mode="all"):
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        items=await shopify.catalog()
    except (shopify.ShopifyUnavailable,shopify.ShopifyError,TimeoutError):
        return await interaction.followup.send(
            "⚠️ The live shop catalog could not be fetched. "
            "Browse directly at https://gymguild.co.uk instead.",ephemeral=True)
    if mode=="sale":
        items=[x for x in items if x["sale"]]
    elif mode=="new":
        items=sorted(items,key=lambda x:x["published_at"],reverse=True)
    if not items:
        return await interaction.followup.send(
            "No matching products are currently published. You can browse the store at "
            "https://gymguild.co.uk.",ephemeral=True)
    await interaction.followup.send(
        "🛍️ **" + ("Sale products" if mode=="sale" else
                   "New arrivals" if mode=="new" else "GYMGUILD products")
        + "** — select one to see its live product card.",
        view=ProductMenu(items),ephemeral=True)


class ShopHub(discord.ui.View):
    """Persistent, public entry point. Private actions use ephemeral responses."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Shop Online",emoji="🛍️",url=shopify.public_domain(),row=1))

    @discord.ui.button(label="Browse Products",emoji="👕",style=discord.ButtonStyle.primary,
                       custom_id="guildkeeper:shop:browse",row=0)
    async def browse_all(self,interaction,button):
        await browse(interaction)

    @discord.ui.button(label="New Arrivals",emoji="✨",style=discord.ButtonStyle.secondary,
                       custom_id="guildkeeper:shop:new",row=0)
    async def arrivals(self,interaction,button):
        await browse(interaction,"new")

    @discord.ui.button(label="On Sale",emoji="🏷️",style=discord.ButtonStyle.secondary,
                       custom_id="guildkeeper:shop:sale",row=0)
    async def sale(self,interaction,button):
        await browse(interaction,"sale")

    @discord.ui.button(label="Verify Order",emoji="📦",style=discord.ButtonStyle.secondary,
                       custom_id="guildkeeper:shop:order",row=0)
    async def order(self,interaction,button):
        if interaction.guild_id!=api.GUILD_ID:
            return await interaction.response.send_message("Available in GYMGUILD only.",ephemeral=True)
        if not shopify.admin_configured():
            return await interaction.response.send_message(
                "Shopify order verification is being configured. "
                "For now, use /milestone_purchase to submit an order for manual review.",
                ephemeral=True)
        await interaction.response.send_modal(VerifyOrderModal())


class VerifyOrderModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Verify GYMGUILD Order",timeout=300)
        self.reference=discord.ui.TextInput(
            label="Shopify order number",placeholder="#1001",max_length=48)
        self.email=discord.ui.TextInput(
            label="Email used at Shopify checkout",max_length=254,
            placeholder="name@example.com")
        self.add_item(self.reference)
        self.add_item(self.email)

    async def on_submit(self,interaction):
        if interaction.guild_id!=api.GUILD_ID or interaction.user.bot:
            return await interaction.response.send_message("Unavailable.",ephemeral=True)
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            verification=await shopify.verify_order(self.reference.value,self.email.value)
        except ValueError as exc:
            return await interaction.followup.send("⚠️ "+str(exc),ephemeral=True)
        except (shopify.ShopifyError,shopify.ShopifyUnavailable,TimeoutError):
            return await interaction.followup.send(
                "⚠️ Shopify could not verify this order right now. Try again later "
                "or use the existing manual milestone request.",ephemeral=True)
        if verification["status"]!="verified":
            return await interaction.followup.send(
                "⚠️ "+verification["reason"]+
                " If you need help, contact a moderator privately.",ephemeral=True)
        ensure_tables()
        with api.connect() as db:
            existing=db.execute(
                "SELECT purchase_id FROM shopify_order_verifications WHERE guild_id=? AND order_gid=?",
                (api.GUILD_ID,verification["order_gid"])).fetchone()
        if existing:
            return await interaction.followup.send(
                "This order has already been submitted for a Guild milestone review. "
                "An order can only be used for one member.",ephemeral=True)
        status, purchase_id=api.submit_purchase_order(
            api.GUILD_ID,interaction.user.id,verification["reference"])
        if status not in ("submitted","pending"):
            messages={
                "approved":"Your First Purchase milestone is already claimed.",
                "duplicate":"This order has already been used for another member.",
                "used_reference":"The order number cannot be reused.",
            }
            return await interaction.followup.send(
                "⚠️ "+messages.get(status,"This order request is already being processed."),
                ephemeral=True)
        try:
            with api.connect() as db:
                db.execute("INSERT INTO shopify_order_verifications "
                           "(guild_id,purchase_id,order_gid,verified_at) VALUES (?,?,?,?)",
                           (api.GUILD_ID,purchase_id,verification["order_gid"],
                            api.utc_now().isoformat()))
        except sqlite3.IntegrityError:
            return await interaction.followup.send(
                "Order already verified; contact a moderator if your request is pending.",
                ephemeral=True)
        if status=="submitted":
            await api.post_purchase_review(interaction.guild,interaction.user.id,purchase_id)
        await interaction.followup.send(
            "✅ Shopify confirms that your order number and checkout email match a "
            "**fully paid, uncancelled order**. Your First Purchase request is now "
            "**awaiting moderator approval**. XP is only awarded once approved.\n"
            "Your email address was not saved by GuildKeeper.",
            ephemeral=True)


async def post_shop_panel(channel):
    if not isinstance(channel,discord.TextChannel) or channel.guild.id!=api.GUILD_ID:
        return False,"Choose a GYMGUILD text channel."
    me=channel.guild.me
    if me is None:
        return False,"GuildKeeper permissions are unavailable."
    perms=channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links
            and perms.read_message_history):
        return False,"GuildKeeper needs View Channel, Send Messages, Embed Links and Message History."
    key=f"shopify-shop-post-{api.GUILD_ID}"
    with api.connect() as db:
        row=db.execute("SELECT value FROM v12_config WHERE key=?",(key,)).fetchone()
    old_channel=None
    old=None
    if row:
        try:
            cid,mid=map(int,row["value"].split(":"))
            old_channel=channel.guild.get_channel(cid)
            if isinstance(old_channel,discord.TextChannel):
                old=await old_channel.fetch_message(mid)
        except (ValueError,discord.HTTPException):
            pass
    try:
        if old is not None and old_channel.id==channel.id:
            message=old
            await old.edit(embed=shop_embed(),view=ShopHub())
        else:
            message=await channel.send(embed=shop_embed(),view=ShopHub(),
                                       allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        return False,"Unable to post; check the channel and GuildKeeper permissions."
    with api.connect() as db:
        db.execute("INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)",
                   (key,f"{channel.id}:{message.id}"))
    if old is not None and old_channel.id!=channel.id:
        try:
            await old.delete()
        except discord.HTTPException:
            pass
    return True,"GYMGUILD Shop is ready in "+channel.mention


def install(module):
    global api
    api=module
    ensure_tables()

    @api.bot.tree.command(name="admin_shop_post",
                          description="Create or move the permanent GYMGUILD Discord Shop panel")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_shop_post(interaction:discord.Interaction,channel:discord.TextChannel):
        if interaction.guild_id!=api.GUILD_ID:
            return await interaction.response.send_message("GYMGUILD only.",ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ok,text=await post_shop_panel(channel)
        await interaction.followup.send(("✅ " if ok else "⚠️ ")+text,ephemeral=True)
