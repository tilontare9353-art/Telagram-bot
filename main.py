#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Universal Downloader Bot (Railway-ready, python-telegram-bot v20+)

Asosiy imkoniyatlar:
- YouTube: faqat MAVJUD formatlar tugmalari chiqadi (144p/360p/720p... mavjud bo'lsa bor).
- TikTok/Instagram/Facebook va boshqalar: 📹 Video + 🎵 Audio tugmalari.
- Guruhda: bot media faylni aynan link yuborilgan xabar ostiga REPLY qilib tashlaydi.
- /broadcast va /broadcastpost (faqat admin) — start bosgan foydalanuvchilarga.

Til:
- /start da til tanlash: 🇺🇿 O‘zbekcha / 🇷🇺 Русский
- O‘zbekcha salomlashish matni o'zgarmaydi.
- Ruscha tanlanganda barcha asosiy yozuvlar ruscha chiqadi.

Railway/Cloud учун:
- Tavsiya: polling режими (RUN_MODE=polling). Webhook ҳам мумкин (RUN_MODE=webhook).
- ENV:
  BOT_TOKEN          (majburiy)
  ADMIN_IDS          (ixtiyoriy) "123,456"
  DATABASE_URL       (tavsiya) Postgres connection string (Railway Postgres ёки бошқа)
  WEBHOOK_URL        (webhook rejimi uchun) масалан: https://<your-domain>
  WEBHOOK_PATH       (ixtiyoriy) default: webhook
  PORT               (webhook режимда платформа беради: Railway ва бошқалар)
  DATA_DIR           (fallback json storage uchun; cloud серверда тавсия этилмайди)

Eslatma:
- MP3 konvertatsiya uchun ffmpeg tavsiya qilinadi. Bo'lmasa m4a/webm audio yuboriladi.
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # python-dotenv optional (local test учун)
    pass

import os
import uuid
import re
import json
import asyncio
import logging
import tempfile
import shutil
import secrets
import base64
import html
import subprocess
import zipfile
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urlunsplit, urlparse
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

# Fallback json storage (cloud серверда тавсия этилмайди)
USERS_FILE = DATA_DIR / "users.json"
PREFS_FILE = DATA_DIR / "prefs.json"

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

BOT_USERNAME_TAG = "@universal_downloader_uzb_bot"

CALLBACK_CACHE: Dict[str, Dict[str, Any]] = {}
CALLBACK_CACHE_MAX = 3000

RUN_MODE = (os.getenv("RUN_MODE") or "").strip().lower()  # "webhook" or "polling"
def _guess_public_base_url() -> str:
    """Webhook учун public base URL ни топиш (RUN_MODE=webhook бўлса)."""
    v = (os.getenv("WEBHOOK_URL") or "").strip()
    if v:
        return v.rstrip("/")
    # Railway: best-effort (ҳамма аккаунтларда бўлмаслиги мумкин)
    dom = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("RAILWAY_STATIC_DOMAIN") or "").strip()
    if dom:
        return f"https://{dom}".rstrip("/")
    v = (os.getenv("RAILWAY_PUBLIC_URL") or "").strip()
    if v:
        return v.rstrip("/")
    # Render (ixtiyoriy fallback, агар керак бўлса)
    v = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    if v:
        return v.rstrip("/")
    return ""

WEBHOOK_URL_BASE = _guess_public_base_url()
WEBHOOK_PATH = (os.getenv("WEBHOOK_PATH") or "webhook").strip().lstrip("/")
PORT = int(os.getenv("PORT") or "8080")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("downloader")


# ---------------------------- i18n ----------------------------

LANG_UZ = "uz"
LANG_RU = "ru"

START_TEXT_UZ = (
    "👋🏻 <b>Salom</b>\n"
    "Telegramdagi <b>YouTube</b>’dan, <b>Tiktokdan</b>, <b>Instagram</b> va <b>Focebook</b>dan video, audiolarni yuklab olish uchun eng tezkor "
    f"{BOT_USERNAME_TAG} ga xush kelibsiz.\n\n"
    "✅ <b>Botning imkoniyatlari:</b>\n"
    "✨ Youtubedan Video sifatini tanlash imkoniyati;\n"
    "📁 Video va audioni saqlab olish(cheksiz);\n"
    "💫 Yuklab olingan faylni do'stlarga ulashish;\n"
    "ℹ️ Botni guruxingizda admin qiling va guruhga yuborilgan havolalarni video ko’rinishida guruxingizga shu havola ostiga tashlab beradi.\n"
    "ℹ️ <b>Botni guruxingizda reklama tarqatmaydi</b>.\n"
    "ℹ️ Biror bir xatolikga duch kelsangiz bizni botlar kanaliga o’ting va u yerdagi adminlarga habar bering.\n"
    "Bizning foydali botlar kanali 👉 https://t.me/+skp5TgimYIJjYzIy\n\n"
    "🔗 <b>BOSHLASH UCHUN VIDEO HAVOLASINI YUBORING</b>…⤵️"
)

