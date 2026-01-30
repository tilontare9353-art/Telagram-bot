#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Universal Downloader Bot (Render-ready, python-telegram-bot v20+)

Asosiy imkoniyatlar:
- YouTube: faqat MAVJUD formatlar tugmalari chiqadi (144p/360p/720p... mavjud bo'lsa bor).
- TikTok/Instagram/Facebook va boshqalar: 📹 Video + 🎵 Audio tugmalari.
- Guruhda: bot media faylni aynan link yuborilgan xabar ostiga REPLY qilib tashlaydi.
- /broadcast va /broadcastpost (faqat admin) — start bosgan foydalanuvchilarga.

Til:
- /start da til tanlash: 🇺🇿 O‘zbekcha / 🇷🇺 Русский
- O‘zbekcha salomlashish matni o'zgarmaydi.
- Ruscha tanlanganda barcha asosiy yozuvlar ruscha chiqadi.

Render uchun:
- Tavsiya: webhook rejimi (RUN_MODE=webhook).
- ENV:
  BOT_TOKEN          (majburiy)
  ADMIN_IDS          (ixtiyoriy) "123,456"
  DATABASE_URL       (tavsiya) Render Postgres connection string
  WEBHOOK_URL        (webhook rejimi uchun) masalan: https://your-service.onrender.com
  WEBHOOK_PATH       (ixtiyoriy) default: webhook
  PORT               (Render beradi)
  DATA_DIR           (fallback json storage uchun; Renderda tavsiya emas)

Eslatma:
- MP3 konvertatsiya uchun ffmpeg tavsiya qilinadi. Bo'lmasa m4a/webm audio yuboriladi.
"""

from dotenv import load_dotenv
load_dotenv()

import os
import re
import json
import asyncio
import logging
import tempfile
import secrets
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.error import TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from yt_dlp import YoutubeDL

try:
    import asyncpg
except Exception:
    asyncpg = None  # fallback to json storage


# ---------------------------- Config ----------------------------

TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
if not TOKEN:
    raise RuntimeError("BOT_TOKEN env topilmadi ('.env' borligini va BOT_TOKEN to'g'ri yozilganini tekshiring)")

ADMIN_IDS: set[int] = set()
_admin_raw = (os.getenv("ADMIN_IDS") or "").strip()
if _admin_raw:
    for part in _admin_raw.split(","):
        part = part.strip()
        if part.isdigit():
            ADMIN_IDS.add(int(part))

DATA_DIR = Path((os.getenv("DATA_DIR") or ".")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Fallback json storage (Renderda tavsiya emas)
USERS_FILE = DATA_DIR / "users.json"
PREFS_FILE = DATA_DIR / "prefs.json"

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

BOT_USERNAME_TAG = "@universal_downloader_uzb_bot"

CALLBACK_CACHE: Dict[str, Dict[str, Any]] = {}
CALLBACK_CACHE_MAX = 3000

RUN_MODE = (os.getenv("RUN_MODE") or "").strip().lower()  # "webhook" or "polling"
WEBHOOK_URL_BASE = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()
WEBHOOK_PATH = (os.getenv("WEBHOOK_PATH") or "webhook").strip().lstrip("/")
PORT = int(os.getenv("PORT") or "8080")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("downloader")


# ---------------------------- i18n ----------------------------

LANG_UZ = "uz"
LANG_RU = "ru"

START_TEXT_UZ = (
    "👋🏻 Salom\n"
    "Telegramdagi YouTube’dan, Tiktokdan, Instagram va Focebookdan video, audiolarni yuklab olish uchun eng tezkor "
    f"{BOT_USERNAME_TAG} ga xush kelibsiz.\n\n"
    "✅ Botning imkoniyatlari:\n"
    "✨ Youtubedan Video sifatini tanlash imkoniyati;\n"
    "📁 Video va audioni saqlab olish(cheksiz);\n"
    "💫 Yuklab olingan faylni do'stlarga ulashish;\n"
    "ℹ️ Botni guruxingizda admin qiling va guruhga yuborilgan havolalarni video ko’rinishida guruxingizga shu havola ostiga tashlab beradi.\n"
    "ℹ️ Botni guruxingizda reklama tarqatmaydi.\n"
    "ℹ️ Biror bir xatolikga duch kelsangiz bizni botlar kanaliga o’ting va u yerdagi adminlarga habar bering.\n"
    "Bizning foydali botlar kanali 👉 https://t.me/+skp5TgimYIJjYzIy\n\n"
    "🔗 BOSHLASH UCHUN YOUTUBEDAGI VIDEO HAVOLASINI YUBORING…⤵️"
)

START_TEXT_RU = (
    "👋🏻 Привет\n"
    "Добро пожаловать в самый быстрый бот, чтобы скачивать видео и аудио из YouTube, TikTok, Instagram и Facebook: "
    f"{BOT_USERNAME_TAG}\n\n"
    "✅ Возможности бота:\n"
    "✨ Выбор качества видео YouTube;\n"
    "📁 Скачивание видео и аудио (без ограничений);\n"
    "💫 Возможность делиться скачанным файлом с друзьями;\n"
    "ℹ️ Сделайте бота администратором в группе — и он будет отправлять скачанное видео ответом под сообщением со ссылкой.\n"
    "ℹ️ Бот не распространяет рекламу в вашей группе.\n"
    "ℹ️ Если столкнётесь с ошибкой — перейдите в наш канал ботов и напишите администраторам.\n"
    "Наш полезный канал ботов 👉 https://t.me/+skp5TgimYIJjYzIy\n\n"
    "🔗 ДЛЯ НАЧАЛА ОТПРАВЬТЕ ССЫЛКУ НА ВИДЕО С YOUTUBE…⤵️"
)

TEXT = {
    "choose_lang": {
        LANG_UZ: "Tilni tanlang:",
        LANG_RU: "Выберите язык:",
    },
    "btn_uz": {LANG_UZ: "🇺🇿 O‘zbekcha", LANG_RU: "🇺🇿 O‘zbekcha"},
    "btn_ru": {LANG_UZ: "🇷🇺 Русский", LANG_RU: "🇷🇺 Русский"},
    "yt_fetching": {
        LANG_UZ: "🔎 YouTube formatlar olinmoqda...",
        LANG_RU: "🔎 Получаю форматы YouTube...",
    },
    "choose": {LANG_UZ: "Tanlang:", LANG_RU: "Выберите:"},
    "btn_video": {LANG_UZ: "📹 Video yuklab olish", LANG_RU: "📹 Скачать видео"},
    "btn_audio": {LANG_UZ: "🎵 Audio", LANG_RU: "🎵 Аудио"},
    "yt_choose_fmt": {
        LANG_UZ: "Formatni tanlang (YouTube):",
        LANG_RU: "Выберите формат (YouTube):",
    },
    "btn_expired": {
        LANG_UZ: "❌ Bu tugma eskirib qolgan. Iltimos linkni qayta yuboring.",
        LANG_RU: "❌ Эта кнопка устарела. Пожалуйста, отправьте ссылку ещё раз.",
    },
    "downloading_answer": {
        LANG_UZ: "⏳ Yuklab olinmoqda...",
        LANG_RU: "⏳ Скачиваю...",
    },
    "downloading_wait": {
        LANG_UZ: "⏳ Yuklab olinmoqda, iltimos kuting...",
        LANG_RU: "⏳ Скачиваю, пожалуйста подождите...",
    },
    "fmt_error": {
        LANG_UZ: "❌ Formatlarni olishda xatolik: {err}",
        LANG_RU: "❌ Ошибка при получении форматов: {err}",
    },
    "err_generic": {LANG_UZ: "❌ Xatolik: {err}", LANG_RU: "❌ Ошибка: {err}"},
    "not_admin": {LANG_UZ: "❌ Siz admin emassiz.", LANG_RU: "❌ Вы не админ."},
    "usage_broadcast": {
        LANG_UZ: "Ishlatish: /broadcast xabar_matni",
        LANG_RU: "Использование: /broadcast текст_сообщения",
    },
    "bc_started": {
        LANG_UZ: "📣 Broadcast boshlandi. Users: {n}",
        LANG_RU: "📣 Рассылка началась. Пользователей: {n}",
    },
    "bc_done": {
        LANG_UZ: "✅ Yakunlandi. Yuborildi: {sent}, Xato: {failed}",
        LANG_RU: "✅ Готово. Отправлено: {sent}, Ошибок: {failed}",
    },
    "usage_broadcastpost": {
        LANG_UZ: "Ishlatish: Kerakli postga reply qiling va /broadcastpost yozing.",
        LANG_RU: "Использование: Ответьте на нужный пост и отправьте /broadcastpost.",
    },
    "bcpost_started": {
        LANG_UZ: "📣 BroadcastPost boshlandi. Users: {n}",
        LANG_RU: "📣 Пересылка поста началась. Пользователей: {n}",
    },
    "caption_suffix": {
        LANG_UZ: f"{BOT_USERNAME_TAG} da yuklab olindi",
        LANG_RU: f"Скачано в {BOT_USERNAME_TAG}",
    },
}


def _t(lang: str, key: str, **kwargs) -> str:
    d = TEXT.get(key) or {}
    s = d.get(lang) or d.get(LANG_UZ) or key
    if kwargs:
        try:
            return s.format(**kwargs)
        except Exception:
            return s
    return s


# ---------------------------- User storage (DB + fallback JSON) ----------------------------

def _json_load(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def _json_save(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_users_json() -> Dict[str, Any]:
    """
    Supports both formats:
      - old: [123,456]
      - new: {"users":[123,456]}
    """
    raw = _json_load(USERS_FILE, {"users": []})
    if isinstance(raw, list):
        return {"users": raw}
    if isinstance(raw, dict):
        users = raw.get("users")
        if not isinstance(users, list):
            raw["users"] = []
        return raw
    return {"users": []}

def _add_user_json(user_id: int) -> None:
    data = _load_users_json()
    users = set(int(x) for x in (data.get("users") or []) if str(x).isdigit())
    users.add(int(user_id))
    data["users"] = sorted(users)
    _json_save(USERS_FILE, data)

def _get_users_json() -> List[int]:
    data = _load_users_json()
    return [int(x) for x in (data.get("users") or []) if str(x).isdigit()]

def _load_prefs_json() -> Dict[str, Any]:
    raw = _json_load(PREFS_FILE, {})
    return raw if isinstance(raw, dict) else {}

def _set_lang_json(user_id: int, lang: str) -> None:
    prefs = _load_prefs_json()
    prefs[str(int(user_id))] = lang
    _json_save(PREFS_FILE, prefs)

def _get_lang_json(user_id: int) -> Optional[str]:
    prefs = _load_prefs_json()
    v = prefs.get(str(int(user_id)))
    return v if v in (LANG_UZ, LANG_RU) else None


class UserStore:
    def __init__(self) -> None:
        self.pool: Optional["asyncpg.pool.Pool"] = None

    async def init(self) -> None:
        if not DATABASE_URL or asyncpg is None:
            if not DATABASE_URL:
                log.warning("DATABASE_URL topilmadi — fallback: users.json ishlatiladi (Renderda tavsiya emas).")
            else:
                log.warning("asyncpg import bo'lmadi — fallback: users.json ishlatiladi.")
            return

        ssl_opt: Optional[bool] = None
        if "sslmode=require" in DATABASE_URL.lower() or (os.getenv("PGSSLMODE") or "").lower() == "require":
            ssl_opt = True

        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, ssl=ssl_opt)
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_users (
              user_id    BIGINT PRIMARY KEY,
              first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              last_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              lang       TEXT NOT NULL DEFAULT 'uz',
              username   TEXT,
              first_name TEXT,
              last_name  TEXT
            );
            """
        )
        await self.pool.execute("CREATE INDEX IF NOT EXISTS bot_users_last_seen_idx ON bot_users(last_seen);")
        log.info("DB tayyor: bot_users jadvali tekshirildi/yaratildi.")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def touch_user(self, user: User, lang: Optional[str] = None) -> None:
        """Insert/update user. lang berilsa — yangilanadi; berilmasa — avvalgisi saqlanadi."""
        uid = int(user.id)
        if self.pool:
            await self.pool.execute(
                """
                INSERT INTO bot_users (user_id, username, first_name, last_name, last_seen, lang)
                VALUES ($1, $2, $3, $4, NOW(), COALESCE($5, 'uz'))
                ON CONFLICT (user_id) DO UPDATE SET
                  username   = EXCLUDED.username,
                  first_name = EXCLUDED.first_name,
                  last_name  = EXCLUDED.last_name,
                  last_seen  = NOW(),
                  lang       = COALESCE($5, bot_users.lang);
                """,
                uid,
                getattr(user, "username", None),
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
                lang,
            )
        else:
            _add_user_json(uid)
            if lang in (LANG_UZ, LANG_RU):
                _set_lang_json(uid, lang)

    async def set_lang(self, user: User, lang: str) -> None:
        uid = int(user.id)
        if self.pool:
            await self.pool.execute(
                """
                INSERT INTO bot_users (user_id, username, first_name, last_name, last_seen, lang)
                VALUES ($1, $2, $3, $4, NOW(), $5)
                ON CONFLICT (user_id) DO UPDATE SET
                  username   = EXCLUDED.username,
                  first_name = EXCLUDED.first_name,
                  last_name  = EXCLUDED.last_name,
                  last_seen  = NOW(),
                  lang       = EXCLUDED.lang;
                """,
                uid,
                getattr(user, "username", None),
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
                lang,
            )
        else:
            _add_user_json(uid)
            _set_lang_json(uid, lang)

    async def get_lang(self, user_id: int) -> str:
        uid = int(user_id)
        if self.pool:
            row = await self.pool.fetchrow("SELECT lang FROM bot_users WHERE user_id=$1", uid)
            lang = (row["lang"] if row else None)  # type: ignore[index]
            return lang if lang in (LANG_UZ, LANG_RU) else LANG_UZ
        v = _get_lang_json(uid)
        return v if v in (LANG_UZ, LANG_RU) else LANG_UZ

    async def get_users(self) -> List[int]:
        if self.pool:
            rows = await self.pool.fetch("SELECT user_id FROM bot_users")
            return [int(r["user_id"]) for r in rows]  # type: ignore[index]
        return _get_users_json()


