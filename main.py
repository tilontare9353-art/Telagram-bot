
from telegram import Chat, Message, Update, BotCommand, BotCommandScopeAllPrivateChats, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, ContextTypes, filters

import threading
import os
import re
import html
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Flask

try:
    from waitress import serve  # production-grade WSGI server (Railway uchun tavsiya)
except Exception:
    serve = None

# --- New (Postgres) ---
import asyncio
import json
import ssl
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from typing import List, Optional

try:
    import asyncpg
except ImportError:
    asyncpg = None  # handled below with a log warning


# ---------------------- Linked channel helpers ----------------------
def _extract_forward_origin_chat(msg: Message):
    fo = getattr(msg, "forward_origin", None)
    if fo is not None:
        chat = getattr(fo, "chat", None) or getattr(fo, "from_chat", None)
        if chat is not None:
            return chat
    return getattr(msg, "forward_from_chat", None)


# ---- Linked channel cache helpers (added) ----
_GROUP_LINKED_ID_CACHE: dict[int, int | None] = {}

async def _get_linked_id(chat_id: int, bot) -> int | None:
    """Fetch linked_chat_id reliably using get_chat (cached)."""
    if chat_id in _GROUP_LINKED_ID_CACHE:
        return _GROUP_LINKED_ID_CACHE[chat_id]
    try:
        chat = await bot.get_chat(chat_id)
        linked_id = getattr(chat, "linked_chat_id", None)
        _GROUP_LINKED_ID_CACHE[chat_id] = linked_id
        return linked_id
    except Exception:
        _GROUP_LINKED_ID_CACHE[chat_id] = None
        return None

async def is_linked_channel_autoforward(msg: Message, bot) -> bool:
    """
    TRUE faqat guruhning bog'langan kanalidan avtomatik forward bo'lgan postlar uchun.
    - msg.is_automatic_forward True
    - get_chat(chat_id).linked_chat_id mavjud
    - va (sender_chat.id == linked_id) yoki (forward_origin chat.id == linked_id)
    - origin yashirilgan bo‘lsa ham fallback True (is_automatic_forward bo‘lsa)
    """
    try:
        if not getattr(msg, "is_automatic_forward", False):
            return False
        linked_id = await _get_linked_id(msg.chat_id, bot)
        if not linked_id:
            return False
        sc = getattr(msg, "sender_chat", None)
        if sc and getattr(sc, "id", None) == linked_id:
            return True
        fwd_chat = _extract_forward_origin_chat(msg)
        if fwd_chat and getattr(fwd_chat, "id", None) == linked_id:
            return True
        # Fallback: origin yashirilgan bo‘lishi mumkin
        return True
    except Exception:
        return False


# ---------------------- Small keep-alive web server ----------------------
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "Bot ishlayapti!"

def run_web():
    port = int(os.getenv("PORT", "8080"))
    if serve:
        serve(app_flask, host="0.0.0.0", port=port)
    else:
        # Fallback: Flask dev server (agar waitress o'rnatilmagan bo'lsa)
        app_flask.run(host="0.0.0.0", port=port)

def start_web():
    # Railway "web" service uchun PORT talab qilinadi.
    # Agar siz botni "worker" sifatida ishga tushirsangiz, ENABLE_WEB=0 qilib qo'ying.
    enable = os.getenv("ENABLE_WEB")
    if enable is None:
        enable = "1" if os.getenv("PORT") else "0"
    if str(enable).strip() in ("1", "true", "True", "yes", "YES"):
        threading.Thread(target=run_web, daemon=True).start()


# ---------------------- Config ----------------------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN env o'rnatilmagan. Railway Variables ga TOKEN=... qo'ying.")
WHITELIST = {165553982, "Yunus1995"}
TUN_REJIMI = False
KANAL_USERNAME = None

MAJBUR_LIMIT = 0
FOYDALANUVCHI_HISOBI = defaultdict(int)
RUXSAT_USER_IDS = set()
BLOK_VAQTLARI = {}  # (chat_id, user_id) -> until_datetime (UTC)
MAJBUR_WARN_MSG_IDS = {}  # (chat_id, user_id) -> last warning message_id
KANAL_WARN_MSG_IDS = {}   # (chat_id, user_id) -> last warning message_id

# ✅ To'liq yozish ruxsatlari (guruh sozlamalari ruxsat bergan taqdirda)
FULL_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)

# Blok uchun ruxsatlar (1 daqiqa): faqat yozish yopiladi, odam qo'shishga ruxsat qoldiriladi
BLOCK_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_invite_users=True,
)

# So'kinish lug'ati (qisqartirilgan, aslidagi ro'yxat saqlandi)
UYATLI_SOZLAR = {
    # --- Latin (alfavit) ---
    "am",
    "am latta",
    "am yalayman",
    "am yaliman",
    "aminga",
    "aminga sikay",
    "aminga ske",
    "amlar",
    "amlatta",
    "ammisan",
    "ammisan?",
    "ammisizlar",
    "ammisizlar?",
    "ammislar",
    "ammislar?",
    "amsan",
    "amxor",
    "amyalaq",
    "amyalar",
    "amyaloq",
    "biyindi ami",
    "biyundiami",
    "blya",
    "blyat",
    "buyindi omi",
    "buyingdi ami",
    "buyingdi omi",
    "buyingni ami",
    "buynami",
    "buyundiomi",
    "chochaq",
    "chochoq",
    "dalbayob",
    "dalbayoblar",
    "dalbayobmisan",
    "dalbayobmisan?",
    "debil",
    "dolboyob",
    "durak",
    "fakyou",
    "fohisha",
    "fohishalar",
    "fohishamisan?",
    "fohishasan",
    "foxisha",
    "foxishalar",
    "foxishamisan?",
    "foxishasan",
    "fuck",
    "fuckyou",
    "g'ar",
    "gandon",
    "gandonlar",
    "gandonmisan",
    "gandonmisan?",
    "haromi",
    "haromilar",
    "horomi",
    "horomilar",
    "huy",
    "huygami",
    "huyimga",
    "idin naxuy",
    "idin naxxuy",
    "idinaxxuy",
    "idinnaxxuy",
    "isqirt",
    "isqirtlar",
    "isqirtsan",
    "jalap",
    "jalapkot",
    "jalapkoz",
    "jalaplar",
    "jalapsan",
    "ko't",
    "ko'tak",
    "ko'tinga",
    "ko'tlar",
    "ko'tmisan",
    "ko'tsan",
    "kot",
    "kotagim",
    "kotak",
    "kotinga",
    "kotinga sikay",
    "kotinga ske",
    "kotingaske",
    "kotingga",
    "kotlar",
    "kotmisan",
    "kotmisan?",
    "kotmisizlar",
    "kotmisizlar?",
    "kotmislar",
    "kotmislar?",
    "kotsan",
    "kotvacha",
    "kutagim",
    "lanat",
    "lanati",
    "lanatilar",
    "lanatisan",
    "mudak",
    "naxxuy",
    "og'zinga skay",
    "og'zinga skey",
    "og'zingaskay",
    "ogzinga skay",
    "ogzinga skey",
    "ogzingaskay",
    "onagni ami",
    "onagni omi",
    "onagniomi",
    "onangniami",
    "otni qotagi",
    "otti qo'tag'i",
    "otti qotagi",
    "padarlanat",
    "padarlanatlar",
    "padarlanatsan",
    "pashol naxuy",
    "pasholnaxuy",
    "pasholnaxxuy",
    "pidor",
    "poshol naxxuy",
    "posholnaxxuy",
    "poxuy",
    "poxxuy",
    "qanjik",
    "qanjiq",
    "qanjiqlar",
    "qanjiqsan",
    "qo'tag'im",
    "qo'taq",
    "qo'taqxo'r",
    "qo'tog'lar",
    "qo'toqlar",
    "qonjiq",
    "qotag'im",
    "qotagim",
    "qotaq",
    "qotaqlar",
    "qotaqmisan",
    "qotaqsan",
    "qotaqxor",
    "qotoglar",
    "qotoqlar",
    "sik",
    "sikalak",
    "sikaman",
    "sikasizmi",
    "sikay",
    "sikey",
    "sikish",
    "sikishaman",
    "sikishamiz",
    "sikishish",
    "skaman",
    "skasizmi",
    "skay",
    "skey",
    "skishaman",
    "skishamiz",
    "skishamizmi?",
    "skiy",
    "soska",
    "suka",
    "sukalar",
    "tashak",
    "tashaklar",
    "tashaq",
    "tashaqlar",
    "toshok",
    "toshoq",
    "toshoqlar",
    "xaromi",
    "xoramilar",
    "xoromi",
    "xoromilar",
    "xuramilar",
    "xuy",
    "xuyna",

    # --- Krill (alfavit) ---
    "ам",
    "ам латта",
    "ам ялайман",
    "ам ялиман",
    "аминга",
    "аминга сикай",
    "аминга ске",
    "амлар",
    "амлатта",
    "аммисан",
    "аммисан?",
    "аммисизлар",
    "аммисизлар?",
    "аммислар",
    "аммислар?",
    "амсан",
    "амхор",
    "амялар",
    "амялақ",
    "амялоқ",
    "бийинди ами",
    "биюндиами",
    "бля",
    "блят",
    "буйингди ами",
    "буйингди оми",
    "буйингни ами",
    "буйинди оми",
    "буйнами",
    "буюндиоми",
    "гандон",
    "гандонлар",
    "гандонмисан",
    "гандонмисан?",
    "далбаёб",
    "далбаёблар",
    "далбаёбмисан",
    "далбаёбмисан?",
    "дебил",
    "долбоёб",
    "дурак",
    "жалап",
    "жалапкоз",
    "жалапкот",
    "жалаплар",
    "жалапсан",
    "идин нахуй",
    "идин наххуй",
    "идинаххуй",
    "идиннаххуй",
    "исқирт",
    "исқиртлар",
    "исқиртсан",
    "кот",
    "котагим",
    "котак",
    "котвача",
    "котинга",
    "котинга сикай",
    "котинга ске",
    "котингаске",
    "котингга",
    "котлар",
    "котмисан",
    "котмисан?",
    "котмисизлар",
    "котмисизлар?",
    "котмислар",
    "котмислар?",
    "котсан",
    "кутагим",
    "кўт",
    "кўтак",
    "кўтинга",
    "кўтлар",
    "кўтмисан",
    "кўтсан",
    "ланат",
    "ланати",
    "ланатилар",
    "ланатисан",
    "мудак",
    "наххуй",
    "огзинга скай",
    "огзинга скей",
    "огзингаскай",
    "онагни ами",
    "онагни оми",
    "онагниоми",
    "онангниами",
    "отни қотаги",
    "отти қотаги",
    "отти қўтағи",
    "оғзинга скай",
    "оғзинга скей",
    "оғзингаскай",
    "падарланат",
    "падарланатлар",
    "падарланатсан",
    "пашол нахуй",
    "пашолнахуй",
    "пашолнаххуй",
    "пидор",
    "похуй",
    "поххуй",
    "пошол наххуй",
    "пошолнаххуй",
    "сик",
    "сикай",
    "сикалак",
    "сикаман",
    "сикасизми",
    "сикей",
    "сикиш",
    "сикишаман",
    "сикишамиз",
    "сикишиш",
    "скай",
    "скаман",
    "скасизми",
    "скей",
    "скейсикиш",
    "ский",
    "скишаман",
    "скишамиз",
    "скишамизми?",
    "соска",
    "сука",
    "сукалар",
    "ташак",
    "ташаклар",
    "ташақ",
    "ташақлар",
    "тошок",
    "тошоқ",
    "тошоқлар",
    "факёу",
    "фохиша",
    "фохишалар",
    "фохишамисан?",
    "фохишасан",
    "фоҳиша",
    "фоҳишалар",
    "фоҳишамисан?",
    "фоҳишасан",
    "фуcк",
    "фуcкёу",
    "хароми",
    "хорамилар",
    "хороми",
    "хоромилар",
    "хуй",
    "хуйна",
    "хурамилар",
    "чочақ",
    "чочоқ",
    "ғар",
    "қанжик",
    "қанжиқ",
    "қанжиқлар",
    "қанжиқсан",
    "қонжиқ",
    "қотагим",
    "қотағим",
    "қотақ",
    "қотақлар",
    "қотақмисан",
    "қотақсан",
    "қотақхор",
    "қотоглар",
    "қотоқлар",
    "қўтағим",
    "қўтақ",
    "қўтақхўр",
    "қўтоғлар",
    "қўтоқлар",
    "ҳароми",
    "ҳаромилар",
    "ҳороми",
    "ҳоромилар",
    "ҳуй",
    "ҳуйгами",
    "ҳуйимга",
}

