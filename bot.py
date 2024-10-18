import discord
from discord.ext import commands, tasks
import sqlite3
import os
import asyncio
from datetime import datetime, timedelta
import pytz

# Замените 'YOUR_BOT_TOKEN' на токен вашего бота
TOKEN = 
DELO_CHANNEL_ID = 1296452024900259890
OBRASHENIE_CHANNEL_ID = 1296222080823590912
STATISTICS_CHANNEL_ID = 1289980889241092147
NOTIFICATION_CHANNEL_ID = 1289981506042728610
GEN_PROKUROR_ROLE_ID = 1296222557774549172
ROLE_NAME = "Офис Генерального Прокурора"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

statistics_message = None

if not os.path.exists('database.db'):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS assignments")
    c.execute('''CREATE TABLE assignments (
                    user_id INTEGER, case_id INTEGER, channel_id INTEGER, type TEXT, 
                    start_time TEXT, end_time TEXT, submit_time TEXT)''')
    c.execute('''CREATE TABLE completed_assignments (
                    user_id INTEGER, case_id INTEGER, type TEXT, submit_time TEXT)''')
    conn.commit()
    conn.close()

moscow_tz = pytz.timezone('Europe/Moscow')

@bot.event
async def on_ready():
    print(f'Мы вошли как {bot.user}')
    send_statistics.start()
    check_deadlines.start()

@tasks.loop(seconds=10)
async def send_statistics():
    global statistics_message
    channel = bot.get_channel(STATISTICS_CHANNEL_ID)
    if channel is None:
        print("Канал для статистики не найден.")
        return
    role = discord.utils.get(channel.guild.roles, name=ROLE_NAME)
    if role is None:
        print("Роль не найдена.")
        return

    members_with_role = [member for member in role.members if member.guild == channel.guild]

    position_hierarchy = {
        "Генеральный Прокурор": 1,
        "Заместитель Генерального Прокурора": 2,
        "Помощник Генерального Прокурора": 3,
        "Прокурор": 4,
        "Помощник Прокурора": 5 }

    embed = discord.Embed(title="Статистика ОФИСА ГЕНЕРАЛЬНОГО ПРОКУРОРА", color=0x00ff00)
    for member in members_with_role:
        active_cases = 0
        active_requests = 0

        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM assignments WHERE user_id = ? AND type = 'case'", (member.id,))
        active_cases = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM assignments WHERE user_id = ? AND type = 'request'", (member.id,))
        active_requests = c.fetchone()[0]
        conn.close()

        priority_role = None
        for role in member.roles:
            if role.name in position_hierarchy:
                if priority_role is None or position_hierarchy[role.name] < position_hierarchy[priority_role.name]:
                    priority_role = role
                    position = priority_role.name if priority_role else "Нет роли"
        embed.add_field(name=f"{position} | {member.display_name}", value=f"Активные дела: {active_cases} | Активные обращения: {active_requests}", inline=False)

    if statistics_message is None:
        statistics_message = await channel.send(embed=embed)
    else:
        await statistics_message.edit(embed=embed)

@tasks.loop(minutes=1)
async def check_deadlines():
    channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
    if channel is None:
        print("Канал для уведомлений не найден.")
        return

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    now = datetime.now(moscow_tz)
    c.execute("SELECT user_id, case_id, type, end_time FROM assignments")
    assignments = c.fetchall()

    for user_id, case_id, assignment_type, end_time in assignments:
        end_time = datetime.fromisoformat(end_time).astimezone(moscow_tz)
        if now >= end_time:
            role = channel.guild.get_role(GEN_PROKUROR_ROLE_ID)
            if role:
                await channel.send(f"{role.mention}, дело/обращение <#{case_id}> не было сдано в срок!")

            c.execute("DELETE FROM assignments WHERE case_id = ?", (case_id,))
            conn.commit()

    conn.close()

