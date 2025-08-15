import os
import json
import typing
import discord
from discord import app_commands
from discord.ext import commands

# ---- Keep-alive tiny web server (for Railway) ----
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def _run_web():
    port = int(os.environ.get("PORT", 8080))
    # host=0.0.0.0 so Railway exposes it
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=_run_web, daemon=True)
    t.start()

# ---- Config via environment variables ----
TOKEN =  "MTM5MzY2NDEzNTc0MDI2NDU4MQ.GZ68tX.GlEL2b0gUxhdJJXIr2cV5BDJvoQswlWR_W4NLo # required"
GUILD_ID = int(os.getenv("1313621783152295978") or 0)  # required
STAFF_ROLE_ID = int(os.getenv("1313665982560079912") or 0)  # optional
MODMAIL_CATEGORY_NAME = os.getenv("STAFF") or "Modmail"
LOG_CHANNEL_ID = int(os.getenv("1393664902408704000") or 0)  # optional

if not TOKEN or not GUILD_ID:
    raise RuntimeError("Missing DISCORD_TOKEN and/or GUILD_ID env vars.")

# ---- Intents ----
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

TICKETS_FILE = "tickets.json"
tickets: dict[str, int] = {}  # user_id -> channel_id

# ---- Persistence ----
def load_tickets():
    global tickets
    try:
        with open(TICKETS_FILE, "r", encoding="utf-8") as f:
            tickets = json.load(f)
    except FileNotFoundError:
        tickets = {}
    except Exception as e:
        print("Failed to load tickets:", e)
        tickets = {}

def save_tickets():
    try:
        with open(TICKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(tickets, f)
    except Exception as e:
        print("Failed to save tickets:", e)

# ---- Helpers ----
async def get_or_create_category(guild: discord.Guild) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=MODMAIL_CATEGORY_NAME)
    if category:
        return category
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            read_message_history=True, attach_files=True
        ),
    }
    if STAFF_ROLE_ID:
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True
            )
    return await guild.create_category(MODMAIL_CATEGORY_NAME, overwrites=overwrites)

async def get_or_create_ticket_channel(user: discord.User) -> discord.TextChannel:
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        raise RuntimeError("Bot is not in the target guild.")

    # Existing?
    channel_id = tickets.get(str(user.id))
    if channel_id:
        ch = guild.get_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            return ch

    category = await get_or_create_category(guild)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            read_message_history=True, attach_files=True
        ),
    }
    if STAFF_ROLE_ID:
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True
            )

    safe_name = f"ticket-{user.name}".lower().replace(" ", "-")
    channel_name = f"{safe_name[:64]}-{str(user.id)[-4:]}"
    channel = await guild.create_text_channel(
        name=channel_name[:90], category=category, overwrites=overwrites,
        topic=f"Modmail for {user} ({user.id})"
    )

    tickets[str(user.id)] = channel.id
    save_tickets()

    await channel.send(f"📬 **New ticket opened by `{user}` (`{user.id}`)**")
    try:
        await user.send("Thanks for your message! A moderator will reply here shortly. ✅")
    except discord.Forbidden:
        pass

    return channel

async def log(guild: discord.Guild, text: str):
    if LOG_CHANNEL_ID:
        ch = guild.get_channel(LOG_CHANNEL_ID)
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(text)
            except discord.Forbidden:
                pass

def find_user_id_by_channel_id(channel_id: int) -> typing.Optional[int]:
    for uid, cid in tickets.items():
        if cid == channel_id:
            return int(uid)
    return None

# ---- Events ----
@bot.event
async def on_ready():
    load_tickets()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await tree.sync(guild=discord.Object(id=GUILD_ID))
        print("Slash commands synced to guild.")
    except Exception as e:
        print("Slash sync failed:", e)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        try:
            channel = await get_or_create_ticket_channel(message.author)
            content = message.content or ""
            files = [await a.to_file() for a in message.attachments] if message.attachments else []
            if files:
                await channel.send(f"**From {message.author}:** {content}", files=files)
            else:
                await channel.send(f"**From {message.author}:** {content}")
        except Exception as e:
            print("Error handling DM:", e)
            try:
                await message.channel.send("Sorry, I couldn't forward your message. Please try again later.")
            except discord.Forbidden:
                pass
        return

    await bot.process_commands(message)

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    if isinstance(channel, discord.TextChannel):
        uid = find_user_id_by_channel_id(channel.id)
        if uid:
            tickets.pop(str(uid), None)
            save_tickets()

# ---- Slash commands (guild-scoped) ----
@tree.command(name="reply", description="Reply to the user in this modmail channel.", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message="What to say to the user", attachment="Optional attachment to include")
async def reply_cmd(interaction: discord.Interaction, message: str, attachment: typing.Optional[discord.Attachment] = None):
    if STAFF_ROLE_ID:
        has_role = any(r.id == STAFF_ROLE_ID for r in getattr(interaction.user, "roles", []))
        if not has_role:
            return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or (channel.category and channel.category.name != MODMAIL_CATEGORY_NAME):
        return await interaction.response.send_message("Use this inside a modmail ticket channel.", ephemeral=True)

    user_id = find_user_id_by_channel_id(channel.id)
    if not user_id:
        return await interaction.response.send_message("This channel is not linked to a user.", ephemeral=True)

    user = await bot.fetch_user(user_id)
    files = [await attachment.to_file()] if attachment else None

    try:
        if files:
            await user.send(f"**Staff:** {message}", files=files)
        else:
            await user.send(f"**Staff:** {message}")
    except discord.Forbidden:
        return await interaction.response.send_message("Couldn't DM the user (their DMs may be closed).", ephemeral=True)

    if files:
        await channel.send(f"**To {user}:** {message}", files=files)
    else:
        await channel.send(f"**To {user}:** {message}")

    await interaction.response.send_message("Sent. ✅", ephemeral=True)

@tree.command(name="close", description="Close this modmail ticket.", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(reason="Optional reason to include in the closing DM")
async def close_cmd(interaction: discord.Interaction, reason: typing.Optional[str] = None):
    if STAFF_ROLE_ID:
        has_role = any(r.id == STAFF_ROLE_ID for r in getattr(interaction.user, "roles", []))
        if not has_role:
            return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or (channel.category and channel.category.name != MODMAIL_CATEGORY_NAME):
        return await interaction.response.send_message("Use this inside a modmail ticket channel.", ephemeral=True)

    user_id = find_user_id_by_channel_id(channel.id)
    if not user_id:
        return await interaction.response.send_message("This channel is not linked to a user.", ephemeral=True)

    user = await bot.fetch_user(user_id)
    closing_text = "This ticket has been closed."
    if reason:
        closing_text += f" Reason: {reason}"

    try:
        await user.send(f"🔒 {closing_text}")
    except discord.Forbidden:
        pass

    tickets.pop(str(user_id), None)
    save_tickets()

    await interaction.response.send_message("Closing ticket… 🔒", ephemeral=True)
    try:
        await channel.delete(reason=f"Modmail closed by {interaction.user} ({reason or 'no reason'})")
    except discord.Forbidden:
        await interaction.followup.send("I couldn't delete the channel (missing permission).", ephemeral=True)

# ---- Start ----
if __name__ == "__main__":
    keep_alive()   # start tiny web server for Railway/UptimeRobot
    bot.run(TOKEN)