# Game/inline reklama kalit so'zlar/domenlar
SUSPECT_KEYWORDS = {"open game", "play", "играть", "открыть игру", "game", "cattea", "gamee", "hamster", "notcoin", "tap to earn", "earn", "clicker"}
SUSPECT_DOMAINS = {"cattea", "gamee", "hamster", "notcoin", "tgme", "t.me/gamee", "textra.fun", "ton"}

# ----------- DM (Postgres-backed) -----------
SUB_USERS_FILE = "subs_users.json"  # fallback/migration manbasi

OWNER_IDS = {165553982}

def is_owner(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id in OWNER_IDS)

# Postgres connection pool
DB_POOL: Optional["asyncpg.Pool"] = None

def _get_db_url() -> Optional[str]:
    return (
        os.getenv("DATABASE_URL")
        or os.getenv("INTERNAL_DATABASE_URL")
        or os.getenv("DATABASE_INTERNAL_URL")
        or os.getenv("DB_URL")
    )

async def init_db(app=None):
    """Create asyncpg pool and ensure tables exist. Also migrate JSON -> DB once."""
    global DB_POOL
    db_url = _get_db_url()
    if not db_url:
        log.warning("DATABASE_URL topilmadi; DM ro'yxati JSON faylga yoziladi (ephemeral).")
        return
    if asyncpg is None:
        log.error("asyncpg o'rnatilmagan. requirements.txt ga 'asyncpg' qo'shing.")
        return
    # Railway/Render kabi PaaS larda Postgres ko'pincha SSL talab qiladi.
    # asyncpg uchun SSL konteksti beramiz. (Mahalliy DB ham odatda muammo qilmaydi.)
    ssl_ctx = ssl.create_default_context()
    # Railway ba'zan `postgres://` beradi; moslik uchun sxemani normalizatsiya qilamiz.
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]
    # asyncpg SSL'ni `ssl=` orqali boshqaradi; dsn ichidagi sslmode parametrlari ba'zan muammo qiladi.
    try:
        u = urlparse(db_url)
        qs = dict(parse_qsl(u.query, keep_blank_values=True))
        for k in list(qs.keys()):
            if k.lower() in ("sslmode", "sslrootcert", "sslcert", "sslkey"):
                qs.pop(k, None)
        db_url = urlunparse(u._replace(query=urlencode(qs)))
    except Exception:
        pass
    # Ba'zi PaaS/DB (ayniqsa Render free) birinchi ulanishda connection'ni yopib yuborishi mumkin.
    # Shuning uchun retry/backoff bilan pool ochamiz.
    DB_POOL = None
    for attempt in range(1, 6):
        try:
            DB_POOL = await asyncpg.create_pool(
                dsn=db_url,
                min_size=1,
                max_size=5,
                ssl=(False if (urlparse(db_url).hostname or '').endswith('.railway.internal') else ssl_ctx),
                timeout=30,
                max_inactive_connection_lifetime=300,
            )
            log.info("Postgres DB_POOL ochildi (attempt=%s).", attempt)
            break
        except Exception as e:
            log.warning("Postgres ulanish xatosi (attempt=%s/5): %r", attempt, e)
            # exponential backoff: 1,2,4,8,16 (max 16s)
            await asyncio.sleep(min(2 ** (attempt - 1), 16))
    if DB_POOL is None:
        log.error("Postgres'ga ulanib bo'lmadi. DB funksiyalar vaqtincha o'chadi; bot ishlashda davom etadi.")
        return

    async with DB_POOL.acquire() as con:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS dm_users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_bot BOOLEAN DEFAULT FALSE,
                language_code TEXT,
                last_seen TIMESTAMPTZ DEFAULT now()
            );
            """
        )
    # Ensure per-group tables exist
    try:
        await init_group_db()
    except Exception as e:
        log.warning("init_group_db xatolik: %s", e)

    # Migrate from JSON (best-effort, only if DB empty)
    try:
        if DB_POOL:
            async with DB_POOL.acquire() as con:
                count_row = await con.fetchval("SELECT COUNT(*) FROM dm_users;")
            if count_row == 0 and os.path.exists(SUB_USERS_FILE):
                s = _load_ids(SUB_USERS_FILE)
                if s:
                    async with DB_POOL.acquire() as con:
                        async with con.transaction():
                            for cid in s:
                                try:
                                    cid_int = int(cid)
                                except Exception:
                                    continue
                                await con.execute(
                                    "INSERT INTO dm_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING;", cid_int
                                )
                    log.info(f"Migratsiya: JSON dan Postgresga {len(s)} ta ID import qilindi.")
    except Exception as e:
        log.warning(f"Migratsiya vaqtida xato: {e}")

async def dm_upsert_user(user):
    """Add/update a user to dm_users (Postgres if available, else JSON)."""
    global DB_POOL
    if user is None:
        return
    if DB_POOL:
        try:
            async with DB_POOL.acquire() as con:
                await con.execute(
                    """
                    INSERT INTO dm_users (user_id, username, first_name, last_name, is_bot, language_code, last_seen)
                    VALUES ($1,$2,$3,$4,$5,$6, now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        username=EXCLUDED.username,
                        first_name=EXCLUDED.first_name,
                        last_name=EXCLUDED.last_name,
                        is_bot=EXCLUDED.is_bot,
                        language_code=EXCLUDED.language_code,
                        last_seen=now();
                    """,
                    user.id, user.username, user.first_name, user.last_name, user.is_bot, getattr(user, "language_code", None)
                )
        except Exception as e:
            log.warning(f"dm_upsert_user(DB) xatolik: {e}")
    else:
        # Fallback to JSON
        add_chat_to_subs_fallback(user)

async def dm_all_ids() -> List[int]:
    global DB_POOL
    if DB_POOL:
        try:
            async with DB_POOL.acquire() as con:
                rows = await con.fetch("SELECT user_id FROM dm_users;")
            return [r["user_id"] for r in rows]
        except Exception as e:
            log.warning(f"dm_all_ids(DB) xatolik: {e}")
            return []
    else:
        return list(_load_ids(SUB_USERS_FILE))

async def dm_remove_user(user_id: int):
    global DB_POOL
    if DB_POOL:
        try:
            async with DB_POOL.acquire() as con:
                await con.execute("DELETE FROM dm_users WHERE user_id=$1;", user_id)
        except Exception as e:
            log.warning(f"dm_remove_user(DB) xatolik: {e}")
    else:
        remove_chat_from_subs_fallback(user_id)


# ----------- Fallback JSON helpers (only used if DB not available) -----------
def _load_ids(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_ids(path: str, data: set):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(data)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        try:
            log.warning(f"IDs saqlashda xatolik: {e}")
        except Exception:
            print(f"IDs saqlashda xatolik: {e}")

def add_chat_to_subs_fallback(user_or_chat):
    s = _load_ids(SUB_USERS_FILE)
    # user_or_chat is User in our call sites
    cid = getattr(user_or_chat, "id", None)
    if cid is not None:
        s.add(cid)
        _save_ids(SUB_USERS_FILE, s)
    return "user"

def remove_chat_from_subs_fallback(user_id: int):
    s = _load_ids(SUB_USERS_FILE)
    if user_id in s:
        s.remove(user_id)
        _save_ids(SUB_USERS_FILE, s)
    return "user"


# ----------- Privilege/Admin helpers -----------
async def is_admin(update: Update) -> bool:
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user
    if not chat:
        return False
    try:
        # Anonymous admin (message on behalf of the group itself)
        if msg and getattr(msg, "sender_chat", None):
            sc = msg.sender_chat
            if sc.id == chat.id:
                return True
            # Linked channel posting into a supergroup
            linked_id = getattr(chat, "linked_chat_id", None)
            if linked_id and sc.id == linked_id:
                return True
        # Regular user-based admin check
        if user:
            member = await update.get_bot().get_chat_member(chat.id, user.id)
            return member.status in ("administrator", "creator", "owner")
        return False
    except Exception as e:
        log.warning(f"is_admin tekshiruvda xatolik: {e}")
        return False

async def is_privileged_message(msg, bot) -> bool:
    """Adminlar, creatorlar yoki guruh/linked kanal nomidan yozilgan (sender_chat) xabarlar uchun True."""
    try:
        chat = msg.chat
        user = msg.from_user
        # Anonymous admin (group) yoki linked kanal
        if getattr(msg, "sender_chat", None):
            sc = msg.sender_chat
            if sc.id == chat.id:
                return True
            linked_id = getattr(chat, "linked_chat_id", None)
            if linked_id and sc.id == linked_id:
                return True
        # Odatdagi admin/creator
        if user:
            member = await bot.get_chat_member(chat.id, user.id)
            if member.status in ("administrator", "creator", "owner"):
                return True
    except Exception as e:
        log.warning(f"is_privileged_message xatolik: {e}")
    return False

async def kanal_tekshir(user_id: int, bot) -> bool:
    global KANAL_USERNAME
    if not KANAL_USERNAME:
        return True
    try:
        member = await bot.get_chat_member(KANAL_USERNAME, user_id)
        return member.status in ("member", "creator", "administrator")
    except Exception as e:
        log.warning(f"kanal_tekshir xatolik: {e}")
        return False

def matndan_sozlar_olish(matn: str):
    return re.findall(r"\b\w+\b", (matn or "").lower())

def admin_add_link(bot_username: str) -> str:
    rights = [
        'delete_messages','restrict_members','invite_users',
        'pin_messages','manage_topics','manage_video_chats','manage_chat'
    ]
    rights_param = '+'.join(rights)
    return f"https://t.me/{bot_username}?startgroup&admin={rights_param}"

def add_to_group_kb(bot_username: str):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("➕ Guruhga qo‘shish", url=admin_add_link(bot_username))]]
    )

def has_suspicious_buttons(msg) -> bool:
    try:
        kb = msg.reply_markup.inline_keyboard if msg.reply_markup else []
        for row in kb:
            for btn in row:
                if getattr(btn, "callback_game", None) is not None:
                    return True
                u = getattr(btn, "url", "") or ""
                if u:
                    low = u.lower()
                    if any(dom in low for dom in SUSPECT_DOMAINS) or any(x in low for x in ("game", "play", "tgme")):
                        return True
                wa = getattr(btn, "web_app", None)
                if wa and getattr(wa, "url", None):
                    if any(dom in wa.url.lower() for dom in SUSPECT_DOMAINS):
                        return True
        return False
    except Exception:
        return False


# ---------------------- Commands ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Auto-subscribe: /start bosgan foydalanuvchini DM ro'yxatiga qo'shamiz (DB)
    try:
        if update.effective_chat.type == 'private':
            await dm_upsert_user(update.effective_user)
    except Exception as e:
        log.warning(f"/start dm_upsert_user xatolik: {e}")
    kb = [[InlineKeyboardButton("➕ Guruhga qo‘shish", url=admin_add_link(context.bot.username))]]
    await update.effective_message.reply_text(
        "<b>САЛОМ👋</b>\n"
        "Мен барча рекламаларни, ссилкалани ва кирди чиқди хабарларни ҳамда ёрдамчи ботлардан келган рекламаларни гуруҳлардан <b>ўчириб</b> <b>тураман</b>\n\n"
        "Профилингиз <b>ID</b> гизни аниқлаб бераман\n\n"
        "Мажбурий гурухга одам қўштираман ва каналга аъзо бўлдираман (қўшмаса ёзолмайди) ➕\n\n"
        "18+ уятли сўзларни ўчираман ва бошқа кўплаб ёрдамлар бераман 👨🏻‍✈\n\n"
        "Ботнинг ўзи ҳам хечқандай реклама ёки ҳаволалар <b>ТАРҚАТМАЙДИ</b> ⛔\n\n"
        "Бот командалари <b>қўлланмаси</b> 👉 /help\n\n"
        "Фақат ишлашим учун гуруҳингизга қўшиб, <b>ADMIN</b> <b>беришингиз</b> <b>керак</b> 🙂\n\n"
        "Мурожаат ва саволлар бўлса 👉 @SOAuz_admin \n\n"
        "Сиздан фақатгина хомий каналимизга аъзолик 👉 <b>@SOAuz</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📌 <b>БОТ ҚЎЛЛАНМАЛАРИ</b>\n\n"
        "🔹 <b>/id</b> - Аккаунтингиз ID сини кўрсатади.\n\n"
        "📘<b>ЁРДАМЧИ БУЙРУҚЛАР</b>\n"
        "🔹 <b>/tun</b> — Тун режими(шу дақиқадан гурухга ёзилган хабарлар автоматик ўчирилиб турилади).\n"
        "🔹 <b>/tunoff</b> — Тун режимини ўчириш.\n"
        "🔹 <b>/ruxsat</b> — (Ответит) орқали имтиёз бериш.\n\n"
        "👥<b>ГУРУХГА МАЖБУР ОДАМ ҚЎШТИРИШ ВА КАНАЛГА МАЖБУР АЪЗО БЎЛДИРИШ</b>\n"
        "🔹 <b>/kanal @kanal1 @kanal2</b> — Мажбурий кўрсатилган каналга аъзо қилдириш.\n"
        "🔹 <b>/kanaloff</b> — Мажбурий каналга аъзони ўчириш.\n"
        "🔹 <b>/majbur [3–25]</b> — Гурухда мажбурий одам қўшишни ёқиш.\n"
        "🔹 <b>/majburoff</b> — Мажбурий қўшишни ўчириш.\n\n"
        "📈<b>ОДАМ ҚЎШГАНЛАРНИ ХИСОБЛАШ</b>\n"
        "🔹 <b>/top</b> — TOP одам қўшганлар.\n"
        "🔹 <b>/cleangroup</b> — Одам қўшганлар хисобини 0 қилиш.\n"
        "🔹 <b>/count</b> — Ўзингиз нечта қўшдингиз.\n"
        "🔹 <b>/replycount</b> — (Ответит) қилинган одам қўшганлар сони.\n"
        "🔹 <b>/cleanuser</b> — (Ответит) қилинган одам қўшган хисобини 0 қилиш.\n"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

async def id_berish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user = update.effective_user
    await update.effective_message.reply_text(f"🆔 {user.first_name}, sizning Telegram ID’ingiz: {user.id}")

async def tun(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TUN_REJIMI
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    TUN_REJIMI = True
    await update.effective_message.reply_text("🌙 Tun rejimi yoqildi. Oddiy foydalanuvchi xabarlari o‘chiriladi.")

async def tunoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TUN_REJIMI
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    TUN_REJIMI = False
    await update.effective_message.reply_text("🌞 Tun rejimi o‘chirildi.")

async def ruxsat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    if not update.effective_message.reply_to_message:
        return await update.effective_message.reply_text("Iltimos, foydalanuvchi xabariga reply qiling.")
    uid = update.effective_message.reply_to_message.from_user.id
    RUXSAT_USER_IDS.add(uid)
    await update.effective_message.reply_text(f"✅ <code>{uid}</code> foydalanuvchiga ruxsat berildi.", parse_mode="HTML")

async def kanal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    global KANAL_USERNAME
    if context.args:
        KANAL_USERNAME = context.args[0]
        await update.effective_message.reply_text(f"📢 Majburiy kanal: {KANAL_USERNAME}")
    else:
        await update.effective_message.reply_text("Namuna: /kanal @username")

async def kanaloff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    global KANAL_USERNAME
    KANAL_USERNAME = None
    await update.effective_message.reply_text("🚫 Majburiy kanal talabi o‘chirildi.")

def majbur_klaviatura():
    rows = [[3, 5, 7, 10, 12], [15, 18, 20, 25, 30]]
    keyboard = [[InlineKeyboardButton(str(n), callback_data=f"set_limit:{n}") for n in row] for row in rows]
    keyboard.append([InlineKeyboardButton("❌ BEKOR QILISH ❌", callback_data="set_limit:cancel")])
    return InlineKeyboardMarkup(keyboard)

async def majbur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    global MAJBUR_LIMIT
    if context.args:
        try:
            val = int(context.args[0])
            if not (3 <= val <= 30):
                raise ValueError
            MAJBUR_LIMIT = val
            await update.effective_message.reply_text(
                f"✅ Majburiy odam qo‘shish limiti: <b>{MAJBUR_LIMIT}</b>",
                parse_mode="HTML"
            )
        except ValueError:
            await update.effective_message.reply_text(
                "❌ Noto‘g‘ri qiymat. Ruxsat etilgan oraliq: <b>3–30</b>. Masalan: <code>/majbur 10</code>",
                parse_mode="HTML"
            )
    else:
        await update.effective_message.reply_text(
            "👥 Guruhda majburiy odam qo‘shishni nechta qilib belgilay? 👇\n"
            "Qo‘shish shart emas — /majburoff",
            reply_markup=majbur_klaviatura()
        )

async def on_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.callback_query.answer("Faqat adminlar!", show_alert=True)
    q = update.callback_query
    await q.answer()
    data = q.data.split(":", 1)[1]
    global MAJBUR_LIMIT
    if data == "cancel":
        return await q.edit_message_text("❌ Bekor qilindi.")
    try:
        val = int(data)
        if not (3 <= val <= 30):
            raise ValueError
        MAJBUR_LIMIT = val
        await q.edit_message_text(f"✅ Majburiy limit: <b>{MAJBUR_LIMIT}</b>", parse_mode="HTML")
    except Exception:
        await q.edit_message_text("❌ Noto‘g‘ri qiymat.")

async def majburoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    global MAJBUR_LIMIT
    MAJBUR_LIMIT = 0
    await update.effective_message.reply_text("🚫 Majburiy odam qo‘shish o‘chirildi.")

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    if not FOYDALANUVCHI_HISOBI:
        return await update.effective_message.reply_text("Hali hech kim odam qo‘shmagan.")
    items = sorted(FOYDALANUVCHI_HISOBI.items(), key=lambda x: x[1], reverse=True)[:100]
    lines = ["🏆 <b>Eng ko‘p odam qo‘shganlar</b> (TOP 100):"]
    for i, (uid, cnt) in enumerate(items, start=1):
        lines.append(f"{i}. <code>{uid}</code> — {cnt} ta")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

async def cleangroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    FOYDALANUVCHI_HISOBI.clear()
    RUXSAT_USER_IDS.clear()
    await update.effective_message.reply_text("🗑 Barcha foydalanuvchilar hisobi va imtiyozlar 0 qilindi.")

async def count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cnt = FOYDALANUVCHI_HISOBI.get(uid, 0)
    if MAJBUR_LIMIT > 0:
        qoldi = max(MAJBUR_LIMIT - cnt, 0)
        await update.effective_message.reply_text(f"📊 Siz {cnt} ta odam qo‘shgansiz. Qolgan: {qoldi} ta.")
    else:
        await update.effective_message.reply_text(f"📊 Siz {cnt} ta odam qo‘shgansiz. (Majburiy qo‘shish faol emas)")

async def replycount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    msg = update.effective_message
    if not msg.reply_to_message:
        return await msg.reply_text("Iltimos, kimning hisobini ko‘rmoqchi bo‘lsangiz o‘sha xabarga reply qiling.")
    uid = msg.reply_to_message.from_user.id
    cnt = FOYDALANUVCHI_HISOBI.get(uid, 0)
    await msg.reply_text(f"👤 <code>{uid}</code> {cnt} ta odam qo‘shgan.", parse_mode="HTML")

async def cleanuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    msg = update.effective_message
    if not msg.reply_to_message:
        return await msg.reply_text("Iltimos, kimni 0 qilmoqchi bo‘lsangiz o‘sha foydalanuvchi xabariga reply qiling.")
    uid = msg.reply_to_message.from_user.id
    FOYDALANUVCHI_HISOBI[uid] = 0
    RUXSAT_USER_IDS.discard(uid)
    await msg.reply_text(f"🗑 <code>{uid}</code> foydalanuvchi hisobi 0 qilindi (imtiyoz o‘chirildi).", parse_mode="HTML")

async def kanal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    if not KANAL_USERNAME:
        return await q.edit_message_text("⚠️ Kanal sozlanmagan.")
    try:
        member = await context.bot.get_chat_member(KANAL_USERNAME, user_id)
        if member.status in ("member", "administrator", "creator"):
            try:
                await context.bot.restrict_chat_member(
                    chat_id=q.message.chat.id,
                    user_id=user_id,
                    permissions=FULL_PERMS,
                )
            except Exception:
                pass
            await q.edit_message_text("✅ A’zo bo‘lganingiz tasdiqlandi. Endi guruhda yozishingiz mumkin.")
        else:
            await q.edit_message_text("❌ Hali kanalga a’zo emassiz.")
    except Exception:
        await q.edit_message_text("⚠️ Tekshirishda xatolik. Kanal username noto‘g‘ri yoki bot kanalga a’zo emas.")

async def on_check_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id

    data = q.data
    if ":" in data:
        try:
            owner_id = int(data.split(":", 1)[1])
        except ValueError:
            owner_id = None
        if owner_id and owner_id != uid:
            return await q.answer("Bu tugma siz uchun emas!", show_alert=True)

    cnt = FOYDALANUVCHI_HISOBI.get(uid, 0)

    if uid in RUXSAT_USER_IDS or (MAJBUR_LIMIT > 0 and cnt >= MAJBUR_LIMIT):
        try:
            await context.bot.restrict_chat_member(
                chat_id=q.message.chat.id,
                user_id=uid,
                permissions=FULL_PERMS,
            )
        except Exception:
            pass
        BLOK_VAQTLARI.pop((q.message.chat.id, uid), None)
        return await q.edit_message_text("✅ Talab bajarilgan! Endi guruhda yozishingiz mumkin.")

    qoldi = max(MAJBUR_LIMIT - cnt, 0)
    return await q.answer(
        f"❗ Siz hozirgacha {cnt} ta foydalanuvchi qo‘shdingiz va yana {qoldi} ta foydalanuvchi qo‘shishingiz kerak",
        show_alert=True
    )

async def on_grant_priv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat = q.message.chat if q.message else None
    user = q.from_user
    if not (chat and user):
        return await q.answer()
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in ("administrator", "creator"):
            return await q.answer("Faqat adminlar imtiyoz bera oladi!", show_alert=True)
    except Exception:
        return await q.answer("Tekshirishda xatolik.", show_alert=True)
    await q.answer()
    try:
        target_id = int(q.data.split(":", 1)[1])
    except Exception:
        return await q.edit_message_text("❌ Noto‘g‘ri ma'lumot.")
    RUXSAT_USER_IDS.add(target_id)
    await q.edit_message_text(f"🎟 <code>{target_id}</code> foydalanuvchiga imtiyoz berildi. Endi u yozishi mumkin.", parse_mode="HTML")


# ---------------------- Filters ----------------------
async def reklama_va_soz_filtri(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    # 🔒 Linked kanalning avtomatik forward postlari — teginmaymiz
    try:
        if await is_linked_channel_autoforward(msg, context.bot):
            return
    except Exception:
        pass
    if not msg or not msg.chat or not msg.from_user:
        return
    # Admin/creator/guruh nomidan xabarlar — teginmaymiz
    if await is_privileged_message(msg, context.bot):
        return
    # Oq ro'yxat
    if msg.from_user.id in WHITELIST or (msg.from_user.username and msg.from_user.username in WHITELIST):
        return
    # Tun rejimi
    if TUN_REJIMI:
        try:
            await msg.delete()
        except:
            pass
        return
    # Kanal a'zoligi
    if not await kanal_tekshir(msg.from_user.id, context.bot):
        try:
            await msg.delete()
        except:
            pass
        kb = [
            [InlineKeyboardButton("✅ Men a’zo bo‘ldim", callback_data=f"kanal_azo:{msg.from_user.id}")],
            [InlineKeyboardButton("➕ Guruhga qo‘shish", url=admin_add_link(context.bot.username))]
        ]
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text=f"⚠️ {msg.from_user.mention_html()}, siz {KANAL_USERNAME} kanalga a’zo emassiz!",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="HTML"
        )
        return

    text = msg.text or msg.caption or ""
    entities = msg.entities or msg.caption_entities or []

    # Inline bot orqali kelgan xabar — ko'pincha game reklama
    if getattr(msg, "via_bot", None):
        try:
            await msg.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text=f"⚠️ {msg.from_user.mention_html()}, yashirin ssilka yuborish taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username),
            parse_mode="HTML"
        )
        return

    # Tugmalarda game/web-app/URL bo'lsa — blok
    if has_suspicious_buttons(msg):
        try:
            await msg.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text="⚠️ O‘yin/veb-app tugmali reklama taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username)
        )
        return

    # Matndan o‘yin reklamasini aniqlash
    low = text.lower()
    if any(k in low for k in SUSPECT_KEYWORDS):
        try:
            await msg.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text="⚠️ O‘yin reklamalari taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username)
        )
        return

    # Botlardan kelgan reklama/havola/game
    if getattr(msg.from_user, "is_bot", False):
        has_game = bool(getattr(msg, "game", None))
        has_url_entity = any(ent.type in ("text_link", "url", "mention") for ent in entities)
        has_url_text = any(x in low for x in ("t.me","telegram.me","http://","https://","www.","youtu.be","youtube.com"))
        if has_game or has_url_entity or has_url_text:
            try:
                await msg.delete()
            except:
                pass
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=f"⚠️ {msg.from_user.mention_html()}, reklama/ssilka yuborish taqiqlangan!",
                reply_markup=add_to_group_kb(context.bot.username),
                parse_mode="HTML"
            )
            return

    # Yashirin yoki aniq ssilkalar
    for ent in entities:
        if ent.type in ("text_link", "url", "mention"):
            url = getattr(ent, "url", "") or ""
            if url and ("t.me" in url or "telegram.me" in url or "http://" in url or "https://" in url):
                try:
                    await msg.delete()
                except:
                    pass
                await context.bot.send_message(
                    chat_id=msg.chat_id,
                    text=f"⚠️ {msg.from_user.mention_html()}, yashirin ssilka yuborish taqiqlangan!",
                    reply_markup=add_to_group_kb(context.bot.username),
                    parse_mode="HTML"
                )
                return

    if any(x in low for x in ("t.me","telegram.me","@","www.","https://youtu.be","http://","https://")):
        try:
            await msg.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text=f"⚠️ {msg.from_user.mention_html()}, reklama/ssilka yuborish taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username),
            parse_mode="HTML"
        )
        return

    # So'kinish
    sozlar = matndan_sozlar_olish(text)
    if any(s in UYATLI_SOZLAR for s in sozlar):
        try:
            await msg.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text=f"⚠️ {msg.from_user.mention_html()}, guruhda so‘kinish taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username),
            parse_mode="HTML"
        )
        return

# Yangi a'zolarni qo'shganlarni hisoblash hamda kirdi/chiqdi xabarlarni o'chirish
async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    adder = msg.from_user
    members = msg.new_chat_members or []
    if not adder:
        return
    for m in members:
        if adder.id != m.id:
            FOYDALANUVCHI_HISOBI[adder.id] += 1
    try:
        await msg.delete()
    except:
        pass

# Majburiy qo'shish filtri — yetmaganlarda 1 daqiqaga blok
async def majbur_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if MAJBUR_LIMIT <= 0:
        return
    msg = update.effective_message
    # 🔒 Linked kanalning avtomatik forward postlari — teginmaymiz
    try:
        if await is_linked_channel_autoforward(msg, context.bot):
            return
    except Exception:
        pass
    if not msg or not msg.from_user:
        return
    if await is_privileged_message(msg, context.bot):
        return

    uid = msg.from_user.id

    # Agar foydalanuvchi hanuz blokda bo'lsa — xabarini o'chirib, hech narsa yubormaymiz
    now = datetime.now(timezone.utc)
    key = (msg.chat_id, uid)
    until_old = BLOK_VAQTLARI.get(key)
    if until_old and now < until_old:
        try:
            await msg.delete()
        except:
            pass
        return
    if uid in RUXSAT_USER_IDS:
        return

    cnt = FOYDALANUVCHI_HISOBI.get(uid, 0)
    if cnt >= MAJBUR_LIMIT:
        return

    # Xabarni o'chiramiz
    try:
        await msg.delete()
    except:
        return

    # 1 daqiqaga blok
    until = datetime.now(timezone.utc) + timedelta(minutes=1)
    BLOK_VAQTLARI[(msg.chat_id, uid)] = until
    try:
        await context.bot.restrict_chat_member(
            chat_id=msg.chat_id,
            user_id=uid,
            permissions=BLOCK_PERMS,
            until_date=until
        )
    except Exception as e:
        log.warning(f"Restrict failed: {e}")

    qoldi = max(MAJBUR_LIMIT - cnt, 0)
    kb = [
        [InlineKeyboardButton("✅ Odam qo‘shdim", callback_data=f"check_added:{uid}")],
        [InlineKeyboardButton("🎟 Imtiyoz berish", callback_data=f"grant:{uid}")],
        [InlineKeyboardButton("➕ Guruhga qo‘shish", url=admin_add_link(context.bot.username))],
        [InlineKeyboardButton("⏳ 1 daqiqaga bloklandi", callback_data="noop")]
    ]
    await context.bot.send_message(
        chat_id=msg.chat_id,
        text=f"⚠️ Guruhda yozish uchun {MAJBUR_LIMIT} ta odam qo‘shishingiz kerak! Qolgan: {qoldi} ta.",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# -------------- Bot my_status (admin emas) ogohlantirish --------------
async def on_my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        st = update.my_chat_member.new_chat_member.status
    except Exception:
        return
    if st in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED):
        me = await context.bot.get_me()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            '🔐 Botni admin qilish', url=admin_add_link(me.username)
        )]])
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    '⚠️ Bot hozircha *admin emas*.\n'
                    "Iltimos, pastdagi tugma orqali admin qiling, shunda barcha funksiyalar to'liq ishlaydi."
                ),
                reply_markup=kb,
                parse_mode='Markdown'
            )
        except Exception:
            pass


# ---------------------- DM: Broadcast ----------------------
async def track_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har qanday PRIVATE chatdagi xabarni ko'rsak, u foydalanuvchini DBga upsert qilamiz."""
    try:
        await dm_upsert_user(update.effective_user)
    except Exception as e:
        log.warning(f"track_private upsert xatolik: {e}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(OWNER & DM) Matnni barcha DM obunachilarga yuborish."""
    if update.effective_chat.type != "private":
        return await update.effective_message.reply_text("⛔ Bu buyruq faqat DM (shaxsiy chat)da ishlaydi.")
    if not is_owner(update):
        return await update.effective_message.reply_text("⛔ Bu buyruq faqat bot egasiga ruxsat etilgan.")
    text = " ".join(context.args).strip()
    if not text and update.effective_message.reply_to_message:
        text = update.effective_message.reply_to_message.text_html or update.effective_message.reply_to_message.caption_html
    if not text:
        return await update.effective_message.reply_text("Foydalanish: /broadcast Yangilanish matni")

    ids = await dm_all_ids()
    total = len(ids); ok = 0; fail = 0
    await update.effective_message.reply_text(f"📣 DM jo‘natish boshlandi. Jami foydalanuvchilar: {total}")
    for cid in list(ids):
        try:
            await context.bot.send_message(cid, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            ok += 1
            await asyncio.sleep(0.05)
        except (Exception,) as e:
            # drop forbidden/bad users
            await dm_remove_user(cid)
            fail += 1
    await update.effective_message.reply_text(f"✅ Yuborildi: {ok} ta, ❌ xatolik: {fail} ta.")

async def broadcastpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(OWNER & DM) Reply qilingan postni barcha DM obunachilarga yuborish."""
    if update.effective_chat.type != "private":
        return await update.effective_message.reply_text("⛔ Bu buyruq faqat DM (shaxsiy chat)da ishlaydi.")
    if not is_owner(update):
        return await update.effective_message.reply_text("⛔ Bu buyruq faqat bot egasiga ruxsat etilgan.")
    msg = update.effective_message.reply_to_message
    if not msg:
        return await update.effective_message.reply_text("Foydalanish: /broadcastpost — yubormoqchi bo‘lgan xabarga reply qiling.")

    ids = await dm_all_ids()
    total = len(ids); ok = 0; fail = 0
    await update.effective_message.reply_text(f"📣 DM post tarqatish boshlandi. Jami foydalanuvchilar: {total}")
    for cid in list(ids):
        try:
            await context.bot.copy_message(chat_id=cid, from_chat_id=msg.chat_id, message_id=msg.message_id)
            ok += 1
            await asyncio.sleep(0.05)
        except (Exception,) as e:
            await dm_remove_user(cid)
            fail += 1
    await update.effective_message.reply_text(f"✅ Yuborildi: {ok} ta, ❌ xatolik: {fail} ta.")



# ====================== PER-GROUP SETTINGS (DB-backed) ======================
# Muammo: TUN_REJIMI / KANAL_USERNAME / MAJBUR_LIMIT va hisoblar global edi.
# Yechim: Har bir chat_id (guruh) uchun alohida saqlash (Railway Postgres).

_GROUP_SETTINGS_CACHE = {}  # chat_id -> (settings_dict, fetched_monotonic)
_GROUP_SETTINGS_TTL_SEC = 20

# In-memory fallback (DB bo'lmasa) — counts per (chat_id, user_id)
_GROUP_COUNTS_MEM = defaultdict(lambda: defaultdict(int))


# In-memory privileges cache per group (DB bo'lsa ham tezkor bypass uchun)
_GROUP_PRIV_MEM = defaultdict(set)  # chat_id -> set(user_id)
def _default_group_settings():
    return {"tun": False, "kanal_username": None, "majbur_limit": 0}

async def init_group_db():
    """Ensure per-group tables exist."""
    global DB_POOL
    if not DB_POOL:
        return
    async with DB_POOL.acquire() as con:
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id BIGINT PRIMARY KEY,
                tun BOOLEAN NOT NULL DEFAULT FALSE,
                kanal_username TEXT,
                majbur_limit INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS group_user_counts (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                cnt INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS group_privileges (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )
        await con.execute(
            """
            CREATE TABLE IF NOT EXISTS group_blocks (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                until_date TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )
    log.info("Per-group DB jadvallari tayyor: group_settings, group_user_counts, group_privileges, group_blocks")

async def get_group_settings(chat_id: int) -> dict:
    """Fetch group settings from DB (cached).

    Muhim: DB vaqtincha uzilib qolsa ham, guruh sozlamalari (tun/kanal/majbur)
    "o'z-o'zidan o'chib ketmasligi" uchun oxirgi cache qilingan qiymat qaytariladi.
    """
    import time
    now = time.monotonic()
    cached = _GROUP_SETTINGS_CACHE.get(chat_id)
    if cached and (now - cached[1]) < _GROUP_SETTINGS_TTL_SEC:
        return dict(cached[0])

    # Cache bo'lsa, DB xatoda shuni qaytaramiz; bo'lmasa default.
    fallback = dict(cached[0]) if cached else _default_group_settings()

    if not DB_POOL:
        # DB yo'q bo'lsa ham cache yangilanadi
        _GROUP_SETTINGS_CACHE[chat_id] = (dict(fallback), now)
        return dict(fallback)

    s = _default_group_settings()
    try:
        async with DB_POOL.acquire() as con:
            row = await con.fetchrow(
                "SELECT tun, kanal_username, majbur_limit FROM group_settings WHERE chat_id=$1;",
                chat_id
            )
        if row:
            s["tun"] = bool(row["tun"])
            s["kanal_username"] = row["kanal_username"]
            s["majbur_limit"] = int(row["majbur_limit"] or 0)
        else:
            # ensure row exists
            async with DB_POOL.acquire() as con:
                await con.execute(
                    "INSERT INTO group_settings (chat_id) VALUES ($1) ON CONFLICT DO NOTHING;",
                    chat_id
                )
    except Exception as e:
        # DB xatoda: oxirgi cache (yoki default) bilan davom etamiz
        log.warning(f"get_group_settings xatolik (cache bilan davom): {e}")
        return dict(fallback)

    _GROUP_SETTINGS_CACHE[chat_id] = (dict(s), now)
    return dict(s)

# Sentinel: differenciate between "parameter not provided" vs explicit None (e.g., /kanaloff)
_GROUP_SETTINGS_UNSET = object()

async def set_group_settings(chat_id: int, *, tun=_GROUP_SETTINGS_UNSET, kanal_username=_GROUP_SETTINGS_UNSET, majbur_limit=_GROUP_SETTINGS_UNSET):
    """Upsert group settings for chat_id.

    Important:
    - If a parameter is not provided (_GROUP_SETTINGS_UNSET), the existing value is preserved.
    - If kanal_username=None is provided, it is stored as None (this is needed for /kanaloff).
    """
    if not DB_POOL:
        # cache-only fallback
        cur = await get_group_settings(chat_id)
        if tun is not _GROUP_SETTINGS_UNSET:
            cur["tun"] = bool(tun)
        if kanal_username is not _GROUP_SETTINGS_UNSET:
            cur["kanal_username"] = kanal_username
        if majbur_limit is not _GROUP_SETTINGS_UNSET:
            cur["majbur_limit"] = int(majbur_limit)
        _GROUP_SETTINGS_CACHE[chat_id] = (cur, __import__("time").monotonic())
        return

    # Keep unspecified fields unchanged (read current first)
    cur = await get_group_settings(chat_id)
    if tun is _GROUP_SETTINGS_UNSET:
        tun = cur["tun"]
    if kanal_username is _GROUP_SETTINGS_UNSET:
        kanal_username = cur["kanal_username"]
    if majbur_limit is _GROUP_SETTINGS_UNSET:
        majbur_limit = cur["majbur_limit"]

    try:
        async with DB_POOL.acquire() as con:
            await con.execute(
                """
                INSERT INTO group_settings (chat_id, tun, kanal_username, majbur_limit, updated_at)
                VALUES ($1,$2,$3,$4, now())
                ON CONFLICT (chat_id) DO UPDATE SET
                    tun=EXCLUDED.tun,
                    kanal_username=EXCLUDED.kanal_username,
                    majbur_limit=EXCLUDED.majbur_limit,
                    updated_at=now();
                """,
                chat_id, bool(tun), kanal_username, int(majbur_limit)
            )
        _GROUP_SETTINGS_CACHE[chat_id] = ({"tun": bool(tun), "kanal_username": kanal_username, "majbur_limit": int(majbur_limit)}, __import__("time").monotonic())
    except Exception as e:
        log.warning(f"set_group_settings xatolik: {e}")

async def group_has_priv(chat_id: int, user_id: int) -> bool:

    # Tezkor cache
    try:
        if user_id in _GROUP_PRIV_MEM.get(chat_id, set()):
            return True
    except Exception:
        pass

    if not DB_POOL:
        # DB yo'q bo'lsa ham cache ishlaydi
        return user_id in _GROUP_PRIV_MEM.get(chat_id, set())

    try:
        async with DB_POOL.acquire() as con:
            v = await con.fetchval(
                "SELECT 1 FROM group_privileges WHERE chat_id=$1 AND user_id=$2;",
                chat_id, user_id
            )
        ok = bool(v)
        if ok:
            _GROUP_PRIV_MEM[chat_id].add(user_id)
        return ok
    except Exception as e:
        log.warning(f"group_has_priv xatolik: {e}")
        # DB vaqtincha muammo qilsa ham cache'dan qaytamiz
        return user_id in _GROUP_PRIV_MEM.get(chat_id, set())

async def grant_priv_db(chat_id: int, user_id: int):
    # Avval cache'ga yozamiz (DB kechiksa ham darhol ishlasin)
    try:
        _GROUP_PRIV_MEM[chat_id].add(user_id)
    except Exception:
        pass

    if not DB_POOL:
        return
    try:
        async with DB_POOL.acquire() as con:
            await con.execute(
                "INSERT INTO group_privileges (chat_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING;",
                chat_id, user_id
            )
    except Exception as e:
        log.warning(f"grant_priv_db xatolik: {e}")

async def clear_privs_db(chat_id: int):
    if not DB_POOL:
        return
    try:
        async with DB_POOL.acquire() as con:
            await con.execute("DELETE FROM group_privileges WHERE chat_id=$1;", chat_id)
    except Exception:
        pass

async def get_user_count_db(chat_id: int, user_id: int) -> int:
    if not DB_POOL:
        try:
            return int(_GROUP_COUNTS_MEM[chat_id].get(user_id, 0))
        except Exception:
            return 0
    try:
        async with DB_POOL.acquire() as con:
            v = await con.fetchval(
                "SELECT cnt FROM group_user_counts WHERE chat_id=$1 AND user_id=$2;",
                chat_id, user_id
            )
        return int(v or 0)
    except Exception:
        return 0

async def inc_user_count_db(chat_id: int, user_id: int, delta: int = 1):
    if not DB_POOL:
        try:
            _GROUP_COUNTS_MEM[chat_id][user_id] = int(_GROUP_COUNTS_MEM[chat_id].get(user_id, 0)) + int(delta)
        except Exception:
            pass
        return
    try:
        async with DB_POOL.acquire() as con:
            await con.execute(
                """
                INSERT INTO group_user_counts (chat_id, user_id, cnt, updated_at)
                VALUES ($1,$2,$3, now())
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    cnt = group_user_counts.cnt + EXCLUDED.cnt,
                    updated_at = now();
                """,
                chat_id, user_id, int(delta)
            )
    except Exception as e:
        log.warning(f"inc_user_count_db xatolik: {e}")

async def set_user_count_db(chat_id: int, user_id: int, cnt: int):
    if not DB_POOL:
        try:
            _GROUP_COUNTS_MEM[chat_id][user_id] = int(cnt)
        except Exception:
            pass
        return
    try:
        async with DB_POOL.acquire() as con:
            await con.execute(
                """
                INSERT INTO group_user_counts (chat_id, user_id, cnt, updated_at)
                VALUES ($1,$2,$3, now())
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    cnt=EXCLUDED.cnt,
                    updated_at=now();
                """,
                chat_id, user_id, int(cnt)
            )
    except Exception:
        pass

async def clear_group_counts_db(chat_id: int):
    if not DB_POOL:
        try:
            _GROUP_COUNTS_MEM.pop(chat_id, None)
        except Exception:
            pass
        return
    try:
        async with DB_POOL.acquire() as con:
            await con.execute("DELETE FROM group_user_counts WHERE chat_id=$1;", chat_id)
    except Exception:
        pass

async def top_group_counts_db(chat_id: int, limit: int = 100):
    if not DB_POOL:
        try:
            items = list(_GROUP_COUNTS_MEM.get(chat_id, {}).items())
            items.sort(key=lambda x: (-int(x[1]), int(x[0])))
            return [(int(uid), int(cnt)) for uid, cnt in items[: int(limit)]]
        except Exception:
            return []
    try:
        async with DB_POOL.acquire() as con:
            rows = await con.fetch(
                "SELECT user_id, cnt FROM group_user_counts WHERE chat_id=$1 ORDER BY cnt DESC, user_id ASC LIMIT $2;",
                chat_id, int(limit)
            )
        return [(int(r["user_id"]), int(r["cnt"])) for r in rows]
    except Exception:
        return []

async def get_block_until_db(chat_id: int, user_id: int):
    """
    1) Avval in-memory BLOK_VAQTLARI'ni tekshiradi (DB ishlamay qolsa ham cooldown ishlashi uchun).
    2) DB bo'lsa — DB'dan ham tekshiradi va eng kattasini qaytaradi.
    """
    mem_until = BLOK_VAQTLARI.get((chat_id, user_id))
    if not DB_POOL:
        return mem_until
    try:
        async with DB_POOL.acquire() as con:
            row = await con.fetchrow(
                "SELECT until_date FROM group_blocks WHERE chat_id=$1 AND user_id=$2;",
                chat_id, user_id
            )
        db_until = row["until_date"] if row else None
        if mem_until and db_until:
            return mem_until if mem_until >= db_until else db_until
        return db_until or mem_until
    except Exception:
        return mem_until
async def set_block_until_db(chat_id: int, user_id: int, until_dt):
    # Har doim in-memory'ni yangilab boramiz (DB xatosida ham cooldown ishlasin)
    BLOK_VAQTLARI[(chat_id, user_id)] = until_dt
    if not DB_POOL:
        return
    try:
        async with DB_POOL.acquire() as con:
            await con.execute(
                """
                INSERT INTO group_blocks (chat_id, user_id, until_date, updated_at)
                VALUES ($1,$2,$3, now())
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    until_date=EXCLUDED.until_date,
                    updated_at=now();
                """,
                chat_id, user_id, until_dt
            )
    except Exception:
        pass
async def clear_block_db(chat_id: int, user_id: int):
    # In-memory'dan har doim o'chiramiz
    BLOK_VAQTLARI.pop((chat_id, user_id), None)
    if not DB_POOL:
        return
    try:
        async with DB_POOL.acquire() as con:
            await con.execute(
                "DELETE FROM group_blocks WHERE chat_id=$1 AND user_id=$2;",
                chat_id, user_id
            )
    except Exception:
        pass
# --------- Override: kanal_tekshir per-group ----------
async def kanal_tekshir(user_id: int, bot, kanal_username: str | None) -> bool:
    if not kanal_username:
        return True
    try:
        member = await bot.get_chat_member(kanal_username, user_id)
        return member.status in ("member", "creator", "administrator")
    except Exception as e:
        log.warning(f"kanal_tekshir xatolik: {e}")
        return False


# --- Multi-channel /kanal helpers (per-group) ---

def _normalize_channel_username(raw: str) -> str:
    s = (raw or "").strip()
    # accept https://t.me/<name> or t.me/<name>
    if "t.me/" in s:
        s = s.split("t.me/", 1)[1]
        s = s.split("?", 1)[0]
        s = s.split("/", 1)[0]
    s = s.strip().rstrip(",;")
    s = s.lstrip("@")
    return "@" + s if s else ""

def _parse_kanal_usernames(raw) -> list[str]:
    # Supported formats in DB: None/empty, single "@ch", space/comma separated, JSON list string.
    if not raw:
        return []

    vals: list[str] = []
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            j = __import__("json").loads(s)
            if isinstance(j, list):
                vals = [str(x) for x in j]
            else:
                vals = [s]
        except Exception:
            vals = s.replace(",", " ").split()
    elif isinstance(raw, list):
        vals = [str(x) for x in raw]
    else:
        vals = [str(raw)]

    out: list[str] = []
    seen: set[str] = set()
    for v in vals:
        ch = _normalize_channel_username(v)
        if not ch or ch == "@":
            continue
        if ch not in seen:
            out.append(ch)
            seen.add(ch)
    return out

def _unique_preserve(seq: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in seq:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out

async def _check_all_channels(user_id: int, bot, channels: list[str]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for ch in channels:
        ok = await kanal_tekshir(user_id, bot, ch)
        if not ok:
            missing.append(ch)
    return (len(missing) == 0, missing)

# --------- Override commands: tun/tunoff/kanal/kanaloff/majbur/majburoff/ruxsat ----------
async def tun(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    chat_id = update.effective_chat.id
    await set_group_settings(chat_id, tun=True)
    await update.effective_message.reply_text("🌙 Tun rejimi yoqildi. Faqat shu guruhga ta’sir qiladi.")

async def tunoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    chat_id = update.effective_chat.id
    await set_group_settings(chat_id, tun=False)
    await update.effective_message.reply_text("🌞 Tun rejimi o‘chirildi. Faqat shu guruhga ta’sir qiladi.")

async def kanal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    chat_id = update.effective_chat.id

    if context.args:
        channels: list[str] = []
        for a in context.args:
            ch = _normalize_channel_username(a)
            if ch and ch != "@":
                channels.append(ch)
        channels = _unique_preserve(channels)
        if not channels:
            return await update.effective_message.reply_text("Namuna: /kanal @kanal1 @kanal2")

        # Store as JSON list (backward compatible: old single value still parses)
        await set_group_settings(chat_id, kanal_username=__import__("json").dumps(channels, ensure_ascii=False))
        chan_lines = "\n".join([f"{i}) {ch}" for i, ch in enumerate(channels, start=1)])
        await update.effective_message.reply_text(
            "📢 Majburiy kanallar (faqat shu guruh учун):\n" + chan_lines
        )
    else:
        await update.effective_message.reply_text("Namuna: /kanal @kanal1 @kanal2")

async def kanaloff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    chat_id = update.effective_chat.id
    await set_group_settings(chat_id, kanal_username=None)
    await update.effective_message.reply_text("🚫 Majburiy kanal talabi o‘chirildi (faqat shu guruh uchun).")

async def majbur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    chat_id = update.effective_chat.id
    if context.args:
        try:
            val = int(context.args[0])
            if not (3 <= val <= 30):
                raise ValueError
            await set_group_settings(chat_id, majbur_limit=val)
            await update.effective_message.reply_text(
                f"✅ Majburiy odam qo‘shish limiti: <b>{val}</b> (faqat shu guruh uchun)",
                parse_mode="HTML"
            )
        except ValueError:
            await update.effective_message.reply_text(
                "❌ Noto‘g‘ri qiymat. Ruxsat etilgan oraliq: <b>3–30</b>. Masalan: <code>/majbur 10</code>",
                parse_mode="HTML"
            )
    else:
        await update.effective_message.reply_text(
            "👥 Guruhda majburiy odam qo‘shishni nechta qilib belgilay? 👇\n\nQo‘shish shart emas — /majburoff",
            reply_markup=majbur_klaviatura()
        )

async def on_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.callback_query.answer("Faqat adminlar!", show_alert=True)
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    data = q.data.split(":", 1)[1]
    if data == "cancel":
        return await q.edit_message_text("❌ Bekor qilindi.")
    try:
        val = int(data)
        if not (3 <= val <= 30):
            raise ValueError
        await set_group_settings(chat_id, majbur_limit=val)
        await q.edit_message_text(f"✅ Majburiy limit: <b>{val}</b> (faqat shu guruh uchun)", parse_mode="HTML")
    except Exception:
        await q.edit_message_text("❌ Noto‘g‘ri qiymat.")

async def majburoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    chat_id = update.effective_chat.id
    await set_group_settings(chat_id, majbur_limit=0)
    await update.effective_message.reply_text("🚫 Majburiy odam qo‘shish o‘chirildi (faqat shu guruh uchun).")

async def ruxsat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    if not update.effective_message.reply_to_message:
        return await update.effective_message.reply_text("Iltimos, foydalanuvchi xabariga reply qiling.")
    chat_id = update.effective_chat.id
    uid = update.effective_message.reply_to_message.from_user.id
    await grant_priv_db(chat_id, uid)
    await update.effective_message.reply_text(f"✅ <code>{uid}</code> foydalanuvchiga ruxsat berildi (shu guruhda).", parse_mode="HTML")

# --------- Override stats commands to be per-group ----------
def _user_label_from_user(u) -> str:
    if getattr(u, "username", None):
        return "@" + u.username
    name = (getattr(u, "full_name", None) or "").strip()
    if not name:
        name = (getattr(u, "first_name", None) or "").strip()
    return name or str(u.id)

def _mention_userid_html(user_id: int, label: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(str(label))}</a>'

def _mention_user_html(u) -> str:
    return _mention_userid_html(u.id, _user_label_from_user(u))

async def _mention_from_id(bot, chat_id: int, user_id: int, cache: dict[int, str]) -> str:
    if user_id in cache:
        return cache[user_id]
    label = str(user_id)
    try:
        cm = await bot.get_chat_member(chat_id, user_id)
        u = getattr(cm, "user", None)
        if u is not None:
            label = _user_label_from_user(u)
    except Exception:
        pass
    mention = _mention_userid_html(user_id, label)
    cache[user_id] = mention
    return mention

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    chat_id = update.effective_chat.id
    items = await top_group_counts_db(chat_id, limit=100)
    if not items:
        return await update.effective_message.reply_text("Hali hech kim odam qo‘shmagan.")
    lines = ["🏆 <b>Eng ko‘p odam qo‘shganlar</b> (TOP 100):"]
    cache: dict[int, str] = {}
    for i, (uid, cnt) in enumerate(items, start=1):
        mention = await _mention_from_id(context.bot, chat_id, uid, cache)
        lines.append(f"{i}. {mention} — <b>{cnt}</b> ta")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

async def cleangroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    chat_id = update.effective_chat.id
    await clear_group_counts_db(chat_id)
    await clear_privs_db(chat_id)
    await update.effective_message.reply_text("🗑 Shu guruh bo‘yicha barcha hisoblar va imtiyozlar 0 qilindi.")

async def count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    settings = await get_group_settings(chat_id)
    limit = int(settings.get("majbur_limit") or 0)
    cnt = await get_user_count_db(chat_id, uid)
    if limit > 0:
        qoldi = max(limit - cnt, 0)
        await update.effective_message.reply_text(f"📊 Siz {cnt} ta odam qo‘shgansiz. Qolgan: {qoldi} ta.")
    else:
        await update.effective_message.reply_text(f"📊 Siz {cnt} ta odam qo‘shgansiz. (Majburiy qo‘shish faol emas)")

async def replycount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    msg = update.effective_message
    if not msg.reply_to_message:
        return await msg.reply_text("Iltimos, kimning hisobini ko‘rmoqchi bo‘lsangiz o‘sha xabarga reply qiling.")
    chat_id = update.effective_chat.id
    u = msg.reply_to_message.from_user
    uid = u.id
    cnt = await get_user_count_db(chat_id, uid)
    await msg.reply_text(f"👤 {_mention_user_html(u)} — <b>{cnt}</b> ta odam qo‘shgan (shu guruhda).", parse_mode="HTML")

async def cleanuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update):
        return await update.effective_message.reply_text("⛔ Faqat adminlar.")
    msg = update.effective_message
    if not msg.reply_to_message:
        return await msg.reply_text("Iltimos, kimni 0 qilmoqchi bo‘lsangiz o‘sha foydalanuvchi xabariga reply qiling.")
    chat_id = update.effective_chat.id
    u = msg.reply_to_message.from_user
    uid = u.id
    await set_user_count_db(chat_id, uid, 0)
    await msg.reply_text(f"🗑 {_mention_user_html(u)} foydalanuvchi hisobi 0 qilindi (shu guruhda).", parse_mode="HTML")

# --------- Override callbacks that depended on global settings ----------
async def kanal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = (q.data or "")
    chat_id = q.message.chat.id if q.message else None
    user_id = q.from_user.id

    # Button ownership check: only the warned user can press.
    owner_id = None
    if ":" in data:
        try:
            owner_id = int(data.split(":", 1)[1])
        except Exception:
            owner_id = None

    # Old messages used callback_data="kanal_azo"; block them to prevent abuse.
    if owner_id is None and data == "kanal_azo":
        return await q.answer("Bu eski tugma. Iltimos yangi ogohlantirishni kuting.", show_alert=True)

    if owner_id is not None and owner_id != user_id:
        return await q.answer("Bu tugma siz uchun emas!", show_alert=True)

    if not chat_id:
        return await q.answer()

    settings = await get_group_settings(chat_id)
    kanal_raw = settings.get("kanal_username")
    kanal_list = _parse_kanal_usernames(kanal_raw)

    # If /kanaloff was used, allow writing.
    if not kanal_list:
        await q.answer()
        try:
            await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=FULL_PERMS)
        except Exception:
            pass
        try:
            await clear_block_db(chat_id, user_id)
        except Exception:
            pass
        return await q.edit_message_text("✅ Majburiy kanal talabi o‘chirilgan. Endi guruhda yozishingiz mumkin.")

    ok_all, _missing = await _check_all_channels(user_id, context.bot, kanal_list)
    if not ok_all:
        return await q.answer("❌ Hali barcha kanalga a’zo emassiz", show_alert=True)

    await q.answer()
    try:
        await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=FULL_PERMS)
    except Exception:
        pass
    try:
        await clear_block_db(chat_id, user_id)
    except Exception:
        pass
    return await q.edit_message_text("✅ A’zo bo‘lganingiz tasdiqlandi. Endi guruhda yozishingiz mumkin.")

async def on_check_added(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    uid = q.from_user.id
    chat_id = q.message.chat.id

    # tugma owner check (old behavior)
    data = q.data
    if ":" in data:
        try:
            owner_id = int(data.split(":", 1)[1])
        except ValueError:
            owner_id = None
        if owner_id and owner_id != uid:
            return await q.answer("Bu tugma siz uchun emas!", show_alert=True)

    settings = await get_group_settings(chat_id)
    limit = int(settings.get("majbur_limit") or 0)
    cnt = await get_user_count_db(chat_id, uid)

    if await group_has_priv(chat_id, uid) or (limit > 0 and cnt >= limit):
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=uid,
                permissions=FULL_PERMS,
            )
        except Exception:
            pass
        await clear_block_db(chat_id, uid)
        return await q.edit_message_text("✅ Talab bajarilgan! Endi guruhda yozishingiz mumkin.")

    qoldi = max(limit - cnt, 0)
    return await q.answer(
        f"❗ Siz hozirgacha {cnt} ta foydalanuvchi qo‘shdingiz va yana {qoldi} ta foydalanuvchi qo‘shishingiz kerak",
        show_alert=True
    )

async def on_grant_priv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat = q.message.chat if q.message else None
    user = q.from_user
    if not (chat and user):
        return await q.answer()
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in ("administrator", "creator"):
            return await q.answer("Faqat adminlar imtiyoz bera oladi!", show_alert=True)
    except Exception:
        return await q.answer("Tekshirishda xatolik.", show_alert=True)
    await q.answer()
    try:
        target_id = int(q.data.split(":", 1)[1])
    except Exception:
        return await q.edit_message_text("❌ Noto‘g‘ri ma'lumot.")
    await grant_priv_db(chat.id, target_id)
    # Agar foydalanuvchi blokda bo'lsa — darhol blokdan chiqaramiz
    try:
        await clear_block_db(chat.id, target_id)
    except Exception:
        pass
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=target_id,
            permissions=FULL_PERMS,
        )
    except Exception:
        pass
    await q.edit_message_text(f"🎟 <code>{target_id}</code> foydalanuvchiga imtiyoz berildi. Endi u yozishi mumkin (shu guruhda).", parse_mode="HTML")

# --------- Override Filters: reklama_va_soz_filtri / majbur_filter ----------
async def reklama_va_soz_filtri(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    # 🔒 Linked kanalning avtomatik forward postlari — teginmaymiz
    try:
        if await is_linked_channel_autoforward(msg, context.bot):
            return
    except Exception:
        pass
    if not msg or not msg.chat or not msg.from_user:
        return

    chat_id = msg.chat_id

    # Admin/creator/guruh nomidan xabarlar — teginmaymiz
    if await is_privileged_message(msg, context.bot):
        return
    # Oq ro'yxat
    if msg.from_user.id in WHITELIST or (msg.from_user.username and msg.from_user.username in WHITELIST):
        return

    settings = await get_group_settings(chat_id)

    # Tun rejimi (shu guruh uchun)
    if settings.get("tun"):
        try:
            await msg.delete()
        except Exception:
            pass
        return

    kanal_raw = settings.get("kanal_username")
    kanal_list = _parse_kanal_usernames(kanal_raw)

    # Cooldown: foydalanuvchi 1 daqiqalik blokda bo'lsa — xabarini o'chirib, ogohlantirmaymiz
    uid = msg.from_user.id
    now = datetime.now(timezone.utc)
    until_old = await get_block_until_db(chat_id, uid)
    if until_old and now < until_old:
        try:
            await msg.delete()
        except Exception:
            pass
        return
    if until_old and now >= until_old:
        await clear_block_db(chat_id, uid)

    # Kanal a'zoligi (shu guruh uchun) - ko'p kanalli
    if kanal_list:
        ok_all, _missing = await _check_all_channels(uid, context.bot, kanal_list)
        if not ok_all:
            try:
                await msg.delete()
            except Exception:
                pass

            # 1 daqiqaga blok (shu guruh uchun)
            until = datetime.now(timezone.utc) + timedelta(minutes=1)
            await set_block_until_db(chat_id, uid, until)
            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=uid,
                    permissions=BLOCK_PERMS,
                    until_date=until
                )
            except Exception as e:
                log.warning(f"Restrict failed: {e}")
            kb = [
                [InlineKeyboardButton("✅ Men a’zo bo‘ldim", callback_data=f"kanal_azo:{uid}")],
                [InlineKeyboardButton("➕ Guruhga qo‘shish", url=admin_add_link(context.bot.username))]
            ]
            mention = _mention_user_html(msg.from_user)
            chan_lines = "\n".join([f"{i}) {html.escape(ch)}" for i, ch in enumerate(kanal_list, start=1)])
            warn_text = (
                f"⚠️ {mention} guruhda yozish uchun shu kanallarga a'zo bo'ling:\n{chan_lines}\n\n"
                "⏳ 1 daqiqaga bloklandi"
            )# Oldingi ogohlantirishni o'chirish (shu foydalanuvchi uchun)
            key = (chat_id, uid)
            prev_mid = KANAL_WARN_MSG_IDS.get(key)
            if prev_mid:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=prev_mid)
                except Exception:
                    pass

            warn_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=warn_text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            KANAL_WARN_MSG_IDS[key] = warn_msg.message_id
            return

    # Quyidagi qism — eski logikangiz (reklama/ssilka/uyatli sozlar) o'zgarishsiz:
    text = msg.text or msg.caption or ""
    entities = msg.entities or msg.caption_entities or []

    if getattr(msg, "via_bot", None):
        try:
            await msg.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ {msg.from_user.mention_html()}, yashirin ssilka yuborish taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username),
            parse_mode="HTML"
        )
        return

    if has_suspicious_buttons(msg):
        try:
            await msg.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ O‘yin/veb-app tugmali reklama taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username)
        )
        return

    low = text.lower()
    if any(k in low for k in SUSPECT_KEYWORDS):
        try:
            await msg.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ O‘yin reklamalari taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username)
        )
        return

    if getattr(msg.from_user, "is_bot", False):
        has_game = bool(getattr(msg, "game", None))
        has_url_entity = any(ent.type in ("text_link", "url", "mention") for ent in entities)
        has_url_text = any(x in low for x in ("t.me","telegram.me","http://","https://","www.","youtu.be","youtube.com"))
        if has_game or has_url_entity or has_url_text:
            try:
                await msg.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ {msg.from_user.mention_html()}, reklama/ssilka yuborish taqiqlangan!",
                reply_markup=add_to_group_kb(context.bot.username),
                parse_mode="HTML"
            )
            return

    for ent in entities:
        if ent.type in ("text_link", "url", "mention"):
            url = getattr(ent, "url", "") or ""
            if url and ("t.me" in url or "telegram.me" in url or "http://" in url or "https://" in url):
                try:
                    await msg.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ {msg.from_user.mention_html()}, yashirin ssilka yuborish taqiqlangan!",
                    reply_markup=add_to_group_kb(context.bot.username),
                    parse_mode="HTML"
                )
                return

    if any(x in low for x in ("t.me","telegram.me","@","www.","https://youtu.be","http://","https://")):
        try:
            await msg.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ {msg.from_user.mention_html()}, reklama/ssilka yuborish taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username),
            parse_mode="HTML"
        )
        return

    sozlar = matndan_sozlar_olish(text)
    if any(s in UYATLI_SOZLAR for s in sozlar):
        try:
            await msg.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ {msg.from_user.mention_html()}, guruhda so‘kinish taqiqlangan!",
            reply_markup=add_to_group_kb(context.bot.username),
            parse_mode="HTML"
        )
        return

