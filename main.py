import discord
from discord.ext import commands
import subprocess
import asyncio

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Constants
SERVER_LIMIT = 5 
AUTHORIZED_ROLE_IDS = [000000000000000000, 000000000000000000]
database_file = "servers.txt"
TOKEN = "YOUR_TOKEN"

# Helper functions
def count_user_servers(user):
    """Count the number of servers for a user."""
    try:
        with open(database_file, 'r') as f:
            return sum(1 for line in f if line.startswith(user))
    except FileNotFoundError:
        return 0

def add_to_database(user, container_name, ssh_command):
    """Add server details to the database."""
    with open(database_file, 'a') as f:
        f.write(f"{user}|{container_name}|{ssh_command}\n")
        
def list_servers():
    """Return a list of servers from the database file."""
    try:
        with open(database_file, 'r') as f:
            return f.readlines()
    except FileNotFoundError:
        return ["No servers found."]
async def capture_ssh_command(process):
    """Capture the SSH session command from tmate output."""
    max_retries = 30  # Wait up to 30 seconds
    ssh_command = None  # To store the relevant SSH command
    for _ in range(max_retries):
        line = await process.stdout.readline()
        if line:
            decoded_line = line.decode().strip()
            print(f"tmate output: {decoded_line}")  # Log for debugging

            # Look for the SSH session command, excluding read-only sessions
            if "ssh " in decoded_line and "ro-" not in decoded_line:
                ssh_command = decoded_line

            # If we have the SSH command, break the loop early
            if ssh_command:
                break

        await asyncio.sleep(1)  # Wait before checking again

    return ssh_command

async def deploy_server(ctx, target_user, ram, cores):
    """Deploy a new server instance and send details to the specified user."""
    user_id = str(target_user.id)

    # Check if the user has reached the server limit
    if count_user_servers(user_id) >= SERVER_LIMIT:
        await ctx.send(embed=discord.Embed(
            description="```Error: Instance Limit Reached```", color=0xff0000))
        return

    image = "ghcr.io/ma4z-spec/hydren-vm:latest"
    try:
        container_id = subprocess.check_output([
            "udocker", "run", "-itd", "--privileged", "--cap-add=ALL",
            "--memory", ram, "--cpus", str(cores), image
        ]).strip().decode('utf-8')
    except subprocess.CalledProcessError as e:
        await ctx.send(embed=discord.Embed(
            description=f"Error creating Docker container: {e}", color=0xff0000))
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "udocker", "exec", container_id, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        await ctx.send(embed=discord.Embed(
            description=f"Error executing tmate in Docker container: {e}", color=0xff0000))
        subprocess.run(["udocker", "kill", container_id])
        subprocess.run(["udocker", "rm", container_id])
        return

    ssh_session_line = await capture_ssh_command(exec_cmd)
    if ssh_session_line:
        # Send SSH details to the specified user
        try:
            await target_user.send(embed=discord.Embed(
                description=f"### Successfully created Instance\n"
                            f"SSH Session Command: ```{ssh_session_line}```\nOS: Ubuntu 22.04",
                color=0x00ff00))
            add_to_database(user_id, container_id, ssh_session_line)
            await ctx.send(embed=discord.Embed(
                description=f"Instance created successfully. SSH details have been sent to {target_user.mention}.", 
                color=0x00ff00))
        except discord.Forbidden:
            await ctx.send(embed=discord.Embed(
                description=f"Unable to DM {target_user.mention}. Please ensure they have DMs enabled.", 
                color=0xff0000))
    else:
        await ctx.send(embed=discord.Embed(
            description="Instance creation failed or timed out. Please try again later.", color=0xff0000))
        subprocess.run(["udocker", "kill", container_id])
        subprocess.run(["udocker", "rm", container_id])

