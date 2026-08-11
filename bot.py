import discord
from discord import app_commands
from discord.ext import commands
import wavelink
import asyncio
import os
import json
import time
import random
from keep_alive import keep_alive

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# 你的伺服器 ID
MY_GUILD_ID = discord.Object(id=1431588910554812540)

# 儲存結構
music_queues = {}
last_played_url = {}
loop_status = {}  # 0: 關閉, 1: 單曲循環, 2: 佇列循環
music_history = {}
current_volumes = {}
current_filters = {}
is_247_mode = {}
vote_skips = {}
PLAYLIST_FILE = "playlists.json"

# --- 歌單管理函式 ---
def load_playlists():
    if os.path.exists(PLAYLIST_FILE):
        with open(PLAYLIST_FILE, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_playlists(data):
    with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

@bot.event
async def on_ready():
    bot.tree.copy_global_to(guild=MY_GUILD_ID)
    await bot.tree.sync(guild=MY_GUILD_ID)
    print(f'機器人已上線: {bot.user.name}')

    # --- 連線到 Lavalink 節點 ---
    # 請將這裡的 URI 與密碼替換為你實際架設的 Lavalink 伺服器資訊
    nodes = [wavelink.Node(uri='http://你的Lavalink伺服器IP:2333', password='你的Lavalink密碼')]
    await wavelink.Pool.connect(nodes=nodes, client=bot)

@bot.event
async def on_wavelink_node_ready(node: wavelink.Node):
    print(f"Lavalink 節點 [{node.identifier}] 已成功連線！")

# --- 互動式進階面板與選單元件 ---
class ChapterSelect(discord.ui.Select):
    def __init__(self, chapters, vc: wavelink.Player):
        self.vc = vc
        options = [discord.SelectOption(label=c.get('title', f'章節 {i+1}')[:100], value=str(c.get('time', 0))) for i, c in enumerate(chapters[:25])]
        super().__init__(placeholder="🎵 即時切換章節...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        seek_sec = float(self.values[0])
        await self.vc.seek(int(seek_sec * 1000))  # Wavelink 接受毫秒
        await interaction.response.send_message(f"⏩ 已跳轉至章節秒數: {int(seek_sec)}秒", ephemeral=True)

class FilterSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="正常音質 (無濾鏡)", value="normal", emoji="🎶"),
            discord.SelectOption(label="重低音 (Bassboost)", value="bassboost", emoji="🔊"),
            discord.SelectOption(label="夜核加速 (Nightcore)", value="nightcore", emoji="⚡"),
            discord.SelectOption(label="等化器人聲強化 (EQ)", value="eq_boost", emoji="🎚️")
        ]
        super().__init__(placeholder="🎛️ 切換音效濾鏡...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc or not vc.playing:
            await interaction.response.send_message("目前沒有音樂正在播放！", ephemeral=True)
            return

        mode = self.values[0]
        filters: wavelink.Filters = vc.filters
        
        if mode == "normal":
            filters.reset()
            await filters.update()
            await interaction.response.send_message("🎛️ 已重設為正常音質", ephemeral=True)
        elif mode == "bassboost":
            filters.equalizer.set(bands=[{"band": 0, "gain": 0.3}, {"band": 1, "gain": 0.3}])
            await filters.update()
            await interaction.response.send_message("🎛️ 已套用 **重低音** 濾鏡！", ephemeral=True)
        elif mode == "nightcore":
            filters.timescale.set(speed=1.25, pitch=1.25)
            await filters.update()
            await interaction.response.send_message("🎛️ 已套用 **夜核加速** 濾鏡！", ephemeral=True)
        elif mode == "eq_boost":
            filters.equalizer.set(bands=[{"band": 2, "gain": 0.25}, {"band": 3, "gain": 0.25}])
            await filters.update()
            await interaction.response.send_message("🎛️ 已套用 **EQ等化器** 濾鏡！", ephemeral=True)

class VolumeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="音量: 20%", value="20", emoji="🔈"),
            discord.SelectOption(label="音量: 50% (預設)", value="50", emoji="🔉"),
            discord.SelectOption(label="音量: 80%", value="80", emoji="🔊"),
            discord.SelectOption(label="音量: 100% (最大)", value="100", emoji="📢")
        ]
        super().__init__(placeholder="🔊 調整播放音量...", options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("機器人未連線！", ephemeral=True)
            return
        vol = int(self.values[0])
        await vc.set_volume(vol)
        await interaction.response.send_message(f"🔊 音量已即時調整為: **{vol}%**", ephemeral=True)

class MusicControlView(discord.ui.View):
    def __init__(self, chapters=None, vc: wavelink.Player = None, url=None):
        super().__init__(timeout=None)
        self.url = url
        if chapters and vc: 
            self.add_item(ChapterSelect(chapters, vc))
        self.add_item(FilterSelect())
        self.add_item(VolumeSelect())

    @discord.ui.button(label="⏮️ 上一首", style=discord.ButtonStyle.secondary, row=3)
    async def prev(self, i: discord.Interaction, b: discord.ui.Button):
        hist = music_history.get(i.guild.id, [])
        if hist: 
            prev_url = hist.pop()
            vc: wavelink.Player = i.guild.voice_client
            tracks = await wavelink.Playable.search(prev_url)
            if tracks:
                await vc.play(tracks[0])
                await i.response.send_message("⏮️ 切換回上一首", ephemeral=True)
        else: 
            await i.response.send_message("無歷史記錄", ephemeral=True)

    @discord.ui.button(label="⏯️ 暫停/繼續", style=discord.ButtonStyle.primary, row=3)
    async def pr(self, i: discord.Interaction, b: discord.ui.Button):
        vc: wavelink.Player = i.guild.voice_client
        if vc and vc.playing:
            await vc.pause(not vc.paused)
            await i.response.send_message("播放狀態已切換", ephemeral=True)
        else:
            await i.response.send_message("目前沒有音樂在播放", ephemeral=True)

    @discord.ui.button(label="⏭️ 下一首", style=discord.ButtonStyle.secondary, row=3)
    async def next(self, i: discord.Interaction, b: discord.ui.Button):
        vc: wavelink.Player = i.guild.voice_client
        if vc and vc.playing:
            await vc.skip()
            await i.response.send_message("⏭️ 已跳過", ephemeral=True)
        else:
            await i.response.send_message("目前沒有音樂在播放", ephemeral=True)

    @discord.ui.button(label="⏹️ 停止", style=discord.ButtonStyle.danger, row=3)
    async def stop(self, i: discord.Interaction, b: discord.ui.Button):
        vc: wavelink.Player = i.guild.voice_client
        if vc:
            await vc.disconnect()
            await i.response.send_message("⏹️ 機器人已離開", ephemeral=True)
        else:
            await i.response.send_message("機器人不在語音頻道中", ephemeral=True)

    @discord.ui.button(label="💖 收藏", style=discord.ButtonStyle.success, row=4)
    async def bookmark(self, i: discord.Interaction, b: discord.ui.Button):
        if not self.url:
            await i.response.send_message("目前沒有可收藏的網址！", ephemeral=True)
            return
        playlists = load_playlists()
        user_id = str(i.user.id)
        default_name = "我的最愛"

        if user_id not in playlists: playlists[user_id] = {}
        if default_name not in playlists[user_id]: playlists[user_id][default_name] = []

        if self.url not in playlists[user_id][default_name]:
            playlists[user_id][default_name].append(self.url)
            save_playlists(playlists)
            await i.response.send_message(f"💖 成功將此首歌加入你的「{default_name}」歌單！", ephemeral=True)
        else:
            await i.response.send_message("這首歌已經在你的收藏歌單裡囉！", ephemeral=True)

    @discord.ui.button(label="🔄 重新整理進度", style=discord.ButtonStyle.secondary, row=4)
    async def refresh_np(self, i: discord.Interaction, b: discord.ui.Button):
        vc: wavelink.Player = i.guild.voice_client
        if not vc or not vc.playing:
            await i.response.send_message("目前沒有音樂正在播放！", ephemeral=True)
            return

        pos = vc.position // 1000
        dur = vc.current.duration // 1000 if vc.current and vc.current.duration else 0
        if not dur:
            await i.response.send_message(f"⏱️ 目前已播放: {pos//60}:{pos%60:02}", ephemeral=True)
            return

        filled = int(20 * pos / dur)
        bar = "▬" * filled + "●" * (1 if filled < 20 else 0) + "▬" * (19 - filled if filled < 20 else 0)
        await i.response.send_message(f"📊 **即時進度更新：**\n`[{bar}]` {pos//60}:{pos%60:02} / {dur//60}:{dur%60:02}", ephemeral=True)

# --- 核心播放邏輯 ---
async def play_track_with_embed(interaction: discord.Interaction, query: str):
    if not interaction.guild.voice_client:
        if interaction.user.voice:
            vc: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
        else:
            await interaction.followup.send("請先進入語音頻道！", ephemeral=True)
            return
    else:
        vc: wavelink.Player = interaction.guild.voice_client

    tracks = await wavelink.Playable.search(query)
    if not tracks:
        await interaction.followup.send("找不到相關搜尋結果！", ephemeral=True)
        return

    track = tracks[0]
    last_played_url[interaction.guild.id] = track.uri

    if vc.playing:
        vc.queue.put(track)
        await interaction.followup.send(f"🎶 已加入佇列：**{track.title}**", ephemeral=True)
    else:
        await vc.play(track)
        
        # 建立控制面板 Embed
        chapters = getattr(track, 'chapters', None)
        view = MusicControlView(chapters=chapters, vc=vc, url=track.uri)
        
        dur = track.duration // 1000 if track.duration else 0
        embed = discord.Embed(
            title="🎶 正在播放音樂",
            description=f"**[{track.title}]({track.uri})**",
            color=discord.Color.blurple()
        )
        embed.add_field(name="⏱️ 總長度", value=f"{dur//60:02}:{dur%60:02}" if dur else "未知", inline=True)
        embed.add_field(name="🔊 目前音量", value=f"{vc.volume}%", inline=True)
        if hasattr(track, 'artwork') and track.artwork:
            embed.set_thumbnail(url=track.artwork)
        embed.set_footer(text="使用下方按鈕與選單隨時控制播放狀態")

        await interaction.followup.send(embed=embed, view=view)

# --- 斜線指令群組 ---
@bot.tree.command(name="play", description="透過 Lavalink 播放音樂 (支援網址或關鍵字)")
@app_commands.describe(query="YouTube 網址或關鍵字")
async def slash_play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    await play_track_with_embed(interaction, query)

@bot.tree.command(name="nowplaying", description="查看當前播放進度條")
async def slash_np(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc or not vc.playing:
        await interaction.response.send_message("目前沒有音樂正在播放！", ephemeral=True)
        return

    pos = vc.position // 1000
    dur = vc.current.duration // 1000 if vc.current and vc.current.duration else 0
    if not dur:
        await interaction.response.send_message(f"🎵 **{vc.current.title}**\n⏱️ 已播放時間: {pos//60}:{pos%60:02}")
        return

    filled = int(20 * pos / dur)
    bar = "▬" * filled + "●" * (1 if filled < 20 else 0) + "▬" * (19 - filled if filled < 20 else 0)
    await interaction.response.send_message(f"🎵 **{vc.current.title}**\n`[{bar}]` {pos//60}:{pos%60:02} / {dur//60}:{dur%60:02}")

@bot.tree.command(name="queue", description="查看目前的音樂佇列")
async def slash_queue(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc or vc.queue.is_empty:
        await interaction.response.send_message("目前佇列是空的！", ephemeral=True)
        return

    msg = "🎶 **目前音樂佇列：**\n"
    for idx, t in enumerate(list(vc.queue)[:10], 1):
        msg += f"{idx}. [{t.title}]({t.uri})\n"
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="skip", description="跳過當前歌曲")
async def slash_skip(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc or not vc.playing:
        await interaction.response.send_message("目前沒有音樂正在播放！", ephemeral=True)
        return
    await vc.skip()
    await interaction.response.send_message("⏭️ 已跳過當前歌曲！")

@bot.tree.command(name="loop", description="切換循環播放模式")
@app_commands.choices(mode=[
    app_commands.Choice(name="關閉循環", value=0),
    app_commands.Choice(name="單曲循環", value=1),
    app_commands.Choice(name="佇列循環", value=2)
])
async def slash_loop(interaction: discord.Interaction, mode: int):
    vc: wavelink.Player = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("機器人未連線！", ephemeral=True)
        return
    if mode == 1:
        vc.queue.mode = wavelink.QueueMode.loop
    elif mode == 2:
        vc.queue.mode = wavelink.QueueMode.normal  # Wavelink 內建循環支援
    else:
        vc.queue.mode = wavelink.QueueMode.normal
    await interaction.response.send_message(f"🔄 循環模式已更新！")

@bot.tree.command(name="playlist_add", description="將歌曲加入自訂歌單")
async def slash_playlist_add(interaction: discord.Interaction, name: str, url: str):
    playlists = load_playlists()
    user_id = str(interaction.user.id)
    if user_id not in playlists: playlists[user_id] = {}
    if name not in playlists[user_id]: playlists[user_id][name] = []
    playlists[user_id][name].append(url)
    save_playlists(playlists)
    await interaction.response.send_message(f"✅ 已成功將歌曲加入歌單 **{name}**！", ephemeral=True)

@bot.tree.command(name="playlist_play", description="播放指定自訂歌單")
async def slash_playlist_play(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)
    playlists = load_playlists()
    user_id = str(interaction.user.id)
    if user_id not in playlists or name not in playlists[user_id]:
        await interaction.followup.send(f"找不到歌單 **{name}**！", ephemeral=True)
        return
    songs = playlists[user_id][name]
    for s in songs:
        tracks = await wavelink.Playable.search(s)
        if tracks:
            vc: wavelink.Player = interaction.guild.voice_client
            if vc and vc.playing:
                vc.queue.put(tracks[0])
            else:
                await play_track_with_embed(interaction, s)
                break
    await interaction.followup.send(f"🎶 開始播放歌單: **{name}**")

if __name__ == "__main__":
    keep_alive()
    TOKEN = os.getenv("DISCORD_TOKEN")
    bot.run(TOKEN)