async def majbur_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    # 🔒 Linked kanalning avtomatik forward postlari — teginmaymiz
    try:
        if await is_linked_channel_autoforward(msg, context.bot):
            return
    except Exception:
        pass
    if not msg or not msg.from_user:
        return
    if await is_privileged_message(msg, context.bot):
        return

    chat_id = msg.chat_id
    uid = msg.from_user.id

    settings = await get_group_settings(chat_id)
    limit = int(settings.get("majbur_limit") or 0)
    if limit <= 0:
        return

    # Agar foydalanuvchi hanuz blokda bo'lsa — xabarini o'chirib, hech narsa yubormaymiz
    now = datetime.now(timezone.utc)
    until_old = await get_block_until_db(chat_id, uid)
    if until_old and now < until_old:
        try:
            await msg.delete()
        except Exception:
            pass
        return
    if until_old and now >= until_old:
        await clear_block_db(chat_id, uid)

    if await group_has_priv(chat_id, uid):
        return

    cnt = await get_user_count_db(chat_id, uid)
    if cnt >= limit:
        return

    # Xabarni o'chiramiz
    try:
        await msg.delete()
    except Exception:
        return

    # 1 daqiqaga blok (shu guruh uchun)
    until = datetime.now(timezone.utc) + timedelta(minutes=1)
    await set_block_until_db(chat_id, uid, until)
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=uid,
            permissions=BLOCK_PERMS,
            until_date=until
        )
    except Exception as e:
        log.warning(f"Restrict failed: {e}")

    qoldi = max(limit - cnt, 0)
    kb = [
        [InlineKeyboardButton("✅ Odam qo‘shdim", callback_data=f"check_added:{uid}")],
        [InlineKeyboardButton("🎟 Imtiyoz berish", callback_data=f"grant:{uid}")],
        [InlineKeyboardButton("➕ Guruhga qo‘shish", url=admin_add_link(context.bot.username))],
        [InlineKeyboardButton("⏳ 1 daqiqaga bloklandi", callback_data="noop")]
    ]
    # Oldingi ogohlantirishni o'chirish (shu foydalanuvchi uchun)
    key = (chat_id, uid)
    prev_mid = MAJBUR_WARN_MSG_IDS.get(key)
    if prev_mid:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=prev_mid)
        except Exception:
            pass

    warn_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⚠️ {_mention_user_html(msg.from_user)} guruhda yozish uchun {limit} ta odam qo‘shishingiz kerak! Qolgan: {qoldi} ta.",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML"
    )
    MAJBUR_WARN_MSG_IDS[key] = warn_msg.message_id

