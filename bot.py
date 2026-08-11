import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import os
import json
import time
import random
import urllib.parse
import urllib.request
from keep_alive import keep_alive

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# 你的伺服器 ID
MY_GUILD_ID = discord.Object(id=1431588910554812540)

ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'auto',
    'cookiefile': 'cookies.txt',
    'remote_components': 'ejs:github',
    'js_runtimes': {
        'deno': {
            'path': '/root/.deno/bin/deno'
        }
    },
}
ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


# 儲存結構
music_queues = {}
last_played_url = {}
loop_status = {}  # 0: 關閉, 1: 單曲循環, 2: 佇列循環
music_history = {}
start_times = {}
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
def play_next(ctx):
    guild_id = ctx.guild.id
    mode = loop_status.get(guild_id, 0)

    if guild_id in vote_skips:
        vote_skips[guild_id].clear()

    if mode == 1 and guild_id in last_played_url:
        asyncio.run_coroutine_threadsafe(play_song(ctx, last_played_url[guild_id]), bot.loop)
        return

    if guild_id in music_queues and len(music_queues[guild_id]) > 0:
        next_url = music_queues[guild_id].pop(0)
        if mode == 2 and guild_id in last_played_url:
            music_queues[guild_id].append(last_played_url[guild_id])
        asyncio.run_coroutine_threadsafe(play_song(ctx, next_url), bot.loop)
    elif mode == 2 and guild_id in last_played_url:
        asyncio.run_coroutine_threadsafe(play_song(ctx, last_played_url[guild_id]), bot.loop)
    else:
        if not is_247_mode.get(guild_id, False):
            vc = ctx.guild.voice_client if not isinstance(ctx, discord.Interaction) else ctx.guild.voice_client
            if vc and vc.is_connected():
                asyncio.run_coroutine_threadsafe(vc.disconnect(), bot.loop)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5, filter_type=None):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.chapters = data.get('chapters')
        self.thumbnail = data.get('thumbnail')
        self.duration = data.get('duration', 0)

    @classmethod
    async def create_source(cls, url, *, loop=None, start_time=0, volume=0.5, filter_type=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        if 'entries' in data: data = data['entries'][0]

        filename = data['url']

        # 【確保這裡有啟用 -ss 快速跳轉】
        before_options = f"-ss {start_time}" if start_time > 0 else ""

        options_list = ['-vn']
        if filter_type == 'bassboost':
            options_list.append('-af bass=g=15')
        elif filter_type == 'nightcore':
            options_list.append('-af asetrate=44105*1.25,aresample=44105')
        elif filter_type == 'eq_boost':
            options_list.append('-af equalizer=f=1000:width_type=o:width=2:g=5')

        options_str = ' '.join(options_list)

        # 帶入 before_options 才能真正跳到該秒數
       # 帶入更穩定、抗網路波動的 FFmpeg 參數
        audio = discord.FFmpegPCMAudio(filename, before_options=before_options, options=options_str)
        return cls(audio, data=data, volume=volume, filter_type=filter_type)
async def play_song(ctx, url, start_time=0, is_chapter_seek=False):
    try:
        guild_id = ctx.guild.id
        if not is_chapter_seek: 
            last_played_url[guild_id] = url
            if guild_id in vote_skips:
                vote_skips[guild_id].clear()

        vol = current_volumes.get(guild_id, 0.05)
        flt = current_filters.get(guild_id, None)

        # 建立音訊來源 (帶入 start_time 秒數)
        player = await YTDLSource.create_source(url, loop=bot.loop, start_time=start_time, volume=vol, filter_type=flt)

        def after_playing(error):
            # 如果是章節切換中斷，絕對直接返回，不切歌也不退房
            if is_chapter_seek:
                return
            if guild_id not in music_history: music_history[guild_id] = []
            music_history[guild_id].append(last_played_url.get(guild_id))
            play_next(ctx)

        vc = ctx.guild.voice_client if isinstance(ctx, discord.Interaction) else ctx.guild.voice_client
        if vc:
            if vc.is_playing() or vc.is_paused(): 
                # 停止前先清空 source 的 after，避免 stop() 觸發錯誤事件
                vc.source = None 
                vc.stop()
            vc.play(player, after=after_playing)
            start_times[guild_id] = time.time() - start_time  # 修正計時器的起始時間

        # 只有在「非章節切換（一般點歌）」時才發送全新的控制面板
        if not is_chapter_seek:
            view = MusicControlView(chapters=player.chapters, url=url)

            embed = discord.Embed(
                title="🎶 正在播放音樂",
                description=f"**[{player.title}]({url})**",
                color=discord.Color.blurple()
            )
            embed.add_field(name="⏱️ 總長度", value=f"{player.duration//60:02}:{player.duration%60:02}" if player.duration else "未知", inline=True)
            embed.add_field(name="🔊 目前音量", value=f"{int(vol * 100)}%", inline=True)
            embed.add_field(name="🎛️ 音效濾鏡", value=f"{flt if flt else '正常音質'}", inline=True)
            if player.thumbnail:
                embed.set_thumbnail(url=player.thumbnail)
            embed.set_footer(text="使用下方按鈕與選單隨時控制播放狀態")

            if hasattr(ctx, 'followup'): await ctx.followup.send(embed=embed, view=view)
            else: await ctx.channel.send(embed=embed, view=view)
    except Exception as e:
        print(f"播放錯誤: {e}")
# --- 互動式進階面板與選單元件 ---
class ChapterSelect(discord.ui.Select):
    def __init__(self, chapters, url):
        # 確保這裡抓取的是 c.get('start_time', 0) 或 c.get('time', 0)
        options = [discord.SelectOption(label=c.get('title', f'章節 {i+1}')[:100], value=f"{url}|{c.get('start_time', 0)}") for i, c in enumerate(chapters[:25])]
        super().__init__(placeholder="🎵 即時切換章節...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        data = self.values[0].split("|")
        target_url = data[0]
        start_sec = float(data[1])

        await interaction.response.send_message(f"⏩ 正在背景切換至章節秒數: {int(start_sec)}秒...", ephemeral=True)

        # 使用背景任務 (Task) 獨立處理串流初始化與切換，完全不卡住目前的互動與音訊
        async def perform_seek():
            try:
                await play_song(interaction, target_url, start_time=start_sec, is_chapter_seek=True)
            except Exception as e:
                print(f"章節背景切換錯誤: {e}")

        asyncio.create_task(perform_seek())
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
        guild_id = interaction.guild.id
        mode = self.values[0]
        if mode == "normal":
            current_filters[guild_id] = None
            await interaction.response.send_message("🎛️ 已切換為正常音質 (請重新點歌或切歌生效)", ephemeral=True)
        else:
            current_filters[guild_id] = mode
            await interaction.response.send_message(f"🎛️ 已套用 **{mode}** 濾鏡！ (請重新點歌或切歌生效)", ephemeral=True)

class VolumeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="音量: 20%", value="0.02", emoji="🔈"),
            discord.SelectOption(label="音量: 50% (預設)", value="0.05", emoji="🔉"),
            discord.SelectOption(label="音量: 80%", value="0.08", emoji="🔊"),
            discord.SelectOption(label="音量: 100% (最大)", value="0.1", emoji="📢")
        ]
        super().__init__(placeholder="🔊 調整播放音量...", options=options, row=2)
    async def callback(self, interaction: discord.Interaction):
        data = self.values[0].split("|")
        target_url = data[0]
        start_sec = float(data[1])
        
        print(f"DEBUG: 準備跳轉到秒數 -> {start_sec}") # 檢查這裡印出來的是不是正確的秒數（例如 120.5），而不是 0

        await interaction.response.send_message(f"⏩ 正在背景切換至章節秒數: {int(start_sec)}秒...", ephemeral=True)

        async def perform_seek():
            try:
                await play_song(interaction, target_url, start_time=start_sec, is_chapter_seek=True)
            except Exception as e:
                print(f"章節背景切換錯誤: {e}")

        asyncio.create_task(perform_seek())

