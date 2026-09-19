from discord.ext import tasks
from clan_checker import check_members, get_clan_members, check_clan_membership

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

import os

from database import (
    setup_database,
    add_user,
    mark_removed,
    get_verified_users,
    get_user_by_discord_id,
    get_active_user_by_ign,
    delete_user_by_ign,
    delete_user_by_discord_id,
    pop_migration_report,
    DuplicateIgnError
)


load_dotenv()


TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = int(
    os.getenv("GUILD_ID")
)

LOG_CHANNEL_ID = int(
    os.getenv("LOG_CHANNEL_ID")
)

VERIFIED_CHANNEL_ID = int(
    os.getenv("VERIFIED_CHANNEL_ID")
)

WELCOME_CHANNEL_ID = int(
    os.getenv("WELCOME_CHANNEL_ID")
)

MEMBER_ROLE_NAME = "HCV"

ENABLE_KICK = (
    os.getenv(
        "ENABLE_KICK",
        "false"
    ).lower() == "true"
)


intents = discord.Intents.default()

intents.members = True
intents.message_content = True


class ClanGuard(commands.Bot):

    async def setup_hook(self):

        await setup_database()

        guild = discord.Object(
            id=GUILD_ID
        )

        # Copy the (currently global) commands into guild scope
        # BEFORE wiping the global ones, so they aren't lost.
        self.tree.copy_global_to(
            guild=guild
        )

        # Now clear the old global registrations so they stop
        # showing up as duplicates (run once, then this list stays empty).
        self.tree.clear_commands(
            guild=None
        )

        await self.tree.sync()  # pushes the empty global list -> deletes old globals

        synced = await self.tree.sync(
            guild=guild
        )  # pushes the guild-scoped copies -> instant, no dupes

        print(
            f"Synced {len(synced)} guild commands"
        )


bot = ClanGuard(
    command_prefix="!",
    intents=intents
)


processed_players = set()



@tasks.loop(seconds=10)
async def clan_check():

    try:

        players = await check_members()


    except Exception as e:

        print(
            f"Checker error: {e}"
        )

        return


    if not players:

        return


    guild = bot.get_guild(
        GUILD_ID
    )


    if guild is None:

        print(
            "Guild not found"
        )

        return


    log_channel = bot.get_channel(
        LOG_CHANNEL_ID
    )


    for player in players:

        discord_id = player["discord_id"]

        member = guild.get_member(
            discord_id
        )


        print(
            f"Detected removal candidate: "
            f"{player['ign']}"
        )


        # Dry-run mode must not change the database or kick anyone.
        if not ENABLE_KICK:

            if discord_id in processed_players:

                continue


            processed_players.add(
                discord_id
            )


            print(
                "Kick disabled. Dry-run mode."
            )


            if log_channel:

                if member is not None:

                    discord_target = member.mention

                else:

                    discord_target = f"<@{discord_id}> (already left the server)"


                await log_channel.send(
                    f"🧪 **Dry Run Detection**\n\n"
                    f"Player: `{player['ign']}`\n"
                    f"Discord: {discord_target}\n\n"
                    f"Kick disabled."
                )


            continue


        # The clan API is the source of truth. Mark the verification
        # as removed even when the Discord member has already left.
        await mark_removed(
            discord_id
        )


        print(
            f"Marked verification removed: "
            f"{player['ign']}"
        )


        # If the user already left Discord, there is nothing to kick.
        if member is None:

            print(
                f"Discord member {discord_id} is no longer in the server."
            )

            if log_channel:

                await log_channel.send(
                    f"🚪 **Automatic Clan Removal**\n\n"
                    f"Player: `{player['ign']}`\n"
                    f"Discord ID: `{discord_id}`\n\n"
                    f"The user had already left the Discord server. "
                    f"Their verification record was marked as removed."
                )

            continue


        if member.id == guild.owner_id:

            print(
                "Skipped server owner."
            )

            continue


        if not guild.me.guild_permissions.kick_members:

            print(
                "Bot missing Kick Members permission."
            )

            continue


        try:

            await member.kick(
                reason=
                "No longer in Hidden Cloud Village"
            )


            print(
                f"Kicked {member}"
            )


            if log_channel:

                await log_channel.send(
                    f"🚪 **Automatic Clan Removal**\n\n"
                    f"Player: `{player['ign']}`\n"
                    f"Discord: {member.mention}\n\n"
                    f"Reason: No longer in Hidden Cloud Village"
                )


        except Exception as e:

            print(
                f"Kick failed: {e}"
            )




class AccessServerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Access Server", emoji="🚪", style=discord.ButtonStyle.green, custom_id="access_server_button")
    async def access_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only verified (active) members may unlock the server.
        record = await get_user_by_discord_id(interaction.user.id)
        if record is None or record[3]:  # record[3] == removed
            await interaction.response.send_message(
                "\u274c You need to complete `/verify` first.",
                ephemeral=True
            )
            return
        role = discord.utils.get(interaction.guild.roles, name=MEMBER_ROLE_NAME)
        if role is None:
            await interaction.response.send_message("❌ Member role not found.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message("✅ You already have access.", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role)
            # Send welcome message to #verified-members
            verified_channel = interaction.guild.get_channel(
                   VERIFIED_CHANNEL_ID
            )

            if verified_channel:

                user = await get_user_by_discord_id(
                    interaction.user.id
                )

                ign = user[1] if user else "Unknown"

                embed = discord.Embed(
                    title="🎉 New Clan Member!",
                    description=(
                        f"{interaction.user.mention} has joined **Hidden Cloud Village**!\n\n"
                        f"🥷 **IGN:** `{ign}`\n\n"
                        "Everyone give them a warm welcome! 🎊"
                    ),
                    color=discord.Color.gold()
                )

                await verified_channel.send(embed=embed)

                
            await interaction.response.send_message("🎉 Welcome! You now have access to the server.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to assign roles.", ephemeral=True)


@bot.event
async def on_ready():

    bot.add_view(AccessServerView())

    print(
        f"Logged in as {bot.user}"
    )

    print(
        "Clan Guard online"
    )


    print(
        f"Kick mode: {ENABLE_KICK}"
    )


    report = pop_migration_report()

    if report:

        log_channel = bot.get_channel(
            LOG_CHANNEL_ID
        )

        if log_channel:

            text = (
                "\U0001f4e6 **Database migrated to IGN-based records**\n\n"
                f"Old records: `{report['total_old']}`\n"
                f"Active after migration: `{report['active']}`\n"
                f"Backup file: `{report['backup']}`\n"
            )

            if report["duplicates"]:

                text += (
                    "\n\u26a0\ufe0f **Duplicate game names deactivated** "
                    "(same name was linked to more than one Discord "
                    "account; one was kept):\n"
                )

                for d in report["duplicates"]:

                    text += (
                        f"- `{d['ign']}`: <@{d['discord_id']}> deactivated, "
                        f"<@{d['kept_discord_id']}> kept\n"
                    )

                text += (
                    "\nUse `/modifyverify` (with `force`) if the wrong "
                    "account was kept."
                )

            await log_channel.send(
                text[:1990],
                allowed_mentions=discord.AllowedMentions.none()
            )

    if not clan_check.is_running():

        clan_check.start()

@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(
    name="setupwelcome",
    description="Create and pin the welcome verification message."
)
async def setupwelcome(interaction: discord.Interaction):

    # Prevent duplicate welcome messages
    pinned_messages = await interaction.channel.pins()

    for message in pinned_messages:
        if (
            message.author == bot.user
            and message.embeds
            and message.embeds[0].title == "👋 Welcome to Hidden Cloud Village!"
        ):
            await interaction.response.send_message(
                "✅ A welcome message is already pinned in this channel.",
                ephemeral=True
            )
            return

    embed = discord.Embed(
        title="👋 Welcome to Hidden Cloud Village!",
        description=(
            "Welcome to Hidden Cloud Village!\n\n"

            "Before you can access the server, please complete verification.\n\n"

            "**Step 1️⃣**\n"
            "Use the `/verify` command.\n\n"

            "**Step 2️⃣**\n"
            "Enter your Ninja Saga **IGN** exactly as it appears in-game.\n\n"

            "**Step 3️⃣**\n"
            "If verification succeeds, click the **🚪 Access Server** button to unlock the rest of the server.\n\n"

            "Need help? Contact a moderator."
        ),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text="Hidden Cloud Village Verification System"
    )

    message = await interaction.channel.send(
        embed=embed
    )

    await message.pin()

    await interaction.response.send_message(
        "✅ Welcome message created and pinned successfully.",
        ephemeral=True
    )



@bot.tree.command(
    name="verify",
    description="Link your Discord account with your Ninja Saga character name"
)
@app_commands.describe(
    ign="Your Ninja Saga character name (IGN), exactly as shown in-game"
)
async def verify(
    interaction: discord.Interaction,
    ign: str
):

    # Talking to the clan API can take a moment, so acknowledge
    # the interaction immediately to avoid a 3s timeout.
    await interaction.response.defer(ephemeral=True)

    # Ensure /verify is only used in the welcome channel
    if interaction.channel_id != WELCOME_CHANNEL_ID:
        await interaction.followup.send(
            f"⚠️ Please use `/verify` in <#{WELCOME_CHANNEL_ID}>.",
            ephemeral=True
        )
        return

    ign = ign.strip()

    if not ign:
        await interaction.followup.send(
            "❌ Please enter your Ninja Saga character name."
        )
        return

    # 1) This Discord account already has an active link?
    #    (one Discord account = one game name)
    existing = await get_user_by_discord_id(
        interaction.user.id
    )

    if existing and not existing[3]:  # existing[3] == removed

        await interaction.followup.send(
            f"⚠️ Your Discord account is already linked to the "
            f"Ninja Saga character `{existing[1]}`.\n\n"
            f"If this needs to change, please ask an admin to update it "
            f"with `/modifyverify`."
        )

        return


    # 2) The name must really be a member of Hidden Cloud Village.
    #    Nothing is accepted without this check.
    result = await check_clan_membership(
        ign
    )

    if result["status"] == "error":

        await interaction.followup.send(
            "⚠️ Couldn't verify against the clan list right now. "
            "Please try again in a moment."
        )

        return


    if result["status"] == "not_found":

        await interaction.followup.send(
            f"❌ `{ign}` was not found in Hidden Cloud Village's "
            f"member list.\n\n"
            f"Make sure you're in the clan and that you typed your "
            f"character name exactly as it appears in-game "
            f"(copy and paste works best)."
        )

        return


    if result["status"] == "ambiguous":

        candidates = ", ".join(
            f"`{name}`" for name in result["candidates"]
        )

        await interaction.followup.send(
            f"⚠️ More than one clan member matches `{ign}`: "
            f"{candidates}\n\n"
            f"Please run `/verify` again with your exact name."
        )

        return


    # result["status"] == "ok"  ->  use the game's exact spelling
    verified_name = result["name"]


    # 3) Is this game name already claimed by a *different* Discord account?
    conflict = await get_active_user_by_ign(
        verified_name
    )

    if conflict and conflict[0] != interaction.user.id:

        await interaction.followup.send(
            f"❌ The character `{verified_name}` is already linked to "
            f"another Discord account.\n\n"
            f"If this is a mistake, please contact an admin."
        )

        return


    try:

        await add_user(
            interaction.user.id,
            verified_name
        )

    except DuplicateIgnError:

        # Someone else claimed it between the check above and the save.
        await interaction.followup.send(
            f"❌ The character `{verified_name}` is already linked to "
            f"another Discord account.\n\n"
            f"If this is a mistake, please contact an admin."
        )

        return


    embed = discord.Embed(
        title="✅ Verification Successful",
        description=(
            f"Welcome, {interaction.user.mention}!\n\n"
            f"**IGN:** `{verified_name}`\n\n"
            "Click the **Access Server** button below to unlock the server."
        ),
        color=discord.Color.green()
    )

    await interaction.followup.send(
        embed=embed,
        view=AccessServerView(),
        ephemeral=True
    )






@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(
    name="clancheck",
    description="Check Hidden Cloud Village API members"
)
async def clancheck(
    interaction: discord.Interaction
):

    await interaction.response.defer(
        ephemeral=True
    )

    members = await get_clan_members()

    if not members:

        await interaction.followup.send(
            "⚠️ Couldn't read the Hidden Cloud Village member list "
            "from the API.",
            ephemeral=True
        )

        return

    # Keep the message under Discord's 2000 character limit.
    shown = []

    length = 0

    for name in members:

        entry = f"`{name}`"

        if length + len(entry) + 2 > 1500:
            break

        shown.append(entry)

        length += len(entry) + 2

    remaining = len(members) - len(shown)

    text = ", ".join(shown)

    if remaining > 0:
        text += f" … and {remaining} more"

    await interaction.followup.send(
        f"🔎 **Hidden Cloud Village API Check**\n\n"
        f"Members found: `{len(members)}`\n\n"
        f"{text}",
        ephemeral=True
    )






@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(
    name="verified",
    description="Show all verified Hidden Cloud Village members"
)
async def verified(
    interaction: discord.Interaction
):

    users = await get_verified_users()

    if not users:
        await interaction.response.send_message(
            "No verified players found."
        )
        return

    # Discord allows a maximum of 25 fields per embed.
    # Split the verified members into pages of 25.
    chunks = [
        users[i:i + 25]
        for i in range(0, len(users), 25)
    ]

    embeds = []

    for page_number, chunk in enumerate(chunks, start=1):

        embed = discord.Embed(
            title="🥷 Verified Clan Members",
            color=discord.Color.blue()
        )

        if len(chunks) > 1:
            embed.description = (
                f"Page **{page_number}/{len(chunks)}**\n"
                f"Total verified members: **{len(users)}**"
            )

        for discord_id, ign in chunk:

            member = interaction.guild.get_member(
                discord_id
            )

            if member:
                discord_name = member.display_name
            else:
                discord_name = "Unknown"

            embed.add_field(
                name=ign,
                value=f"Discord: {discord_name}",
                inline=False
            )

        embeds.append(embed)

    await interaction.response.send_message(
        embed=embeds[0]
    )

    for embed in embeds[1:]:
        await interaction.followup.send(
            embed=embed
        )


@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(
    name="modifyverify",
    description="Admin: change (or create) a user's verified IGN"
)
@app_commands.describe(
    member="The Discord member whose verification to modify",
    new_ign="The new Ninja Saga character name",
    force="Take the name over if another Discord account already has it (default: off)",
    skip_clan_check="Don't check the name against the clan member list (default: off)"
)
async def modifyverify(
    interaction: discord.Interaction,
    member: discord.Member,
    new_ign: str,
    force: bool = False,
    skip_clan_check: bool = False
):

    await interaction.response.defer(
        ephemeral=True
    )

    current = await get_user_by_discord_id(
        member.id
    )

    old_ign = current[1] if current else None

    new_ign = new_ign.strip()

    if not new_ign:

        await interaction.followup.send(
            "❌ `new_ign` cannot be empty.",
            ephemeral=True
        )

        return


    resolved_ign = new_ign

    if not skip_clan_check:

        result = await check_clan_membership(
            new_ign
        )

        if result["status"] == "error":

            await interaction.followup.send(
                "⚠️ Couldn't read the clan member list right now. "
                "Try again in a moment, or use `skip_clan_check: True`.",
                ephemeral=True
            )

            return

        if result["status"] == "not_found":

            await interaction.followup.send(
                f"❌ `{new_ign}` is not in Hidden Cloud Village's member "
                f"list. Use `skip_clan_check: True` to override.",
                ephemeral=True
            )

            return

        if result["status"] == "ambiguous":

            candidates = ", ".join(
                f"`{name}`" for name in result["candidates"]
            )

            await interaction.followup.send(
                f"⚠️ More than one clan member matches `{new_ign}`: "
                f"{candidates}\n\nPlease use the exact name.",
                ephemeral=True
            )

            return

        resolved_ign = result["name"]


    if not force:

        conflict = await get_active_user_by_ign(
            resolved_ign
        )

        if conflict and conflict[0] != member.id:

            await interaction.followup.send(
                f"❌ `{resolved_ign}` is already linked to another "
                f"Discord account (<@{conflict[0]}>).\n\n"
                f"If this is intentional, re-run the command with "
                f"`force: True`.",
                ephemeral=True
            )

            return


    try:

        await add_user(
            member.id,
            resolved_ign,
            force=force
        )

    except DuplicateIgnError as e:

        await interaction.followup.send(
            f"❌ `{resolved_ign}` is already linked to another "
            f"Discord account (<@{e.holder_discord_id}>).",
            ephemeral=True
        )

        return


    await interaction.followup.send(
        f"✅ Updated verification for {member.mention}\n\n"
        f"IGN: `{old_ign}` → `{resolved_ign}`",
        ephemeral=True
    )




@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(
    name="delete",
    description="Admin: permanently delete a verification record by IGN or Discord member"
)
@app_commands.describe(
    ign="The character name of the record to delete",
    member="Or: the Discord member whose record to delete"
)
async def delete(
    interaction: discord.Interaction,
    ign: str = None,
    member: discord.Member = None
):

    if not ign and member is None:

        await interaction.response.send_message(
            "❌ Provide either `ign` or `member`.",
            ephemeral=True
        )

        return


    if member is not None:

        row = await delete_user_by_discord_id(
            member.id
        )

        deleted_rows = [row] if row else []

    else:

        deleted_rows = await delete_user_by_ign(
            ign.strip()
        )


    if not deleted_rows:

        target = ign if member is None else member.mention

        await interaction.response.send_message(
            f"❌ No verification record was found for {target}.",
            ephemeral=True
        )

        return


    lines = []

    for discord_id, stored_ign, missing_checks, removed in deleted_rows:

        lines.append(
            f"IGN: `{stored_ign}` | Discord ID: `{discord_id}` | "
            f"Previous status: `{'Removed' if removed else 'Active'}`"
        )


    await interaction.response.send_message(
        "🗑️ **Verification record deleted successfully.**\n\n"
        + "\n".join(lines),
        ephemeral=True
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ You do not have permission to use this command.",
            ephemeral=True
        )

        return

    raise error


bot.run(TOKEN)