# --------- Override join handler: per-group count ----------
async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    adder = msg.from_user
    members = msg.new_chat_members or []
    if not adder:
        return
    chat_id = msg.chat_id
    for m in members:
        if adder.id != m.id:
            await inc_user_count_db(chat_id, adder.id, 1)
    try:
        await msg.delete()
    except Exception:
        pass

# --------- Leave handler: delete “user left / removed” service messages ----------
async def on_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    try:
        await msg.delete()
    except Exception:
        pass

# --------- Override post_init to also init group tables ----------
async def noop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline 'noop' tugmasi uchun: callback query loading'ni darhol yopadi."""
    try:
        if update.callback_query:
            await update.callback_query.answer()
    except Exception:
        pass

async def post_init(app):
    await init_db(app)
    await init_group_db()
    await set_commands(app)

# ==================== END PER-GROUP SETTINGS (DB-backed) ====================

# ---------------------- Setup ----------------------
async def set_commands(app):
    await app.bot.set_my_commands(
        commands=[
            BotCommand("start", "Bot haqida ma'lumot"),
            BotCommand("help", "Bot qo'llanmasi"),
            BotCommand("id", "Sizning ID’ingiz"),
            BotCommand("count", "Siz nechta qo‘shgansiz"),
            BotCommand("top", "TOP 100 ro‘yxati"),
            BotCommand("replycount", "(reply) foydalanuvchi nechta qo‘shganini ko‘rish"),
            BotCommand("majbur", "Majburiy odam limitini (3–30) o‘rnatish"),
            BotCommand("majburoff", "Majburiy qo‘shishni o‘chirish"),
            BotCommand("cleangroup", "Hamma hisobini 0 qilish"),
            BotCommand("cleanuser", "(reply) foydalanuvchi hisobini 0 qilish"),
            BotCommand("ruxsat", "(reply) imtiyoz berish"),
            BotCommand("kanal", "Majburiy kanalni sozlash"),
            BotCommand("kanaloff", "Majburiy kanalni o‘chirish"),
            BotCommand("tun", "Tun rejimini yoqish"),
            BotCommand("tunoff", "Tun rejimini o‘chirish"),
            BotCommand("broadcast", "Barcha DM foydalanuvchilarga matn yuborish (owner)"),
            BotCommand("broadcastpost", "Barcha DM foydalanuvchilarga post-forward (owner)"),
        ],
        scope=BotCommandScopeAllPrivateChats()
    )

