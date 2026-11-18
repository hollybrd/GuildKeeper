"""Permanent staff Message Scheduler dashboard.

READING GUIDE
-------------
The message visible in Discord is a dashboard, not a source of truth.
All schedules and automation rules live in announcements_scheduler.py's
SQLite tables. Only members with Manage Server can operate its buttons.
Submenus are private to the moderator who opened them.
"""
from datetime import datetime, timezone

import discord
from discord import app_commands

import announcements_scheduler as core
import announcement_ui

api=None
KEY_PREFIX="message-scheduler-panel-"


def allowed(interaction):
    return api.admin_hub.allowed(interaction)


def status_lines():
    lines=[]
    for kind,(name,trigger) in core.AUTOMATION_KINDS.items():
        policy=core.automation_policy(kind)
        symbol="🟢" if policy["enabled"] else "⏸️"
        destination=f'<#{policy["channel_id"]}>' if policy["channel_id"] else "No channel"
        origin="Custom" if policy["configured"] else "Default"
        lines.append(f"{symbol} **{name}** [{origin}]\n{trigger} | {destination}")
    return lines


def timeline_text(page=0,per_page=10):
    entries=core.timeline(limit=120)
    page=max(0,page)
    entries=entries[page*per_page:(page+1)*per_page]
    if not entries:
        return "📆 **Posting timeline**\nNo upcoming scheduled posts. Product alerts are event-driven."
    lines=[f"📆 **Posting timeline | Page {page+1} (UK time)**",
           "Automatically generated previews. Event-based Shopify posts have no fixed delivery date.\n"]
    for when,title,frequency,channel,kind in entries:
        local=when.astimezone(core.TZ)
        when_text=local.strftime("%a %d %b, %H:%M")
        lines.append(f'**{when_text}** | {discord.utils.escape_markdown(title[:75])}\n'
                     f'<#{channel}> | {frequency} | {kind}')
    return "\n\n".join(lines)[:3950]


def dashboard_embed():
    custom=core.upcoming(limit=100)
    active=sum(row["status"]=="active" for row in custom)
    problems=core.failed_count()
    automatic=sum(core.automation_policy(k)["enabled"] for k in core.AUTOMATION_KINDS)
    next_posts=core.timeline(limit=4)
    e=discord.Embed(title="📨 GYMGUILD | MESSAGE SCHEDULER",
        description="Admin dashboard for timed messages, repeating posts and event-based announcements.\n"
                    "Use the buttons below to manage everything privately.",
        colour=0x71A7A2)
    e.add_field(name="Messages",value=f"{active} active | {len(custom)-active} inactive",inline=True)
    e.add_field(name="Automations",value=f"{automatic}/4 enabled",inline=True)
    e.add_field(name="Delivery issues",value=f"{problems} to review",inline=True)
    if next_posts:
        e.add_field(name="Next posts",
            value="\n".join(
                f"**{dt.astimezone(core.TZ):%d %b %H:%M}** | {discord.utils.escape_markdown(title[:48])}"
                for dt,title,_,_,_ in next_posts)[:1024],inline=False)
    else:
        e.add_field(name="Next posts",value="No timed posts configured.",inline=False)
    e.add_field(name="Automation status",
                value="\n".join(
                    ("🟢 " if core.automation_policy(k)["enabled"] else "⏸️ ")+name
                    for k,(name,_) in core.AUTOMATION_KINDS.items()),inline=False)
    e.set_footer(text="Times are UK local. Use Refresh Dashboard to update this snapshot.")
    return e