@bot.command(name="deploy")
async def deploy(ctx, userid: int, ram: str, cores: int):
    """Deploy a new server instance."""
    if any(role.id in AUTHORIZED_ROLE_IDS for role in ctx.author.roles):
        target_user = await bot.fetch_user(userid)
        if target_user:
            await ctx.send(embed=discord.Embed(
                description="Creating Instance. This may take a few seconds.", color=0x00ff00))
            await deploy_server(ctx, target_user, ram, cores)
        else:
            await ctx.send(embed=discord.Embed(
                description=f"User with ID {userid} not found.", color=0xff0000))
    else:
        await ctx.send(embed=discord.Embed(
            description="You don't have permission to deploy instances.", color=0xff0000))

@bot.command(name="ressh")
async def ressh(ctx, container_id: str, userid: int):
    """Restart the specified container and capture the SSH command, then DM it to the specified user."""
    try:
        # Check if the container is running
        status = subprocess.check_output(
            ["udocker", "inspect", "--format='{{.State.Running}}'", container_id]
        ).strip().decode('utf-8')
        if status == "'false'":  # If the container is stopped
            subprocess.run(["udocker", "kill", container_id])
            subprocess.run(["udocker", "rm", container_id])
        # Start the container
        subprocess.run(["udocker", "start", container_id])
        exec_cmd = await asyncio.create_subprocess_exec(
            "udocker", "exec", container_id, "tmate", "-F",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        ssh_session_line = await capture_ssh_command(exec_cmd)
        
        if ssh_session_line:
            # Attempt to fetch the target user
            target_user = await bot.fetch_user(userid)
            if target_user:
                try:
                    await target_user.send(embed=discord.Embed(
                        description=f"SSH Session Command: ```{ssh_session_line}```",
                        color=0x00ff00))
                    await ctx.send(embed=discord.Embed(
                        description=f"SSH details have been sent to {target_user.mention}.",
                        color=0x00ff00))
                except discord.Forbidden:
                    await ctx.send(embed=discord.Embed(
                        description=f"Unable to DM {target_user.mention}. Please ensure they have DMs enabled.",
                        color=0xff0000))
            else:
                await ctx.send(embed=discord.Embed(
                    description=f"User with ID {userid} not found.",
                    color=0xff0000))
        else:
            await ctx.send(embed=discord.Embed(
                description="Failed to capture SSH session command.",
                color=0xff0000))
    except subprocess.CalledProcessError as e:
        await ctx.send(embed=discord.Embed(
            description=f"Error: {e}",
            color=0xff0000))
@bot.command(name="list")
async def list_servers(ctx):
    """List all servers by sending the contents of servers.txt via DM to authorized users only."""
    # Check if the user has any of the authorized roles
    if not any(role.id in AUTHORIZED_ROLE_IDS for role in ctx.author.roles):
        await ctx.send(embed=discord.Embed(
            description="You do not have permission to use this command.",
            color=0xff0000))
        return
    # Prepare to read from servers.txt
    try:
        with open(database_file, 'r') as f:
            server_details = f.readlines()  # Read all lines from the file
    except FileNotFoundError:
        # If the file does not exist, inform the user
        await ctx.send(embed=discord.Embed(
            description="No server data found.",
            color=0xff0000))
        return
    # Prepare the message content
    if server_details:
        message_content = "```\n" + "".join(line.strip() for line in server_details) + "\n```"
    else:
        message_content = "No server data available."
    # Attempt to DM the user
    try:
        await ctx.author.send(embed=discord.Embed(
            description="Here are the server details:\n" + message_content,
            color=0x00ff00))
        await ctx.send(embed=discord.Embed(
            description="I've sent you a DM with the server details.",
            color=0x00ff00))
    except discord.Forbidden:
        await ctx.send(embed=discord.Embed(
            description="I can't DM you. Please enable DMs from server members.",
            color=0xff0000))
@bot.event
async def on_ready():
    """Set bot status."""
    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Game(name="with VPS v4")
    )
    print(f"Logged in as {bot.user}")
bot.run(TOKEN)