async def post_init(app):
    await init_db(app)
    await set_commands(app)


def main():
    start_web()

    log.info("Bot start: polling mode (Railway).")
    if os.getenv("DATABASE_URL") or os.getenv("INTERNAL_DATABASE_URL") or os.getenv("DATABASE_INTERNAL_URL") or os.getenv("DB_URL"):
        log.info("DB: Postgres URL topildi (asyncpg pool init qilinadi).")
    else:
        log.warning("DB: DATABASE_URL topilmadi (DM ro'yxat JSON fallback). Railway'da Postgres ulasangiz, Variables ga DATABASE_URL qo'ying.")

    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help))
    app.add_handler(CommandHandler("id", id_berish))
    app.add_handler(CommandHandler("tun", tun))
    app.add_handler(CommandHandler("tunoff", tunoff))
    app.add_handler(CommandHandler("ruxsat", ruxsat))
    app.add_handler(CommandHandler("kanal", kanal))
    app.add_handler(CommandHandler("kanaloff", kanaloff))
    app.add_handler(CommandHandler("majbur", majbur))
    app.add_handler(CommandHandler("majburoff", majburoff))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("cleangroup", cleangroup))
    app.add_handler(CommandHandler("count", count_cmd))
    app.add_handler(CommandHandler("replycount", replycount))
    app.add_handler(CommandHandler("cleanuser", cleanuser))

    # DM broadcast (owner only)
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("broadcastpost", broadcastpost))

    # Callbacks
    app.add_handler(CallbackQueryHandler(on_set_limit, pattern=r"^set_limit:"))
    app.add_handler(CallbackQueryHandler(kanal_callback, pattern=r"^kanal_azo(?::\d+)?$"))
    app.add_handler(CallbackQueryHandler(on_check_added, pattern=r"^check_added(?::\d+)?$"))
    app.add_handler(CallbackQueryHandler(on_grant_priv, pattern=r"^grant:"))
    app.add_handler(CallbackQueryHandler(noop_cb, pattern=r"^noop$"))

    # Events & Filters
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_member))
    media_filters = (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION | filters.VOICE | filters.VIDEO_NOTE | filters.GAME)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, track_private), group=-3)
    app.add_handler(MessageHandler(media_filters & (~filters.COMMAND), majbur_filter), group=-2)
    app.add_handler(MessageHandler(media_filters & (~filters.COMMAND), reklama_va_soz_filtri), group=-1)

    # Post-init hook
    app.post_init = post_init

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