class SchedulerDashboard(discord.ui.View):
    """Persistent panel. All actions produce owner-checked, ephemeral menus."""
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self,interaction):
        if not allowed(interaction):
            await interaction.response.send_message(
                "Manage Server permission is required for the Message Scheduler.",ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Posting Timeline",emoji="📆",style=discord.ButtonStyle.primary,
                       custom_id="guildkeeper:scheduler:timeline",row=0)
    async def timeline(self,interaction,button):
        await interaction.response.send_message(
            timeline_text(),view=TimelineMenu(interaction.user.id),
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Scheduled Messages",emoji="📋",style=discord.ButtonStyle.secondary,
                       custom_id="guildkeeper:scheduler:messages",row=0)
    async def messages(self,interaction,button):
        await interaction.response.send_message(
            announcement_ui.listing(),
            view=announcement_ui.UpcomingMenu(interaction.user.id),
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Automations",emoji="⚙️",style=discord.ButtonStyle.secondary,
                       custom_id="guildkeeper:scheduler:automations",row=0)
    async def automations(self,interaction,button):
        await interaction.response.send_message(
            automation_text(),view=AutomationList(interaction.user.id),
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="New Message",emoji="➕",style=discord.ButtonStyle.success,
                       custom_id="guildkeeper:scheduler:new_message",row=1)
    async def new_message(self,interaction,button):
        await interaction.response.send_message(
            "Choose a channel for your scheduled message.",
            view=announcement_ui.ChooseChannel(interaction.user.id),ephemeral=True)

    @discord.ui.button(label="New Automation",emoji="🔔",style=discord.ButtonStyle.success,
                       custom_id="guildkeeper:scheduler:new_automation",row=1)
    async def new_automation(self,interaction,button):
        await interaction.response.send_message(
            "Choose an event trigger. Each trigger can have one automation rule.",
            view=AutomationChoice(interaction.user.id,create_only=True),ephemeral=True)

    @discord.ui.button(label="Refresh Dashboard",emoji="🔄",style=discord.ButtonStyle.secondary,
                       custom_id="guildkeeper:scheduler:refresh",row=1)
    async def refresh(self,interaction,button):
        await interaction.response.edit_message(
            embed=dashboard_embed(),view=SchedulerDashboard())


def automation_text():
    return ("⚙️ **Event automations**\n\n"+"\n\n".join(status_lines())+
            "\n\nSelect an automation to preview, edit, pause, resume or remove it."
            "\n\nRemoving a custom rule restores the default event behaviour. "
            "To disable an event, choose **Pause** instead.")


class Private(discord.ui.View):
    def __init__(self,owner):
        super().__init__(timeout=300)
        self.owner=owner

    async def interaction_check(self,interaction):
        if not allowed(interaction) or interaction.user.id!=self.owner:
            await interaction.response.send_message(
                "Only the moderator who opened this menu can use it.",ephemeral=True)
            return False
        return True


class TimelineMenu(Private):
    def __init__(self,owner,page=0):
        super().__init__(owner)
        self.page=max(0,page)

    @discord.ui.button(label="Previous Page",emoji="⬅️",style=discord.ButtonStyle.secondary)
    async def previous(self,interaction,button):
        page=max(0,self.page-1)
        await interaction.response.edit_message(
            content=timeline_text(page),view=TimelineMenu(self.owner,page),
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Next Page",emoji="➡️",style=discord.ButtonStyle.secondary)
    async def next(self,interaction,button):
        page=self.page+1
        if not core.timeline(limit=120)[page*10:(page+1)*10]:
            return await interaction.response.send_message(
                "You are on the final page.",ephemeral=True)
        await interaction.response.edit_message(
            content=timeline_text(page),view=TimelineMenu(self.owner,page),
            allowed_mentions=discord.AllowedMentions.none())


class AutomationSelect(discord.ui.Select):
    def __init__(self,create_only=False):
        options=[]
        for kind,(name,trigger) in core.AUTOMATION_KINDS.items():
            exists=core.automation(kind) is not None
            if create_only and exists:
                continue
            options.append(discord.SelectOption(
                label=name,description=trigger[:95],value=kind))
        if not options:
            options=[discord.SelectOption(
                label="All automation types already configured",value="none")]
        super().__init__(placeholder="Select an event trigger",
                         min_values=1,max_values=1,options=options)

    async def callback(self,interaction):
        kind=self.values[0]
        if kind not in core.AUTOMATION_KINDS:
            return await interaction.response.send_message(
                "All types have rules. Use Automations to edit an existing one.",
                ephemeral=True)
        if self.view.create_only:
            if core.automation(kind) is not None:
                return await interaction.response.send_message(
                    "That automation already exists. Select Edit instead.",ephemeral=True)
            return await interaction.response.send_message(
                f"Choose a destination channel for **{core.AUTOMATION_KINDS[kind][0]}**.",
                view=AutomationChannel(self.view.owner,kind),
                ephemeral=True)
        await interaction.response.send_message(
            automation_detail(kind),view=AutomationActions(self.view.owner,kind),
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


class AutomationChoice(Private):
    def __init__(self,owner,create_only=False):
        super().__init__(owner)
        self.create_only=create_only
        self.add_item(AutomationSelect(create_only))


class AutomationList(AutomationChoice):
    def __init__(self,owner):
        super().__init__(owner,False)


class AutomationChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="Choose destination channel",
                         channel_types=[discord.ChannelType.text])

    async def callback(self,interaction):
        view=self.view
        channel_id=self.values[0].id
        try:
            core.check_channel(interaction.guild,channel_id)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc),ephemeral=True)
        await interaction.response.send_modal(
            AutomationForm(view.owner,view.kind,channel_id))