START_TEXT_RU = (
    "👋🏻 <b>Привет</b>\n"
    "Добро пожаловать в самый быстрый бот, чтобы скачивать видео и аудио из <b>YouTube</b>, <b>TikTok</b>, <b>Instagram</b> и <b>Facebook</b>: "
    f"{BOT_USERNAME_TAG}\n\n"
    "✅ <b>Возможности бота:</b>\n"
    "✨ Выбор качества видео YouTube;\n"
    "📁 Скачивание видео и аудио (без ограничений);\n"
    "💫 Возможность делиться скачанным файлом с друзьями;\n"
    "ℹ️ Сделайте бота администратором в группе — и он будет отправлять скачанное видео ответом под сообщением со ссылкой.\n"
    "ℹ️ <b>Бот не распространяет рекламу в вашей группе</b>.\n"
    "ℹ️ Если столкнётесь с ошибкой — перейдите в наш канал ботов и напишите администраторам.\n"
    "Наш полезный канал ботов 👉 https://t.me/+skp5TgimYIJjYzIy\n\n"
    "🔗 <b>ДЛЯ НАЧАЛА ОТПРАВЬТЕ ССЫЛКУ НА ВИДЕО</b>…⤵️"
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
    "btn_mp3": {LANG_UZ: "🎵 MP3", LANG_RU: "🎵 MP3"},
    "tt_photo_audio_only": {
        LANG_UZ: "Bu TikTok foto-post (/photo/). Faqat audio (MP3) yuklash mumkin:",
        LANG_RU: "Это TikTok фото-пост (/photo/). Доступно только аудио (MP3):",
    },
    "btn_tt_photo": {LANG_UZ: "🖼 Foto post (ZIP)", LANG_RU: "🖼 Фото-пост (ZIP)"},
    "tt_photo_only": {
        LANG_UZ: "Bu TikTok foto-post (/photo/). Rasmlarni ZIP ko‘rinishida yuklab oling:",
        LANG_RU: "Это TikTok фото-пост (/photo/). Скачайте картинки в ZIP:",
    },
    "yt_caption": {
        LANG_UZ: "📹 <b>{title}</b>\n⏱ {dur}\n\n<b>Formatni tanlang:</b>",
        LANG_RU: "📹 <b>{title}</b>\n⏱ {dur}\n\n<b>Выберите формат:</b>",
    },
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

    "err_filename_too_long": {
        LANG_UZ: "❌ Fayl nomi juda uzun bo‘lib кетди (server cheklovi). Boshqa variantni tanlang yoki linkni qayta yuboring.",
        LANG_RU: "❌ Слишком длинное имя файла (ограничение сервера). Выберите другой вариант или отправьте ссылку заново.",
    },
    "yt_need_cookies": {
        LANG_UZ: "❌ YouTube «men robot emasman» tekshiruvini so‘radi. YouTube ишлаши учун (cloud серверда) браузердан экспорт қилинган Netscape formatdagi cookies.txt kerak.",
        LANG_RU: "❌ YouTube требует подтверждение «я не бот». Cloud серверда YouTube учун ҳам керак:  cookies.txt, экспортированный из браузера (формат Netscape).",
    },
    
    "yt_403": {
        LANG_UZ: "❌ YouTube 403 Forbidden. Bu odatda cloud/datacenter IP blok ёки cookies eskirganidan bo‘ladi. Cookies.txt ni yangilang (login bo‘lgan brauzerdan eksport), yoki Proxy/VPS (rezident IP) ishlating.",
        LANG_RU: "❌ YouTube 403 Forbidden. Обычно это блокировка cloud/datacenter IP или устаревшие cookies. Обновите cookies.txt (экспорт из залогиненного браузера) или используйте Proxy/VPS (резидентный IP).",
    },

    "yt_botcheck_even_with_cookies": {
        LANG_UZ: "❌ YouTube «men robot emasman» tekshiruvini so‘radi. Cookies топилган бўлса ҳам cloud/IP блок сабабли baribir captcha chiqishi mumkin. Cookies.txt ni yangilang (login bo‘lgan brauzerdan), yoki VPS/Proxy (rezident IP) ishlating.",
        LANG_RU: "❌ YouTube просит подтверждение «я не бот». Ҳатто cookies билан ҳам cloud (datacenter IP) капча может появляться. Обновите cookies.txt (из залогиненного браузера) или используйте VPS/Proxy (резидентный IP).",
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
                log.warning("DATABASE_URL topilmadi — fallback: users.json ishlatiladi (cloud серверда тавсия этилмайди).")
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


def human_mb_compact(num_bytes: Optional[int]) -> Optional[str]:
    if not num_bytes or num_bytes <= 0:
        return None
    mb = num_bytes / (1024 * 1024)
    if mb >= 10:
        return f"{mb:.0f}MB"
    return f"{mb:.1f}MB"

def human_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "-"
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:d}:{ss:02d}"

def is_tiktok(url: str) -> bool:
    return "tiktok.com" in (url or "").lower()

def is_tiktok_photo(url: str) -> bool:
    u = (url or "").lower()
    return ("tiktok.com" in u) and ("/photo/" in u)


def _strip_query(url: str) -> str:
    """Remove query params/fragments for more stable matching."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return url


def _resolve_final_url(url: str, timeout: float = 6.0) -> str:
    """Follow redirects (useful for vt.tiktok.com short links)."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = getattr(resp, "geturl", lambda: url)()
            return final or url
    except Exception:
        return url

def _estimate_bytes_from_kbps(kbps: Optional[float], duration_s: Optional[float]) -> int:
    if not kbps or not duration_s or kbps <= 0 or duration_s <= 0:
        return 0
    # kbps -> bytes
    return int((kbps * 1000 / 8) * duration_s)

def _best_audio_size_bytes(info: Dict[str, Any]) -> int:
    formats = info.get("formats") or []
    dur = info.get("duration")
    auds = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"]
    if not auds:
        return 0

    def score(a: Dict[str, Any]) -> Tuple[float, int]:
        abr = float(a.get("abr") or 0.0)
        tbr = float(a.get("tbr") or 0.0)
        # prefer m4a, then higher bitrate
        ext = (a.get("ext") or "").lower()
        ext_score = 2 if ext == "m4a" else (1 if ext in ("mp4", "aac") else 0)
        return (ext_score * 1000 + max(abr, tbr), int(a.get("filesize") or a.get("filesize_approx") or 0))

    best = sorted(auds, key=score, reverse=True)[0]
    sz = int(best.get("filesize") or best.get("filesize_approx") or 0)
    if sz > 0:
        return sz
    kbps = float(best.get("tbr") or best.get("abr") or 0.0)
    return _estimate_bytes_from_kbps(kbps, dur)

def _video_total_size_bytes(info: Dict[str, Any], f: Dict[str, Any]) -> int:
    dur = info.get("duration")
    sz = int(f.get("filesize") or f.get("filesize_approx") or 0)
    if sz <= 0:
        kbps = float(f.get("tbr") or 0.0)
        sz = _estimate_bytes_from_kbps(kbps, dur)
    # If this format has no audio, add best audio size for display
    if (f.get("acodec") == "none") or not f.get("acodec"):
        sz += _best_audio_size_bytes(info)
    return sz

def _pick_best_thumbnail_url(info: Dict[str, Any]) -> Optional[str]:
    # yt-dlp may provide 'thumbnail' and list 'thumbnails'
    t = info.get("thumbnail")
    if t:
        return t
    thumbs = info.get("thumbnails") or []
    if not thumbs:
        return None
    # pick biggest by width/height if present
    def score(x: Dict[str, Any]) -> Tuple[int, int]:
        return (int(x.get("width") or 0), int(x.get("height") or 0))
    best = sorted(thumbs, key=score, reverse=True)[0]
    return best.get("url")


def _cache_put(payload: Dict[str, Any]) -> str:
    token = secrets.token_urlsafe(8)[:10]
    if len(CALLBACK_CACHE) >= CALLBACK_CACHE_MAX:
        for k in list(CALLBACK_CACHE.keys())[: CALLBACK_CACHE_MAX // 2]:
            CALLBACK_CACHE.pop(k, None)
    CALLBACK_CACHE[token] = payload
    return token

def _cache_get(token: str) -> Optional[Dict[str, Any]]:
    return CALLBACK_CACHE.get(token)


def _friendly_ydl_error(e: Exception, lang: str) -> str:
    """Minimal, user-friendly error text for logs from yt-dlp / download."""
    s = str(e)
    s_low = s.lower()

    # YouTube bot-check patterns
    if "sign in to confirm you’re not a bot" in s_low or "confirm you’re not a bot" in s_low:
        # Cookies bor-yo‘qligini taxmin qilamiz
        if (os.getenv("YT_COOKIES_B64") or os.getenv("YT_COOKIES_FILE")):
            return _t(lang, "yt_botcheck_even_with_cookies")
        return _t(lang, "yt_need_cookies")

    # 403 Forbidden (ko‘pincha YouTube cloud/IP blok)
    if "http error 403" in s_low or "403 forbidden" in s_low:
        return _t(lang, "yt_403")

    if "unsupported url" in s_low:
        return s

    if "filename too long" in s_low:
        return _t(lang, "err_filename_too_long")

    # Default: qisqa qilib qaytaramiz
    if len(s) > 250:
        s = s[:247] + "..."
    return s




# ---------------------------- yt-dlp cookies helpers ----------------------------

_COOKIEFILE_PATH: Optional[str] = None
_COOKIE_LOGGED: bool = False

def _ensure_cookiefile(workdir: Optional[str] = None) -> Optional[str]:
    """Prepare a **writable** cookies.txt for yt-dlp and return its path.

    Important: do NOT reuse the same temp cookies path across concurrent requests.
    yt-dlp may update cookies on exit, and parallel runs can corrupt a shared file.
    So we create a fresh temp file per call.

    Sources:
    - YT_COOKIES_B64: base64 of cookies.txt
    - YT_COOKIES_FILE: path to cookies.txt (e.g. /etc/secrets/cookies.txt)
    """
    def _dst_path() -> str:
        base_dir = workdir if workdir else tempfile.gettempdir()
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, f"yt_cookies_{uuid.uuid4().hex}.txt")

    def _warn_if_suspicious(path: str) -> None:
        try:
            sz = os.path.getsize(path)
            if sz <= 0:
                log.warning("YT cookies file is empty: %s", path)
                return
            with open(path, "rb") as f:
                head = f.read(256)
            head_txt = head.decode("utf-8", errors="ignore").strip()
            if head_txt and ("Netscape" not in head_txt) and ("# HTTP Cookie File" not in head_txt):
                log.warning("YT cookies file may be in a non-Netscape format: %s", path)
        except Exception:
            pass

    # 1) Base64 variant
    b64 = (os.getenv("YT_COOKIES_B64") or "").strip()
    if b64:
        # If user pasted the PowerShell command instead of output, ignore.
        if ("[Convert]::ToBase64String" in b64) or ("ReadAllBytes" in b64):
            log.warning("YT_COOKIES_B64 qiymati base64 emas (buyruq matni ko‘rinadi). Uni o‘chirib tashlang yoki haqiqiy base64 natijani kiriting.")
        else:
            try:
                import base64
                clean = re.sub(r"\s+", "", b64)
                # Fix missing padding
                pad = (-len(clean)) % 4
                if pad:
                    clean += "=" * pad
                data = base64.b64decode(clean.encode("ascii"), validate=False)
                tmp_path = _dst_path()
                with open(tmp_path, "wb") as f:
                    f.write(data)
                _warn_if_suspicious(tmp_path)
                log.info("YT cookies (b64) tayyor: %s (exists=%s, size=%s)", tmp_path, os.path.exists(tmp_path), os.path.getsize(tmp_path))
                return tmp_path
            except Exception as e:
                log.warning("YT_COOKIES_B64 decode xatosi: %s", e)

    # 2) File path variant
    src = (os.getenv("YT_COOKIES_FILE") or "").strip()
    candidates: list[str] = []
    if src:
        candidates.append(src)
        candidates.append(os.path.join("/etc/secrets", os.path.basename(src)))
        candidates.append(os.path.basename(src))
    candidates += ["/etc/secrets/cookies.txt", "/etc/secrets/Cookies.txt", "cookies.txt", "Cookies.txt"]

    src_path = None
    for p in candidates:
        try:
            if p and os.path.exists(p) and os.path.getsize(p) > 0:
                src_path = p
                break
        except Exception:
            continue

    if not src_path:
        if src:
            log.warning("YT_COOKIES_FILE topildi, lekin fayl yo'q: %s", src)
        return None

    try:
        tmp_path = _dst_path()
        shutil.copyfile(src_path, tmp_path)
        _warn_if_suspicious(tmp_path)
        log.info("YT cookies (file) tayyor: %s (exists=%s, size=%s, src=%s)", tmp_path, os.path.exists(tmp_path), os.path.getsize(tmp_path), src_path)
        return tmp_path
    except Exception as e:
        log.warning("YT cookies copy xatosi: %s", e)
        return None



def _normalize_proxy(raw: str) -> Optional[str]:
    """Validate and normalize proxy string from env.
    Accepts: http(s)://user:pass@host:port , socks5://host:port , etc.
    Returns normalized proxy URL or None if invalid.
    """
    if not raw:
        return None
    p = raw.strip()
    if not p:
        return None
    # If scheme missing, assume http
    if "://" not in p:
        p = "http://" + p
    try:
        u = urlparse(p)
        if u.scheme not in ("http", "https", "socks5", "socks5h"):
            return None
        # urlparse raises ValueError for bad port in py3.13 sometimes when accessing .port
        host = u.hostname
        if not host:
            return None
        try:
            port = u.port
        except Exception:
            return None
        if port is None:
            return None
    except Exception:
        return None
    return p

def build_ydl_base(outtmpl: str, workdir: Optional[str] = None) -> Dict[str, Any]:
    opts = {
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


    # Cookies (YouTube datacenter bloklari uchun foydali)
    cookiefile = _ensure_cookiefile(workdir)
    if cookiefile:
        opts["cookiefile"] = cookiefile

    # YouTube extractor: ba'zan mobile client yumshoqroq ishlaydi
    opts.setdefault("extractor_args", {})
    opts["extractor_args"].setdefault("youtube", {})
    opts["extractor_args"]["youtube"].setdefault("player_client", ["android", "ios", "web"])

    # HTTP headers (User-Agent / Accept-Language)
    opts.setdefault("http_headers", {})
    ua = (os.getenv("YTDLP_UA") or "").strip()
    if ua:
        opts["http_headers"]["User-Agent"] = ua
    else:
        # default browser UA
        opts["http_headers"].setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
    opts["http_headers"].setdefault("Accept-Language", "en-US,en;q=0.9")
    opts["http_headers"].setdefault("Referer", "https://www.youtube.com/")


    # Impersonate (ixtiyoriy): YTDLP_IMPERSONATE=chrome|chrome-124:windows-10|safari|...
    # Yangi yt-dlp (2026+) Python API'da opts["impersonate"] satri endi str emas, ImpersonateTarget bo‘lishi kerak.
    imp = (os.getenv("YTDLP_IMPERSONATE") or "").strip()
    if imp:
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget  # type: ignore
            opts["impersonate"] = ImpersonateTarget.from_str(imp.lower())
        except Exception as e:
            # Agar kutubxona/target mos kelmasa, bot yiqilib qolmasligi uchun impersonate'ni o‘chirib yuboramiz.
            log.warning("Impersonate sozlamasi o‘chirildi (xato: %s). YTDLP_IMPERSONATE=%s", e, imp)
    # Proxy (ixtiyoriy): YTDLP_PROXY=http://user:pass@host:port
    proxy_raw = (os.getenv("YTDLP_PROXY") or "").strip()
    proxy = _normalize_proxy(proxy_raw)
    if proxy:
        opts["proxy"] = proxy
    elif proxy_raw:
        # noto‘g‘ri proxy bo‘lsa, bot yiqilmasin — proxy’ni e'tiborsiz qoldiramiz
        log.warning("YTDLP_PROXY noto‘g‘ri formatda, e'tiborsiz qoldirildi: %s", proxy_raw)


    # ffmpeg (merge/MP3 uchun) — Railway/Render'да PATH'da bo'lishi mumkin
    try:
        ff = shutil.which('ffmpeg')
        if ff:
            opts['ffmpeg_location'] = ff
    except Exception:
        pass

    return opts

def _extract_info(url: str) -> Dict[str, Any]:
    # Formatlarni ko‘rsatish uchun to‘liq "process=True" kerak bo‘ladi,
    # aks holda ba'zan faqat audio ko‘rinib qoladi.
    ydl_opts = build_ydl_base(outtmpl="%(title)s.%(ext)s", workdir=tempfile.gettempdir())
    ydl_opts["ignore_no_formats_error"] = True
    ydl_opts["skip_download"] = True
    # Format ro'yxatini olishda "web" client ko'proq formatlarni qaytaradi.
    try:
        ydl_opts.setdefault("extractor_args", {})
        ydl_opts["extractor_args"].setdefault("youtube", {})
        ydl_opts["extractor_args"]["youtube"]["player_client"] = ["web", "android", "ios"]
    except Exception:
        pass
    try:
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        msg = str(e)
        if "Impersonate target" in msg and "not available" in msg:
            # Railway/host muhitida curl-cffi yoki kerakli handler bo‘lmasa, impersonate target mavjud bo‘lmay qoladi.
            ydl_opts.pop("impersonate", None)
            log.warning("Impersonate o‘chirildi (mavjud emas): %s", msg)
            with YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
        raise

def _select_youtube_formats(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return a small curated list of YouTube video formats for buttons.

    We show ONLY these labels (if available): 144/240/360/480/720.
    Important: some videos have "almost" heights (e.g. 358 instead of 360),
    so we pick the best format within a tolerance band below each target and
    store the target label in f["_label_h"].
    """
    formats = info.get("formats") or []
    vids = [f for f in formats if f.get("vcodec") != "none" and f.get("height")]

    # group by height
    by_h: Dict[int, List[Dict[str, Any]]] = {}
    for f in vids:
        try:
            h = int(f.get("height"))
        except Exception:
            continue
        by_h.setdefault(h, []).append(f)

    desired = [144, 240, 360, 480, 720]
    # allow slight deviations (some uploads are 358/478/etc.)
    tol_map = {144: 40, 240: 50, 360: 60, 480: 80, 720: 140}

    def score(x: Dict[str, Any]) -> Tuple[int, float, int]:
        # prefer mp4, then higher bitrate, then known filesize
        ext = (x.get("ext") or "").lower()
        ext_score = 2 if ext == "mp4" else (1 if ext in ("mkv", "webm") else 0)
        tbr = float(x.get("tbr") or 0.0)
        fs = int(x.get("filesize") or x.get("filesize_approx") or 0)
        return (ext_score, tbr, fs)

    picked: List[Dict[str, Any]] = []
    used_ids: set[str] = set()

    all_heights = sorted(by_h.keys())

    for target in desired:
        tol = tol_map.get(target, 60)
        lo = max(0, target - tol)
        hi = target

        # pick candidate heights within [lo, hi]
        hs = [h for h in all_heights if lo <= h <= hi]
        if not hs:
            # as a fallback, pick the closest lower-or-equal height
            hs = [h for h in all_heights if h <= target]
        if not hs:
            continue

        # choose the height closest to target (prefer higher), then best score within that height
        best_h = sorted(hs, key=lambda h: (h, -abs(target - h)), reverse=True)[0]
        cand = by_h.get(best_h) or []
        if not cand:
            continue
        best = sorted(cand, key=score, reverse=True)[0]

        fid = str(best.get("format_id") or "")
        if not fid or fid in used_ids:
            continue
        used_ids.add(fid)

        # store label height for UI
        best["_label_h"] = target
        picked.append(best)

    # absolute fallback: show up to 3 best formats up to 720p
    if not picked:
        vids_sorted = sorted(
            [v for v in vids if int(v.get("height") or 0) <= 720],
            key=lambda x: float(x.get("tbr") or 0.0),
            reverse=True,
        )[:3]
        for v in vids_sorted:
            try:
                v["_label_h"] = int(v.get("height") or 0)
            except Exception:
                v["_label_h"] = 0
        picked = vids_sorted

    return picked


def _download_video(url: str, format_id: Optional[str], workdir: str) -> Path:
    outtmpl = os.path.join(workdir, "%(id)s.%(ext)s")

    def _run_with_opts(opts: Dict[str, Any]) -> Path:
        def _do(local_opts: Dict[str, Any]) -> Path:
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

        try:
            return _do(opts)
        except Exception as e:
            msg = str(e)
            if "Impersonate target" in msg and "not available" in msg:
                opts.pop("impersonate", None)
                log.warning("Impersonate o‘chirildi (mavjud emas): %s", msg)
                return _do(opts)
            raise


    ydl_opts = build_ydl_base(outtmpl=outtmpl, workdir=workdir)

    if format_id:
        # Special pseudo format: "h:720" means request max height <= 720
        if isinstance(format_id, str) and format_id.lower().startswith("h:"):
            try:
                hmax = int(format_id.split(":", 1)[1])
            except Exception:
                hmax = 360
            # Prefer mp4 video + m4a audio, fallback to best within height cap
            ydl_opts["format"] = (
                f"bv*[height<={hmax}][ext=mp4]+ba[ext=m4a]/"
                f"bv*[height<={hmax}]+ba/"
                f"b[height<={hmax}][ext=mp4]/b[height<={hmax}]/best"
            )
            ydl_opts["merge_output_format"] = "mp4"
        else:
            # Exact itag / format_id
            ydl_opts["format"] = f"{format_id}+bestaudio/{format_id}/best"
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
    outtmpl = os.path.join(workdir, "%(id)s.%(ext)s")

    ydl_opts = build_ydl_base(outtmpl=outtmpl, workdir=workdir)
    ydl_opts["format"] = "bestaudio/best"
    ydl_opts["postprocessors"] = [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "192",
    }]
    try:
        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:
            msg = str(e)
            if "Impersonate target" in msg and "not available" in msg:
                ydl_opts.pop("impersonate", None)
                log.warning("Impersonate o‘chirildi (mavjud emas): %s", msg)
                with YoutubeDL(ydl_opts) as ydl:
                    ydl.extract_info(url, download=True)
            else:
                raise
        mp3s = sorted(Path(workdir).glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
        if mp3s:
            return mp3s[0]
    except Exception as e:
        log.warning("MP3 konvertatsiya muvaffaqiyatsiz (ffmpeg yo'q bo'lishi mumkin). Fallback audio: %s", e)

    ydl_opts2 = build_ydl_base(outtmpl=outtmpl, workdir=workdir)
    ydl_opts2["format"] = "bestaudio/best"
    try:
        with YoutubeDL(ydl_opts2) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        msg = str(e)
        if "Impersonate target" in msg and "not available" in msg:
            ydl_opts2.pop("impersonate", None)
            log.warning("Impersonate o‘chirildi (mavjud emas): %s", msg)
            with YoutubeDL(ydl_opts2) as ydl:
                info = ydl.extract_info(url, download=True)
        else:
            raise
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
        parse_mode=ParseMode.HTML,
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
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=markup,
        )
    except Exception:
        # Agar edit bo'lmasa, yangi xabar yuboramiz
        try:
            await context.bot.send_message(
                chat_id=q.message.chat_id if q.message else update.effective_chat.id,
                text=start_text_by_lang(lang),
                parse_mode=ParseMode.HTML,
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

    # TikTok short links (vt.tiktok.com/...) ni to‘liq URL ga yechib olamiz,
    # shunda /photo/ postlarni to‘g‘ri aniqlash mumkin.
    url_eff = url
    if is_tiktok(url):
        u_low = url.lower()
        if any(x in u_low for x in ("vt.tiktok.com", "vm.tiktok.com", "tiktok.com/t/")):
            loop = asyncio.get_running_loop()
            url_eff = await loop.run_in_executor(None, _resolve_final_url, url)
        url_eff = _strip_query(url_eff)

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
        # TikTok photo-post (/photo/) — bu turda faqat audio (MP3) taklif qilamiz
        if is_tiktok_photo(url_eff):
            token_p = _cache_put({
                "url": url_eff, "kind": "tt_photo_audio", "format_id": None,
                "origin_chat_id": origin_chat_id, "origin_message_id": origin_message_id,
                "lang": lang,
            })
            kb = [[InlineKeyboardButton(_t(lang, "btn_mp3"), callback_data=f"dl|{token_p}")]]
            await update.message.reply_text(_t(lang, "tt_photo_audio_only"), reply_markup=InlineKeyboardMarkup(kb))
            return

        url_for_dl = url_eff if is_tiktok(url) else url

        kb = []
        t_v = _cache_put({
            "url": url_for_dl, "kind": "video", "format_id": None,
            "origin_chat_id": origin_chat_id, "origin_message_id": origin_message_id,
            "lang": lang,
        })
        t_a = _cache_put({
            "url": url_for_dl, "kind": "audio", "format_id": None,
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

        # Agar yt-dlp faqat bitta video format qaytarsa (ko'pincha 360p atrofida),
        # UI baribir 144/240/360/480/720 variantlarni ko'rsatadi.
        # Bu variantlar "h:XXX" pseudo format bo'lib, yuklash paytida height cap sifatida ishlatiladi.
        if not formats or len(formats) < 2:
            formats = []
            for h in (144, 240, 360, 480, 720):
                formats.append({"format_id": f"h:{h}", "height": h, "_label_h": h})

        # Buttonlar: faqat 144/240/360/480/720 (mavjud bo‘lsa)
        btns: List[InlineKeyboardButton] = []
        for f in sorted(formats, key=lambda x: int(x.get("height") or 0), reverse=True):
            fmt_id = str(f.get("format_id"))
            h = int(f.get("height") or 0)
            label_h = int(f.get("_label_h") or h)

            total_bytes = _video_total_size_bytes(info, f)
            size = human_mb_compact(total_bytes)
            label = f"{label_h}p - {size}" if size else f"{label_h}p"

            token = _cache_put({
                "url": url, "kind": "video", "format_id": fmt_id,
                "origin_chat_id": origin_chat_id, "origin_message_id": origin_message_id,
                "lang": lang,
            })
            btns.append(InlineKeyboardButton(label, callback_data=f"dl|{token}"))

        # 2-column layout (rasmdagidek)
        kb: List[List[InlineKeyboardButton]] = []
        for i in range(0, len(btns), 2):
            kb.append(btns[i:i+2])

        token_a = _cache_put({
            "url": url, "kind": "audio", "format_id": None,
            "origin_chat_id": origin_chat_id, "origin_message_id": origin_message_id,
            "lang": lang,
        })
        kb.append([InlineKeyboardButton("🎵 MP3", callback_data=f"dl|{token_a}")])

        # Placeholder "formatlar olinmoqda" xabarini o‘chirib, oblojka (thumbnail) bilan yuboramiz
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

        title_raw = (info.get("title") or "YouTube").strip()
        # Caption limit uchun title’ni qisqartiramiz
        if len(title_raw) > 200:
            title_raw = title_raw[:197] + "..."
        title = html.escape(title_raw)
        dur = human_duration(info.get("duration"))

        caption = _t(lang, "yt_caption", title=title, dur=dur)
        thumb_url = _pick_best_thumbnail_url(info)

        try:
            if thumb_url:
                await context.bot.send_photo(
                    chat_id=origin_chat_id,
                    photo=thumb_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb),
                    reply_to_message_id=origin_message_id,
                )
            else:
                await context.bot.send_message(
                    chat_id=origin_chat_id,
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb),
                    reply_to_message_id=origin_message_id,
                )
        except Exception:
            # Thumbnail yuborilmasa ham — text bilan yuboramiz
            await context.bot.send_message(
                chat_id=origin_chat_id,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(kb),
                reply_to_message_id=origin_message_id,
            )

    except Exception as e:
        log.exception("Formatlarni olishda xato: %s", e)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=_t(lang, "fmt_error", err=_friendly_ydl_error(e, lang)),
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
            # Eski tugma
            if q.message:
                await q.edit_message_text(_t(lang, "btn_expired"))
        except Exception:
            pass
        return

    # Payload topildi — endi format menyusini (tugmalar) xabarini avtomat o‘chirib yuboramiz
    try:
        if q.message is not None:
            await context.bot.delete_message(chat_id=q.message.chat_id, message_id=q.message.message_id)
    except Exception:
        pass

    url = payload["url"]
    kind = payload["kind"]
    format_id = payload.get("format_id")
    lang = payload.get("lang") or lang

    origin_chat_id = int(payload.get("origin_chat_id") or (q.message.chat_id if q.message else update.effective_chat.id))
    origin_message_id = payload.get("origin_message_id")
    reply_to_message_id = int(origin_message_id) if str(origin_message_id).isdigit() else None

    # "⏳ ..." ogohlantirishni alohida yuboramiz va yuklab bo‘lganda o‘chirib tashlaymiz
    status_chat_id: Optional[int] = None
    status_message_id: Optional[int] = None
    try:
        m = await context.bot.send_message(
            chat_id=origin_chat_id,
            text=_t(lang, "downloading_wait"),
            reply_to_message_id=reply_to_message_id,
        )
        status_chat_id = m.chat_id
        status_message_id = m.message_id
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
        status_chat_id=status_chat_id,
        status_message_id=status_message_id,
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

async def _send_document_with_retry(
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
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    caption=caption,
                    reply_to_message_id=reply_to_message_id,
                )
            return
        except TimedOut as e:
            last_exc = e
            await asyncio.sleep(2)
    if last_exc:
        raise last_exc


def _download_tiktok_photos_zip(url: str, workdir: str) -> Path:
    """Download TikTok /photo/ post images with gallery-dl and pack into ZIP."""
    outdir = Path(workdir) / "tiktok_photos"
    outdir.mkdir(parents=True, exist_ok=True)

    # gallery-dl CLI (pip orqali o‘rnatiladi). requirements.txt ga: gallery-dl
    try:
        subprocess.run(
            ["gallery-dl", "-D", str(outdir), url],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        raise RuntimeError("gallery-dl topilmadi. requirements.txt ga 'gallery-dl' qo‘shing va redeploy qiling.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"gallery-dl xato: {e.stderr.strip()[:300] if e.stderr else e}")

    imgs: List[Path] = []
    for p in outdir.rglob("*"):
        if p.is_file() and p.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
            imgs.append(p)

    if not imgs:
        raise RuntimeError("TikTok foto topilmadi (ehtimol captcha/blok).")

    zip_path = Path(workdir) / "tiktok_photos.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(imgs):
            z.write(p, arcname=p.name)

    return zip_path


def _download_tiktok_photo_audio(url: str, workdir: str) -> Path:
    """Best-effort: TikTok /photo/ postdan audio (MP3) chiqarib beradi.

    1) /photo/ID -> /video/ID ko‘rinishiga aylantirib yt-dlp orqali audio
    2) Agar bo‘lmasa, gallery-dl orqali medialarni tushirib, eng katta mp4/m4a dan audio ajratadi.
    """
    clean = _strip_query(url)
    # 1) Urinib ko‘ramiz: /photo/<id> -> /video/<id>
    video_variant = re.sub(r"/photo/([0-9]+)/?$", r"/video/\1", clean)

    try:
        return _download_audio(video_variant, workdir)
    except Exception as e1:
        # ba'zi hollarda original URL ham ishlashi mumkin
        try:
            return _download_audio(clean, workdir)
        except Exception:
            pass

        # 2) Fallback: gallery-dl
        outdir = Path(workdir) / "tiktok_media"
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["gallery-dl", "-D", str(outdir), clean],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except FileNotFoundError:
            # e1 ni yo‘qotmaslik uchun kerakli hint beramiz
            raise RuntimeError(
                "TikTok foto-post audio uchun 'gallery-dl' kerak. requirements.txt ga 'gallery-dl' qo‘shing va redeploy qiling. "
                f"Asl xato: {str(e1)[:200]}"
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"gallery-dl xato: {e.stderr.strip()[:300] if e.stderr else e}")

        candidates: List[Path] = []
        for p in outdir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in [".m4a", ".mp3", ".aac", ".ogg", ".webm", ".mp4"]:
                candidates.append(p)

        if not candidates:
            raise RuntimeError("TikTok media topilmadi (ehtimol captcha/blok).")

        src = max(candidates, key=lambda p: p.stat().st_size)
        if src.suffix.lower() in [".mp3", ".m4a", ".aac", ".ogg"]:
            return src

        # mp4/webm bo‘lsa, audio ajratamiz
        out_mp3 = Path(workdir) / "tiktok_audio.mp3"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            # ffmpeg yo‘q bo‘lsa, bor formatni qaytaramiz (Telegram audio sifatida ham yuboriladi)
            return src

        try:
            subprocess.run(
                [ffmpeg, "-y", "-i", str(src), "-vn", "-acodec", "libmp3lame", "-b:a", "192k", str(out_mp3)],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            return out_mp3
        except subprocess.CalledProcessError:
            # oxirgi urinish: audio streamni copy qilib ko‘ramiz
            out_m4a = Path(workdir) / "tiktok_audio.m4a"
            subprocess.run(
                [ffmpeg, "-y", "-i", str(src), "-vn", "-c:a", "copy", str(out_m4a)],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            return out_m4a




async def _task_download_and_send(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    reply_to_message_id: Optional[int],
    url: str,
    kind: str,
    format_id: Optional[str],
    lang: str,
    status_chat_id: Optional[int] = None,
    status_message_id: Optional[int] = None,
) -> None:
    loop = asyncio.get_running_loop()
    try:
        with tempfile.TemporaryDirectory(prefix="dlbot_") as td:
            caption = _t(lang, "caption_suffix")

            if kind == "audio":
                path: Path = await loop.run_in_executor(None, _download_audio, url, td)
                await _send_audio_with_retry(context, chat_id, path, caption, reply_to_message_id)

            elif kind == "tt_photo_audio":
                path = await loop.run_in_executor(None, _download_tiktok_photo_audio, url, td)
                await _send_audio_with_retry(context, chat_id, path, caption, reply_to_message_id)

            else:
                path = await loop.run_in_executor(None, _download_video, url, format_id, td)
                await _send_video_with_retry(context, chat_id, path, caption, reply_to_message_id)

    except Exception as e:
        log.exception("Download/send xato: %s", e)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_t(lang, "err_generic", err=_friendly_ydl_error(e, lang)),
                reply_to_message_id=reply_to_message_id,
            )
        except Exception:
            pass
    finally:
        # "⏳ ..." ogohlantirishini o‘chiramiz
        if status_chat_id and status_message_id:
            try:
                await context.bot.delete_message(chat_id=status_chat_id, message_id=status_message_id)
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
