"""Private Admin Hub screens for one-off and recurring GuildKeeper announcements."""
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
import announcements_scheduler as core

api = None
TZ = ZoneInfo("Europe/London")


def allowed(interaction, owner):
    return api.admin_hub.allowed(interaction) and interaction.user.id == owner


class Private(discord.ui.View):
    def __init__(self, owner):
        super().__init__(timeout=300)
        self.owner = owner

    async def interaction_check(self, interaction):
        if not allowed(interaction, self.owner):
            await interaction.response.send_message("This staff menu belongs to another moderator.", ephemeral=True)
            return False
        return True


def when_label(row):
    dt = datetime.fromisoformat(row["next_run_utc"]).astimezone(TZ)
    return dt.strftime("%d %b %Y, %H:%M UK")


def summary(row):
    ping = "GuildBlast ping" if row["ping_guildblast"] else "no ping"
    return (f'**#{row["id"]} — {discord.utils.escape_markdown(row["title"])}**\n'
            f'<#{row["channel_id"]}> · {row["recurrence"].title()} · '
            f'{when_label(row)} · {ping} · **{row["status"]}**')


def listing():
    rows = core.upcoming()
    if not rows:
        return "📆 **Scheduled Announcements**\nNo scheduled announcements yet."
    active = next((r for r in rows if r["status"] == "active"), None)
    lines = ["📆 **Scheduled Announcements**",
             "**Next post:** "+when_label(active) + f' — {active["title"]}' if active else
             "**Next post:** None scheduled", ""]
    for row in rows:
        lines.append(summary(row))
    lines.append("\nChoose an announcement below to preview, edit, cancel or delete it.")
    return "\n\n".join(lines)[:3900]


class ChannelChoice(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="Choose the announcement channel",
                         channel_types=[discord.ChannelType.text])

    async def callback(self, interaction):
        if not allowed(interaction, self.view.owner):
            return await interaction.response.send_message("Not authorised.", ephemeral=True)
        channel = self.values[0]
        try:
            core.check_channel(interaction.guild, channel.id)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.send_message(
            f"📣 Channel selected: <#{channel.id}>. Choose a schedule:",
            view=FrequencyMenu(self.view.owner, channel.id), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())


class ChooseChannel(Private):
    def __init__(self, owner):
        super().__init__(owner)
        self.add_item(ChannelChoice())


class FrequencyChoice(discord.ui.Select):
    def __init__(self, existing=None):
        super().__init__(placeholder="How often should the message post?",
                         options=[
                             discord.SelectOption(label="One-off",value="once"),
                             discord.SelectOption(label="Every day",value="daily"),
                             discord.SelectOption(label="Every week",value="weekly"),
                             discord.SelectOption(label="Every month",value="monthly"),
                         ])
        self.existing = existing

    async def callback(self, interaction):
        if not allowed(interaction, self.view.owner):
            return await interaction.response.send_message("Not authorised.", ephemeral=True)
        await interaction.response.send_modal(
            ScheduleForm(self.view.owner, self.view.channel_id, self.values[0],
                         existing=self.existing))


class FrequencyMenu(Private):
    def __init__(self, owner, channel_id, existing=None):
        super().__init__(owner)
        self.channel_id = channel_id
        self.add_item(FrequencyChoice(existing))