class AutomationChannel(Private):
    def __init__(self,owner,kind):
        super().__init__(owner)
        self.kind=kind
        self.add_item(AutomationChannelSelect())


def automation_detail(kind):
    policy=core.automation_policy(kind)
    name,trigger=core.AUTOMATION_KINDS[kind]
    return (f'⚙️ **{name}**\nTrigger: {trigger}\n'
            f'Status: **{"Enabled" if policy["enabled"] else "Paused"}**\n'
            f'Channel: <#{policy["channel_id"]}>\n'
            f'Custom rule: {"Yes" if policy["configured"] else "No (using defaults)"}\n'
            f'Title: {policy["title"] or "(default event title)"}\n'
            f'Extra introduction: {policy["intro"] or "(none)"}\n\n'
            "Editing changes future event messages only. Previously posted messages stay as they are.")


class AutomationForm(discord.ui.Modal):
    def __init__(self,owner,kind,channel_id):
        super().__init__(title="Configure Guild Automation",timeout=300)
        self.owner,self.kind,self.channel_id=owner,kind,channel_id
        row=core.automation(kind)
        self.heading=discord.ui.TextInput(
            label="Announcement title",max_length=100,
            default=row["title"] if row else core.AUTOMATION_KINDS[kind][0])
        self.intro=discord.ui.TextInput(
            label="Extra message text (optional)",max_length=500,required=False,
            style=discord.TextStyle.paragraph,
            default=row["intro"] if row else "")
        self.enabled=discord.ui.TextInput(
            label="Enabled? yes or no",max_length=3,
            default="yes" if row is None or row["enabled"] else "no")
        for field in (self.heading,self.intro,self.enabled):
            self.add_item(field)

    async def on_submit(self,interaction):
        if not allowed(interaction) or interaction.user.id!=self.owner:
            return await interaction.response.send_message("Not authorised.",ephemeral=True)
        status=self.enabled.value.lower().strip()
        if status not in ("yes","no"):
            return await interaction.response.send_message(
                "Enabled must be yes or no.",ephemeral=True)
        try:
            core.check_channel(interaction.guild,self.channel_id)
            if not self.heading.value.strip():
                raise ValueError("An announcement title is required.")
        except ValueError as exc:
            return await interaction.response.send_message(str(exc),ephemeral=True)
        await interaction.response.send_message(
            f'**Confirm automation settings**\nEvent: {core.AUTOMATION_KINDS[self.kind][0]}\n'
            f'Channel: <#{self.channel_id}>\n'
            f'Enabled: {status}\n\n'
            'No message will be sent until its event happens.',
            embed=core.embed(discord.utils.escape_mentions(self.heading.value),
                             discord.utils.escape_mentions(self.intro.value) or
                             "(Event-specific information is added automatically.)"),
            view=ConfirmAutomation(self.owner,self.kind,self.channel_id,
                                  self.heading.value,self.intro.value,status=="yes"),
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


class ConfirmAutomation(Private):
    def __init__(self,owner,kind,channel,title,intro,enabled):
        super().__init__(owner)
        self.kind,self.channel,self.title,self.intro,self.enabled=kind,channel,title,intro,enabled
        self.used=False

    @discord.ui.button(label="Save Automation",emoji="✅",style=discord.ButtonStyle.success)
    async def save(self,interaction,button):
        if self.used:
            return await interaction.response.send_message("Already processed.",ephemeral=True)
        self.used=True
        try:
            core.check_channel(interaction.guild,self.channel)
            core.save_automation(self.kind,self.channel,self.title,self.intro,
                                 self.enabled,interaction.user.id)
        except ValueError as exc:
            return await interaction.response.edit_message(
                content="⚠️ "+str(exc),view=None,embed=None)
        api.admin_hub.audit(interaction.user.id,"scheduler_automation_saved",
                            f'{self.kind}, channel {self.channel}, enabled {self.enabled}')
        await interaction.response.edit_message(
            content="✅ Automation saved.\n\n"+automation_detail(self.kind),
            view=None,embed=None,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Cancel",style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button):
        self.used=True
        await interaction.response.edit_message(
            content="No changes made.",view=None,embed=None)


class AutomationActions(Private):
    def __init__(self,owner,kind):
        super().__init__(owner)
        self.kind=kind

    @discord.ui.button(label="Edit Content",emoji="✏️",style=discord.ButtonStyle.primary)
    async def edit(self,interaction,button):
        policy=core.automation_policy(self.kind)
        if not policy["channel_id"]:
            return await interaction.response.send_message(
                "Choose a channel first.",view=AutomationChannel(self.owner,self.kind),
                ephemeral=True)
        await interaction.response.send_modal(
            AutomationForm(self.owner,self.kind,policy["channel_id"]))

    @discord.ui.button(label="Change Channel",emoji="📍",style=discord.ButtonStyle.secondary)
    async def channel(self,interaction,button):
        await interaction.response.send_message(
            "Choose a new destination channel.",
            view=AutomationChannel(self.owner,self.kind),ephemeral=True)

    @discord.ui.button(label="Pause / Resume",emoji="⏯️",style=discord.ButtonStyle.secondary)
    async def toggle(self,interaction,button):
        existing=core.automation(self.kind)
        if existing is None:
            return await interaction.response.send_message(
                "This is a built-in default. Save it as a custom automation first.",
                view=AutomationChannel(self.owner,self.kind),ephemeral=True)
        new_state=not bool(existing["enabled"])
        core.set_automation_enabled(self.kind,new_state,interaction.user.id)
        api.admin_hub.audit(interaction.user.id,"scheduler_automation_toggle",
                            f'{self.kind} enabled={new_state}')
        await interaction.response.edit_message(
            content=automation_detail(self.kind),view=self,
            allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Delete Custom Rule",emoji="🗑️",style=discord.ButtonStyle.danger)
    async def delete(self,interaction,button):
        if core.automation(self.kind) is None:
            return await interaction.response.send_message(
                "This uses GuildKeeper's built-in default and cannot be deleted. "
                "Create a custom rule to pause it.",ephemeral=True)
        await interaction.response.send_message(
            "Delete the custom automation? The original built-in default will return. "
            "Use Pause if you want to stop the event instead.",
            view=ConfirmDeleteAutomation(self.owner,self.kind),ephemeral=True)


class ConfirmDeleteAutomation(Private):
    def __init__(self,owner,kind):
        super().__init__(owner)
        self.kind=kind

    @discord.ui.button(label="Confirm Delete",emoji="🗑️",style=discord.ButtonStyle.danger)
    async def confirm(self,interaction,button):
        core.delete_automation(self.kind)
        api.admin_hub.audit(interaction.user.id,"scheduler_automation_deleted",self.kind)
        await interaction.response.edit_message(
            content="Custom rule deleted. The event now follows GuildKeeper defaults.",
            view=None)


async def post_panel(channel):
    """Post once or edit a saved message in place, never expose panel in public."""
    if not isinstance(channel,discord.TextChannel) or channel.guild.id!=api.GUILD_ID:
        return False,"Choose a server text channel."
    if channel.permissions_for(channel.guild.default_role).view_channel:
        return False,"Use a staff-only channel. @everyone must not be able to view it."
    me=channel.guild.me
    if me is None:
        return False,"Cannot inspect GuildKeeper permissions."
    perms=channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links and
            perms.read_message_history):
        return False,"GuildKeeper needs View Channel, Send Messages, Embed Links and Message History."
    key=KEY_PREFIX+str(api.GUILD_ID)
    with api.connect() as db:
        row=db.execute("SELECT value FROM v12_config WHERE key=?",(key,)).fetchone()
    old=None
    old_channel=None
    if row:
        try:
            cid,mid=(int(x) for x in row["value"].split(":"))
            old_channel=channel.guild.get_channel(cid)
            if isinstance(old_channel,discord.TextChannel):
                old=await old_channel.fetch_message(mid)
        except (ValueError,discord.HTTPException):
            pass
    try:
        if old is not None and old_channel.id==channel.id:
            msg=old
            await msg.edit(embed=dashboard_embed(),view=SchedulerDashboard())
        else:
            msg=await channel.send(embed=dashboard_embed(),
                                   view=SchedulerDashboard(),
                                   allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        return False,"Could not post the scheduler dashboard."
    with api.connect() as db:
        db.execute("INSERT OR REPLACE INTO v12_config(key,value) VALUES (?,?)",
                   (key,f"{channel.id}:{msg.id}"))
    if old is not None and old_channel.id!=channel.id:
        try:
            await old.delete()
        except discord.HTTPException:
            pass
    return True,"Permanent Message Scheduler ready in "+channel.mention


def install(module):
    global api
    api=module

    @api.bot.tree.command(name="admin_scheduler_post",
                          description="Create or move the staff-only Message Scheduler dashboard")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_scheduler_post(interaction:discord.Interaction,channel:discord.TextChannel):
        if not allowed(interaction):
            return await interaction.response.send_message("Manage Server required.",ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ok,message=await post_panel(channel)
        await interaction.followup.send(("✅ " if ok else "⚠️ ")+message,ephemeral=True)