class MusicControlView(discord.ui.View):
    def __init__(self, chapters=None, url=None):
        super().__init__(timeout=None)
        self.url = url
        if chapters: 
            self.add_item(ChapterSelect(chapters, url))
        self.add_item(FilterSelect())
        self.add_item(VolumeSelect())

    @discord.ui.button(label="⏮️ 上一首", style=discord.ButtonStyle.secondary, row=3)
    async def prev(self, i: discord.Interaction, b: discord.ui.Button):
        hist = music_history.get(i.guild.id, [])
        if hist: 
            prev_url = hist.pop()
            await i.response.send_message("⏮️ 切換回上一首", ephemeral=True)
            await play_song(i, prev_url)
        else: 
            await i.response.send_message("無歷史記錄", ephemeral=True)

    @discord.ui.button(label="⏯️ 暫停/繼續", style=discord.ButtonStyle.primary, row=3)
    async def pr(self, i: discord.Interaction, b: discord.ui.Button):
        vc = i.guild.voice_client
        if vc:
            if vc.is_playing(): vc.pause()
            elif vc.is_paused(): vc.resume()
            await i.response.send_message("播放狀態已切換", ephemeral=True)
        else:
            await i.response.send_message("機器人未連線", ephemeral=True)

    @discord.ui.button(label="⏭️ 下一首", style=discord.ButtonStyle.secondary, row=3)
    async def next(self, i: discord.Interaction, b: discord.ui.Button):
        vc = i.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await i.response.send_message("⏭️ 已跳過", ephemeral=True)
        else:
            await i.response.send_message("目前沒有音樂在播放", ephemeral=True)

    @discord.ui.button(label="⏹️ 停止", style=discord.ButtonStyle.danger, row=3)
    async def stop(self, i: discord.Interaction, b: discord.ui.Button):
        vc = i.guild.voice_client
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
        vc = i.guild.voice_client
        if not vc or not vc.is_playing():
            await i.response.send_message("目前沒有音樂正在播放！", ephemeral=True)
            return

        start = start_times.get(i.guild.id, time.time())
        elapsed = int(time.time() - start)
        dur = vc.source.data.get('duration', 0)

        if not dur or dur <= 0:
            await i.response.send_message(f"⏱️ 目前已播放: {elapsed//60}:{elapsed%60:02} (直播或未知長度)", ephemeral=True)
            return

        progress = min(elapsed, dur)
        filled = int(20 * progress / dur)
        bar = "▬" * filled + "●" * (1 if filled < 20 else 0) + "▬" * (19 - filled if filled < 20 else 0)
        await i.response.send_message(f"📊 **即時進度更新：**\n`[{bar}]` {elapsed//60}:{elapsed%60:02} / {dur//60}:{dur%60:02}", ephemeral=True)