STORE = UserStore()


async def get_user_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Lang priority: context.user_data -> DB/JSON -> default uz"""
    uid = update.effective_user.id if update.effective_user else None
    if not uid:
        return LANG_UZ
    cached = context.user_data.get("lang")
    if cached in (LANG_UZ, LANG_RU):
        return cached
    lang = await STORE.get_lang(uid)
    context.user_data["lang"] = lang
    return lang


# ---------------------------- Utils ----------------------------

URL_RE = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

def extract_first_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = URL_RE.search(text)
    if not m:
        return None
    return m.group(1).strip().rstrip(").,!?;\"'")

def is_youtube(url: str) -> bool:
    u = url.lower()
    return ("youtube.com" in u) or ("youtu.be" in u)

def human_mb(num_bytes: Optional[int]) -> Optional[str]:
    if not num_bytes or num_bytes <= 0:
        return None
    return f"{num_bytes / (1024 * 1024):.1f}MB"

def _cache_put(payload: Dict[str, Any]) -> str:
    token = secrets.token_urlsafe(8)[:10]
    if len(CALLBACK_CACHE) >= CALLBACK_CACHE_MAX:
        for k in list(CALLBACK_CACHE.keys())[: CALLBACK_CACHE_MAX // 2]:
            CALLBACK_CACHE.pop(k, None)
    CALLBACK_CACHE[token] = payload
    return token

def _cache_get(token: str) -> Optional[Dict[str, Any]]:
    return CALLBACK_CACHE.get(token)


# ---------------------------- yt-dlp helpers ----------------------------

def build_ydl_base(outtmpl: str) -> Dict[str, Any]:
    return {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 10,
        "fragment_retries": 10,
        "skip_unavailable_fragments": True,
        "continuedl": True,
        "concurrent_fragment_downloads": 8,
        "socket_timeout": 30,
        "extractor_retries": 3,
        "nocheckcertificate": True,
        "buffersize": 1024 * 1024,
        "http_chunk_size": 10 * 1024 * 1024,
    }

def _extract_info(url: str) -> Dict[str, Any]:
    ydl_opts = build_ydl_base(outtmpl="%(title)s.%(ext)s")
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

def _select_youtube_formats(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    formats = info.get("formats") or []
    vids = [f for f in formats if f.get("vcodec") != "none" and f.get("height")]
    by_h: Dict[int, List[Dict[str, Any]]] = {}
    for f in vids:
        try:
            h = int(f.get("height"))
        except Exception:
            continue
        by_h.setdefault(h, []).append(f)

    desired_heights = [144, 240, 360, 480, 720, 1080]
    picked: List[Dict[str, Any]] = []

    for h in desired_heights:
        cand = by_h.get(h)
        if not cand:
            continue

        def score(x: Dict[str, Any]) -> Tuple[int, float, int]:
            has_audio = 1 if x.get("acodec") != "none" else 0
            tbr = float(x.get("tbr") or 0.0)
            fs = int(x.get("filesize") or x.get("filesize_approx") or 0)
            return (has_audio, tbr, fs)

        best = sorted(cand, key=score, reverse=True)[0]
        picked.append(best)

    if not picked:
        vids_sorted = sorted(vids, key=lambda x: float(x.get("tbr") or 0.0), reverse=True)
        picked = vids_sorted[:6]
    return picked

def _download_video(url: str, format_id: Optional[str], workdir: str) -> Path:
    outtmpl = os.path.join(workdir, "%(title).200s.%(ext)s")

    def _run_with_opts(opts: Dict[str, Any]) -> Path:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

            candidates: List[Path] = []
            try:
                fp = ydl.prepare_filename(info)
                candidates.append(Path(fp))
            except Exception:
                pass

            try:
                for rd in (info.get("requested_downloads") or []):
                    p = rd.get("filepath")
                    if p:
                        candidates.append(Path(p))
            except Exception:
                pass

            try:
                files = sorted(
                    Path(workdir).glob("*"),
                    key=lambda x: x.stat().st_mtime,
                    reverse=True,
                )
                candidates.extend(files)
            except Exception:
                pass

            for p in candidates:
                try:
                    if p.exists() and p.is_file() and p.stat().st_size > 0:
                        return p
                except Exception:
                    continue

        raise RuntimeError("ERROR: The downloaded file is empty")

    ydl_opts = build_ydl_base(outtmpl=outtmpl)

    if format_id:
        ydl_opts["format"] = f"{format_id}+bestaudio/best"
        ydl_opts["merge_output_format"] = "mp4"
    else:
        ydl_opts["format"] = "bv*+ba/best"
        ydl_opts["merge_output_format"] = "mp4"

    try:
        return _run_with_opts(ydl_opts)
    except Exception:
        ydl_opts_fallback = dict(ydl_opts)
        ydl_opts_fallback["format"] = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"
        ydl_opts_fallback["merge_output_format"] = "mp4"
        return _run_with_opts(ydl_opts_fallback)

def _download_audio(url: str, workdir: str) -> Path:
    outtmpl = os.path.join(workdir, "%(title).200s.%(ext)s")

    ydl_opts = build_ydl_base(outtmpl=outtmpl)
    ydl_opts["format"] = "bestaudio/best"
    ydl_opts["postprocessors"] = [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "192",
    }]
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
        mp3s = sorted(Path(workdir).glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
        if mp3s:
            return mp3s[0]
    except Exception as e:
        log.warning("MP3 konvertatsiya muvaffaqiyatsiz (ffmpeg yo'q bo'lishi mumkin). Fallback audio: %s", e)

    ydl_opts2 = build_ydl_base(outtmpl=outtmpl)
    ydl_opts2["format"] = "bestaudio/best"
    with YoutubeDL(ydl_opts2) as ydl:
        info = ydl.extract_info(url, download=True)
        fp = ydl.prepare_filename(info)
        p = Path(fp)
        if p.exists():
            return p
        files = sorted(Path(workdir).glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not files:
            raise RuntimeError("Audio fayl topilmadi")
        return files[0]


# ---------------------------- Bot Handlers ----------------------------

def is_admin(user_id: Optional[int]) -> bool:
    return bool(user_id) and (user_id in ADMIN_IDS)

def start_text_by_lang(lang: str) -> str:
    return START_TEXT_RU if lang == LANG_RU else START_TEXT_UZ

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    # start bosganlarni DBga yozib boramiz
    await STORE.touch_user(update.effective_user)

    # Avval saqlangan til bo'lsa — shuni ishlatamiz, bo'lmasa default uz
    lang = await STORE.get_lang(update.effective_user.id)
    context.user_data["lang"] = lang

    kb = [[
        InlineKeyboardButton(_t(LANG_UZ, "btn_uz"), callback_data="lang|uz"),
        InlineKeyboardButton(_t(LANG_RU, "btn_ru"), callback_data="lang|ru"),
    ]]

    await update.message.reply_text(
        start_text_by_lang(lang),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def on_lang_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.from_user:
        return

    data = q.data or ""
    parts = data.split("|", maxsplit=1)
    if len(parts) != 2:
        return
    lang = parts[1].strip().lower()
    if lang not in (LANG_UZ, LANG_RU):
        lang = LANG_UZ

    # Saqlaymiz
    context.user_data["lang"] = lang
    await STORE.set_lang(q.from_user, lang)

    # Javob
    try:
        await q.answer()
    except Exception:
        pass

    kb = [[
        InlineKeyboardButton(_t(LANG_UZ, "btn_uz"), callback_data="lang|uz"),
        InlineKeyboardButton(_t(LANG_RU, "btn_ru"), callback_data="lang|ru"),
    ]]
    markup = InlineKeyboardMarkup(kb)

    try:
        await q.edit_message_text(
            start_text_by_lang(lang),
            disable_web_page_preview=True,
            reply_markup=markup,
        )
    except Exception:
        # Agar edit bo'lmasa, yangi xabar yuboramiz
        try:
            await context.bot.send_message(
                chat_id=q.message.chat_id if q.message else update.effective_chat.id,
                text=start_text_by_lang(lang),
                disable_web_page_preview=True,
                reply_markup=markup,
            )
        except Exception:
            pass


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if update.message:
        await update.message.reply_text(f"ID: `{uid}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = update.effective_user.id if update.effective_user else None
    lang = await get_user_lang(update, context)

    if not is_admin(uid):
        await update.message.reply_text(_t(lang, "not_admin"))
        return

    text = update.message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(_t(lang, "usage_broadcast"))
        return

    msg = parts[1].strip()
    users = await STORE.get_users()
    sent = 0
    failed = 0

    await update.message.reply_text(_t(lang, "bc_started", n=len(users)))
    for u in users:
        try:
            await context.bot.send_message(chat_id=u, text=msg)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(_t(lang, "bc_done", sent=sent, failed=failed))

async def cmd_broadcastpost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = update.effective_user.id if update.effective_user else None
    lang = await get_user_lang(update, context)

    if not is_admin(uid):
        await update.message.reply_text(_t(lang, "not_admin"))
        return
    if not update.message.reply_to_message:
        await update.message.reply_text(_t(lang, "usage_broadcastpost"))
        return

    src: Message = update.message.reply_to_message
    users = await STORE.get_users()
    sent = 0
    failed = 0

    await update.message.reply_text(_t(lang, "bcpost_started", n=len(users)))
    for u in users:
        try:
            await context.bot.copy_message(chat_id=u, from_chat_id=src.chat_id, message_id=src.message_id)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(_t(lang, "bc_done", sent=sent, failed=failed))


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    # user touch (langni majburan o'zgartirmaymiz)
    await STORE.touch_user(update.effective_user)

    url = extract_first_url(update.message.text or "")
    if not url:
        return

    lang = await get_user_lang(update, context)

    origin_chat_id = update.message.chat_id
    origin_message_id = update.message.message_id

    if is_youtube(url):
        msg = await update.message.reply_text(_t(lang, "yt_fetching"))
        asyncio.create_task(
            _task_show_youtube_formats(
                context=context,
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                url=url,
                origin_chat_id=origin_chat_id,
                origin_message_id=origin_message_id,
                lang=lang,
            )
        )
    else:
        kb = []
        t_v = _cache_put({
            "url": url, "kind": "video", "format_id": None,
            "origin_chat_id": origin_chat_id, "origin_message_id": origin_message_id,
            "lang": lang,
        })
        t_a = _cache_put({
            "url": url, "kind": "audio", "format_id": None,
            "origin_chat_id": origin_chat_id, "origin_message_id": origin_message_id,
            "lang": lang,
        })
        kb.append([InlineKeyboardButton(_t(lang, "btn_video"), callback_data=f"dl|{t_v}")])
        kb.append([InlineKeyboardButton(_t(lang, "btn_audio"), callback_data=f"dl|{t_a}")])
        await update.message.reply_text(_t(lang, "choose"), reply_markup=InlineKeyboardMarkup(kb))


async def _task_show_youtube_formats(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    url: str,
    origin_chat_id: int,
    origin_message_id: int,
    lang: str,
) -> None:
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, _extract_info, url)
        formats = _select_youtube_formats(info)

        kb = []
        for f in formats:
            fmt_id = str(f.get("format_id"))
            h = f.get("height")
            size = human_mb(f.get("filesize") or f.get("filesize_approx"))
            label = f"{size}, {h}p" if size else f"{h}p"

            token = _cache_put({
                "url": url, "kind": "video", "format_id": fmt_id,
                "origin_chat_id": origin_chat_id, "origin_message_id": origin_message_id,
                "lang": lang,
            })
            kb.append([InlineKeyboardButton(label, callback_data=f"dl|{token}")])

        token_a = _cache_put({
            "url": url, "kind": "audio", "format_id": None,
            "origin_chat_id": origin_chat_id, "origin_message_id": origin_message_id,
            "lang": lang,
        })
        kb.append([InlineKeyboardButton(_t(lang, "btn_audio"), callback_data=f"dl|{token_a}")])

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=_t(lang, "yt_choose_fmt"),
            reply_markup=InlineKeyboardMarkup(kb),
        )
    except Exception as e:
        log.exception("Formatlarni olishda xato: %s", e)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=_t(lang, "fmt_error", err=str(e)),
            )
        except Exception:
            pass


async def on_download_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query:
        return
    q = update.callback_query

    # callback timeout bo'lmasligi uchun darhol javob beramiz
    lang = LANG_UZ
    if q.from_user:
        context.user_data.setdefault("lang", await STORE.get_lang(q.from_user.id))
        lang = context.user_data.get("lang", LANG_UZ)
    try:
        await q.answer(_t(lang, "downloading_answer"), show_alert=False)
    except Exception:
        pass

    data = q.data or ""
    if not data.startswith("dl|"):
        return

    token = data.split("|", maxsplit=1)[1]
    payload = _cache_get(token)
    if not payload:
        try:
            await q.edit_message_text(_t(lang, "btn_expired"))
        except Exception:
            pass
        return

    url = payload["url"]
    kind = payload["kind"]
    format_id = payload.get("format_id")
    lang = payload.get("lang") or lang

    origin_chat_id = int(payload.get("origin_chat_id") or (q.message.chat_id if q.message else update.effective_chat.id))
    origin_message_id = payload.get("origin_message_id")
    reply_to_message_id = int(origin_message_id) if str(origin_message_id).isdigit() else None

    try:
        await q.edit_message_text(_t(lang, "downloading_wait"))
    except Exception:
        pass

    asyncio.create_task(_task_download_and_send(
        context=context,
        chat_id=origin_chat_id,
        reply_to_message_id=reply_to_message_id,
        url=url,
        kind=kind,
        format_id=format_id,
        lang=lang,
    ))


async def _send_audio_with_retry(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    path: Path,
    caption: str,
    reply_to_message_id: Optional[int],
) -> None:
    last_exc: Optional[Exception] = None
    for _ in range(2):
        try:
            with open(path, "rb") as f:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    caption=caption,
                    reply_to_message_id=reply_to_message_id,
                )
            return
        except TimedOut as e:
            last_exc = e
            await asyncio.sleep(2)
    if last_exc:
        raise last_exc

async def _send_video_with_retry(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    path: Path,
    caption: str,
    reply_to_message_id: Optional[int],
) -> None:
    last_exc: Optional[Exception] = None
    for _ in range(2):
        try:
            with open(path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    supports_streaming=True,
                    caption=caption,
                    reply_to_message_id=reply_to_message_id,
                )
            return
        except TimedOut as e:
            last_exc = e
            await asyncio.sleep(2)
    if last_exc:
        raise last_exc


async def _task_download_and_send(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    reply_to_message_id: Optional[int],
    url: str,
    kind: str,
    format_id: Optional[str],
    lang: str,
) -> None:
    loop = asyncio.get_running_loop()
    try:
        with tempfile.TemporaryDirectory(prefix="dlbot_") as td:
            caption = _t(lang, "caption_suffix")

            if kind == "audio":
                path: Path = await loop.run_in_executor(None, _download_audio, url, td)
                await _send_audio_with_retry(context, chat_id, path, caption, reply_to_message_id)
            else:
                path: Path = await loop.run_in_executor(None, _download_video, url, format_id, td)
                await _send_video_with_retry(context, chat_id, path, caption, reply_to_message_id)

    except Exception as e:
        log.exception("Download/send xato: %s", e)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_t(lang, "err_generic", err=str(e)),
                reply_to_message_id=reply_to_message_id,
            )
        except Exception:
            pass


# ---------------------------- App lifecycle ----------------------------

async def _post_init(app):
    await STORE.init()
    try:
        users = await STORE.get_users()
        log.info("Users loaded: %d", len(users))
    except Exception:
        pass

async def _post_shutdown(app):
    await STORE.close()

def build_app():
    # Telegram upload vaqtida timeout kamayishi uchun timeoutlarni kattalashtiramiz
    request = HTTPXRequest(connect_timeout=30, read_timeout=300, write_timeout=300, pool_timeout=30)
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("broadcastpost", cmd_broadcastpost))

    app.add_handler(CallbackQueryHandler(on_lang_button, pattern=r"^lang\|"))
    app.add_handler(CallbackQueryHandler(on_download_button, pattern=r"^dl\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    return app


def main() -> None:
    app = build_app()
    log.info("Bot started. Admins: %s", ",".join(str(x) for x in sorted(ADMIN_IDS)) if ADMIN_IDS else "(not set)")

    mode = RUN_MODE
    if mode not in ("webhook", "polling"):
        mode = "webhook" if WEBHOOK_URL_BASE else "polling"

    if mode == "webhook":
        if not WEBHOOK_URL_BASE:
            raise RuntimeError("RUN_MODE=webhook, lekin WEBHOOK_URL (yoki RENDER_EXTERNAL_URL) topilmadi")

        full_webhook_url = WEBHOOK_URL_BASE.rstrip("/") + "/" + WEBHOOK_PATH
        log.info("Webhook mode. URL: %s", full_webhook_url)

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=full_webhook_url,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        log.info("Polling mode.")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