class ScheduleForm(discord.ui.Modal):
    def __init__(self, owner, channel_id, recurrence, existing=None):
        super().__init__(title="Schedule Guild Announcement", timeout=300)
        self.owner, self.channel_id, self.recurrence, self.existing = owner, channel_id, recurrence, existing
        previous = core.get(existing) if existing is not None else None
        old_when = when_label(previous) if previous else None
        default_time = (datetime.fromisoformat(previous["next_run_utc"])
                        .astimezone(TZ).strftime(core.FORMAT) if previous else None)
        self.heading = discord.ui.TextInput(label="Title",max_length=100,
                                            default=previous["title"] if previous else None)
        self.message = discord.ui.TextInput(label="Message",max_length=1500,
                                            style=discord.TextStyle.paragraph,
                                            default=previous["body"] if previous else None)
        self.when = discord.ui.TextInput(label="First/next post (YYYY-MM-DD HH:MM UK)",
                                         max_length=16,placeholder="2026-12-01 18:30",default=default_time)
        self.image = discord.ui.TextInput(label="Image HTTPS URL (optional)",required=False,
                                          max_length=500,default=previous["image_url"] if previous else None)
        self.ping = discord.ui.TextInput(label="Mention GuildBlast? yes or no",max_length=3,
                                         default="yes" if previous and previous["ping_guildblast"] else "no")
        for field in (self.heading,self.message,self.when,self.image,self.ping):
            self.add_item(field)

    async def on_submit(self, interaction):
        if not allowed(interaction, self.owner):
            return await interaction.response.send_message("Not authorised.", ephemeral=True)
        ping = self.ping.value.strip().lower()
        if ping not in ("yes","no"):
            return await interaction.response.send_message("GuildBlast option must be yes or no.",ephemeral=True)
        try:
            data=core.build_data(
                interaction.guild,self.channel_id,self.heading.value,
                self.message.value,self.when.value,self.recurrence,
                self.image.value,ping=="yes")
        except ValueError as exc:
            return await interaction.response.send_message("⚠️ "+str(exc),ephemeral=True)
        next_at=datetime.fromisoformat(data["next_run_utc"]).astimezone(TZ)
        await interaction.response.send_message(
            f'📣 **Review scheduled message**\n'
            f'Channel: <#{self.channel_id}>\n'
            f'First post: **{next_at.strftime("%d %b %Y %H:%M")} UK**\n'
            f'Recurrence: **{self.recurrence}**\n'
            f'GuildBlast mention: **{"Yes" if ping=="yes" else "No"}**\n'
            + ("Image included\n" if data["image_url"] else "")
            + '\nConfirm to save. Nothing is posted immediately.',
            embed=core.embed(data["title"],data["body"],data["image_url"]),
            view=ConfirmSchedule(self.owner,data,self.existing),
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


class ConfirmSchedule(Private):
    def __init__(self,owner,data,existing=None):
        super().__init__(owner)
        self.data,self.existing,self.used=data,existing,False

    @discord.ui.button(label="Confirm Schedule",emoji="✅",style=discord.ButtonStyle.success)
    async def confirm(self,interaction,button):
        if self.used:
            return await interaction.response.send_message("Already processed.",ephemeral=True)
        self.used=True
        try:
            core.check_channel(interaction.guild,self.data["channel_id"],
                               bool(self.data["ping_guildblast"]))
            if self.existing is None:
                job_id=core.create(self.data,interaction.user.id)
                operation="create"
            else:
                core.update(self.existing,self.data)
                job_id=self.existing
                operation="edit"
            api.admin_hub.audit(interaction.user.id,"announcement_schedule_"+operation,
                                f'Job #{job_id} · {self.data["title"]} · {self.data["next_run_utc"]}')
        except ValueError as exc:
            return await interaction.response.edit_message(
                content="⚠️ "+str(exc),embed=None,view=None)
        await interaction.response.edit_message(
            content=f'✅ Announcement **#{job_id}** saved. It will post at its scheduled UK time.',
            embed=None,view=None)

    @discord.ui.button(label="Cancel",emoji="✖️",style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button):
        self.used=True
        await interaction.response.edit_message(content="Schedule discarded.",embed=None,view=None)


class AnnouncementSelect(discord.ui.Select):
    def __init__(self):
        rows=core.upcoming()
        super().__init__(placeholder="Select an announcement to manage",
                         options=[discord.SelectOption(
                             label=f'#{r["id"]} {r["title"][:70]}',
                             description=(r["recurrence"]+" · "+r["status"])[:100],
                             value=str(r["id"])) for r in rows[:25]])

    async def callback(self,interaction):
        if not allowed(interaction,self.view.owner):
            return await interaction.response.send_message("Not authorised.",ephemeral=True)
        job_id=int(self.values[0])
        row=core.get(job_id)
        if row is None:
            return await interaction.response.send_message("Job has been deleted.",ephemeral=True)
        await interaction.response.send_message(
            summary(row)+"\n\nSelect an action below.",
            view=JobMenu(self.view.owner,job_id),ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())


class UpcomingMenu(Private):
    def __init__(self,owner):
        super().__init__(owner)
        if core.upcoming():
            self.add_item(AnnouncementSelect())

    @discord.ui.button(label="Refresh List",emoji="🔄",style=discord.ButtonStyle.secondary,row=1)
    async def refresh(self,interaction,button):
        await interaction.response.edit_message(content=listing(),view=UpcomingMenu(self.owner),
                                                allowed_mentions=discord.AllowedMentions.none())


class JobMenu(Private):
    def __init__(self,owner,job_id):
        super().__init__(owner)
        self.job_id=job_id

    @discord.ui.button(label="Preview",emoji="👁️",style=discord.ButtonStyle.secondary)
    async def preview(self,interaction,button):
        row=core.get(self.job_id)
        if row is None:
            return await interaction.response.send_message("Job not found.",ephemeral=True)
        await interaction.response.send_message(
            summary(row),embed=core.embed(row["title"],row["body"],row["image_url"]),
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Edit",emoji="✏️",style=discord.ButtonStyle.primary)
    async def edit(self,interaction,button):
        row=core.get(self.job_id)
        if row is None or row["status"]=="sending":
            return await interaction.response.send_message("Job unavailable while sending.",ephemeral=True)
        await interaction.response.send_message(
            "Choose the schedule type (you can also change it). Edit the contents, time, image and GuildBlast option.",
            view=FrequencyMenu(self.owner,row["channel_id"],existing=self.job_id),
            ephemeral=True)

    @discord.ui.button(label="Cancel",emoji="⏸️",style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button):
        await interaction.response.send_message(
            f'Cancel future posting for announcement #{self.job_id}?',
            view=ConfirmChange(self.owner,self.job_id,"cancelled"),ephemeral=True)

    @discord.ui.button(label="Delete",emoji="🗑️",style=discord.ButtonStyle.danger)
    async def delete(self,interaction,button):
        await interaction.response.send_message(
            f'Delete announcement #{self.job_id} from the scheduler? Existing Discord posts are kept.',
            view=ConfirmChange(self.owner,self.job_id,"deleted"),ephemeral=True)


class ConfirmChange(Private):
    def __init__(self,owner,job_id,operation):
        super().__init__(owner)
        self.job_id,self.operation,self.used=job_id,operation,False

    @discord.ui.button(label="Confirm",emoji="✅",style=discord.ButtonStyle.danger)
    async def confirm(self,interaction,button):
        if self.used:
            return await interaction.response.send_message("Already processed.",ephemeral=True)
        self.used=True
        try:
            core.change_status(self.job_id,self.operation)
            api.admin_hub.audit(interaction.user.id,"announcement_"+self.operation,
                                f'Scheduled announcement #{self.job_id}')
        except ValueError as exc:
            return await interaction.response.edit_message(content="⚠️ "+str(exc),view=None)
        await interaction.response.edit_message(
            content=f'✅ Announcement #{self.job_id} {self.operation}. Existing Discord posts are unchanged.',
            view=None)

    @discord.ui.button(label="Keep",emoji="✖️",style=discord.ButtonStyle.secondary)
    async def keep(self,interaction,button):
        self.used=True
        await interaction.response.edit_message(content="No change made.",view=None)


def automatic_summary():
    return ('📢 **Automatic notices**\n\n'
            '**Weekly Side Quest refresh** — posted once each week after the new quest board '
            'has been published, in the configured announcements channel.\n\n'
            '**Monthly leaderboard recap** — posted on the first day of each UK month, '
            'showing the top XP gained during the previous month. '
            '**Lifetime XP and ranks never reset.**\n\n'
            'Automatic notices never ping @everyone or GuildBlast. '
            'Failed/uncertain sends do not blindly retry; they are visible in Health Check.\n\n'
            f'**Scheduled message issues:** {core.failed_count()}')