@bot.event
async def on_ready():
    bot.tree.copy_global_to(guild=MY_GUILD_ID)
    await bot.tree.sync(guild=MY_GUILD_ID)
    print(f'機器人已上線: {bot.user.name}')

# --- 斜線指令 ---
@bot.tree.command(name="play", description="播放音樂 (支援網址或關鍵字搜尋)")
@app_commands.describe(query="YouTube 網址或關鍵字")
async def slash_play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    if not interaction.guild.voice_client: 
        if interaction.user.voice:
            await interaction.user.voice.channel.connect()
        else:
            await interaction.followup.send("請先進入語音頻道！", ephemeral=True)
            return

    url = query
    if not query.startswith("http"):
        try:
            info = ytdl.extract_info(f"ytsearch:{query}", download=False)
            if 'entries' in info and len(info['entries']) > 0:
                url = info['entries'][0]['url']
            else:
                await interaction.followup.send("找不到相關搜尋結果！", ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(f"搜尋發生錯誤: {e}", ephemeral=True)
            return

    await play_song(interaction, url)

@bot.tree.command(name="nowplaying", description="查看當前播放進度條")
async def slash_np(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("目前沒有音樂正在播放！", ephemeral=True)
        return

    start = start_times.get(interaction.guild.id, time.time())
    elapsed = int(time.time() - start)
    dur = vc.source.data.get('duration', 0)

    if not dur or dur <= 0:
        await interaction.response.send_message(f"🎵 **{vc.source.title}**\n⏱️ 已播放時間: {elapsed//60}:{elapsed%60:02} (未知長度/直播)")
        return

    progress = min(elapsed, dur)
    filled = int(20 * progress / dur)
    bar = "▬" * filled + "●" * (1 if filled < 20 else 0) + "▬" * (19 - filled if filled < 20 else 0)

    await interaction.response.send_message(f"🎵 **{vc.source.title}**\n`[{bar}]` {elapsed//60}:{elapsed%60:02} / {dur//60}:{dur%60:02}")

@bot.tree.command(name="recommend", description="根據當前歌曲推薦相似音樂")
async def slash_rec(interaction: discord.Interaction):
    last_url = last_played_url.get(interaction.guild.id)
    if not last_url:
        await interaction.response.send_message("請先播放一首歌，我才能為你推薦！", ephemeral=True)
        return

    await interaction.response.defer()
    info = ytdl.extract_info(last_url, download=False)
    related = info.get('related_videos', [])[:5]

    if not related:
        await interaction.followup.send("找不到相關推薦歌曲。")
        return

    rec_msg = "💡 **為您推薦以下歌曲：**\n"
    for r in related:
        rec_msg += f"- [{r.get('title')}]({r.get('url')})\n"

    await interaction.followup.send(rec_msg)

@bot.tree.command(name="download", description="取得當前播放歌曲的直接音訊下載網址")
async def slash_download(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("目前沒有音樂正在播放！", ephemeral=True)
        return

    song_url = last_played_url.get(interaction.guild.id)
    song_title = vc.source.title

    if not song_url:
        await interaction.response.send_message("無法取得當前歌曲網址。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        info = ytdl.extract_info(song_url, download=False)
        direct_audio_url = info.get('url')
        await interaction.followup.send(f"📥 **{song_title}** 的音訊串流下載網址：\n{direct_audio_url}\n*(提示：此連結為暫時性串流網址)*", ephemeral=True)
    except Exception:
        await interaction.followup.send("無法產生下載連結。", ephemeral=True)

@bot.tree.command(name="trending", description="查看當前熱門音樂精選排行榜")
async def slash_trending(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        info = ytdl.extract_info("ytsearch5:热门音乐 流行歌曲", download=False)
        entries = info.get('entries', [])

        if not entries:
            await interaction.followup.send("目前無法取得熱門音樂清單。")
            return

        msg = "🔥 **當前熱門精選排行榜：**\n"
        for idx, entry in enumerate(entries, 1):
            msg += f"{idx}. [{entry.get('title')}]({entry.get('url')})\n"

        await interaction.followup.send(msg)
    except Exception:
        await interaction.followup.send("取得熱門排行榜失敗。")

@bot.tree.command(name="voteskip", description="發起投票跳過當前歌曲")
async def slash_voteskip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("目前沒有音樂正在播放！", ephemeral=True)
        return

    guild_id = interaction.guild.id
    if guild_id not in vote_skips:
        vote_skips[guild_id] = set()

    user_id = interaction.user.id
    if not interaction.user.voice or interaction.user.voice.channel != vc.channel:
        await interaction.response.send_message("你必須和機器人在同一個語音頻道才能投票！", ephemeral=True)
        return

    if user_id in vote_skips[guild_id]:
        await interaction.response.send_message("你已經投過票了！", ephemeral=True)
        return

    vote_skips[guild_id].add(user_id)

    channel_members = [m for m in vc.channel.members if not m.bot]
    required_votes = max(1, len(channel_members) // 2)
    current_votes = len(vote_skips[guild_id])

    if current_votes >= required_votes:
        vc.stop()
        await interaction.response.send_message(f"🗳️ 投票跳過成功！ ({current_votes}/{required_votes} 票)")
    else:
        await interaction.response.send_message(f"🗳️ **{interaction.user.display_name}** 發起了跳過投票。目前票數：`{current_votes}/{required_votes}` (需要過半數)")

@bot.tree.command(name="shuffle", description="將目前的音樂佇列隨機打亂")
async def slash_shuffle(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    q = music_queues.get(guild_id, [])
    if not q or len(q) < 2:
        await interaction.response.send_message("目前的佇列歌曲太少，無法進行洗牌！", ephemeral=True)
        return

    random.shuffle(music_queues[guild_id])
    await interaction.response.send_message("🔀 已成功將目前的音樂佇列隨機洗牌！", ephemeral=True)

@bot.tree.command(name="247", description="切換 24 小時不離線模式")
async def slash_247(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    current_status = is_247_mode.get(guild_id, False)
    is_247_mode[guild_id] = not current_status
    status_str = "✅ 已開啟 (音樂結束後將留在頻道中)" if is_247_mode[guild_id] else "❌ 已關閉 (音樂結束後將自動離開)"
    await interaction.response.send_message(f"🌙 **24/7 模式狀態：** {status_str}", ephemeral=True)

@bot.tree.command(name="history", description="查看最近播過的歷史歌曲記錄")
async def slash_history(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    hist = music_history.get(guild_id, [])
    if not hist:
        await interaction.response.send_message("目前沒有歷史播放記錄！", ephemeral=True)
        return

    msg = "📜 **最近播放歷史記錄 (最近 10 首)：**\n"
    for idx, song_url in enumerate(hist[-10:][::-1], 1):
        msg += f"{idx}. {song_url}\n"
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="lyrics", description="搜尋目前播放歌曲的歌詞")
async def slash_lyrics(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("目前沒有音樂正在播放，無法查詢歌詞！", ephemeral=True)
        return

    song_title = vc.source.title
    await interaction.response.defer()

    try:
        await interaction.followup.send(f"🎤 正在為您尋找 **{song_title}** 的歌詞建議...\n(提示：您也可以直接至 Google 搜尋該歌名搭配「歌詞」以獲得最完整的圖文排版)")
    except Exception:
        await interaction.followup.send("暫時無法取得該歌曲的線上歌詞。")

@bot.tree.command(name="queue", description="查看目前的音樂佇列")
async def slash_queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    q = music_queues.get(guild_id, [])
    if not q:
        await interaction.response.send_message("目前佇列是空的！", ephemeral=True)
        return

    msg = "🎶 **目前音樂佇列：**\n"
    for idx, song_url in enumerate(q[:10], 1):
        msg += f"{idx}. {song_url}\n"
    if len(q) > 10:
        msg += f"... 還有另外 {len(q) - 10} 首歌曲"
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="remove", description="從佇列中移除指定編號的歌曲")
@app_commands.describe(index="要移除的歌曲編號 (可透過 /queue 查看)")
async def slash_remove(interaction: discord.Interaction, index: int):
    guild_id = interaction.guild.id
    q = music_queues.get(guild_id, [])
    if not q or index < 1 or index > len(q):
        await interaction.response.send_message("無效的編號或佇列為空！", ephemeral=True)
        return
    removed = q.pop(index - 1)
    await interaction.response.send_message(f"🗑️ 已從佇列中移除第 {index} 首歌曲。", ephemeral=True)

@bot.tree.command(name="loop", description="切換循環播放模式")
@app_commands.choices(mode=[
    app_commands.Choice(name="關閉循環", value=0),
    app_commands.Choice(name="單曲循環", value=1),
    app_commands.Choice(name="佇列循環", value=2)
])
async def slash_loop(interaction: discord.Interaction, mode: int):
    guild_id = interaction.guild.id
    loop_status[guild_id] = mode
    await interaction.response.send_message(f"🔄 循環模式已更新為: **{['關閉', '單曲循環', '佇列循環'][mode]}**")

@bot.tree.command(name="playlist_add", description="將歌曲加入自訂歌單")
@app_commands.describe(name="歌單名稱", url="YouTube 網址或關鍵字")
async def slash_playlist_add(interaction: discord.Interaction, name: str, url: str):
    playlists = load_playlists()
    user_id = str(interaction.user.id)

    if user_id not in playlists: playlists[user_id] = {}
    if name not in playlists[user_id]: playlists[user_id][name] = []

    playlists[user_id][name].append(url)
    save_playlists(playlists)
    await interaction.response.send_message(f"✅ 已成功將歌曲加入歌單 **{name}**！", ephemeral=True)

@bot.tree.command(name="playlist_list", description="查看你建立的所有自訂歌單")
async def slash_playlist_list(interaction: discord.Interaction):
    playlists = load_playlists()
    user_id = str(interaction.user.id)

    if user_id not in playlists or not playlists[user_id]:
        await interaction.response.send_message("你目前沒有建立任何自訂歌單！", ephemeral=True)
        return

    msg = "📂 **你的自訂歌單列表：**\n"
    for name, songs in playlists[user_id].items():
        msg += f"- **{name}**（共 {len(songs)} 首歌曲）\n"
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="playlist_delete", description="刪除指定的自訂歌單")
@app_commands.describe(name="要刪除的歌單名稱")
async def slash_playlist_delete(interaction: discord.Interaction, name: str):
    playlists = load_playlists()
    user_id = str(interaction.user.id)

    if user_id in playlists and name in playlists[user_id]:
        del playlists[user_id][name]
        save_playlists(playlists)
        await interaction.response.send_message(f"🗑️ 已成功刪除歌單 **{name}**！", ephemeral=True)
    else:
        await interaction.response.send_message(f"找不到名為 **{name}** 的歌單！", ephemeral=True)

@bot.tree.command(name="playlist_export", description="匯出指定歌單的分享代碼")
@app_commands.describe(name="要匯出的歌單名稱")
async def slash_playlist_export(interaction: discord.Interaction, name: str):
    playlists = load_playlists()
    user_id = str(interaction.user.id)

    if user_id in playlists and name in playlists[user_id]:
        songs = playlists[user_id][name]
        export_data = json.dumps(songs, ensure_ascii=False)
        await interaction.response.send_message(f"📤 歌單 **{name}** 的匯出代碼如下（請複製整串代碼）：\n```json\n{export_data}\n```", ephemeral=True)
    else:
        await interaction.response.send_message(f"找不到名為 **{name}** 的歌單！", ephemeral=True)

@bot.tree.command(name="playlist_import", description="透過分享代碼匯入別人的歌單")
@app_commands.describe(name="新歌單名稱", code="朋友給你的匯出代碼 (JSON)")
async def slash_playlist_import(interaction: discord.Interaction, name: str, code: str):
    try:
        songs = json.loads(code)
        if not isinstance(songs, list):
            raise ValueError()

        playlists = load_playlists()
        user_id = str(interaction.user.id)

        if user_id not in playlists: playlists[user_id] = {}
        playlists[user_id][name] = songs
        save_playlists(playlists)

        await interaction.response.send_message(f"📥 成功匯入新歌單 **{name}**（共 {len(songs)} 首歌曲）！", ephemeral=True)
    except Exception:
        await interaction.response.send_message("❌ 匯入失敗！請確認代碼格式是否正確。", ephemeral=True)

@bot.tree.command(name="playlist_play", description="播放指定自訂歌單")
@app_commands.describe(name="歌單名稱")
async def slash_playlist_play(interaction: discord.Interaction, name: str):
    # 第一時間執行 defer 避免 3 秒超時
    await interaction.response.defer(ephemeral=True)

    playlists = load_playlists()
    user_id = str(interaction.user.id)

    if user_id not in playlists or name not in playlists[user_id] or not playlists[user_id][name]:
        await interaction.followup.send(f"找不到歌單 **{name}** 或歌單內沒有歌曲！", ephemeral=True)
        return

    if not interaction.guild.voice_client:
        if interaction.user.voice:
            await interaction.user.voice.channel.connect()
        else:
            await interaction.followup.send("請先進入語音頻道！", ephemeral=True)
            return

    guild_id = interaction.guild.id
    songs = playlists[user_id][name]

    first_song = songs[0]
    if guild_id not in music_queues: music_queues[guild_id] = []
    for s in songs[1:]:
        music_queues[guild_id].append(s)

    await play_song(interaction, first_song)
    await interaction.followup.send(f"🎶 開始播放歌單: **{name}**（共 {len(songs)} 首）")

if __name__ == "__main__":
    keep_alive()
    TOKEN = os.getenv("DISCORD_TOKEN")
    bot.run(TOKEN)