@bot.command()
@commands.has_role(ROLE_NAME)
async def delo(ctx, member: discord.Member):
    if ctx.channel.id != DELO_CHANNEL_ID and ctx.channel.id not in [b.id for b in ctx.channel.parent.threads]:
        return await ctx.send("(( Эта команда может использоваться только в канале дел или его ветках. ))")

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM assignments WHERE case_id = ? AND type = 'case'", (ctx.channel.id,))
    existing_assignment = c.fetchone()

    if existing_assignment:
        if existing_assignment[0] != member.id:
            c.execute("DELETE FROM assignments WHERE case_id = ? AND type = 'case'", (ctx.channel.id,))
            conn.commit()
            await ctx.send(f"{existing_assignment[0]} потерял дело.")
        else:
            return await ctx.send("Этот пользователь уже назначен на дело.")

    start_time = datetime.now(moscow_tz)
    end_time = start_time + timedelta(hours=72)
    c.execute("INSERT INTO assignments (user_id, case_id, channel_id, type, start_time, end_time, submit_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (member.id, ctx.channel.id, ctx.channel.id, 'case', start_time.isoformat(), end_time.isoformat(), None))
    conn.commit()
    conn.close()

    await ctx.send(f"{member.mention} назначен на дело! Дедлайн: {end_time.strftime('%Y-%m-%d %H:%M:%S')} MSK.")
    await ctx.message.delete()

    button1 = discord.ui.Button(label="Сдать", style=discord.ButtonStyle.green)
    button2 = discord.ui.Button(label="Продлить", style=discord.ButtonStyle.gray)

    async def button1_callback(interaction):
        await interaction.response.send_message("Пожалуйста, отправьте ссылку на дело.")

        def check(m):
            return m.author == member and m.channel == interaction.channel

        try:
            msg = await bot.wait_for('message', check=check, timeout=60.0)
            link = msg.content
            conn = sqlite3.connect('database.db')
            c = conn.cursor()
            c.execute("SELECT case_id FROM assignments WHERE user_id = ? AND type = 'case'", (member.id,))
            case = c.fetchone()
            if case:
                case_id = case[0]
                submit_time = datetime.now(moscow_tz).isoformat()
                c.execute("INSERT INTO completed_assignments (user_id, case_id, type, submit_time) VALUES (?, ?, 'case', ?)",
                          (member.id, case_id, submit_time))
                c.execute("DELETE FROM assignments WHERE case_id = ? AND user_id = ? AND type = 'case'", (case_id, member.id))
                conn.commit()
                await interaction.followup.send(f"{member.mention}, дело <#{case_id}> сдано! Ссылка: {link}")
            else:
                await interaction.followup.send(f"{member.mention}, у вас нет активных дел для сдачи.")
            conn.close()
        except asyncio.TimeoutError:
            await interaction.followup.send("Время ожидания истекло. Пожалуйста, попробуйте снова.")

        await interaction.message.edit(view=None)

    async def button2_callback(interaction):
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT end_time FROM assignments WHERE user_id = ? AND type = 'case'", (member.id,))
        case = c.fetchone()
        if case:
            end_time = datetime.fromisoformat(case[0]).astimezone(moscow_tz)
            new_end_time = end_time + timedelta(hours=72)
            c.execute("UPDATE assignments SET end_time = ? WHERE user_id = ? AND type = 'case'", (new_end_time.isoformat(), member.id))
            conn.commit()
            await interaction.response.send_message(f"(( Дедлайн дела продлен на 72 часа! Новый дедлайн: {new_end_time.strftime('%Y-%m-%d %H:%M:%S')} MSK. Обратите внимание, что для продления самого дела у суда вам также необходимо подать ходатайство, шаблон ищите в важной инфе. Продление у бота - чтобы вас не наказали старшие! ))")
        else:
            await interaction.response.send_message(f"{member.mention}, у вас нет активных дел для продления.")
        conn.close()

    button1.callback = button1_callback
    button2.callback = button2_callback

    view = discord.ui.View()
    view.add_item(button1)
    view.add_item(button2)

    await ctx.send("Выберите действие:", view=view)

@bot.command()
@commands.has_role(ROLE_NAME)
async def obr(ctx, member: discord.Member):
    if ctx.channel.id != OBRASHENIE_CHANNEL_ID and ctx.channel.id not in [b.id for b in ctx.channel.parent.threads]:
        return await ctx.send("(( Эта команда может использоваться только в канале обращений или его ветках. ))")

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM assignments WHERE case_id = ? AND type = 'request'", (ctx.channel.id,))
    existing_assignment = c.fetchone()

    if existing_assignment:
        if existing_assignment[0] != member.id:
            c.execute("DELETE FROM assignments WHERE case_id = ? AND type = 'request'", (ctx.channel.id,))
            conn.commit()
            await ctx.send(f"{existing_assignment[0]} потерял обращение.")
        else:
            return await ctx.send("Этот пользователь уже назначен на обращение.")

    start_time = datetime.now(moscow_tz)
    end_time = start_time + timedelta(hours=72)
    c.execute("INSERT INTO assignments (user_id, case_id, channel_id, type, start_time, end_time, submit_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (member.id, ctx.channel.id, ctx.channel.id, 'request', start_time.isoformat(), end_time.isoformat(), None))
    conn.commit()
    conn.close()

    await ctx.send(f"{member.mention} назначен на обращение! Дедлайн: {end_time.strftime('%Y-%m-%d %H:%M:%S')} MSK.")
    await ctx.message.delete()

    button1 = discord.ui.Button(label="Сдать", style=discord.ButtonStyle.green)
    button2 = discord.ui.Button(label="Продлить", style=discord.ButtonStyle.gray)

    async def button1_callback(interaction):
        await interaction.response.send_message("Пожалуйста, отправьте ссылку на обращение.")

        def check(m):
            return m.author == member and m.channel == interaction.channel

        try:
            msg = await bot.wait_for('message', check=check, timeout=60.0)
            link = msg.content
            conn = sqlite3.connect('database.db')
            c = conn.cursor()
            c.execute("SELECT case_id FROM assignments WHERE user_id = ? AND type = 'request'", (member.id,))
            request = c.fetchone()
            if request:
                case_id = request[0]
                submit_time = datetime.now(moscow_tz).isoformat()
                c.execute("INSERT INTO completed_assignments (user_id, case_id, type, submit_time) VALUES (?, ?, 'request', ?)",
                          (member.id, case_id, submit_time))
                c.execute("DELETE FROM assignments WHERE case_id = ? AND user_id = ? AND type = 'request'", (case_id, member.id))
                conn.commit()
                await interaction.followup.send(f"{member.mention}, обращение <#{case_id}> сдано! Ссылка: {link}")
            else:
                await interaction.followup.send(f"{member.mention}, у вас нет активных обращений для сдачи.")
            conn.close()
        except asyncio.TimeoutError:
            await interaction.followup.send("Время ожидания истекло. Пожалуйста, попробуйте снова.")

        await interaction.message.edit(view=None)

    async def button2_callback(interaction):
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT end_time FROM assignments WHERE user_id = ? AND type = 'request'", (member.id,))
        request = c.fetchone()
        if request:
            end_time = datetime.fromisoformat(request[0]).astimezone(moscow_tz)
            new_end_time = end_time + timedelta(hours=72)
            c.execute("UPDATE assignments SET end_time = ? WHERE user_id = ? AND type = 'request'", (new_end_time.isoformat(), member.id))
            conn.commit()
            await interaction.response.send_message(f"((Дедлайн обращения продлен на 72 часа! Новый дедлайн: {new_end_time.strftime('%Y-%m-%d %H:%M:%S')} MSK.))")
        else:
            await interaction.response.send_message(f"(({member.mention}, у вас нет активных обращений для продления.))")
        conn.close()

    button1.callback = button1_callback
    button2.callback = button2_callback

    view = discord.ui.View()
    view.add_item(button1)
    view.add_item(button2)

    await ctx.send("Выберите действие:", view=view)

@bot.command()
@commands.has_role(ROLE_NAME)
async def stats(ctx, member: discord.Member):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    c.execute("SELECT case_id, start_time, end_time FROM assignments WHERE user_id = ? AND type = 'case'", (member.id,))
    active_cases = c.fetchall()

    c.execute("SELECT case_id, start_time, end_time FROM assignments WHERE user_id = ? AND type = 'request'", (member.id,))
    active_requests = c.fetchall()

    conn.close()

    embed = discord.Embed(title=f"Статистика для {member.name}", color=0x00ff00)
    embed.add_field(name="Активные дела", value="\n".join([f"<#{case[0]}> (Начато: {datetime.fromisoformat(case[1]).astimezone(moscow_tz).strftime('%Y-%m-%d %H:%M:%S')} MSK, Дедлайн: {datetime.fromisoformat(case[2]).astimezone(moscow_tz).strftime('%Y-%m-%d %H:%M:%S')} MSK)" for case in active_cases]) if active_cases else "Нет активных дел.", inline=False)
    embed.add_field(name="Активные обращения", value="\n".join([f"<#{request[0]}> (Начато: {datetime.fromisoformat(request[1]).astimezone(moscow_tz).strftime('%Y-%m-%d %H:%M:%S')} MSK, Дедлайн: {datetime.fromisoformat(request[2]).astimezone(moscow_tz).strftime('%Y-%m-%d %H:%M:%S')} MSK)" for request in active_requests]) if active_requests else "Нет активных обращений.", inline=False)

    user_dm_channel = await member.create_dm()
    message = await user_dm_channel.send(embed=embed)

    await asyncio.sleep(60)
    await message.delete()

@bot.event
async def on_member_update(before, after):
    if ROLE_NAME not in [role.name for role in before.roles] and ROLE_NAME in [role.name for role in after.roles]:
        return

    if ROLE_NAME in [role.name for role in before.roles] and ROLE_NAME not in [role.name for role in after.roles]:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        c.execute("SELECT case_id, type FROM assignments WHERE user_id = ?", (before.id,))
        assignments = c.fetchall()

        if assignments:
            channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
            role = after.guild.get_role(GEN_PROKUROR_ROLE_ID)
            if channel and role:
                for case_id, assignment_type in assignments:
                    await channel.send(f"{role.mention}, {before.mention} потерял роль Офиса Генерального Прокурора и имеет активное {assignment_type}: <#{case_id}>.")

            c.execute("DELETE FROM assignments WHERE user_id = ?", (before.id,))
            conn.commit()

        conn.close()

bot.run(TOKEN)
