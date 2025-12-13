"""
LEGENDS OF ARUNA: JOURNEY TO KAMPAR
Telegram Turn-Based Text RPG
================================

- Satu file Python.
- Menggunakan python-telegram-bot v20+ (async).
- Fokus: sistem state user, scene story, kota, hutan, battle sederhana, stats & skill dasar.
- Banyak konten dari GDD sudah disusun sebagai data, tapi kamu bebas menambah/merapikan.

Cara pakai (singkat):
1. pip install python-telegram-bot==20.7
2. Isi TOKEN_BOT di bawah.
3. Jalankan: python legends_of_aruna_bot.py
4. Chat bot di Telegram, pakai /start

NB: Untuk produksi, sebaiknya simpan state di database, bukan di memory seperti contoh ini.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from advanced_features import (
    AchievementPlugin,
    DailyChallengeManager,
    EnemyAI,
    EventBus,
    EventManager,
    EventType,
    GameEvent,
    HybridStorage,
    LeaderboardManager,
    PluginManager,
    PerformanceMonitor,
    ScheduledTaskManager,
    StatisticsTracker,
    StatusEffect,
    StatusEffectManager,
    StatusType,
    TaskQueue,
    WEAPON_UPGRADE_RULES,
    WeaponAffinity,
    WeaponUpgrade,
    CRAFTING_RECIPES,
    calculate_party_synergies,
    can_craft,
    craft_item,
    perform_weapon_upgrade,
)
from auto_hunt import AutoHuntSession
from config import (
    AUTO_HUNT_RATE_LIMIT,
    CONFIG,
    LOAD_RATE_LIMIT,
    MAP_RATE_LIMIT,
    SAVE_RATE_LIMIT,
    TOKEN_ENV_KEY,
)
from domain_models import BattleSnapshot, Inventory, Party, PlayerProfile, QuestLog
from rate_limiters import RateLimiter
from safe_storage import safe_load_json, safe_save_json
from session_manager import SessionManager
from telegram_utils import safe_edit_message, safe_reply, safe_send_message

# Import sistem baru
from combat_engine import (
    ActionType,
    CombatEngine,
    CombatEntity,
    CombatStats,
    create_skill_database,
    ai_choose_action,
)
from crafting_system import (
    CraftingSystem,
    ItemRarity,
    generate_material_drops,
    roll_material_drops,
)
from jobs_system import (
    JobSystem,
    JobProgress,
    EnergySystem,
    WorkSession,
    format_time_remaining,
)
from combat_ui import (
    create_hp_bar,
    create_mp_bar,
    format_battle_state,
    format_crafting_menu,
    format_job_menu,
    format_victory_message,
    format_work_progress,
)

# ==========================
# KONFIGURASI
# ==========================

TOKEN_BOT = os.getenv(TOKEN_ENV_KEY)
if not TOKEN_BOT:
    raise RuntimeError(
        "BOT token tidak ditemukan. Pastikan variabel lingkungan BOT_TOKEN terisi."
    )
ADMIN_USER_IDS = [123456789]  # <--- Ganti dengan daftar ID Telegram admin/developer

LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logger = logging.getLogger("legends_of_aruna")
logger.setLevel(LOG_LEVEL)
logger.handlers.clear()

console_handler = logging.StreamHandler()
console_handler.setLevel(LOG_LEVEL)
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(console_handler)

try:
    os.makedirs("logs", exist_ok=True)
    file_handler = logging.FileHandler(os.path.join("logs", "bot.log"))
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(file_handler)
except Exception:
    logger.warning("File logging tidak aktif karena konfigurasi gagal.", exc_info=True)

AUTOSAVE_ENABLED = True
AUTOSAVE_BOSS_KEYS = {
    "CORRUPTED_FOREST_GOLEM",
    "HOUND_OF_VOID",
    "VOID_SENTINEL",
    "FEBRI_LORD",
}
AUTOSAVE_NOTICE_TEXT = "Progress otomatis disimpan."
PENDING_AUTOSAVE_FLAG = "_PENDING_AUTOSAVE"
UNKNOWN_CALLBACK_MESSAGE = "Perintah ini tidak dikenal. Coba tekan menu lagi."


EVENT_BUS = EventBus()
PLUGIN_MANAGER = PluginManager(EVENT_BUS)
ACHIEVEMENT_PLUGIN = AchievementPlugin()
STATISTICS_PLUGIN = StatisticsTracker()
PLUGIN_MANAGER.load_plugin(ACHIEVEMENT_PLUGIN)
PLUGIN_MANAGER.load_plugin(STATISTICS_PLUGIN)

LEADERBOARD_MANAGER = LeaderboardManager()
DAILY_CHALLENGE_MANAGER = DailyChallengeManager(LEADERBOARD_MANAGER)
EVENT_MANAGER = EventManager()
PERFORMANCE_MONITOR = PerformanceMonitor()
TASK_QUEUE = TaskQueue()
SCHEDULER = ScheduledTaskManager(TASK_QUEUE, DAILY_CHALLENGE_MANAGER)
HYBRID_STORAGE = HybridStorage()

# Sistem baru: Crafting & Jobs
CRAFTING_SYSTEM = CraftingSystem()
JOB_SYSTEM = JobSystem()
SKILL_DATABASE = create_skill_database()


async def safe_edit_text(
    query: Optional[CallbackQuery],
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    context_info: str = "",
) -> None:
    """Edit pesan callback dengan perlindungan error umum dari Telegram."""

    # Proteksi tambahan agar error API Telegram tidak memutus alur handler callback
    # dan tetap memberikan balasan ramah ke pemain.

    if not query:
        return
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
    except BadRequest as exc:
        message = str(exc)
        if "message is not modified" in message.lower():
            logger.debug(
                "Edit pesan diabaikan (%s) karena tidak ada perubahan: %s",
                context_info or "tanpa konteks",
                message,
            )
            return
        user_id = query.from_user.id if query.from_user else "unknown"
        logger.warning(
            "Gagal mengedit pesan (%s) untuk user %s: %s",
            context_info or "callback",
            user_id,
            message,
        )
        if query.message:
            try:
                await query.message.reply_text(
                    "Terjadi kesalahan saat memperbarui pesan. Coba lagi."
                )
            except Exception:
                logger.exception(
                    "Gagal mengirim pesan fallback setelah error edit (%s)",
                    context_info or "callback",
                )


def parse_callback_parts(data: str, min_parts: int) -> Optional[List[str]]:
    parts = data.split("|")
    if len(parts) < min_parts:
        logger.warning(
            "Callback data tidak lengkap (%s), butuh minimal %s bagian",
            data,
            min_parts,
        )
        return None
    return parts


async def notify_unknown_callback(
    update: Update, message: str = UNKNOWN_CALLBACK_MESSAGE
) -> None:
    query = update.callback_query
    if query:
        await safe_edit_text(query, message)
    elif update.effective_chat:
        try:
            await update.effective_chat.send_message(message)
        except Exception:
            user_id = update.effective_user.id if update.effective_user else "unknown"
            logger.exception(
                "Gagal mengirim pesan unknown callback ke user %s", user_id
            )


# ==========================
# DATA DASAR DARI GDD
# ==========================

# Lokasi utama dan fitur kotanya
LOCATIONS = {
    "SELATPANJANG": {
        "name": "Selatpanjang",
        "min_level": 1,
        "type": "CITY",
        "has_shop": False,
        "has_guild": False,
        "has_inn": False,
        "has_clinic": False,
    },
    "SIAK": {
        "name": "Siak",
        "min_level": 2,
        "type": "CITY",
        "has_shop": True,
        "has_guild": True,
        "has_inn": True,
        "has_clinic": True,
    },
    "RENGAT": {
        "name": "Rengat",
        "min_level": 5,
        "type": "CITY",
        "has_shop": True,
        "has_guild": True,
        "has_inn": True,
        "has_clinic": False,
    },
    "PEKANBARU": {
        "name": "Pekanbaru",
        "min_level": 8,
        "type": "CITY",
        "has_shop": True,
        "has_guild": True,
        "has_inn": True,
        "has_clinic": False,
    },
    "KAMPAR": {
        "name": "Kampar",
        "min_level": 12,
        "type": "CURSED",
        "has_shop": False,
        "has_guild": False,
        "has_inn": False,
        "has_clinic": False,
    },
}

CITY_FEATURES = {
    "SELATPANJANG": {
        "description": "Kota pelabuhan kecil yang menyimpan reruntuhan kekaisaran lama.",
        "shop_items": [],
        "inn_cost": 0,
    },
    "SIAK": {
        "description": "Kota sungai dengan klinik Umar dan cabang guild pemburu.",
        "shop_items": ["WOODEN_SWORD", "LEATHER_ARMOR", "POTION_SMALL", "ETHER_SMALL"],
        "inn_cost": 20,
    },
    "RENGAT": {
        "description": "Kota para magi penjaga segel kuno.",
        "shop_items": [
            "LIGHT_ROBE",
            "POTION_SMALL",
            "POTION_MEDIUM",
            "ETHER_SMALL",
            "ETHER_MEDIUM",
            "HERBAL_TEA",
        ],
        "inn_cost": 30,
    },
    "PEKANBARU": {
        "description": "Metropolis suram tempat para guild mempersiapkan ekspedisi ke Kampar.",
        "shop_items": [
            "BRONZE_SWORD",
            "CHAIN_ARMOR",
            "MYSTIC_CLOAK",
            "POTION_MEDIUM",
            "ETHER_MEDIUM",
            "HERBAL_TEA",
        ],
        "inn_cost": 45,
    },
    "KAMPAR": {
        "description": "Kota terkutuk yang sunyi; semua jalan menuju kastil Zabx.",
        "shop_items": [],
        "inn_cost": 0,
    },
}

# =====================================
# ITEM DEFINITIONS
# =====================================

ITEMS = {
    "POTION_SMALL": {
        "id": "POTION_SMALL",
        "name": "Potion Kecil",
        "description": "Memulihkan 50 HP satu karakter.",
        "type": "consumable",
        "buy_price": 20,
        "sell_price": 10,
        "effects": {"hp_restore": 50, "target": "single"},
    },
    "POTION_MEDIUM": {
        "id": "POTION_MEDIUM",
        "name": "Potion Sedang",
        "description": "Memulihkan 120 HP satu karakter.",
        "type": "consumable",
        "buy_price": 60,
        "sell_price": 30,
        "effects": {"hp_restore": 120, "target": "single"},
    },
    "ETHER_SMALL": {
        "id": "ETHER_SMALL",
        "name": "Ether Kecil",
        "description": "Memulihkan 15 MP satu karakter.",
        "type": "consumable",
        "buy_price": 40,
        "sell_price": 20,
        "effects": {"mp_restore": 15, "target": "single"},
    },
    "ETHER_MEDIUM": {
        "id": "ETHER_MEDIUM",
        "name": "Ether Sedang",
        "description": "Memulihkan 30 MP satu karakter.",
        "type": "consumable",
        "buy_price": 90,
        "sell_price": 45,
        "effects": {"mp_restore": 30, "target": "single"},
    },
    "HERBAL_TEA": {
        "id": "HERBAL_TEA",
        "name": "Teh Herbal Hangat",
        "description": "Ramuan ringan yang menyembuhkan seluruh party sedikit.",
        "type": "consumable",
        "buy_price": 120,
        "sell_price": 60,
        "effects": {"hp_restore": 40, "target": "party"},
    },
    "WOODEN_SWORD": {
        "id": "WOODEN_SWORD",
        "name": "Pedang Kayu",
        "description": "Pedang sederhana untuk pemula.",
        "type": "weapon",
        "buy_price": 50,
        "sell_price": 25,
        "allowed_users": ["ARUNA"],
        "effects": {"atk_bonus": 3},
    },
    "BRONZE_SWORD": {
        "id": "BRONZE_SWORD",
        "name": "Pedang Perunggu",
        "description": "Pedang logam ringan yang menambah daya serang sang ksatria abadi.",
        "type": "weapon",
        "buy_price": 140,
        "sell_price": 70,
        "allowed_users": ["ARUNA"],
        "effects": {"atk_bonus": 7},
    },
    "LEATHER_ARMOR": {
        "id": "LEATHER_ARMOR",
        "name": "Baju Kulit",
        "description": "Pelindung ringan yang meningkatkan ketahanan sang ksatria abadi.",
        "type": "armor",
        "buy_price": 65,
        "sell_price": 30,
        "allowed_users": ["ARUNA"],
        "effects": {"def_bonus": 3, "hp_bonus": 12},
    },
    "CHAIN_ARMOR": {
        "id": "CHAIN_ARMOR",
        "name": "Zirah Rantai",
        "description": "Armor rantai sederhana yang menambah pertahanan fisik.",
        "type": "armor",
        "buy_price": 150,
        "sell_price": 75,
        "allowed_users": ["ARUNA"],
        "effects": {"def_bonus": 6, "hp_bonus": 18},
    },
    "LIGHT_ROBE": {
        "id": "LIGHT_ROBE",
        "name": "Jubah Cahaya",
        "description": "Jubah tipis untuk Umar atau Reza yang menambah MAG.",
        "type": "armor",
        "buy_price": 110,
        "sell_price": 55,
        "allowed_users": ["UMAR", "REZA"],
        "effects": {"def_bonus": 2, "mag_bonus": 3, "hp_bonus": 8},
    },
    "MYSTIC_CLOAK": {
        "id": "MYSTIC_CLOAK",
        "name": "Jubah Mistik",
        "description": "Cloak langka yang melindungi penyihir dari serangan gelap.",
        "type": "armor",
        "buy_price": 200,
        "sell_price": 100,
        "allowed_users": ["ARUNA", "UMAR", "REZA"],
        "effects": {
            "def_bonus": 4,
            "mag_bonus": 4,
            "mp_bonus": 12,
            "passives": {"element_boost": {"CAHAYA": 0.05}},
        },
    },
    "STEEL_SWORD": {
        "id": "STEEL_SWORD",
        "name": "Pedang Baja",
        "description": "Pedang baja berkualitas tinggi",
        "type": "weapon",
        "buy_price": 400,
        "sell_price": 200,
        "allowed_users": ["ARUNA"],
        "effects": {"atk_bonus": 12},
    },
    "GUARDIAN_PLATE": {
        "id": "GUARDIAN_PLATE",
        "name": "Armor Pelindung",
        "description": "Armor berat untuk pertahanan maksimal",
        "type": "armor",
        "buy_price": 500,
        "sell_price": 250,
        "allowed_users": ["ARUNA"],
        "effects": {"def_bonus": 10, "hp_bonus": 30},
    },
    "MITHRIL_BLADE": {
        "id": "MITHRIL_BLADE",
        "name": "Pedang Mithril",
        "description": "Pedang legendaris dari mithril",
        "type": "weapon",
        "buy_price": 1200,
        "sell_price": 600,
        "allowed_users": ["ARUNA"],
        "effects": {"atk_bonus": 18, "spd_bonus": 3},
    },
    "VOID_ARMOR": {
        "id": "VOID_ARMOR",
        "name": "Armor Kehampaan",
        "description": "Armor yang diperkuat energi void",
        "type": "armor",
        "buy_price": 1500,
        "sell_price": 750,
        "allowed_users": ["ARUNA"],
        "effects": {"def_bonus": 15, "hp_bonus": 50, "mag_bonus": 5},
    },
    "HARSAN_LEGACY_BLADE": {
        "id": "HARSAN_LEGACY_BLADE",
        "name": "Pedang Warisan Kekaisaran",
        "description": "Pedang asli milik sang ksatria abadi yang terbangun kembali saat bersatu dengan sigil keabadiannya.",
        "type": "weapon",
        "buy_price": 0,
        "sell_price": 0,
        "allowed_users": ["ARUNA"],
        "effects": {
            "atk_bonus": 16,
            "passives": {
                "element_boost": {"CAHAYA": 0.35},
                "bonus_vs_element": {"GELAP": 0.3, "ABYSS": 0.3},
                "light_skill_amp": 0.15,
            },
        },
    },
}

# Area hutan/dungeon terdekat per kota
# Monster definitions lengkap sesuai GDD
MONSTERS = {
    "SHADOW_SLIME": {
        "name": "Shadow Slime",
        "area": "HUTAN_SELATPANJANG",
        "level": 1,
        "hp": 24,
        "mp": 5,
        "atk": 5,
        "defense": 3,
        "mag": 1,
        "spd": 3,
        "luck": 1,
        "xp": 6,
        "gold": 4,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 1.2,
    },
    "MIST_WOLF": {
        "name": "Mist Wolf",
        "area": "HUTAN_SELATPANJANG",
        "level": 2,
        "hp": 32,
        "mp": 5,
        "atk": 7,
        "defense": 4,
        "mag": 1,
        "spd": 5,
        "luck": 2,
        "xp": 10,
        "gold": 6,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 1.0,
    },
    "SCARRED_PANTHER": {
        "name": "Scarred Panther",
        "area": "HUTAN_SELATPANJANG",
        "level": 4,
        "hp": 90,
        "mp": 10,
        "atk": 13,
        "defense": 7,
        "mag": 3,
        "spd": 9,
        "luck": 5,
        "xp": 40,
        "gold": 30,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "rarity": "RARE",
        "encounter_weight": 0.08,
    },
    "ECHO_SHADE": {
        "name": "Gaung Bayangan",
        "area": "HUTAN_SELATPANJANG",
        "level": 5,
        "hp": 110,
        "mp": 18,
        "atk": 14,
        "defense": 8,
        "mag": 8,
        "spd": 8,
        "luck": 6,
        "xp": 65,
        "gold": 48,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "rarity": "RARE",
        "encounter_weight": 0.03,
    },
    "SHADOW_BANDIT": {
        "name": "Shadow Bandit",
        "area": "HUTAN_SIAK",
        "level": 3,
        "hp": 38,
        "mp": 10,
        "atk": 9,
        "defense": 5,
        "mag": 2,
        "spd": 6,
        "luck": 2,
        "xp": 14,
        "gold": 12,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 1.1,
    },
    "GATE_SPIRIT": {
        "name": "Gate Spirit",
        "area": "HUTAN_SIAK",
        "level": 4,
        "hp": 44,
        "mp": 20,
        "atk": 6,
        "defense": 5,
        "mag": 9,
        "spd": 4,
        "luck": 3,
        "xp": 18,
        "gold": 14,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 0.9,
        "can_escape": False,
    },
    "HERB_GUARDIAN": {
        "name": "Herb Guardian",
        "area": "HUTAN_SIAK",
        "level": 6,
        "hp": 95,
        "mp": 15,
        "atk": 11,
        "defense": 10,
        "mag": 6,
        "spd": 6,
        "luck": 4,
        "xp": 40,
        "gold": 26,
        "element": "ALAM",
        "weakness": ["API"],
        "resist": ["ALAM"],
        "encounter_weight": 0.6,
        "can_escape": False,
    },
    "BLOODTHORN_VINE": {
        "name": "Bloodthorn Vine",
        "area": "HUTAN_SIAK",
        "level": 7,
        "hp": 150,
        "mp": 22,
        "atk": 14,
        "defense": 12,
        "mag": 6,
        "spd": 7,
        "luck": 5,
        "xp": 55,
        "gold": 38,
        "element": "ALAM",
        "weakness": ["API"],
        "resist": ["ALAM"],
        "rarity": "RARE",
        "encounter_weight": 0.05,
    },
    "CORRUPTED_TREANT": {
        "name": "Corrupted Treant",
        "area": "HUTAN_RENGAT",
        "level": 5,
        "hp": 70,
        "mp": 10,
        "atk": 9,
        "defense": 9,
        "mag": 4,
        "spd": 3,
        "luck": 2,
        "xp": 24,
        "gold": 18,
        "element": "ALAM",
        "weakness": ["API"],
        "resist": ["ALAM"],
        "encounter_weight": 1.1,
    },
    "FOREST_WISP": {
        "name": "Forest Wisp",
        "area": "HUTAN_RENGAT",
        "level": 6,
        "hp": 46,
        "mp": 32,
        "atk": 4,
        "defense": 3,
        "mag": 11,
        "spd": 7,
        "luck": 4,
        "xp": 26,
        "gold": 20,
        "element": "CAHAYA",
        "weakness": ["GELAP"],
        "resist": ["CAHAYA"],
        "encounter_weight": 0.9,
    },
    "SEAL_WARDEN": {
        "name": "Penjaga Segel Retak",
        "area": "HUTAN_RENGAT",
        "level": 9,
        "hp": 140,
        "mp": 30,
        "atk": 15,
        "defense": 13,
        "mag": 15,
        "spd": 7,
        "luck": 4,
        "xp": 65,
        "gold": 38,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 0.6,
    },
    "CORRUPTED_FOREST_GOLEM": {
        "name": "Corrupted Forest Golem",
        "area": "HUTAN_RENGAT",
        "level": 8,
        "hp": 180,
        "mp": 30,
        "atk": 20,
        "defense": 16,
        "mag": 8,
        "spd": 4,
        "luck": 3,
        "xp": 95,
        "gold": 60,
        "element": "ALAM",
        "weakness": ["API"],
        "resist": ["ALAM"],
        "encounter_weight": 0.2,
        "can_escape": False,
    },
    "PHANTOM_MERCHANT": {
        "name": "Phantom Merchant",
        "area": "HUTAN_PEKANBARU",
        "level": 9,
        "hp": 80,
        "mp": 28,
        "atk": 11,
        "defense": 9,
        "mag": 11,
        "spd": 7,
        "luck": 5,
        "xp": 38,
        "gold": 30,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 1.0,
    },
    "CURSED_MILITIA": {
        "name": "Cursed Militia",
        "area": "HUTAN_PEKANBARU",
        "level": 10,
        "hp": 95,
        "mp": 12,
        "atk": 15,
        "defense": 13,
        "mag": 4,
        "spd": 6,
        "luck": 3,
        "xp": 45,
        "gold": 34,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 0.9,
    },
    "ANCIENT_WOLF": {
        "name": "Serigala Purba",
        "area": "HUTAN_PEKANBARU",
        "level": 12,
        "hp": 180,
        "mp": 22,
        "atk": 21,
        "defense": 15,
        "mag": 8,
        "spd": 11,
        "luck": 6,
        "xp": 120,
        "gold": 90,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "rarity": "RARE",
        "encounter_weight": 0.04,
    },
    "ABYSS_HOUND": {
        "name": "Abyss Hound",
        "area": "KAMPAR_LUAR",
        "level": 13,
        "hp": 110,
        "mp": 22,
        "atk": 19,
        "defense": 11,
        "mag": 6,
        "spd": 12,
        "luck": 4,
        "xp": 55,
        "gold": 45,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 1.0,
    },
    "VOID_KNIGHT": {
        "name": "Void Knight",
        "area": "KAMPAR_LUAR",
        "level": 15,
        "hp": 145,
        "mp": 34,
        "atk": 22,
        "defense": 18,
        "mag": 10,
        "spd": 8,
        "luck": 5,
        "xp": 80,
        "gold": 58,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 0.6,
    },
    "ABYSS_REVENANT": {
        "name": "Revenant Abyss",
        "area": "KAMPAR_LUAR",
        "level": 16,
        "hp": 230,
        "mp": 45,
        "atk": 26,
        "defense": 19,
        "mag": 18,
        "spd": 11,
        "luck": 6,
        "xp": 180,
        "gold": 110,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "rarity": "RARE",
        "encounter_weight": 0.03,
    },
    "HOUND_OF_VOID": {
        "name": "Hound of Void",
        "area": "KASTIL_FEBRI",
        "level": 17,
        "hp": 220,
        "mp": 50,
        "atk": 26,
        "defense": 16,
        "mag": 16,
        "spd": 14,
        "luck": 5,
        "xp": 120,
        "gold": 0,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 0.3,
        "can_escape": False,
    },
    "VOID_SENTINEL": {
        "name": "Void Sentinel",
        "area": "KASTIL_FEBRI",
        "level": 18,
        "hp": 260,
        "mp": 40,
        "atk": 28,
        "defense": 20,
        "mag": 18,
        "spd": 10,
        "luck": 6,
        "xp": 150,
        "gold": 0,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 0.2,
        "can_escape": False,
    },
    "LUMINAR_SENTINEL": {
        "name": "Luminar Sentinel",
        "area": "HARSAN_SHRINE",
        "level": 11,
        "hp": 160,
        "mp": 40,
        "atk": 18,
        "defense": 14,
        "mag": 18,
        "spd": 10,
        "luck": 5,
        "xp": 90,
        "gold": 80,
        "element": "CAHAYA",
        "weakness": ["GELAP"],
        "resist": ["CAHAYA"],
    },
    "ABYSS_SHADE": {
        "name": "Abyss Shade",
        "area": "HARSAN_SHRINE",
        "level": 10,
        "hp": 130,
        "mp": 30,
        "atk": 17,
        "defense": 12,
        "mag": 16,
        "spd": 12,
        "luck": 6,
        "xp": 70,
        "gold": 70,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 0.5,
    },
    "FEBRI_LORD": {
        "name": "Almighty Zabx",
        "area": "KASTIL_FEBRI",
        "level": 20,
        "hp": 400,
        "mp": 120,
        "atk": 32,
        "defense": 22,
        "mag": 32,
        "spd": 16,
        "luck": 8,
        "xp": 999,
        "gold": 0,
        "element": "GELAP",
        "weakness": ["CAHAYA"],
        "resist": ["GELAP"],
        "encounter_weight": 0.05,
        "can_escape": False,
    },
}

# Peluang encounter monster langka per area (bisa disesuaikan untuk balancing).
RARE_ENCOUNTER_CHANCE = {
    "HUTAN_SELATPANJANG": 0.05,
    "HUTAN_SIAK": 0.05,
    "HUTAN_RENGAT": 0.06,
    "HUTAN_PEKANBARU": 0.06,
    "KAMPAR_LUAR": 0.08,
    "KASTIL_FEBRI": 0.12,
}

RARE_LEVEL_BUFFER = 2  # selisih level minimal player agar rare bisa muncul


def _infer_monster_rank(monster: Dict[str, Any]) -> str:
    rating = (
        monster.get("hp", 50) / 25 + monster.get("atk", 5) + monster.get("defense", 5)
    )
    if rating < 6:
        return "WEAK"
    if rating < 12:
        return "MEDIUM"
    if rating < 20:
        return "STRONG"
    if monster.get("rarity") == "RARE":
        return "RARE"
    return "BOSS"


for _mid, _mdata in MONSTERS.items():
    _mdata.setdefault("rank", _infer_monster_rank(_mdata))


HUNTING_AREAS = {
    "HUNT_SELATPANJANG": {
        "name": "Perbatasan Selatpanjang",
        "area_key": "HUTAN_SELATPANJANG",
        "min_level": 1,
        "element": "NEUTRAL",
        "level_range": "Lv 1-3",
        "description": "Reruntuhan dermaga lama tempat makhluk kabut berkeliaran.",
        "monsters": ["SHADOW_SLIME", "MIST_WOLF", "SCARRED_PANTHER"],
    },
    "HUNT_SIAK": {
        "name": "Hutan Berkabut Siak",
        "area_key": "HUTAN_SIAK",
        "min_level": 3,
        "element": "WATER",
        "level_range": "Lv 3-5",
        "description": "Jalur sungai lama dengan roh kabut yang agresif.",
        "monsters": ["SHADOW_BANDIT", "GATE_SPIRIT", "HERB_GUARDIAN"],
    },
    "HUNT_RENGAT": {
        "name": "Rimba Arcana Rengat",
        "area_key": "HUTAN_RENGAT",
        "min_level": 5,
        "element": "EARTH",
        "level_range": "Lv 5-8",
        "description": "Pepohonan sihir yang menyimpan energi segel tua.",
        "monsters": ["CORRUPTED_TREANT", "FOREST_WISP", "CORRUPTED_FOREST_GOLEM"],
    },
    "HUNT_PEKANBARU": {
        "name": "Hutan Pekanbaru",
        "area_key": "HUTAN_PEKANBARU",
        "min_level": 8,
        "element": "FIRE",
        "level_range": "Lv 8-12",
        "description": "Jalur terbakar akibat percobaan demon.",
        "monsters": ["PHANTOM_MERCHANT", "CURSED_MILITIA", "ANCIENT_WOLF"],
    },
    "HUNT_KAMPAR": {
        "name": "Kabut Kampar",
        "area_key": "KAMPAR_LUAR",
        "min_level": 12,
        "element": "DARK",
        "level_range": "Lv 12-18",
        "description": "Kabut kutukan yang menutup jalan ke kastil.",
        "monsters": ["ABYSS_HOUND", "VOID_KNIGHT", "ABYSS_REVENANT"],
    },
    "KASTIL_ZABX": {
        "name": "Kastil Terkurung Zabx",
        "area_key": "KASTIL_FEBRI",
        "min_level": 18,
        "element": "DARK",
        "level_range": "Lv 18-20",
        "description": "Benteng terakhir sang Raja Iblis.",
        "monsters": ["VOID_SENTINEL", "FEBRI_LORD"],
    },
}

DEFAULT_CITY_HUNTING = {
    "SELATPANJANG": "HUNT_SELATPANJANG",
    "SIAK": "HUNT_SIAK",
    "RENGAT": "HUNT_RENGAT",
    "PEKANBARU": "HUNT_PEKANBARU",
    "KAMPAR": "HUNT_KAMPAR",
}


GUILD_QUESTS = {
    "SIAK_WOLVES": {
        "name": "Kabut Serigala",
        "location": "SIAK",
        "type": "HUNT",
        "target": "MIST_WOLF",
        "required_amount": 5,
        "min_level": 3,
        "reward_gold": 80,
        "reward_items": {"POTION_SMALL": 2},
        "description": "Lenyapkan kawanan Mist Wolf yang mengganggu jalur suplai klinik.",
    },
    "RENGAT_GOLEM": {
        "name": "Jantung Golem Retak",
        "location": "RENGAT",
        "type": "HUNT",
        "target": "CORRUPTED_FOREST_GOLEM",
        "required_amount": 3,
        "min_level": 6,
        "reward_gold": 150,
        "reward_items": {"ETHER_MEDIUM": 1},
        "description": "Hancurkan Golem korup di rimba arcana sebelum mereka menghancurkan menara sihir.",
    },
    "PEKANBARU_EMBER": {
        "name": "Arwah Bara",
        "location": "PEKANBARU",
        "type": "HUNT",
        "target": "CURSED_MILITIA",
        "required_amount": 6,
        "min_level": 9,
        "reward_gold": 220,
        "reward_items": {"POTION_MEDIUM": 2},
        "description": "Guild ingin meredam Ember Phantom sebelum nyala iblis menyebar ke kota.",
    },
}

ELEMENTAL_ADVANTAGE: Dict[str, Dict[str, float]] = {
    "FIRE": {"WATER": 0.6, "EARTH": 1.4},
    "WATER": {"FIRE": 2.2, "EARTH": 0.8},
    "EARTH": {"WIND": 2.0, "FIRE": 0.75},
    "WIND": {"EARTH": 0.7},
    "LIGHT": {"DARK": 1.8},
    "DARK": {"LIGHT": 1.8},
}


def reset_auto_hunt_state(state: "GameState") -> None:
    state.auto_hunt = False
    state.auto_hunt_area = None
    state.auto_hunt_stats = {}
    if state.auto_hunt_session:
        state.auto_hunt_session.active = False


def get_city_guild_quests(location: str) -> Dict[str, Dict[str, Any]]:
    return {
        qid: data
        for qid, data in GUILD_QUESTS.items()
        if data.get("location") == location
    }


def find_completed_quest(state: GameState, quest_id: str) -> Optional[QuestState]:
    for quest in state.quests_completed:
        if quest.id == quest_id:
            return quest
    return None


def accept_guild_quest(state: GameState, quest_id: str) -> Tuple[bool, str]:
    data = GUILD_QUESTS.get(quest_id)
    if not data:
        return False, "Quest tidak ditemukan."
    if quest_id in state.quests_active:
        return False, "Quest ini sudah kamu ambil."
    if find_completed_quest(state, quest_id):
        return False, "Quest ini sudah pernah kamu selesaikan."
    if highest_party_level(state) < data.get("min_level", 1):
        return False, "Levelmu belum cukup untuk menerima quest ini."
    quest = QuestState(
        id=quest_id,
        type=data.get("type", "HUNT"),
        target=data.get("target"),
        required_amount=data.get("required_amount", 0),
        reward_gold=data.get("reward_gold", 0),
        reward_items=dict(data.get("reward_items", {})),
        description=data.get("description", ""),
        status="ACTIVE",
    )
    state.quests_active[quest_id] = quest
    return True, f"Quest guild '{data.get('name')}' diterima."


def complete_guild_quest(state: GameState, quest_id: str) -> Tuple[bool, str]:
    quest = state.quests_active.get(quest_id)
    if not quest:
        return False, "Quest ini belum kamu ambil."
    if quest.status != "COMPLETED":
        return False, "Quest ini belum selesai."
    quest.reward_received = True
    quest.status = "REWARDED"
    quest.completion_time = quest.completion_time or datetime.utcnow().isoformat()
    if quest.reward_gold:
        state.gold += quest.reward_gold
    for item_id, qty in quest.reward_items.items():
        adjust_inventory(state, item_id, qty)
    state.quests_completed.append(quest)
    state.quests_active.pop(quest_id, None)
    reward_parts = []
    if quest.reward_gold:
        reward_parts.append(f"{quest.reward_gold} Gold")
    for item_id, qty in quest.reward_items.items():
        item = ITEMS.get(item_id, {"name": item_id})
        reward_parts.append(f"{item['name']} x{qty}")
    reward_text = ", ".join(reward_parts) if reward_parts else "pengakuan guild"
    return True, f"Quest '{quest.id}' selesai. Hadiah: {reward_text}."


def update_hunt_quest_progress(state: GameState, defeated_ids: List[str]) -> List[str]:
    if not defeated_ids or not state.quests_active:
        return []
    counts = Counter([mid for mid in defeated_ids if mid])
    logs: List[str] = []
    for quest in state.quests_active.values():
        if quest.type != "HUNT" or not quest.target:
            continue
        gained = counts.get(quest.target, 0)
        if not gained:
            continue
        before = quest.progress
        quest.progress = min(quest.required_amount, quest.progress + gained)
        logs.append(
            f"{quest.id}: {quest.progress}/{quest.required_amount} target terbunuh (+'{gained}')."
        )
        if quest.progress >= quest.required_amount and quest.status != "COMPLETED":
            quest.status = "COMPLETED"
            quest.completion_time = datetime.utcnow().isoformat()
            logs.append(f"{quest.id}: Laporkan ke guild untuk klaim hadiah.")
    return logs


# Drop tables per area
DROP_TABLES = {
    "HUTAN_SELATPANJANG": [
        {"item_id": "POTION_SMALL", "chance": 0.35, "min_qty": 1, "max_qty": 2},
        {"item_id": "WOODEN_SWORD", "chance": 0.08, "min_qty": 1, "max_qty": 1},
    ],
    "HUTAN_SIAK": [
        {"item_id": "POTION_SMALL", "chance": 0.3, "min_qty": 1, "max_qty": 2},
        {"item_id": "LEATHER_ARMOR", "chance": 0.06, "min_qty": 1, "max_qty": 1},
        {"item_id": "ETHER_SMALL", "chance": 0.1, "min_qty": 1, "max_qty": 1},
    ],
    "HUTAN_RENGAT": [
        {"item_id": "POTION_MEDIUM", "chance": 0.25, "min_qty": 1, "max_qty": 1},
        {"item_id": "LIGHT_ROBE", "chance": 0.08, "min_qty": 1, "max_qty": 1},
        {"item_id": "HERBAL_TEA", "chance": 0.12, "min_qty": 1, "max_qty": 1},
    ],
    "HUTAN_PEKANBARU": [
        {"item_id": "ETHER_SMALL", "chance": 0.25, "min_qty": 1, "max_qty": 1},
        {"item_id": "MYSTIC_CLOAK", "chance": 0.05, "min_qty": 1, "max_qty": 1},
        {"item_id": "ETHER_MEDIUM", "chance": 0.16, "min_qty": 1, "max_qty": 1},
    ],
    "KAMPAR_LUAR": [
        {"item_id": "POTION_MEDIUM", "chance": 0.35, "min_qty": 1, "max_qty": 2},
        {"item_id": "BRONZE_SWORD", "chance": 0.07, "min_qty": 1, "max_qty": 1},
    ],
    "HARSAN_SHRINE": [
        {"item_id": "ETHER_SMALL", "chance": 0.4, "min_qty": 1, "max_qty": 2},
    ],
}

# Skill dasar lengkap
SKILLS = {
    "SLASH": {
        "name": "Tebasan",
        "mp_cost": 0,
        "type": "PHYS",
        "power": 1.0,
        "element": "NETRAL",
        "description": "Serangan fisik standar sang ksatria abadi.",
    },
    "LIGHT_BURST": {
        "name": "Light Burst",
        "mp_cost": 5,
        "type": "MAG",
        "power": 1.3,
        "element": "CAHAYA",
        "description": "Serangan cahaya fokus ke satu musuh.",
    },
    "RADIANT_SLASH": {
        "name": "Radiant Slash",
        "mp_cost": 8,
        "type": "PHYS",
        "power": 1.3,
        "element": "CAHAYA",
        "description": "Tebasan fisik bercahaya yang melemahkan musuh.",
    },
    "SERANGAN_KEMBAR": {
        "name": "Serangan Kembar",
        "mp_cost": 7,
        "type": "PHYS",
        "power": 0.85,
        "hits": 2,
        "element": "NETRAL",
        "description": "Menyerang musuh 2x dengan kecepatan pedang warisan sang ksatria.",
    },
    "TRISULA_CAHAYA": {
        "name": "Trisula Cahaya",
        "mp_cost": 14,
        "type": "PHYS",
        "power": 0.75,
        "hits": 3,
        "element": "CAHAYA",
        "description": "Tebasan cahaya beruntun 3x yang membelah kegelapan.",
    },
    "GUARDIAN_OATH": {
        "name": "Guardian's Oath",
        "mp_cost": 10,
        "type": "BUFF_DEF_SELF",
        "duration": 3,
        "buffs": {"defense": 5},
        "description": "Sang ksatria memperkuat pertahanan dan resistensi kegelapan sementara.",
    },
    "LIGHT_WAVE": {
        "name": "Light Wave",
        "mp_cost": 14,
        "type": "MAG",
        "power": 0.9,
        "element": "CAHAYA",
        "description": "Gelombang cahaya yang menghantam semua musuh.",
    },
    "ARUNA_CORE_AWAKENING": {
        "name": "Sigil Keabadian",
        "mp_cost": 0,
        "type": "LIMIT_HEAL",
        "description": "Skill cerita: sigil keabadian meledak, menyembuhkan dan memberkati seluruh party sekali per battle.",
    },
    "HEAL": {
        "name": "Heal",
        "mp_cost": 4,
        "type": "HEAL_SINGLE",
        "power": 0.3,
        "description": "Memulihkan HP seorang ally.",
    },
    "SMALL_BARRIER": {
        "name": "Small Barrier",
        "mp_cost": 5,
        "type": "BUFF_DEF_SINGLE",
        "duration": 3,
        "buffs": {"defense": 4},
        "description": "Meningkatkan DEF satu ally untuk beberapa turn.",
    },
    "GROUP_HEAL": {
        "name": "Group Heal",
        "mp_cost": 10,
        "type": "HEAL_ALL",
        "power": 0.25,
        "description": "Heal kecil ke seluruh party.",
    },
    "PURIFY": {
        "name": "Purify",
        "mp_cost": 8,
        "type": "CLEANSE",
        "target": "party",
        "description": "Menghilangkan 1 debuff dari ally.",
    },
    "REVIVE": {
        "name": "Revive",
        "mp_cost": 18,
        "type": "REVIVE",
        "revive_ratio": 0.4,
        "description": "Menghidupkan ally yang tumbang.",
    },
    "SAFIYA_GRACE": {
        "name": "Grace Safiya",
        "mp_cost": 20,
        "type": "HEAL_ALL",
        "power": 0.5,
        "description": "Ultimate Umar: heal besar seluruh tim dan membersihkan luka batin.",
    },
    "FIRE_BOLT": {
        "name": "Fire Bolt",
        "mp_cost": 4,
        "type": "MAG",
        "power": 1.2,
        "element": "API",
        "description": "Serangan api standar Reza.",
    },
    "MANA_SHIELD": {
        "name": "Mana Shield",
        "mp_cost": 6,
        "type": "BUFF_SPECIAL",
        "duration": 3,
        "description": "Mengubah damage fisik menjadi konsumsi MP sementara.",
    },
    "CHAIN_LIGHTNING": {
        "name": "Chain Lightning",
        "mp_cost": 10,
        "type": "MAG",
        "power": 0.8,
        "element": "PETIR",
        "description": "Serangan AoE petir dengan peluang stun.",
    },
    "ARCANE_BARRAGE": {
        "name": "Badai Arkan",
        "mp_cost": 12,
        "type": "MAG",
        "power": 0.55,
        "hits": 3,
        "element": "PETIR",
        "description": "Semburan sihir 3x berturut-turut ke satu musuh.",
    },
    "ARCANE_FOCUS": {
        "name": "Arcane Focus",
        "mp_cost": 8,
        "type": "BUFF_SELF",
        "duration": 3,
        "buffs": {"mag": 5},
        "penalties": {"spd": -2},
        "description": "Meningkatkan MAG Reza namun menurunkan SPD sementara.",
    },
    "LEGACY_RADIANCE": {
        "name": "Legacy Radiance",
        "mp_cost": 12,
        "type": "PHYS",
        "power": 1.6,
        "element": "CAHAYA",
        "description": "Tebasan cahaya dari pedang warisan kekaisaran yang membakar kegelapan.",
    },
    "ABYSS_SEAL": {
        "name": "Abyss Seal",
        "mp_cost": 15,
        "type": "DEBUFF_ENEMY",
        "debuffs": {"mag": -4, "spd": -3},
        "duration": 3,
        "description": "Menurunkan MAG dan SPD musuh.",
    },
    "MASTER_LEGACY": {
        "name": "Warisan Sang Guru",
        "mp_cost": 20,
        "type": "BUFF_TEAM",
        "buffs": {"atk": 3, "mag": 3, "defense": 3},
        "duration": 3,
        "description": "Ultimate Reza: buff ATK/MAG/DEF dan tekad melindungi dari kegelapan.",
    },
}

# Base stats karakter sesuai GDD (disederhanakan)
CHAR_BASE = {
    "ARUNA": {
        "name": "Ksatria Abadi",
        "level": 1,
        "hp": 40,
        "mp": 15,
        "atk": 8,
        "defense": 6,
        "mag": 5,
        "spd": 7,
        "luck": 5,
        "skills": ["SLASH"],
    },
    "UMAR": {
        "name": "Umar",
        "level": 1,
        "hp": 32,
        "mp": 25,
        "atk": 4,
        "defense": 4,
        "mag": 8,
        "spd": 6,
        "luck": 6,
        "skills": ["HEAL"],
    },
    "REZA": {
        "name": "Reza",
        "level": 1,
        "hp": 30,
        "mp": 30,
        "atk": 3,
        "defense": 4,
        "mag": 10,
        "spd": 5,
        "luck": 5,
        "skills": ["FIRE_BOLT"],
    },
}

LEVEL_XP = {
    1: 30,
    2: 70,
    3: 120,
    4: 180,
    5: 250,
    6: 330,
    7: 420,
    8: 520,
    9: 630,
    10: 750,
}

CHAR_GROWTH = {
    "ARUNA": {"hp": 9, "mp": 3, "atk": 3, "defense": 2, "mag": 2, "spd": 2, "luck": 1},
    "UMAR": {"hp": 8, "mp": 5, "atk": 2, "defense": 2, "mag": 3, "spd": 1, "luck": 2},
    "REZA": {"hp": 8, "mp": 5, "atk": 2, "defense": 2, "mag": 4, "spd": 1, "luck": 1},
}

CHAR_SKILL_UNLOCKS = {
    "ARUNA": [
        (3, "LIGHT_BURST"),
        (6, "RADIANT_SLASH"),
        (7, "SERANGAN_KEMBAR"),
        (9, "GUARDIAN_OATH"),
        (10, "TRISULA_CAHAYA"),
        (12, "LIGHT_WAVE"),
    ],
    "UMAR": [
        (2, "HEAL"),
        (5, "SMALL_BARRIER"),
        (7, "GROUP_HEAL"),
        (10, "PURIFY"),
        (13, "REVIVE"),
    ],
    "REZA": [
        (2, "FIRE_BOLT"),
        (5, "CHAIN_LIGHTNING"),
        (7, "MANA_SHIELD"),
        (9, "ARCANE_BARRAGE"),
        (10, "ARCANE_FOCUS"),
        (14, "ABYSS_SEAL"),
    ],
}

# STORY DATA LOADER
# Story/story data diambil dari file eksternal
SCENE_FILES = [os.path.join("data", "scenes_main.json")]
SCENES: Dict[str, Dict[str, Any]] = {}


def _normalize_flags(flag_data: Any) -> Dict[str, List[str]]:
    set_flags: List[str] = []
    unset_flags: List[str] = []
    if isinstance(flag_data, dict):
        set_raw = flag_data.get("set") or flag_data.get("set_flags") or []
        unset_raw = flag_data.get("unset") or flag_data.get("unset_flags") or []
        if isinstance(set_raw, list):
            set_flags = [f for f in set_raw if isinstance(f, str)]
        if isinstance(unset_raw, list):
            unset_flags = [f for f in unset_raw if isinstance(f, str)]
    elif isinstance(flag_data, list):
        set_flags = [f for f in flag_data if isinstance(f, str)]
    return {"set": set_flags, "unset": unset_flags}


def _normalize_requirements(req_data: Any) -> Dict[str, Any]:
    req_flags: List[str] = []
    min_level: Optional[int] = None
    if isinstance(req_data, dict):
        flags_raw = req_data.get("flags", [])
        if isinstance(flags_raw, list):
            req_flags = [f for f in flags_raw if isinstance(f, str)]
        level_raw = req_data.get("min_level")
        if isinstance(level_raw, int):
            min_level = level_raw
    return {"flags": req_flags, "min_level": min_level}


def _normalize_choice(
    choice: Any, scene_id: str, index: int
) -> Optional[Dict[str, Any]]:
    if not isinstance(choice, dict):
        return None
    label = choice.get("label")
    next_scene = choice.get("next_scene") or choice.get("next")
    battle_key = choice.get("battle")
    command = choice.get("command")
    callback_data = (
        choice.get("callback_data")
        or command
        or next_scene
        or battle_key
        or f"SCENECHOICE|{scene_id}|{index}"
    )
    return {
        "label": label or "Lanjut",
        "next_scene": next_scene,
        "battle": battle_key,
        "command": command,
        "flags": _normalize_flags(choice.get("flags")),
        "requirements": _normalize_requirements(choice.get("requirements")),
        "callback_data": str(callback_data),
    }


def _normalize_text(text_data: Any) -> List[str]:
    if isinstance(text_data, list):
        return [str(line) for line in text_data]
    if isinstance(text_data, str):
        return text_data.split("\n")
    return []


def load_scenes(paths: Optional[List[str]] = None) -> None:
    """Muat semua file scene eksternal ke dalam kamus global SCENES."""

    global SCENES
    paths = paths or SCENE_FILES
    # Mengurai file JSON eksternal memungkinkan penulis cerita menambah dialog
    # tanpa menyentuh kode Python, sehingga alur lebih aman dan terpisah.
    loaded: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        if not os.path.exists(path):
            logger.warning("Scene file tidak ditemukan: %s", path)
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Gagal memuat scene file %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("Format scene file tidak valid (harus dict): %s", path)
            continue
        for scene_id, scene_data in data.items():
            text_lines = _normalize_text(scene_data.get("text", []))
            flags = _normalize_flags(scene_data.get("flags"))
            requirements = _normalize_requirements(scene_data.get("requirements"))
            choices_raw = scene_data.get("choices", [])
            choices: List[Dict[str, Any]] = []
            if isinstance(choices_raw, list):
                for idx, choice in enumerate(choices_raw):
                    normalized = _normalize_choice(choice, scene_id, idx)
                    if normalized:
                        choices.append(normalized)
            loaded[scene_id] = {
                "text": text_lines,
                "choices": choices,
                "flags": flags,
                "requirements": requirements,
            }
    SCENES = loaded


def get_scene(scene_id: str) -> Optional[Dict[str, Any]]:
    return SCENES.get(scene_id)


# Muat scene utama saat startup
load_scenes()

# ==========================
# STRUKTUR STATE GAME
# ==========================


@dataclass
class CharacterState:
    id: str
    name: str
    level: int
    hp: int
    max_hp: int
    mp: int
    max_mp: int
    atk: int
    defense: int
    mag: int
    spd: int
    luck: int
    skills: List[str] = field(default_factory=list)
    weapon_id: Optional[str] = None
    armor_id: Optional[str] = None
    equipment_upgrades: Dict[str, WeaponUpgrade] = field(default_factory=dict)
    weapon_affinity: Dict[str, WeaponAffinity] = field(default_factory=dict)
    temporary_bonuses: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "mp": self.mp,
            "max_mp": self.max_mp,
            "atk": self.atk,
            "defense": self.defense,
            "mag": self.mag,
            "spd": self.spd,
            "luck": self.luck,
            "skills": list(self.skills),
            "weapon_id": self.weapon_id,
            "armor_id": self.armor_id,
            "equipment_upgrades": {
                item: {
                    "level": up.level,
                    "bonus_stats": dict(up.bonus_stats),
                    "base_success_rate": up.base_success_rate,
                }
                if isinstance(up, WeaponUpgrade)
                else dict(up)
                for item, up in self.equipment_upgrades.items()
            },
            "weapon_affinity": {
                item: {
                    "kills_with_weapon": affinity.kills_with_weapon,
                    "mastery_level": affinity.mastery_level,
                }
                if isinstance(affinity, WeaponAffinity)
                else dict(affinity)
                for item, affinity in self.weapon_affinity.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CharacterState":
        return cls(
            id=data.get("id", "UNKNOWN"),
            name=data.get("name", ""),
            level=data.get("level", 1),
            hp=data.get("hp", 1),
            max_hp=data.get("max_hp", 1),
            mp=data.get("mp", 0),
            max_mp=data.get("max_mp", 0),
            atk=data.get("atk", 1),
            defense=data.get("defense", 1),
            mag=data.get("mag", 1),
            spd=data.get("spd", 1),
            luck=data.get("luck", 1),
            skills=list(data.get("skills", [])),
            weapon_id=data.get("weapon_id"),
            armor_id=data.get("armor_id"),
            equipment_upgrades={
                item: WeaponUpgrade(
                    level=info.get("level", 0),
                    bonus_stats=info.get("bonus_stats", {}),
                    base_success_rate=info.get("base_success_rate", 0.8),
                )
                for item, info in (data.get("equipment_upgrades", {}) or {}).items()
            },
            weapon_affinity={
                item: WeaponAffinity(
                    kills_with_weapon=info.get("kills_with_weapon", 0),
                    mastery_level=info.get("mastery_level", 0),
                )
                for item, info in (data.get("weapon_affinity", {}) or {}).items()
            },
        )


@dataclass
class BattleTurnState:
    turn_order: List[str] = field(default_factory=list)
    current_turn_index: int = -1
    enemies: List[Dict[str, Any]] = field(default_factory=list)
    awaiting_player_input: bool = False
    active_token: Optional[str] = None
    pending_action: Optional[Dict[str, Any]] = None


@dataclass
class QuestState:
    id: str
    type: str
    target: Optional[str] = None
    required_amount: int = 0
    progress: int = 0
    reward_gold: int = 0
    reward_items: Dict[str, int] = field(default_factory=dict)
    status: str = "ACTIVE"
    description: str = ""
    completion_time: Optional[str] = None
    reward_received: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "target": self.target,
            "required_amount": self.required_amount,
            "progress": self.progress,
            "reward_gold": self.reward_gold,
            "reward_items": dict(self.reward_items),
            "status": self.status,
            "description": self.description,
            "completion_time": self.completion_time,
            "reward_received": self.reward_received,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuestState":
        return cls(
            id=data.get("id", "UNKNOWN_QUEST"),
            type=data.get("type", "HUNT"),
            target=data.get("target"),
            required_amount=int(data.get("required_amount", 0)),
            progress=int(data.get("progress", 0)),
            reward_gold=int(data.get("reward_gold", 0)),
            reward_items=dict(data.get("reward_items", {})),
            status=data.get("status", "ACTIVE"),
            description=data.get("description", ""),
            completion_time=data.get("completion_time"),
            reward_received=bool(data.get("reward_received", False)),
        )


@dataclass
class GameState:
    """Kontainer utama state per pemain agar semua agent membaca sumber yang sama."""

    user_id: int
    scene_id: str = "CH0_S1"
    location: str = "SELATPANJANG"
    player_name: Optional[str] = None
    in_battle: bool = False
    battle_enemies: List[Dict[str, Any]] = field(default_factory=list)
    battle_turn: str = "PLAYER"
    battle_state: BattleTurnState = field(default_factory=BattleTurnState)
    gold: int = 0
    main_progress: str = "PROLOG"
    party: Dict[str, CharacterState] = field(default_factory=dict)
    party_order: List[str] = field(default_factory=list)
    inventory: Dict[str, int] = field(default_factory=dict)
    xp_pool: Dict[str, int] = field(default_factory=dict)
    flags: Dict[str, Any] = field(default_factory=dict)
    equipment_upgrades: Dict[str, WeaponUpgrade] = field(default_factory=dict)
    weapon_affinity: Dict[str, WeaponAffinity] = field(default_factory=dict)
    active_world_events: List[Dict[str, Any]] = field(default_factory=list)
    return_scene_after_battle: Optional[str] = None
    loss_scene_after_battle: Optional[str] = None
    auto_hunt: bool = False
    auto_hunt_area: Optional[str] = None
    auto_hunt_stats: Dict[str, Any] = field(default_factory=dict)
    quests_active: Dict[str, QuestState] = field(default_factory=dict)
    quests_completed: List[QuestState] = field(default_factory=list)
    battle_snapshot: BattleSnapshot = field(default_factory=BattleSnapshot)
    daily_challenge_id: Optional[str] = None
    daily_score: int = 0
    version: int = 3  # Upgrade version untuk sistem baru
    status_manager: StatusEffectManager = field(default_factory=StatusEffectManager)
    
    # Sistem baru: Crafting & Materials
    materials: Dict[str, int] = field(default_factory=dict)  # material_id -> quantity
    
    # Sistem baru: Jobs & Energy
    current_job_id: Optional[str] = None  # ID pekerjaan saat ini
    job_progress: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # job_id -> progress data
    energy_system: Optional[EnergySystem] = None  # Sistem energy untuk kerja
    active_work_session: Optional[WorkSession] = None  # Work session yang sedang berlangsung
    
    # Combat engine baru
    combat_engine: Optional[CombatEngine] = None  # Engine pertempuran yang ditingkatkan
    battle_message_id: Optional[int] = None  # ID pesan battle untuk editing
    profile: PlayerProfile = field(init=False)
    inventory_model: Inventory = field(init=False)
    quest_log: QuestLog = field(init=False)
    party_model: Party = field(init=False)
    auto_hunt_session: AutoHuntSession = field(default_factory=AutoHuntSession)

    def __post_init__(self):
        self.ensure_flag_defaults()
        self.profile = PlayerProfile(
            user_id=self.user_id,
            name=self.player_name,
            location=self.location,
            main_progress=self.main_progress,
            scene_id=self.scene_id,
            gold=self.gold,
        )
        self.inventory_model = Inventory(items=dict(self.inventory))
        self.quest_log = QuestLog(
            active={qid: qstate.to_dict() for qid, qstate in self.quests_active.items()},
            completed=[entry.to_dict() for entry in self.quests_completed],
        )
        self.party_model = Party(members=self.party, order=self.party_order)
        self.battle_snapshot = BattleSnapshot(
            in_battle=self.in_battle,
            enemies=list(self.battle_enemies),
            turn=self.battle_turn,
        )
        
        # Inisialisasi sistem baru jika belum ada
        if self.energy_system is None:
            self.energy_system = EnergySystem()
        
        # Update energy saat load state
        if self.energy_system:
            self.energy_system.update_energy()

    def sync_models_from_fields(self) -> None:
        self.profile.name = self.player_name
        self.profile.location = self.location
        self.profile.main_progress = self.main_progress
        self.profile.scene_id = self.scene_id
        self.profile.gold = self.gold
        self.inventory_model.items = dict(self.inventory)
        self.quest_log.active = {
            qid: qstate.to_dict() if hasattr(qstate, "to_dict") else dict(qstate)
            for qid, qstate in self.quests_active.items()
        }
        self.quest_log.completed = [
            quest.to_dict() if hasattr(quest, "to_dict") else dict(quest)
            for quest in self.quests_completed
        ]
        self.party_model.members = self.party
        self.party_model.order = self.party_order
        self.battle_snapshot.in_battle = self.in_battle
        self.battle_snapshot.enemies = list(self.battle_enemies)
        self.battle_snapshot.turn = self.battle_turn

    def sync_fields_from_models(self) -> None:
        self.player_name = self.profile.name
        self.location = self.profile.location
        self.main_progress = self.profile.main_progress
        self.scene_id = self.profile.scene_id
        self.gold = self.profile.gold
        self.inventory = dict(self.inventory_model.items)
        self.quests_active = {
            qid: QuestState.from_dict(data)
            if isinstance(data, dict)
            else data
            for qid, data in self.quest_log.active.items()
        }
        self.quests_completed = [
            QuestState.from_dict(entry) if isinstance(entry, dict) else entry
            for entry in self.quest_log.completed
        ]
        self.party = self.party_model.members
        self.party_order = list(self.party_model.order)
        self.in_battle = self.battle_snapshot.in_battle
        self.battle_enemies = list(self.battle_snapshot.enemies)
        self.battle_turn = self.battle_snapshot.turn

    def ensure_flag_defaults(self):
        default_flags = {
            "HAS_UMAR": False,
            "HAS_REZA": False,
            "UMAR_QUEST_DONE": False,
            "REZA_QUEST_DONE": False,
            "QUEST_WEAPON_STARTED": False,
            "QUEST_WEAPON_DONE": False,
            "WEAPON_QUEST_STARTED": False,
            "WEAPON_QUEST_DONE": False,
            "SYNERGY_BONUSES": {},
        }
        for key, value in default_flags.items():
            self.flags.setdefault(key, value)

    def to_dict(self) -> Dict[str, Any]:
        self.sync_models_from_fields()
        safe_flags = {
            k: v
            for k, v in self.flags.items()
            if k
            not in {
                "ACTIVE_BUFFS",
                "DEFENDING",
                "LIGHT_BUFF_TURNS",
                "ARUNA_LIMIT_USED",
                "CURRENT_BATTLE_AREA",
                "MANA_SHIELD",
            }
        }
        return {
            "scene_id": self.scene_id,
            "location": self.location,
            "player_name": self.player_name,
            "main_progress": self.main_progress,
            "gold": self.gold,
            "party_order": list(self.party_order),
            "party": {cid: ch.to_dict() for cid, ch in self.party.items()},
            "inventory": dict(self.inventory),
            "xp_pool": dict(self.xp_pool),
            "flags": safe_flags,
            "equipment_upgrades": {
                item: {
                    "level": up.level,
                    "bonus_stats": dict(up.bonus_stats),
                    "base_success_rate": up.base_success_rate,
                }
                if isinstance(up, WeaponUpgrade)
                else dict(up)
                for item, up in self.equipment_upgrades.items()
            },
            "weapon_affinity": {
                item: {
                    "kills_with_weapon": affinity.kills_with_weapon,
                    "mastery_level": affinity.mastery_level,
                }
                if isinstance(affinity, WeaponAffinity)
                else dict(affinity)
                for item, affinity in self.weapon_affinity.items()
            },
            "active_world_events": list(self.active_world_events),
            "auto_hunt": self.auto_hunt,
            "auto_hunt_area": self.auto_hunt_area,
            "quests_active": {
                qid: quest.to_dict() for qid, quest in self.quests_active.items()
            },
            "quests_completed": [quest.to_dict() for quest in self.quests_completed],
            "daily_challenge_id": self.daily_challenge_id,
            "daily_score": self.daily_score,
            "version": self.version,
            "materials": dict(self.materials),
            "current_job_id": self.current_job_id,
            "job_progress": dict(self.job_progress),
            "energy_system": self.energy_system.to_dict() if self.energy_system else None,
            "active_work_session": self.active_work_session.to_dict() if self.active_work_session else None,
        }

    @classmethod
    def from_dict(cls, user_id: int, data: Dict[str, Any]) -> "GameState":
        state = cls(user_id=user_id)
        state.scene_id = data.get("scene_id", state.scene_id)
        state.location = data.get("location", state.location)
        state.player_name = data.get("player_name")
        state.main_progress = data.get("main_progress", state.main_progress)
        state.gold = data.get("gold", 0)
        party_data = data.get("party", {})
        state.party = {
            cid: CharacterState.from_dict(ch) for cid, ch in party_data.items()
        }
        saved_order = data.get("party_order", [])
        state.party_order = [cid for cid in saved_order if cid in state.party]
        for cid in state.party:
            if cid not in state.party_order:
                state.party_order.append(cid)
        if not state.party:
            state.ensure_aruna()
        state.inventory = data.get("inventory", {})
        state.xp_pool = data.get("xp_pool", {})
        for cid in state.party_order:
            state.xp_pool.setdefault(cid, 0)
        state.flags = data.get("flags", {})
        state.equipment_upgrades = {
            item: WeaponUpgrade(
                level=info.get("level", 0),
                bonus_stats=info.get("bonus_stats", {}),
                base_success_rate=info.get("base_success_rate", 0.8),
            )
            for item, info in (data.get("equipment_upgrades", {}) or {}).items()
        }
        state.weapon_affinity = {
            item: WeaponAffinity(
                kills_with_weapon=info.get("kills_with_weapon", 0),
                mastery_level=info.get("mastery_level", 0),
            )
            for item, info in (data.get("weapon_affinity", {}) or {}).items()
        }
        state.ensure_flag_defaults()
        state.auto_hunt = False
        state.auto_hunt_area = None
        state.auto_hunt_stats = {}
        quests_active_raw = data.get("quests_active", {}) or {}
        state.quests_active = {
            qid: QuestState.from_dict(qdata)
            for qid, qdata in quests_active_raw.items()
            if isinstance(qdata, dict)
        }
        state.quests_completed = [
            QuestState.from_dict(entry)
            for entry in data.get("quests_completed", [])
            if isinstance(entry, dict)
        ]
        state.in_battle = False
        state.battle_enemies = []
        state.battle_state = BattleTurnState()
        state.return_scene_after_battle = None
        state.loss_scene_after_battle = None
        state.active_world_events = data.get("active_world_events", [])
        state.daily_challenge_id = data.get("daily_challenge_id")
        state.daily_score = int(data.get("daily_score", 0))
        state.version = int(data.get("version", 1))
        
        # Load new systems
        state.materials = data.get("materials", {})
        state.current_job_id = data.get("current_job_id")
        state.job_progress = data.get("job_progress", {})
        
        # Load energy system
        energy_data = data.get("energy_system")
        if energy_data:
            state.energy_system = EnergySystem.from_dict(energy_data)
        else:
            state.energy_system = EnergySystem()
        
        # Load work session
        work_data = data.get("active_work_session")
        if work_data:
            state.active_work_session = WorkSession.from_dict(work_data)
        else:
            state.active_work_session = None
        
        if not state.player_name:
            hero = state.party.get("ARUNA")
            if hero:
                state.player_name = hero.name
        state.sync_models_from_fields()
        return state

    def ensure_aruna(self):
        if "ARUNA" not in self.party:
            base = CHAR_BASE["ARUNA"]
            self.party["ARUNA"] = CharacterState(
                id="ARUNA",
                name=self.player_name or base["name"],
                level=base["level"],
                hp=base["hp"],
                max_hp=base["hp"],
                mp=base["mp"],
                max_mp=base["mp"],
                atk=base["atk"],
                defense=base["defense"],
                mag=base["mag"],
                spd=base["spd"],
                luck=base["luck"],
                skills=list(base["skills"]),
            )
            self.party_order.append("ARUNA")
            self.xp_pool["ARUNA"] = 0
        else:
            hero = self.party.get("ARUNA")
            if hero and self.player_name:
                hero.name = self.player_name

    def reset_for_new_journey(self):
        self.scene_id = "CH0_S1"
        self.location = "SELATPANJANG"
        self.in_battle = False
        self.battle_enemies = []
        self.battle_state = BattleTurnState()
        self.battle_turn = "PLAYER"
        self.gold = 0
        self.main_progress = "PROLOG"
        self.party = {}
        self.party_order = []
        self.inventory = {}
        self.xp_pool = {}
        self.flags = {}
        self.equipment_upgrades = {}
        self.weapon_affinity = {}
        self.auto_hunt = False
        self.auto_hunt_area = None
        self.auto_hunt_stats = {}
        self.quests_active = {}
        self.quests_completed = []
        self.active_world_events = []
        self.daily_challenge_id = None
        self.daily_score = 0
        self.ensure_flag_defaults()
        self.ensure_aruna()

    def add_umar(self):
        if "UMAR" not in self.party:
            base = CHAR_BASE["UMAR"]
            self.party["UMAR"] = CharacterState(
                id="UMAR",
                name=base["name"],
                level=base["level"],
                hp=base["hp"],
                max_hp=base["hp"],
                mp=base["mp"],
                max_mp=base["mp"],
                atk=base["atk"],
                defense=base["defense"],
                mag=base["mag"],
                spd=base["spd"],
                luck=base["luck"],
                skills=list(base["skills"]),
            )
            self.party_order.append("UMAR")
            self.xp_pool["UMAR"] = 0
            self.flags["HAS_UMAR"] = True

    def add_reza(self):
        if "REZA" not in self.party:
            base = CHAR_BASE["REZA"]
            self.party["REZA"] = CharacterState(
                id="REZA",
                name=base["name"],
                level=base["level"],
                hp=base["hp"],
                max_hp=base["hp"],
                mp=base["mp"],
                max_mp=base["mp"],
                atk=base["atk"],
                defense=base["defense"],
                mag=base["mag"],
                spd=base["spd"],
                luck=base["luck"],
                skills=list(base["skills"]),
            )
            self.party_order.append("REZA")
            self.xp_pool["REZA"] = 0
            self.flags["HAS_REZA"] = True


# Storage in-memory dengan TTL + LRU
SAVE_DIR = "saves"  # Untuk VPS, pastikan folder ini ada & bisa ditulis (chmod/chown sesuai user bot)


def _on_session_evict(user_id: int, state: "GameState") -> None:
    if state.auto_hunt_session and state.auto_hunt_session.active:
        try:
            # best-effort cancellation; cannot await here
            if state.auto_hunt_session.task:
                state.auto_hunt_session.task.cancel()
        except Exception:
            logger.exception("Gagal membatalkan auto-hunt saat evict user %s", user_id)


SESSION_MANAGER = SessionManager(on_evict=_on_session_evict)
SAVE_RATE_LIMITER = RateLimiter(*SAVE_RATE_LIMIT)
LOAD_RATE_LIMITER = RateLimiter(*LOAD_RATE_LIMIT)
AUTO_HUNT_RATE_LIMITER = RateLimiter(*AUTO_HUNT_RATE_LIMIT)
MAP_RATE_LIMITER = RateLimiter(*MAP_RATE_LIMIT)


def get_save_path(user_id: int) -> str:
    return os.path.join(SAVE_DIR, f"{user_id}.json")


def serialize_game_state(state: "GameState") -> Dict[str, Any]:
    return state.to_dict()


def save_game_state(user_id: int, state: "GameState") -> bool:
    path = get_save_path(user_id)
    payload = serialize_game_state(state)
    success = safe_save_json(path, payload)
    HYBRID_STORAGE.save_state_sync(user_id, payload)
    if not success:
        logger.error("Gagal menyimpan progress user %s secara aman", user_id)
    return success


def load_game_state(user_id: int) -> Optional["GameState"]:
    path = get_save_path(user_id)
    data = safe_load_json(path)
    if not data:
        data = HYBRID_STORAGE.load_state_sync(user_id)
    if not data:
        return None
    try:
        return GameState.from_dict(user_id=user_id, data=data)
    except Exception as exc:
        logger.exception("Gagal deserialisasi save user %s: %s", user_id, exc)
        return None


def maybe_autosave(state: "GameState", reason: str = "checkpoint") -> bool:
    """Simpan otomatis state pemain bila fitur aktif."""

    if not AUTOSAVE_ENABLED:
        logger.debug(
            "Autosave dimatikan. Checkpoint user %s (%s) dilewati.",
            state.user_id,
            reason,
        )
        return False

    success = save_game_state(state.user_id, state)
    if success:
        logger.info("Autosave berhasil untuk user %s (%s)", state.user_id, reason)
    else:
        logger.warning("Autosave gagal untuk user %s (%s)", state.user_id, reason)
    return success


def queue_pending_autosave(
    state: "GameState", reason: str, notify: bool = False
) -> None:
    state.flags[PENDING_AUTOSAVE_FLAG] = {"reason": reason, "notify": notify}


def flush_pending_autosave(state: "GameState") -> Optional[str]:
    payload = state.flags.pop(PENDING_AUTOSAVE_FLAG, None)
    if not payload:
        return None
    reason = payload.get("reason", "checkpoint")
    notify = bool(payload.get("notify"))
    saved = maybe_autosave(state, reason)
    if saved and notify:
        return AUTOSAVE_NOTICE_TEXT
    return None


def trigger_checkpoint_autosave(
    state: "GameState", reason: str, notify: bool = False
) -> Optional[str]:
    saved = maybe_autosave(state, reason)
    if saved and notify:
        return AUTOSAVE_NOTICE_TEXT
    return None


def append_optional_text(base: Optional[str], addition: Optional[str]) -> str:
    base = base or ""
    if addition:
        return f"{base}\n\n{addition}" if base else addition
    return base


def get_game_state(user_id: int) -> "GameState":
    def _factory(uid: int) -> GameState:
        state = GameState(user_id=uid)
        state.ensure_aruna()
        return state

    return SESSION_MANAGER.get_state(user_id, _factory)


def get_user_lock(user_id: int) -> asyncio.Lock:
    return SESSION_MANAGER.get_lock(user_id)


def apply_synergy_bonuses(state: "GameState") -> None:
    bonuses = calculate_party_synergies(state.party_order)
    state.flags["SYNERGY_BONUSES"] = bonuses
    for cid, character in state.party.items():
        character.temporary_bonuses = dict(bonuses.get(cid, {}))


EQUIP_BONUS_MAP = {
    "atk_bonus": "atk",
    "def_bonus": "defense",
    "mag_bonus": "mag",
    "hp_bonus": "max_hp",
    "mp_bonus": "max_mp",
    "spd_bonus": "spd",
    "luck_bonus": "luck",
}


def get_equipment_stat_bonuses(character: CharacterState) -> Dict[str, int]:
    bonuses: Dict[str, int] = {attr: 0 for attr in EQUIP_BONUS_MAP.values()}
    for slot in [character.weapon_id, character.armor_id]:
        if not slot:
            continue
        item = ITEMS.get(slot)
        if not item:
            continue
        effects = item.get("effects", {})
        for effect_key, attr in EQUIP_BONUS_MAP.items():
            bonus = effects.get(effect_key, 0)
            if bonus:
                bonuses[attr] = bonuses.get(attr, 0) + bonus
        upgrade = character.equipment_upgrades.get(slot)
        if upgrade:
            applied = upgrade.apply_bonus()
            for effect_key, attr in EQUIP_BONUS_MAP.items():
                bonus = applied.get(effect_key, 0)
                if bonus:
                    bonuses[attr] = bonuses.get(attr, 0) + bonus
    return bonuses


def get_effective_stat(character: CharacterState, attr: str) -> int:
    bonuses = get_equipment_stat_bonuses(character)
    temp_bonus = character.temporary_bonuses.get(attr, 0)
    affinity_bonus = 0
    if attr == "atk" and character.weapon_id:
        affinity = character.weapon_affinity.get(character.weapon_id)
        if affinity:
            affinity_bonus += affinity.atk_bonus
    return getattr(character, attr, 0) + bonuses.get(attr, 0) + temp_bonus + affinity_bonus


def get_effective_max_hp(character: CharacterState) -> int:
    return get_effective_stat(character, "max_hp")


def get_effective_max_mp(character: CharacterState) -> int:
    return get_effective_stat(character, "max_mp")


def get_effective_combat_stats(character: CharacterState) -> Dict[str, int]:
    bonuses = get_equipment_stat_bonuses(character)
    stats: Dict[str, int] = {}
    for attr in ["atk", "defense", "mag", "spd", "luck", "max_hp", "max_mp"]:
        stats[attr] = getattr(character, attr, 0) + bonuses.get(attr, 0)
    return stats


def clamp_resource_to_effective_cap(character: CharacterState):
    effective_max_hp = get_effective_max_hp(character)
    effective_max_mp = get_effective_max_mp(character)
    if character.hp > effective_max_hp:
        character.hp = effective_max_hp
    if character.mp > effective_max_mp:
        character.mp = effective_max_mp


def format_effective_stat_summary(character: CharacterState) -> str:
    stats = get_effective_combat_stats(character)
    return (
        f"{character.name} Lv {character.level} | HP {character.hp}/{stats['max_hp']} | "
        f"MP {character.mp}/{stats['max_mp']} | ATK {stats['atk']} DEF {stats['defense']} MAG {stats['mag']}"
    )


def adjust_inventory(state: GameState, item_id: str, delta: int) -> int:
    if delta == 0:
        return state.inventory.get(item_id, 0)
    new_value = state.inventory.get(item_id, 0) + delta
    if new_value <= 0:
        state.inventory.pop(item_id, None)
        return 0
    state.inventory[item_id] = new_value
    return new_value


def generate_loot_for_area(area_id: str) -> List[Tuple[str, int]]:
    loot: List[Tuple[str, int]] = []
    for entry in DROP_TABLES.get(area_id, []):
        chance = entry.get("chance", 0)
        if random.random() > chance:
            continue
        qty = random.randint(entry.get("min_qty", 1), entry.get("max_qty", 1))
        loot.append((entry["item_id"], qty))
    return loot


def grant_battle_drops(state: GameState) -> Tuple[List[str], List[Tuple[str, int]]]:
    area = state.flags.get("CURRENT_BATTLE_AREA")
    if not area:
        return [], []
    drops: List[str] = []
    details: List[Tuple[str, int]] = []
    for item_id, qty in generate_loot_for_area(area):
        adjust_inventory(state, item_id, qty)
        item = ITEMS.get(item_id)
        name = item["name"] if item else item_id
        drops.append(f"{name} x{qty}")
        details.append((item_id, qty))
    return drops, details


def unequip_item(state: GameState, char_id: str, slot: str) -> Tuple[bool, str]:
    character = state.party.get(char_id)
    if not character:
        return False, "Karakter tidak ditemukan."
    slot_attr = "weapon_id" if slot == "weapon" else "armor_id"
    equipped_id = getattr(character, slot_attr)
    if not equipped_id:
        return False, "Tidak ada equipment yang terpasang."
    item = ITEMS.get(equipped_id)
    adjust_inventory(state, equipped_id, 1)
    setattr(character, slot_attr, None)
    clamp_resource_to_effective_cap(character)
    message = (
        f"{character.name} melepas {item['name']}." if item else "Equipment dilepas."
    )
    return True, message


def equip_item(
    state: GameState, char_id: str, item_id: str, expected_type: Optional[str] = None
) -> Tuple[bool, str]:
    character = state.party.get(char_id)
    if not character:
        return False, "Karakter tidak ditemukan."
    item = ITEMS.get(item_id)
    if not item:
        return False, "Item tidak dikenal."
    if item.get("type") not in {"weapon", "armor"}:
        return False, "Item itu bukan equipment."
    if expected_type and item.get("type") != expected_type:
        return False, "Item tidak cocok dengan slot."
    allowed = item.get("allowed_users")
    if allowed and char_id not in allowed:
        return False, f"{item['name']} tidak cocok untuk {character.name}."
    qty = state.inventory.get(item_id, 0)
    if qty <= 0:
        return False, "Kamu tidak memiliki item tersebut."
    slot_attr = "weapon_id" if item["type"] == "weapon" else "armor_id"
    currently_equipped = getattr(character, slot_attr)
    if currently_equipped:
        unequip_item(state, char_id, item["type"])
    adjust_inventory(state, item_id, -1)
    setattr(character, slot_attr, item_id)
    clamp_resource_to_effective_cap(character)
    return True, f"{character.name} memasang {item['name']}."


def get_equipped_owners(state: GameState, item_id: str) -> List[str]:
    owners = []
    for cid in state.party_order:
        character = state.party.get(cid)
        if not character:
            continue
        if character.weapon_id == item_id or character.armor_id == item_id:
            owners.append(character.name)
    return owners


def get_character_passive_effects(character: CharacterState) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for slot in [character.weapon_id, character.armor_id]:
        if not slot:
            continue
        item = ITEMS.get(slot)
        if not item:
            continue
        passives = item.get("effects", {}).get("passives", {})
        for key, value in passives.items():
            if key == "element_boost" and isinstance(value, dict):
                dest = result.setdefault("element_boost", {})
                for elem, bonus in value.items():
                    dest[elem] = dest.get(elem, 0.0) + bonus
            else:
                result[key] = result.get(key, 0) + value
    return result


def get_character_weapon_element(character: CharacterState) -> str:
    weapon = ITEMS.get(character.weapon_id) if character.weapon_id else None
    if not weapon:
        return "NETRAL"
    return weapon.get("effects", {}).get("element", "NETRAL")


def list_equippable_items(
    state: GameState, char_id: str, slot_type: str
) -> List[Tuple[str, Dict[str, Any], int]]:
    results: List[Tuple[str, Dict[str, Any], int]] = []
    for item_id, qty in state.inventory.items():
        if qty <= 0:
            continue
        item = ITEMS.get(item_id)
        if not item or item.get("type") != slot_type:
            continue
        allowed = item.get("allowed_users")
        if allowed and char_id not in allowed:
            continue
        results.append((item_id, item, qty))
    return results


def xp_required_for_next_level(current_level: int) -> int:
    current_level = max(1, current_level)
    if current_level in LEVEL_XP:
        return LEVEL_XP[current_level]
    max_defined_level = max(LEVEL_XP)
    base_requirement = LEVEL_XP[max_defined_level]
    growth_step = LEVEL_XP[max_defined_level] - LEVEL_XP.get(max_defined_level - 1, 0)
    return base_requirement + growth_step * (current_level - max_defined_level)


def grant_skill_to_character(
    character: CharacterState, skill_id: str, logs: Optional[List[str]] = None
):
    if skill_id not in SKILLS:
        return
    if skill_id in character.skills:
        return
    character.skills.append(skill_id)
    if logs is not None:
        logs.append(
            f"{character.name} mempelajari skill baru: {SKILLS[skill_id]['name']}!"
        )


def apply_growth(character: CharacterState) -> Optional[Dict[str, int]]:
    growth = CHAR_GROWTH.get(character.id)
    if not growth:
        return None
    character.level += 1
    increments = {
        "hp": growth["hp"],
        "mp": growth["mp"],
        "atk": growth["atk"],
        "defense": growth["defense"],
        "mag": growth["mag"],
        "spd": growth["spd"],
        "luck": growth["luck"],
    }
    character.max_hp += increments["hp"]
    character.max_mp += increments["mp"]
    character.atk += increments["atk"]
    character.defense += increments["defense"]
    character.mag += increments["mag"]
    character.spd += increments["spd"]
    character.luck += increments["luck"]
    character.hp = get_effective_max_hp(character)
    character.mp = get_effective_max_mp(character)
    return increments


def check_level_up(state: GameState) -> List[str]:
    messages: List[str] = []
    for cid in state.party_order:
        character = state.party.get(cid)
        if not character:
            continue
        pool = state.xp_pool.get(cid, 0)
        while pool >= xp_required_for_next_level(character.level):
            requirement = xp_required_for_next_level(character.level)
            pool -= requirement
            before_stats = get_effective_combat_stats(character)
            apply_growth(character)
            after_stats = get_effective_combat_stats(character)
            lines = [
                "==== LEVEL UP ====",
                f"{character.name} naik ke Level {character.level}!",
                "",
                f"HP: {before_stats['max_hp']} -> {after_stats['max_hp']}",
                f"MP: {before_stats['max_mp']} -> {after_stats['max_mp']}",
                f"ATK: {before_stats['atk']} -> {after_stats['atk']}",
                f"DEF: {before_stats['defense']} -> {after_stats['defense']}",
                f"MAG: {before_stats['mag']} -> {after_stats['mag']}",
                f"SPD: {before_stats['spd']} -> {after_stats['spd']}",
            ]
            messages.append("\n".join(lines))
            for req_level, skill in CHAR_SKILL_UNLOCKS.get(cid, []):
                if character.level >= req_level:
                    grant_skill_to_character(character, skill, messages)
        state.xp_pool[cid] = pool
    return messages


def handle_after_battle_xp_and_level_up(
    state: GameState, total_xp: int, total_gold: int
) -> List[str]:
    for cid in state.party_order:
        state.xp_pool[cid] += total_xp
    state.gold += total_gold
    return check_level_up(state)


def manual_targeting_enabled(state: GameState) -> bool:
    """Placeholder to toggle manual target selection in the future."""
    return bool(state.flags.get("MANUAL_TARGETING"))


def clear_manual_target_request(state: GameState):
    state.flags.pop("PENDING_TARGET", None)


def make_char_buff_key(char_id: str) -> str:
    return f"CHAR:{char_id}"


def make_enemy_buff_key(index: int) -> str:
    return f"ENEMY:{index}"


def get_buff_target(state: GameState, key: str):
    if not key:
        return None
    if key.startswith("CHAR:"):
        cid = key.split(":", 1)[1]
        return state.party.get(cid)
    if key.startswith("ENEMY:"):
        try:
            idx = int(key.split(":", 1)[1])
        except ValueError:
            return None
        if 0 <= idx < len(state.battle_enemies):
            return state.battle_enemies[idx]
    return None


def adjust_stat_value(target: Any, stat: str, amount: int):
    if target is None or amount == 0:
        return
    if isinstance(target, CharacterState):
        current = getattr(target, stat, None)
        if current is not None:
            setattr(target, stat, current + amount)
    elif isinstance(target, dict):
        target[stat] = target.get(stat, 0) + amount


def apply_temporary_modifier(
    state: GameState, target_key: str, stat: str, amount: int, duration: int
):
    if amount == 0 or duration <= 0:
        return
    target = get_buff_target(state, target_key)
    if target is None:
        return
    adjust_stat_value(target, stat, amount)
    buffs = state.flags.setdefault("ACTIVE_BUFFS", {})
    buffs.setdefault(target_key, []).append(
        {
            "stat": stat,
            "amount": amount,
            "turns": duration,
        }
    )


def cleanse_character(state: GameState, char_id: str) -> int:
    key = make_char_buff_key(char_id)
    buffs = state.flags.get("ACTIVE_BUFFS", {}).get(key, [])
    if not buffs:
        return 0
    target = state.party.get(char_id)
    kept = []
    removed = 0
    for buff in buffs:
        if buff["amount"] < 0:
            adjust_stat_value(target, buff["stat"], -buff["amount"])
            removed += 1
        else:
            kept.append(buff)
    active = state.flags.get("ACTIVE_BUFFS", {})
    if kept:
        active[key] = kept
    else:
        active.pop(key, None)
    return removed


def clear_active_buffs(state: GameState):
    active = state.flags.pop("ACTIVE_BUFFS", None)
    if not active:
        return
    for key, buffs in active.items():
        target = get_buff_target(state, key)
        if target is None:
            continue
        for buff in buffs:
            adjust_stat_value(target, buff["stat"], -buff["amount"])


def reset_battle_flags(state: GameState):
    clear_active_buffs(state)
    for key in [
        "LIGHT_BUFF_TURNS",
        "ARUNA_LIMIT_USED",
        "CURRENT_BATTLE_AREA",
    ]:
        state.flags.pop(key, None)
    state.flags.pop("DEFENDING", None)
    state.flags.pop("MANA_SHIELD", None)
    state.battle_state = BattleTurnState()


def tick_buffs(state: GameState) -> List[str]:
    logs: List[str] = []
    active = state.flags.get("ACTIVE_BUFFS")
    if active:
        to_remove = []
        for key, buffs in active.items():
            target = get_buff_target(state, key)
            remaining = []
            for buff in buffs:
                buff["turns"] -= 1
                if buff["turns"] <= 0:
                    adjust_stat_value(target, buff["stat"], -buff["amount"])
                    if target and isinstance(target, CharacterState):
                        logs.append(
                            f"Buff {buff['stat']} pada {target.name} menghilang."
                        )
                else:
                    remaining.append(buff)
            if remaining:
                active[key] = remaining
            else:
                to_remove.append(key)
        for key in to_remove:
            active.pop(key, None)
        if not active:
            state.flags.pop("ACTIVE_BUFFS", None)
    shields = state.flags.get("MANA_SHIELD")
    if shields:
        expired: List[str] = []
        for cid in list(shields.keys()):
            shields[cid] -= 1
            if shields[cid] <= 0:
                expired.append(cid)
        for cid in expired:
            shields.pop(cid, None)
            target = state.party.get(cid)
            if target:
                logs.append(f"Mana Shield di sekitar {target.name} menghilang.")
        if not shields:
            state.flags.pop("MANA_SHIELD", None)
    if state.flags.get("LIGHT_BUFF_TURNS"):
        state.flags["LIGHT_BUFF_TURNS"] -= 1
        if state.flags["LIGHT_BUFF_TURNS"] <= 0:
            state.flags.pop("LIGHT_BUFF_TURNS", None)
            logs.append("Aura sigil keabadian mereda.")
    return logs


def living_party_members(state: GameState) -> List[str]:
    members: List[str] = []
    for cid in state.party_order:
        character = state.party.get(cid)
        if character and character.hp > 0:
            members.append(cid)
    return members


def living_enemies(state: GameState) -> List[tuple]:
    enemies = state.battle_state.enemies or state.battle_enemies
    return [(idx, enemy) for idx, enemy in enumerate(enemies) if enemy.get("hp", 0) > 0]


def get_living_enemies(state: GameState) -> List[Dict[str, Any]]:
    """
    Mendapatkan list musuh yang masih hidup.
    """
    return [enemy for enemy in state.battle_enemies if enemy.get("hp", 0) > 0]


def get_first_alive_enemy(state: GameState) -> Optional[tuple]:
    alive = living_enemies(state)
    return alive[0] if alive else None


def get_enemy_target(
    state: GameState, index: int
) -> Optional[Tuple[int, Dict[str, Any]]]:
    enemies = state.battle_state.enemies or state.battle_enemies
    if 0 <= index < len(enemies):
        enemy = enemies[index]
        if enemy.get("hp", 0) > 0:
            return index, enemy
    return None


def enemy_target_buttons(state: GameState) -> List[Tuple[str, str]]:
    buttons: List[Tuple[str, str]] = []
    for idx, enemy in living_enemies(state):
        label = f"{enemy['name']} (HP {enemy['hp']}/{enemy['max_hp']})"
        buttons.append((label, f"TARGET_ENEMY|{idx}"))
    return buttons


def ally_target_buttons(state: GameState) -> List[Tuple[str, str]]:
    buttons: List[Tuple[str, str]] = []
    for cid in state.party_order:
        member = state.party.get(cid)
        if not member or member.hp <= 0:
            continue
        effective_hp = get_effective_max_hp(member)
        label = f"{member.name} (HP {member.hp}/{effective_hp})"
        buttons.append((label, f"TARGET_ALLY|{cid}"))
    return buttons


def determine_skill_target_type(skill: Dict[str, Any]) -> Optional[str]:
    skill_type = skill.get("type")
    if skill_type in {"PHYS", "MAG", "DEBUFF_ENEMY"}:
        return "ENEMY"
    if skill_type in {"HEAL_SINGLE", "BUFF_DEF_SINGLE"}:
        return "ALLY"
    return None


def build_skill_target_prompt(skill: Dict[str, Any], target_type: str) -> str:
    name = skill.get("name", "skill ini")
    skill_type = skill.get("type")
    if target_type == "ENEMY":
        if skill_type == "DEBUFF_ENEMY":
            return f"Pilih musuh yang akan dilemahkan oleh {name}:"
        return f"Pilih musuh yang akan terkena {name}:"
    if skill_type == "HEAL_SINGLE":
        return f"Pilih anggota party yang akan disembuhkan dengan {name}:"
    return f"Pilih anggota party yang akan menerima {name}:"


def clear_pending_action(state: GameState):
    if state.battle_state:
        state.battle_state.pending_action = None


async def show_pending_target_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    if not state.battle_state:
        logger.warning(
            "Battle state hilang saat meminta target untuk user %s", state.user_id
        )
        await send_battle_state(update, context, state)
        return
    action = state.battle_state.pending_action
    if not action:
        await send_battle_state(update, context, state)
        return
    target_type = action.get("target_type")
    if target_type == "ENEMY":
        options = enemy_target_buttons(state)
        empty_message = "Tidak ada musuh yang bisa ditarget."
    else:
        options = ally_target_buttons(state)
        empty_message = "Tidak ada anggota party yang bisa menerima aksi ini."
    if not options:
        clear_pending_action(state)
        if target_type == "ENEMY":
            if await resolve_battle_outcome(update, context, state, []):
                return
        await send_battle_state(update, context, state, extra_text=empty_message)
        return
    rows = [
        [InlineKeyboardButton(text=label, callback_data=data)]
        for label, data in options
    ]
    actor_id = action.get("actor_id")
    rows.append(
        [InlineKeyboardButton("⬅️ Batalkan", callback_data=f"BATTLE_MENU|{actor_id}")]
    )
    prompt_text = action.get("prompt", "Pilih target:")
    markup = InlineKeyboardMarkup(rows)
    query = update.callback_query
    if query:
        await safe_edit_text(
            query, prompt_text, reply_markup=markup, context_info="target_prompt"
        )
    else:
        await update.message.reply_text(text=prompt_text, reply_markup=markup)


def choose_random_party_target(state: GameState) -> Optional[str]:
    alive = living_party_members(state)
    if not alive:
        return None
    return random.choice(alive)


def pick_lowest_hp_ally(state: GameState) -> Optional[CharacterState]:
    candidates: List[CharacterState] = []
    for cid in state.party_order:
        member = state.party.get(cid)
        if member and member.hp > 0:
            candidates.append(member)
    if not candidates:
        return None
    return min(candidates, key=lambda c: c.hp / max(1, get_effective_max_hp(c)))


def find_revive_target(state: GameState) -> Optional[CharacterState]:
    for cid in state.party_order:
        member = state.party.get(cid)
        if member and member.hp <= 0:
            return member
    return None


def initialize_battle_turn_state(state: GameState):
    entries: List[Tuple[str, int, int, int]] = []
    for pos, cid in enumerate(state.party_order):
        character = state.party.get(cid)
        if not character or character.hp <= 0:
            continue
        spd = get_effective_stat(character, "spd")
        entries.append((f"CHAR:{cid}", spd, 0, pos))
    for idx, enemy in enumerate(state.battle_enemies):
        if enemy.get("hp", 0) <= 0:
            continue
        spd = int(enemy.get("spd", 1))
        entries.append((f"ENEMY:{idx}", spd, 1, idx))
    if not entries:
        order: List[str] = []
    else:
        entries.sort(key=lambda item: (-item[1], item[2], item[3]))
        order = [token for token, *_ in entries]
    logger.debug(
        "Initial turn order (SPD): %s",
        ", ".join(f"{token}:{spd}" for token, spd, *_ in entries) or "(kosong)",
    )
    state.battle_state = BattleTurnState(
        turn_order=order, current_turn_index=-1, enemies=state.battle_enemies
    )
    advance_to_next_actor(state)


def advance_to_next_actor(state: GameState) -> Optional[str]:
    order = state.battle_state.turn_order
    if not order:
        return None
    total = len(order)
    for _ in range(total):
        state.battle_state.current_turn_index = (
            state.battle_state.current_turn_index + 1
        ) % total
        token = order[state.battle_state.current_turn_index]
        if token.startswith("CHAR:"):
            cid = token.split(":", 1)[1]
            character = state.party.get(cid)
            if character and character.hp > 0:
                state.battle_state.active_token = token
                state.battle_state.awaiting_player_input = True
                defending = state.flags.get("DEFENDING", {})
                defending.pop(cid, None)
                if not defending:
                    state.flags.pop("DEFENDING", None)
                return token
        elif token.startswith("ENEMY:"):
            try:
                idx = int(token.split(":", 1)[1])
            except ValueError:
                continue
            if (
                0 <= idx < len(state.battle_enemies)
                and state.battle_enemies[idx]["hp"] > 0
            ):
                state.battle_state.active_token = token
                state.battle_state.awaiting_player_input = False
                return token
    return None


def check_battle_outcome(state: GameState) -> Optional[str]:
    if not living_enemies(state):
        return "WIN"
    if not living_party_members(state):
        return "LOSE"
    return None


async def resolve_battle_outcome(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, log: List[str]
) -> bool:
    outcome = check_battle_outcome(state)
    if not outcome:
        return False
    enemy_keys = [enemy.get("id") for enemy in state.battle_enemies]
    if outcome == "WIN":
        total_xp = sum(enemy.get("xp", 0) for enemy in state.battle_enemies)
        total_gold = sum(enemy.get("gold", 0) for enemy in state.battle_enemies)
        state.in_battle = False
        
        # Material drops dari musuh
        material_drops = {}
        for enemy in state.battle_enemies:
            enemy_level = enemy.get("level", 1)
            enemy_rarity = "COMMON"  # Bisa diperluas dengan sistem rarity
            drops_config = generate_material_drops(enemy_level, enemy_rarity)
            rolled_materials = roll_material_drops(drops_config)
            for mat_id, qty in rolled_materials.items():
                material_drops[mat_id] = material_drops.get(mat_id, 0) + qty
        
        # Tambahkan material ke inventory player
        for mat_id, qty in material_drops.items():
            state.materials[mat_id] = state.materials.get(mat_id, 0) + qty
        
        state.battle_enemies = []
        state.flags["LAST_BATTLE_RESULT"] = "WIN"
        reward_logs = handle_after_battle_xp_and_level_up(state, total_xp, total_gold)
        drop_logs, _ = grant_battle_drops(state)
        
        # Format drop section dengan item + materials
        drop_section = ["Drop:"]
        if drop_logs:
            drop_section.extend(f"- {entry}" for entry in drop_logs)
        
        # Tambahkan material drops
        if material_drops:
            for mat_id, qty in material_drops.items():
                mat = CRAFTING_SYSTEM.materials.get(mat_id)
                mat_name = mat.name if mat else mat_id
                drop_section.append(f"- [Material] {mat_name} x{qty}")
        
        if not drop_logs and not material_drops:
            drop_section.append("- (tidak ada)")
        
        summary_lines = [
            "==== VICTORY ====",
            "Kamu mengalahkan musuh!",
            "",
            f"EXP diperoleh: {total_xp}",
            f"Gold diperoleh: {total_gold}",
            "",
            *drop_section,
        ]
        quest_logs = update_hunt_quest_progress(
            state, [mid for mid in enemy_keys if mid]
        )
        combined_log = summary_lines + [""] + log
        if quest_logs:
            combined_log.extend(["", "Progress Quest Guild:"])
            combined_log.extend(quest_logs)
        if reward_logs:
            combined_log.extend([""] + reward_logs)
        log = combined_log
        logger.info(
            "User %s menyelesaikan battle vs %s dengan hasil WIN",
            state.user_id,
            ",".join([k for k in enemy_keys if k] or ["UNKNOWN"]),
        )
        if any(key in AUTOSAVE_BOSS_KEYS for key in enemy_keys if key):
            boss_key = next(
                (key for key in enemy_keys if key in AUTOSAVE_BOSS_KEYS), "boss"
            )
            queue_pending_autosave(state, f"battle_win_{boss_key}", notify=True)
        await end_battle_and_return(
            update,
            context,
            state,
            log_text="\n".join(log),
        )
        return True
    # LOSE
    summary_lines = [
        "==== KALAH ====",
        "Kamu tumbang dalam pertarungan ini...",
    ]
    reset_auto_hunt_state(state)
    for cid in state.party_order:
        member = state.party.get(cid)
        if not member:
            continue
        member.hp = max(1, get_effective_max_hp(member) // 3)
    state.in_battle = False
    state.battle_enemies = []
    log.append("Seluruh party tumbang! Kamu terlempar keluar dari pertarungan.")
    log = summary_lines + [""] + log
    state.flags["LAST_BATTLE_RESULT"] = "LOSE"
    logger.info(
        "User %s menyelesaikan battle vs %s dengan hasil LOSE",
        state.user_id,
        ",".join([k for k in enemy_keys if k] or ["UNKNOWN"]),
    )
    await end_battle_and_return(update, context, state, log_text="\n".join(log))
    return True


def enemy_take_turn(state: GameState, enemy_index: int) -> List[str]:
    log: List[str] = []
    enemies = state.battle_state.enemies or state.battle_enemies
    if enemy_index < 0 or enemy_index >= len(enemies):
        return log
    enemy = enemies[enemy_index]
    if enemy.get("hp", 0) <= 0:
        return log
    target_id = choose_random_party_target(state)
    if not target_id:
        return log
    target = state.party.get(target_id)
    if not target:
        return log
    target_def = get_effective_stat(target, "defense")
    dmg = calc_enemy_basic_damage(enemy["atk"], target_def)
    defending = state.flags.get("DEFENDING", {})
    if defending.get(target_id):
        dmg = max(1, dmg // 2)
        defending.pop(target_id, None)
        if not defending:
            state.flags.pop("DEFENDING", None)
    dmg = apply_mana_shield_absorption(state, target_id, dmg, log)
    if dmg <= 0:
        return log
    target.hp -= dmg
    log.append(f"{enemy['name']} menyerang {target.name} dan memberikan {dmg} damage!")
    if target.hp <= 0:
        target.hp = 0
        log.append(f"{target.name} tumbang!")
    return log


async def conclude_player_turn(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, log: List[str]
):
    if await resolve_battle_outcome(update, context, state, log):
        return
    next_token = advance_to_next_actor(state)
    if not next_token:
        await send_battle_state(
            update, context, state, intro=False, extra_text="\n".join(log)
        )
        return
    enemy_phase = False
    while next_token and next_token.startswith("ENEMY:"):
        enemy_phase = True
        try:
            enemy_index = int(next_token.split(":", 1)[1])
        except ValueError:
            enemy_index = -1
        log.extend(enemy_take_turn(state, enemy_index))
        if await resolve_battle_outcome(update, context, state, log):
            return
        next_token = advance_to_next_actor(state)
    if enemy_phase:
        buff_logs = tick_buffs(state)
        if buff_logs:
            log.extend(buff_logs)
    await send_battle_state(
        update, context, state, intro=False, extra_text="\n".join(log)
    )


def describe_skill_short(
    character: CharacterState, skill_id: str, state: GameState
) -> str:
    skill = SKILLS.get(skill_id, {})
    base = f"{skill.get('name', skill_id)} (MP {skill.get('mp_cost', 0)})"
    skill_type = skill.get("type")
    parts: List[str] = []
    alive_party = 0
    for cid in state.party_order:
        member = state.party.get(cid)
        if member and member.hp > 0:
            alive_party += 1
    alive_party = alive_party or 1

    if skill_type == "PHYS":
        hits = max(1, int(skill.get("hits", 1)))
        if hits > 1:
            parts.append(f"{hits}x serangan fisik")
        else:
            parts.append("serangan fisik")
        estimate = estimate_skill_damage(character, skill)
        if estimate:
            parts.append(f"~{estimate[0]}-{estimate[1]} DMG")
    elif skill_type == "MAG":
        hits = max(1, int(skill.get("hits", 1)))
        if hits > 1:
            parts.append(f"{hits}x serangan sihir")
        else:
            parts.append("serangan sihir kuat")
        estimate = estimate_skill_damage(character, skill)
        if estimate:
            parts.append(f"~{estimate[0]}-{estimate[1]} DMG")
    elif skill_type == "HEAL_SINGLE":
        estimate = estimate_skill_heal(character, skill, targets=1)
        if estimate:
            parts.append(f"heal {estimate[0]}-{estimate[1]} HP")
        else:
            parts.append("heal satu target")
    elif skill_type == "HEAL_ALL":
        estimate = estimate_skill_heal(character, skill, targets=alive_party)
        if estimate:
            parts.append(f"heal tim {estimate[0]}-{estimate[1]} HP/ally")
        else:
            parts.append("heal seluruh tim")
    elif skill_type == "LIMIT_HEAL":
        estimate = estimate_skill_heal(character, skill, targets=alive_party)
        if estimate:
            parts.append(f"cahaya penyembuh ~{estimate[0]}-{estimate[1]} total")
        parts.append("buff serangan Cahaya")
    elif skill_type in {"BUFF_DEF_SELF", "BUFF_DEF_SINGLE"}:
        duration = skill.get("duration", 3)
        parts.append(f"buff pertahanan {duration} giliran")
    elif skill_type in {"BUFF_TEAM", "BUFF_SELF"}:
        duration = skill.get("duration", 3)
        parts.append(f"buff tim {duration} giliran")
    elif skill_type == "DEBUFF_ENEMY":
        duration = skill.get("duration", 3)
        parts.append(f"debuff musuh {duration} giliran")
    elif skill_type == "REVIVE":
        parts.append("membangkitkan ally tumbang")
    elif skill_type == "CLEANSE":
        parts.append("hapus debuff")
    elif skill_type == "BUFF_SPECIAL":
        duration = skill.get("duration", 3)
        parts.append(f"perisai MP {duration} giliran")
    detail = " – ".join(parts) if parts else skill.get("description", "")
    return f"{base} – {detail}" if detail else base


async def send_skill_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    character: CharacterState,
):
    skills = character.skills
    if not skills:
        await send_battle_state(
            update,
            context,
            state,
            intro=False,
            extra_text=f"{character.name} belum mempelajari skill apa pun.",
        )
        return
    choices = [
        (
            describe_skill_short(character, skill_id, state),
            f"USE_SKILL|{character.id}|{skill_id}",
        )
        for skill_id in skills
    ]
    choices.append(("Kembali", f"BATTLE_MENU|{character.id}"))
    keyboard = make_keyboard(choices)
    text = battle_status_text(state) + f"\n\nPilih skill {character.name}:"
    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=keyboard)


async def send_battle_item_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    char_id: Optional[str] = None,
):
    if not state.battle_state:
        logger.warning(
            "Battle state hilang saat membuka menu item user %s", state.user_id
        )
        await send_battle_state(update, context, state)
        return
    if not char_id:
        token = state.battle_state.active_token
        if token and token.startswith("CHAR:"):
            char_id = token.split(":", 1)[1]
    consumables = [
        (item_id, qty)
        for item_id, qty in state.inventory.items()
        if qty > 0 and ITEMS.get(item_id, {}).get("type") == "consumable"
    ]
    if not consumables:
        await send_battle_state(
            update,
            context,
            state,
            intro=False,
            extra_text="Kamu tidak punya item yang bisa dipakai.",
        )
        return
    buttons = []
    lines = ["Pilih item yang akan dipakai:"]
    for item_id, qty in consumables:
        item = ITEMS[item_id]
        lines.append(f"- {item['name']} x{qty}")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{item['name']} (x{qty})", callback_data=f"USE_ITEM|{item_id}"
                )
            ]
        )
    back_target = f"BATTLE_MENU|{char_id}" if char_id else "BATTLE_BACK"
    buttons.append([InlineKeyboardButton("⬅ Kembali", callback_data=back_target)])
    query = update.callback_query
    if query:
        await safe_edit_text(
            query,
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
            context_info="battle_item_menu",
        )
    elif update.message:
        await update.message.reply_text(
            text="\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons)
        )


def apply_item_effects_in_battle(
    state: GameState, user_char_id: str, item_id: str
) -> Tuple[bool, List[str]]:
    item = ITEMS.get(item_id)
    if not item:
        return False, ["Item tidak dikenal."]
    effects = item.get("effects", {})
    target_mode = effects.get("target", "single")
    targets: List[CharacterState] = []
    if target_mode == "party":
        for cid in state.party_order:
            member = state.party.get(cid)
            if member and member.hp > 0:
                targets.append(member)
    else:
        actor = state.party.get(user_char_id)
        if actor:
            targets = [actor]
    if not targets:
        return False, ["Tidak ada target yang bisa menerima efek item."]
    logs: List[str] = []
    hp_restore = effects.get("hp_restore", 0)
    mp_restore = effects.get("mp_restore", 0)
    for target in targets:
        if hp_restore:
            before = target.hp
            target.hp = min(get_effective_max_hp(target), target.hp + hp_restore)
            logs.append(f"{target.name} memulihkan {target.hp - before} HP.")
        if mp_restore:
            before_mp = target.mp
            target.mp = min(get_effective_max_mp(target), target.mp + mp_restore)
            logs.append(f"{target.name} memulihkan {target.mp - before_mp} MP.")
    if not logs:
        logs.append("Tidak ada efek yang terlihat.")
    return True, logs


async def process_use_item(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, item_id: str
):
    if not state.battle_state:
        logger.warning("Battle state hilang saat USE_ITEM oleh user %s", state.user_id)
        await send_battle_state(update, context, state)
        return
    token = state.battle_state.active_token
    if not token or not token.startswith("CHAR:"):
        await send_battle_state(update, context, state)
        return
    char_id = token.split(":", 1)[1]
    character = state.party.get(char_id)
    if not character:
        await send_battle_state(update, context, state)
        return
    item = ITEMS.get(item_id)
    if not item or item.get("type") != "consumable":
        await send_battle_state(
            update,
            context,
            state,
            intro=False,
            extra_text="Item itu tidak bisa dipakai sekarang.",
        )
        return
    qty = state.inventory.get(item_id, 0)
    if qty <= 0:
        await send_battle_state(
            update,
            context,
            state,
            intro=False,
            extra_text="Kamu tidak memiliki item itu.",
        )
        return
    success, effect_logs = apply_item_effects_in_battle(state, char_id, item_id)
    if not success:
        await send_battle_state(
            update, context, state, intro=False, extra_text="\n".join(effect_logs)
        )
        return
    adjust_inventory(state, item_id, -1)
    log = [f"{character.name} menggunakan {item['name']}."] + effect_logs
    await conclude_player_turn(update, context, state, log)


def create_enemy_from_key(monster_key: str) -> Dict[str, Any]:
    base = MONSTERS.get(monster_key)
    if not base:
        return pick_random_monster_for_area("HUTAN_SELATPANJANG")
    return {
        "name": base["name"],
        "hp": base["hp"],
        "max_hp": base["hp"],
        "mp": base["mp"],
        "atk": base["atk"],
        "defense": base["defense"],
        "mag": base["mag"],
        "spd": base["spd"],
        "luck": base["luck"],
        "xp": base["xp"],
        "gold": base["gold"],
        "element": base.get("element", "NETRAL"),
        "weakness": list(base.get("weakness", [])),
        "resist": list(base.get("resist", [])),
        "area": base.get("area", "UNKNOWN"),
        "id": monster_key,
        "rarity": base.get("rarity", "STORY"),
        "encounter_weight": base.get("encounter_weight", 1.0),
        "can_escape": base.get("can_escape", False),
        "rank": base.get("rank", "MEDIUM"),
        "level": base.get("level", 1),
    }


async def start_fixed_battle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    monster_key: str,
):
    """
    Mulai battle dengan monster spesifik (untuk tutorial atau story battle spesial).
    Tidak ada return scene karena akan dihandle oleh end_battle_and_return.
    """
    enemy = create_enemy_from_key(monster_key)
    logger.info("User %s memulai fixed battle melawan %s", state.user_id, monster_key)
    state.in_battle = True
    state.battle_enemies = [enemy]
    state.battle_turn = "PLAYER"
    state.return_scene_after_battle = None
    state.loss_scene_after_battle = None
    reset_battle_flags(state)
    state.flags["CURRENT_BATTLE_AREA"] = enemy.get("area")
    initialize_battle_turn_state(state)
    await send_battle_state(update, context, state, intro=True)


async def start_story_battle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    enemy_key: str,
    return_scene: str,
    loss_scene: Optional[str] = None,
):
    enemy = create_enemy_from_key(enemy_key)
    logger.info(
        "User %s memulai story battle melawan %s",
        state.user_id,
        enemy.get("id", enemy_key),
    )
    state.in_battle = True
    state.battle_enemies = [enemy]
    state.battle_turn = "PLAYER"
    state.return_scene_after_battle = return_scene
    state.loss_scene_after_battle = loss_scene
    reset_battle_flags(state)
    state.flags["CURRENT_BATTLE_AREA"] = enemy.get("area")
    initialize_battle_turn_state(state)
    await send_battle_state(update, context, state, intro=True)


# ==========================
# HELPER UI
# ==========================


def make_keyboard(choices: List[tuple]) -> InlineKeyboardMarkup:
    """
    choices: list of (label, callback_data)
    """
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=data)]
        for (label, data) in choices
    ]
    return InlineKeyboardMarkup(buttons)


# ==========================
# BATTLE LOGIC SEDERHANA
# ==========================

# Damage tuning constants.
# Ubah angka berikut jika ingin menyesuaikan seberapa besar serangan dasar
# pemain/monster tanpa mengutak-atik rumus di banyak tempat.
PHYSICAL_DEF_RATIO = 0.45  # Semakin besar semakin kuat pertahanan fisik.
MAGICAL_DEF_RATIO = 0.35  # Rasio mitigasi damage sihir.
PLAYER_DAMAGE_VARIANCE = (0.9, 1.1)
ENEMY_DAMAGE_VARIANCE = (0.88, 1.12)
ENEMY_ATTACK_SCALE = 0.92  # Skala ATK musuh relatif ke pemain setara level.
ENEMY_DEF_RATIO = 0.45


def pick_random_monster_for_area(
    area: str, party_level: Optional[int] = None
) -> Dict[str, Any]:
    pool = [(key, m) for key, m in MONSTERS.items() if m["area"] == area]
    if not pool:
        pool = [("SHADOW_SLIME", MONSTERS["SHADOW_SLIME"])]  # fallback
    rare_pool = [(key, m) for key, m in pool if m.get("rarity") == "RARE"]
    common_pool = [(key, m) for key, m in pool if m.get("rarity") != "RARE"]
    selected_pool = common_pool or pool
    rare_ready = False
    if rare_pool:
        min_rare_level = min(m.get("level", 1) for _, m in rare_pool)
        rare_ready = (
            party_level is None or party_level + RARE_LEVEL_BUFFER >= min_rare_level
        )
    if rare_pool and rare_ready:
        rare_chance = RARE_ENCOUNTER_CHANCE.get(area, 0.03)
        if random.random() < rare_chance:
            selected_pool = rare_pool
    weights = [m.get("encounter_weight", 1.0) for _, m in selected_pool]
    base_key, base = random.choices(selected_pool, weights=weights, k=1)[0]
    # copy agar tidak mengubah base
    return {
        "name": base["name"],
        "hp": base["hp"],
        "max_hp": base["hp"],
        "mp": base["mp"],
        "atk": base["atk"],
        "defense": base["defense"],
        "mag": base["mag"],
        "spd": base["spd"],
        "luck": base["luck"],
        "xp": base["xp"],
        "gold": base["gold"],
        "element": base.get("element", "NETRAL"),
        "weakness": list(base.get("weakness", [])),
        "resist": list(base.get("resist", [])),
        "area": base.get("area", area),
        "id": base.get("id", base_key),
        "rarity": base.get("rarity", "COMMON"),
        "encounter_weight": base.get("encounter_weight", 1.0),
        "can_escape": base.get("can_escape", True),
    }


def average_party_speed(state: GameState) -> float:
    speeds: List[int] = []
    for cid in state.party_order:
        member = state.party.get(cid)
        if member and member.hp > 0:
            speeds.append(get_effective_stat(member, "spd"))
    return sum(speeds) / len(speeds) if speeds else 0.0


def average_enemy_speed(state: GameState) -> float:
    speeds = [
        enemy.get("spd", 0) for enemy in state.battle_enemies if enemy.get("hp", 0) > 0
    ]
    return sum(speeds) / len(speeds) if speeds else 0.0


def average_party_level(state: GameState) -> int:
    levels: List[int] = []
    for cid in state.party_order:
        member = state.party.get(cid)
        if member:
            levels.append(member.level)
    if not levels:
        return 1
    return max(1, int(sum(levels) / len(levels)))


def compute_escape_chance(state: GameState) -> float:
    base = 0.6
    party_spd = average_party_speed(state)
    enemy_spd = average_enemy_speed(state)
    diff = party_spd - enemy_spd
    chance = base + diff * 0.02
    return max(0.2, min(0.95, chance))


def compute_monster_power(monster: Dict[str, Any]) -> float:
    hp = monster.get("hp", 1)
    atk = monster.get("atk", 1)
    defense = monster.get("defense", 1)
    level = monster.get("level", 1)
    rank = monster.get("rank", "MEDIUM")
    rank_bonus = {
        "WEAK": 0.8,
        "MEDIUM": 1.0,
        "STRONG": 1.2,
        "RARE": 1.3,
        "BOSS": 1.6,
    }.get(rank, 1.0)
    return (hp / 10 + atk * 1.4 + defense * 1.2 + level * 1.1) * rank_bonus


def compute_party_power(state: GameState) -> float:
    total = 0.0
    for cid in state.party_order:
        member = state.party.get(cid)
        if not member or member.hp <= 0:
            continue
        stats = get_effective_combat_stats(member)
        total += stats["max_hp"] / 12
        total += stats["atk"] * 1.3
        total += stats["mag"] * 1.2
        total += stats["defense"] * 0.9
        total += member.level * 1.1
    return total


def compute_elemental_multiplier(
    element: str,
    target_weakness: Optional[List[str]],
    target_resist: Optional[List[str]],
    passives: Optional[Dict[str, Any]] = None,
    target_element: Optional[str] = None,
) -> Tuple[float, bool, bool]:
    multiplier = 1.0
    hit_weakness = False
    hit_resist = False
    used_element = element or "NETRAL"
    if target_element:
        advantage = ELEMENTAL_ADVANTAGE.get(used_element, {}).get(target_element)
        if advantage:
            multiplier *= advantage
            if advantage > 1:
                hit_weakness = True
            elif advantage < 1:
                hit_resist = True
    if used_element and target_weakness and used_element in target_weakness:
        multiplier *= 1.5
        hit_weakness = True
    if used_element and target_resist and used_element in target_resist:
        multiplier *= 0.75
        hit_resist = True
    if passives:
        boost = passives.get("element_boost", {})
        if used_element in boost:
            multiplier *= 1 + boost[used_element]
    return multiplier, hit_weakness, hit_resist


def compute_passive_damage_bonus(
    passives: Dict[str, Any], target_element: Optional[str], used_element: str
) -> float:
    multiplier = 1.0
    if not passives:
        return multiplier
    bonus_vs = passives.get("bonus_vs_element", {})
    if target_element and target_element in bonus_vs:
        multiplier *= 1 + bonus_vs[target_element]
    if used_element == "CAHAYA":
        multiplier *= 1 + passives.get("light_skill_amp", 0)
    return multiplier


def calc_physical_damage(
    attacker: CharacterState,
    target_def: int,
    power: float = 1.0,
    element: str = "NETRAL",
    target_weakness: Optional[List[str]] = None,
    target_resist: Optional[List[str]] = None,
    target_element: Optional[str] = None,
) -> Tuple[int, bool, bool]:
    attacker_atk = get_effective_stat(attacker, "atk")
    base = attacker_atk - target_def * PHYSICAL_DEF_RATIO
    if base < 1:
        base = 1
    base = int(base * power)
    base = int(base * random.uniform(*PLAYER_DAMAGE_VARIANCE))
    passives = get_character_passive_effects(attacker)
    element_multiplier, hit_weakness, hit_resist = compute_elemental_multiplier(
        element,
        target_weakness,
        target_resist,
        passives,
        target_element=target_element,
    )
    passive_bonus = compute_passive_damage_bonus(passives, target_element, element)
    base = int(base * element_multiplier * passive_bonus)
    return max(1, base), hit_weakness, hit_resist


def calc_magic_damage(
    attacker: CharacterState,
    target_def: int,
    power: float,
    element: str = "NETRAL",
    target_weakness: Optional[List[str]] = None,
    target_resist: Optional[List[str]] = None,
    target_element: Optional[str] = None,
) -> Tuple[int, bool, bool]:
    attacker_mag = get_effective_stat(attacker, "mag")
    base = attacker_mag - target_def * MAGICAL_DEF_RATIO
    if base < 1:
        base = 1
    base = int(base * power)
    base = int(base * random.uniform(*PLAYER_DAMAGE_VARIANCE))
    passives = get_character_passive_effects(attacker)
    element_multiplier, hit_weakness, hit_resist = compute_elemental_multiplier(
        element,
        target_weakness,
        target_resist,
        passives,
        target_element=target_element,
    )
    passive_bonus = compute_passive_damage_bonus(passives, target_element, element)
    base = int(base * element_multiplier * passive_bonus)
    return max(1, base), hit_weakness, hit_resist


def calc_enemy_basic_damage(enemy_atk: int, target_def: int) -> int:
    """Damage fisik standar musuh → pemain berdasarkan konstanta balancing."""
    base = (enemy_atk * ENEMY_ATTACK_SCALE) - (target_def * ENEMY_DEF_RATIO)
    if base < 1:
        base = 1
    dmg = int(base * random.uniform(*ENEMY_DAMAGE_VARIANCE))
    return max(1, dmg)


def calc_heal_amount(caster: CharacterState, power: float) -> int:
    base = int(get_effective_stat(caster, "mag") * power)
    if base < 1:
        base = 1
    return base


def estimate_enemy_defense(caster: CharacterState) -> int:
    """Perkiraan sederhana DEF musuh berdasarkan level caster."""
    return max(8, caster.level + 10)


def estimate_skill_damage(
    caster: CharacterState, skill: Dict[str, Any]
) -> Optional[Tuple[int, int]]:
    skill_type = skill.get("type")
    if skill_type not in ("PHYS", "MAG"):
        return None
    target_def = estimate_enemy_defense(caster)
    element = skill.get("element", "NETRAL")
    hits = max(1, int(skill.get("hits", 1)))
    if skill_type == "PHYS":
        base = max(
            1, get_effective_stat(caster, "atk") - target_def * PHYSICAL_DEF_RATIO
        )
    else:
        base = max(
            1, get_effective_stat(caster, "mag") - target_def * MAGICAL_DEF_RATIO
        )
    per_hit = int(base * skill.get("power", 1.0))
    if element == "CAHAYA" and caster.id == "ARUNA":
        per_hit = int(per_hit * 1.05)
    min_hit = max(1, int(per_hit * 0.9))
    max_hit = max(1, int(per_hit * 1.1))
    return min_hit * hits, max_hit * hits


def estimate_skill_heal(
    caster: CharacterState, skill: Dict[str, Any], targets: int = 1
) -> Optional[Tuple[int, int]]:
    skill_type = skill.get("type")
    if skill_type not in {"HEAL_SINGLE", "HEAL_ALL", "LIMIT_HEAL"}:
        return None
    targets = max(1, targets)
    base = calc_heal_amount(caster, skill.get("power", 0.3))
    min_val = max(1, int(base * 0.9))
    max_val = max(1, int(base * 1.1))
    if skill_type == "LIMIT_HEAL":
        min_val = max(1, int(get_effective_max_hp(caster) * 0.4))
        max_val = min_val
        targets = max(targets, 3)
    if skill_type == "HEAL_ALL":
        return min_val, max_val
    return min_val * targets, max_val * targets


def apply_mana_shield_absorption(
    state: GameState, target_id: str, damage: int, log: List[str]
) -> int:
    if damage <= 0:
        return 0
    shields = state.flags.get("MANA_SHIELD")
    if not shields or target_id not in shields:
        return damage
    target = state.party.get(target_id)
    if not target:
        return damage
    absorb = min(target.mp, damage)
    if absorb > 0:
        target.mp -= absorb
        damage -= absorb
        log.append(f"Mana Shield menyerap {absorb} damage dari {target.name}.")
    if damage <= 0:
        log.append(f"{target.name} terlindungi sepenuhnya oleh Mana Shield!")
        return 0
    return damage


async def start_random_battle(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    area_id = DEFAULT_CITY_HUNTING.get(state.location, "HUNT_SELATPANJANG")
    await start_random_battle_in_area(
        update,
        context,
        state,
        area_id,
        source="WORLD",
    )


async def start_random_battle_in_area(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    area_id: str,
    *,
    source: str = "HUNTING",
):
    area_info = HUNTING_AREAS.get(area_id, {"area_key": area_id, "name": area_id})
    battle_area = area_info.get("area_key", area_id)
    avg_level = average_party_level(state)
    enemy = pick_random_monster_for_area(battle_area, avg_level)
    logger.info(
        "User %s memulai random battle di %s (%s) melawan %s",
        state.user_id,
        area_id,
        battle_area,
        enemy.get("id", enemy.get("name", "UNKNOWN")),
    )
    state.in_battle = True
    state.battle_enemies = [enemy]
    state.battle_turn = "PLAYER"
    state.return_scene_after_battle = None
    state.loss_scene_after_battle = None
    reset_battle_flags(state)
    state.flags["CURRENT_BATTLE_AREA"] = battle_area
    state.flags["LAST_BATTLE_SOURCE"] = {"type": source, "area": area_id}
    state.flags["LAST_HUNT_AREA"] = area_id
    initialize_battle_turn_state(state)
    intro_lines = []
    rank = enemy.get("rank")
    level = enemy.get("level", "?")
    intro_lines.append(
        f"{enemy['name']} [{rank}] Lv {level} muncul di {area_info.get('name', 'area liar')}!"
    )
    if enemy.get("rarity") == "RARE":
        intro_lines.append("Aura kuat menyelimuti udara. Ini monster langka!")
    await send_battle_state(
        update,
        context,
        state,
        intro=True,
        extra_text="\n".join(intro_lines),
    )


def battle_status_text(
    state: GameState, action_text: str = "", intro_text: str = ""
) -> str:
    lines = ["==== BATTLE ====", ""]
    if intro_text:
        lines.append(intro_text)
        lines.append("")

    lines.append("[Party]")
    for cid in state.party_order:
        c = state.party.get(cid)
        if not c:
            continue
        effective_hp = get_effective_max_hp(c)
        effective_mp = get_effective_max_mp(c)
        lines.append(
            f"{c.name:<6} Lv {c.level:<2}  HP {c.hp}/{effective_hp}  MP {c.mp}/{effective_mp}"
        )

    lines.append("")
    lines.append("[Musuh]")
    for e in state.battle_enemies:
        rank = e.get("rank", "?")
        level = e.get("level", "?")
        lines.append(f"{e['name']} [{rank}] Lv {level}  HP {e['hp']}/{e['max_hp']}")

    token = state.battle_state.active_token
    if token:
        lines.append("")
        if token.startswith("CHAR:"):
            cid = token.split(":", 1)[1]
            actor = state.party.get(cid)
            if actor:
                lines.append(f"Giliran: {actor.name}")
        elif token.startswith("ENEMY:"):
            try:
                idx = int(token.split(":", 1)[1])
            except ValueError:
                idx = -1
            if 0 <= idx < len(state.battle_enemies):
                enemy = state.battle_enemies[idx]
                lines.append(f"Giliran: {enemy['name']}")

    lines.append("")
    lines.append("Aksi Terakhir:")
    if action_text.strip():
        lines.extend(action_text.splitlines())
    else:
        lines.append("(belum ada aksi)")
    return "\n".join(lines)


async def send_battle_state(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    intro: bool = False,
    extra_text: str = "",
):
    intro_text = ""
    action_text = extra_text
    if intro:
        monster_name = state.battle_enemies[0]["name"]
        intro_lines = [f"Kamu berhadapan dengan {monster_name}!"]
        if extra_text:
            intro_lines.append(extra_text)
            action_text = ""
        intro_text = "\n".join(intro_lines)
    text = battle_status_text(state, action_text=action_text, intro_text=intro_text)

    keyboard = None
    token = state.battle_state.active_token
    if token and token.startswith("CHAR:"):
        cid = token.split(":", 1)[1]
        keyboard = make_keyboard(
            [
                ("⚔ Serang", f"BATTLE_ATTACK|{cid}"),
                ("✨ Skill", f"BATTLE_SKILL_MENU|{cid}"),
                ("🎒 Item", f"BATTLE_ITEM|{cid}"),
                ("🛡 Bertahan", f"BATTLE_DEFEND|{cid}"),
                ("🏃 Kabur", f"BATTLE_RUN|{cid}"),
            ]
        )

    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text=text, reply_markup=keyboard)


async def execute_basic_attack(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    attacker_id: str,
    enemy_index: int,
) -> bool:
    character = state.party.get(attacker_id)
    target_info = get_enemy_target(state, enemy_index)
    if not character or not target_info:
        await send_battle_state(
            update,
            context,
            state,
            extra_text="Target musuh tidak valid untuk serangan ini.",
        )
        return False
    _, enemy = target_info
    weapon_element = get_character_weapon_element(character)
    dmg, hit_weakness, hit_resist = calc_physical_damage(
        character,
        enemy["defense"],
        element=weapon_element,
        target_weakness=enemy.get("weakness"),
        target_resist=enemy.get("resist"),
        target_element=enemy.get("element"),
    )
    enemy["hp"] -= dmg
    element_text = f" ({weapon_element})" if weapon_element != "NETRAL" else ""
    log = [f"{character.name} menebas {enemy['name']}! Damage {dmg}{element_text}."]
    if hit_weakness:
        log.append("Serangan itu mengenai kelemahan musuh!")
    if hit_resist:
        log.append("Musuh menahan sebagian seranganmu.")
    await conclude_player_turn(update, context, state, log)
    return True


async def execute_skill_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    user: str,
    skill_id: str,
    *,
    target_enemy_index: Optional[int] = None,
    target_ally_id: Optional[str] = None,
) -> bool:
    character = state.party.get(user)
    skill = SKILLS.get(skill_id)
    if not character or not skill:
        await send_battle_state(update, context, state)
        return False
    mp_cost = skill.get("mp_cost", 0)
    if character.mp < mp_cost:
        await send_battle_state(
            update,
            context,
            state,
            intro=False,
            extra_text=f"{character.name} tidak punya MP yang cukup untuk menggunakan {skill['name']}!",
        )
        return False

    character.mp -= mp_cost
    log: List[str] = []
    skill_type = skill.get("type")
    element = skill.get("element", "NETRAL")

    if skill_type in ("PHYS", "MAG"):
        target_info: Optional[Tuple[int, Dict[str, Any]]]
        if target_enemy_index is not None:
            target_info = get_enemy_target(state, target_enemy_index)
        else:
            target_info = get_first_alive_enemy(state)
        if not target_info:
            character.mp += mp_cost
            if await resolve_battle_outcome(update, context, state, log):
                return False
            return False
        idx, enemy = target_info
        hits = max(1, int(skill.get("hits", 1)))
        total_dmg = 0
        per_hit_logs: List[str] = []
        hit_weakness = False
        hit_resist = False
        header = f"{character.name} melancarkan {skill['name']}!"
        if element != "NETRAL":
            header += f" ({element})"
        log.append(header)
        for hit in range(hits):
            if skill_type == "PHYS":
                dmg, h_weak, h_res = calc_physical_damage(
                    character,
                    enemy["defense"],
                    skill.get("power", 1.0),
                    element,
                    enemy.get("weakness"),
                    enemy.get("resist"),
                    enemy.get("element"),
                )
            else:
                dmg, h_weak, h_res = calc_magic_damage(
                    character,
                    enemy["defense"],
                    skill.get("power", 1.0),
                    element,
                    enemy.get("weakness"),
                    enemy.get("resist"),
                    enemy.get("element"),
                )
            if element == "CAHAYA" and state.flags.get("LIGHT_BUFF_TURNS"):
                dmg = int(dmg * 1.2)
            enemy["hp"] -= dmg
            total_dmg += dmg
            hit_weakness = hit_weakness or h_weak
            hit_resist = hit_resist or h_res
            per_hit_logs.append(f"Hantaman {hit + 1}: {dmg} damage.")
        if hits > 1:
            log.extend(per_hit_logs)
            log.append(f"Total damage kombo ke {enemy['name']}: {total_dmg}.")
        else:
            log.append(f"{enemy['name']} menerima {total_dmg} damage.")
        if hit_weakness:
            log.append("Serangan ini menghantam kelemahan musuh!")
        if hit_resist:
            log.append("Musuh menahan sebagian energi seranganmu.")
    elif skill_type == "HEAL_SINGLE":
        target = (
            state.party.get(target_ally_id)
            if target_ally_id
            else pick_lowest_hp_ally(state)
        )
        if not target or target.hp <= 0:
            character.mp += mp_cost
            log.append("Tidak ada target untuk disembuhkan.")
            await send_battle_state(update, context, state, extra_text="\n".join(log))
            return False
        heal_amount = calc_heal_amount(character, skill.get("power", 0.3))
        before = target.hp
        target.hp = min(get_effective_max_hp(target), target.hp + heal_amount)
        healed = target.hp - before
        log.append(
            f"{character.name} merapal {skill['name']} dan memulihkan {target.name} sebesar {healed} HP."
        )
    elif skill_type == "HEAL_ALL":
        total = []
        for cid in state.party_order:
            member = state.party.get(cid)
            if not member or member.hp <= 0:
                continue
            heal_amount = calc_heal_amount(character, skill.get("power", 0.25))
            before = member.hp
            member.hp = min(get_effective_max_hp(member), member.hp + heal_amount)
            total.append(f"{member.name}+{member.hp - before}HP")
        log.append(f"{character.name} menyalurkan {skill['name']} ke seluruh party.")
        log.append("Pemulihan: " + ", ".join(total))
    elif skill_type == "BUFF_DEF_SELF":
        buffs = skill.get("buffs", {"defense": 3})
        duration = skill.get("duration", 3)
        for stat, amount in buffs.items():
            apply_temporary_modifier(
                state, make_char_buff_key(user), stat, amount, duration
            )
        log.append(
            f"{character.name} memperkuat pertahanan dengan {skill['name']}! DEF meningkat selama {duration} giliran."
        )
    elif skill_type == "BUFF_DEF_SINGLE":
        target = (
            state.party.get(target_ally_id)
            if target_ally_id
            else pick_lowest_hp_ally(state)
        )
        if not target:
            target = character
        buffs = skill.get("buffs", {"defense": 3})
        duration = skill.get("duration", 3)
        for stat, amount in buffs.items():
            apply_temporary_modifier(
                state, make_char_buff_key(target.id), stat, amount, duration
            )
        log.append(
            f"{character.name} menyalurkan {skill['name']} pada {target.name}! Pertahanan meningkat selama {duration} giliran."
        )
    elif skill_type == "LIMIT_HEAL":
        state.flags["ARUNA_LIMIT_USED"] = True
        state.flags["LIGHT_BUFF_TURNS"] = 3
        total = []
        for cid in state.party_order:
            member = state.party.get(cid)
            if not member:
                continue
            heal_amount = max(1, int(get_effective_max_hp(member) * 0.4))
            before = member.hp
            member.hp = min(get_effective_max_hp(member), member.hp + heal_amount)
            total.append(f"{member.name}+{member.hp - before}HP")
        log.append(
            "==== SIGIL KEABADIAN ====\nSigil Keabadian memulihkan party dan memberkati serangan cahaya!"
        )
        log.append("Pemulihan: " + ", ".join(total))
    elif skill_type == "BUFF_TEAM":
        buffs = skill.get("buffs", {})
        duration = skill.get("duration", 3)
        affected = []
        for cid in state.party_order:
            member = state.party.get(cid)
            if not member or member.hp <= 0:
                continue
            for stat, amount in buffs.items():
                apply_temporary_modifier(
                    state, make_char_buff_key(cid), stat, amount, duration
                )
            affected.append(member.name)
        log.append(
            f"{character.name} menyalurkan {skill['name']}! Buff menyelimuti {', '.join(affected)} selama {duration} giliran."
        )
    elif skill_type == "DEBUFF_ENEMY":
        target_info = (
            get_enemy_target(state, target_enemy_index)
            if target_enemy_index is not None
            else get_first_alive_enemy(state)
        )
        if not target_info:
            character.mp += mp_cost
            log.append("Tidak ada musuh untuk didebuff.")
            await send_battle_state(update, context, state, extra_text="\n".join(log))
            return False
        idx, enemy = target_info
        duration = skill.get("duration", 3)
        for stat, amount in skill.get("debuffs", {}).items():
            apply_temporary_modifier(
                state, make_enemy_buff_key(idx), stat, amount, duration
            )
        log.append(
            f"{character.name} melempar {skill['name']}! Statistik {enemy['name']} melemah selama {duration} giliran."
        )
    elif skill_type == "CLEANSE":
        target_mode = skill.get("target", "party")
        total_removed = 0
        if target_mode == "party":
            for cid in state.party_order:
                total_removed += cleanse_character(state, cid)
        else:
            total_removed = cleanse_character(state, user)
        if total_removed:
            log.append(
                f"{character.name} membersihkan {total_removed} debuff dengan {skill['name']}!"
            )
        else:
            log.append(
                f"{character.name} menggunakan {skill['name']}, tetapi tidak ada debuff yang perlu dibersihkan."
            )
    elif skill_type == "BUFF_SELF":
        duration = skill.get("duration", 3)
        for stat, amount in skill.get("buffs", {}).items():
            apply_temporary_modifier(
                state, make_char_buff_key(user), stat, amount, duration
            )
        for stat, amount in skill.get("penalties", {}).items():
            apply_temporary_modifier(
                state, make_char_buff_key(user), stat, amount, duration
            )
        log.append(
            f"{character.name} memfokuskan energi melalui {skill['name']} untuk {duration} giliran."
        )
    elif skill_type == "BUFF_SPECIAL":
        duration = skill.get("duration", 3)
        shields = state.flags.setdefault("MANA_SHIELD", {})
        shields[user] = duration
        log.append(
            f"{character.name} menciptakan {skill['name']}! Damage akan menguras MP lebih dulu selama {duration} giliran."
        )
    elif skill_type == "REVIVE":
        target = find_revive_target(state)
        if not target:
            character.mp += mp_cost
            log.append("Tidak ada ally yang butuh dihidupkan.")
            await send_battle_state(update, context, state, extra_text="\n".join(log))
            return False
        ratio = skill.get("revive_ratio", 0.4)
        target.hp = max(1, int(get_effective_max_hp(target) * ratio))
        log.append(
            f"{character.name} menghidupkan {target.name} dengan {skill['name']}! HP pulih {target.hp}."
        )
    else:
        log.append(
            f"{skill['name']} belum bisa digunakan di sistem battle sederhana ini."
        )
        await send_battle_state(
            update, context, state, intro=False, extra_text="\n".join(log)
        )
        return False

    await conclude_player_turn(update, context, state, log)
    return True


async def process_battle_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, action: str
):
    action_parts = action.split("|")
    action_key = action_parts[0]
    requested_char = action_parts[1] if len(action_parts) > 1 else None

    if not state.battle_state:
        logger.warning("Battle state hilang saat memproses aksi user %s", state.user_id)
        await send_battle_state(update, context, state)
        return
    token = state.battle_state.active_token
    if not token or not token.startswith("CHAR:"):
        token = advance_to_next_actor(state)
    if not token or not token.startswith("CHAR:"):
        await send_battle_state(update, context, state)
        return

    active_char_id = token.split(":", 1)[1]
    if requested_char and requested_char != active_char_id:
        await send_battle_state(update, context, state)
        return

    character = state.party.get(active_char_id)
    if not character:
        await send_battle_state(update, context, state)
        return

    clear_pending_action(state)

    if action_key in {"BATTLE_MENU", "BATTLE_BACK"}:
        await send_battle_state(update, context, state)
        return

    if action_key == "BATTLE_SKILL_MENU":
        await send_skill_menu(update, context, state, character)
        return
    if action_key == "BATTLE_ITEM":
        await send_battle_item_menu(update, context, state, active_char_id)
        return

    log: List[str] = []

    if action_key == "BATTLE_ATTACK":
        living = get_living_enemies(state)
        if not living:
            if await resolve_battle_outcome(update, context, state, log):
                return
            await send_battle_state(
                update,
                context,
                state,
                extra_text="Tidak ada musuh yang tersisa untuk diserang.",
            )
            return

        # Jika hanya 1 musuh, langsung serang tanpa menu
        if len(living) == 1:
            target_idx = state.battle_enemies.index(living[0])
            target = living[0]
            damage, hit_weak, hit_resist = calc_physical_damage(
                character,
                target.get("defense", 0),
                power=1.0,
                element="NETRAL",
                target_weakness=target.get("weakness"),
                target_resist=target.get("resist"),
                target_element=target.get("element"),
            )
            damage = apply_mana_shield_absorption(state, active_char_id, damage, log)
            defending = state.flags.get("DEFENDING", {})
            if defending.get(active_char_id):
                damage = max(1, damage // 2)
                log.append(
                    f"{character.name} menyerang sambil bertahan, damage berkurang."
                )
            target["hp"] -= damage
            log.append(
                f"{character.name} menyerang {target['name']} → {damage} damage!"
            )
            if hit_weak:
                log.append("Mengenai kelemahan musuh!")
            if hit_resist:
                log.append("Musuh resisten terhadap serangan ini.")
        else:
            # Jika lebih dari 1 musuh, tampilkan menu pemilihan target
            state.battle_state.pending_action = {
                "actor_id": active_char_id,
                "action_kind": "ATTACK",
                "target_type": "ENEMY",
                "prompt": "Pilih musuh yang akan diserang:",
            }
            await show_pending_target_prompt(update, context, state)
            return

    elif action_key == "BATTLE_DEFEND":
        defend_flags = state.flags.setdefault("DEFENDING", {})
        defend_flags[active_char_id] = True
        log.append(
            f"{character.name} mengambil posisi bertahan untuk mengurangi damage sementara."
        )

    elif action_key == "BATTLE_RUN":
        if any(not enemy.get("can_escape", True) for enemy in state.battle_enemies):
            await send_battle_state(
                update,
                context,
                state,
                intro=False,
                extra_text="Kamu tidak bisa kabur dari pertarungan ini!",
            )
            return
        chance = compute_escape_chance(state)
        if random.random() < chance:
            log.append("Kamu berhasil kabur dari battle!")
            state.in_battle = False
            state.battle_enemies = []
            state.flags["LAST_BATTLE_RESULT"] = "ESCAPE"
            await end_battle_and_return(update, context, state, log_text="\n".join(log))
            return
        log.append("Gagal kabur! Musuh bersiap menyerang!")

    else:
        log.append("Aksi belum dikenal dalam sistem battle ini.")
        await send_battle_state(
            update, context, state, intro=False, extra_text="\n".join(log)
        )
        return

    await conclude_player_turn(update, context, state, log)


async def process_use_skill(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    user: str,
    skill_id: str,
):
    if not state.battle_state:
        logger.warning("Battle state hilang saat USE_SKILL oleh user %s", state.user_id)
        await send_battle_state(update, context, state)
        return
    token = state.battle_state.active_token
    if not token or not token.startswith("CHAR:") or token.split(":", 1)[1] != user:
        await send_battle_state(update, context, state)
        return
    character = state.party.get(user)
    if not character:
        await send_battle_state(update, context, state)
        return
    skill = SKILLS.get(skill_id)
    if not skill:
        await send_battle_state(update, context, state)
        return

    if skill_id == "ARUNA_CORE_AWAKENING" and state.flags.get("ARUNA_LIMIT_USED"):
        await send_battle_state(
            update,
            context,
            state,
            intro=False,
            extra_text="Sigil Keabadian sudah digunakan sekali di pertarungan ini!",
        )
        return

    mp_cost = skill.get("mp_cost", 0)
    if character.mp < mp_cost:
        await send_battle_state(
            update,
            context,
            state,
            intro=False,
            extra_text=f"{character.name} tidak punya MP yang cukup untuk menggunakan {skill['name']}!",
        )
        return

    target_type = determine_skill_target_type(skill)
    if target_type == "ENEMY" and not enemy_target_buttons(state):
        if await resolve_battle_outcome(update, context, state, []):
            return
        await send_battle_state(
            update,
            context,
            state,
            extra_text="Tidak ada musuh yang bisa ditarget saat ini.",
        )
        return
    if target_type == "ALLY" and not ally_target_buttons(state):
        await send_battle_state(
            update,
            context,
            state,
            extra_text="Tidak ada anggota party yang bisa menerima skill ini.",
        )
        return

    if target_type:
        # Untuk skill single-target enemy, cek jumlah musuh hidup
        if target_type == "ENEMY":
            living = get_living_enemies(state)
            if len(living) == 1:
                # Langsung eksekusi ke musuh tunggal
                target_idx = state.battle_enemies.index(living[0])
                await execute_skill_action(
                    update,
                    context,
                    state,
                    user,
                    skill_id,
                    target_enemy_index=target_idx,
                )
                await conclude_player_turn(update, context, state, [])
                return

        prompt = build_skill_target_prompt(skill, target_type)
        state.battle_state.pending_action = {
            "actor_id": user,
            "action_kind": "SKILL",
            "skill_id": skill_id,
            "target_type": target_type,
            "prompt": prompt,
        }
        await show_pending_target_prompt(update, context, state)
        return

    await execute_skill_action(update, context, state, user, skill_id)


async def process_target_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, data: str
):
    if not state.battle_state:
        logger.warning("Battle state hilang saat memilih target user %s", state.user_id)
        await send_battle_state(update, context, state)
        return
    action = state.battle_state.pending_action
    if not action:
        await send_battle_state(update, context, state)
        return
    actor_id = action.get("actor_id")
    token = state.battle_state.active_token
    if not token or token != f"CHAR:{actor_id}":
        clear_pending_action(state)
        await send_battle_state(
            update,
            context,
            state,
            extra_text="Giliran sudah berganti sebelum aksi dijalankan.",
        )
        return

    if data.startswith("TARGET_ENEMY|"):
        if action.get("target_type") != "ENEMY":
            await show_pending_target_prompt(update, context, state)
            return
        try:
            idx = int(data.split("|", 1)[1])
        except ValueError:
            await show_pending_target_prompt(update, context, state)
            return
        target_info = get_enemy_target(state, idx)
        if not target_info:
            await show_pending_target_prompt(update, context, state)
            return
        if action.get("action_kind") == "ATTACK":
            success = await execute_basic_attack(update, context, state, actor_id, idx)
        elif action.get("action_kind") == "SKILL":
            success = await execute_skill_action(
                update,
                context,
                state,
                actor_id,
                action.get("skill_id"),
                target_enemy_index=idx,
            )
        else:
            success = False
        if success:
            clear_pending_action(state)
        else:
            await show_pending_target_prompt(update, context, state)
        return

    if data.startswith("TARGET_ALLY|"):
        if action.get("target_type") != "ALLY":
            await show_pending_target_prompt(update, context, state)
            return
        target_id = data.split("|", 1)[1]
        target = state.party.get(target_id)
        if not target or target.hp <= 0:
            await show_pending_target_prompt(update, context, state)
            return
        if action.get("action_kind") != "SKILL":
            await show_pending_target_prompt(update, context, state)
            return
        success = await execute_skill_action(
            update,
            context,
            state,
            actor_id,
            action.get("skill_id"),
            target_ally_id=target_id,
        )
        if success:
            clear_pending_action(state)
        else:
            await show_pending_target_prompt(update, context, state)
        return

    await send_battle_state(update, context, state)


async def end_battle_and_return(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    log_text: str = "",
):
    """
    Setelah battle selesai, balik ke menu yang sesuai dengan lokasi (hutan/kota).
    Untuk sekarang: jika battle random, balik ke 'DUNGEON_MENU', kalau battle story, balik ke scene.
    """
    last_result = state.flags.pop("LAST_BATTLE_RESULT", None)
    battle_source = state.flags.pop("LAST_BATTLE_SOURCE", None)
    source_area = battle_source.get("area") if isinstance(battle_source, dict) else None
    reset_battle_flags(state)
    autosave_note = flush_pending_autosave(state)

    # Deteksi: kalau scene main prolog battle tutorial
    if state.scene_id == "CH0_S3" or state.scene_id.startswith("BATTLE_TUTORIAL"):
        state.scene_id = "CH0_S4_POST_BATTLE"
        await send_scene(
            update,
            context,
            state,
            extra_text=append_optional_text(log_text, autosave_note),
        )
        return

    if state.return_scene_after_battle:
        next_scene = state.return_scene_after_battle
        if last_result == "LOSE" and state.loss_scene_after_battle:
            next_scene = state.loss_scene_after_battle
        state.return_scene_after_battle = None
        state.loss_scene_after_battle = None
        if next_scene:
            state.scene_id = next_scene
            await send_scene(
                update,
                context,
                state,
                extra_text=append_optional_text(log_text, autosave_note),
            )
            return

    text = append_optional_text(log_text, autosave_note)

    if source_area and source_area in HUNTING_AREAS:
        if (
            last_result == "WIN"
            and state.auto_hunt
            and state.auto_hunt_area == source_area
        ):
            if text and update.effective_chat:
                await update.effective_chat.send_message(text)
            await start_random_battle_in_area(
                update,
                context,
                state,
                source_area,
                source=(
                    battle_source.get("type", "HUNTING")
                    if isinstance(battle_source, dict)
                    else "HUNTING"
                ),
            )
            return
        await send_hunting_area_menu(
            update, context, state, source_area, extra_text=text
        )
        return

    text = append_optional_text(text, "Kamu kembali ke area hutan.")
    keyboard = make_keyboard(
        [
            ("🗺️ Menu Hunting", "MENU_HUNTING"),
            ("Kembali ke kota", "RETURN_TO_CITY"),
        ]
    )
    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=keyboard)


# ==========================
# STORY / SCENE HANDLER
# ==========================

STORY_BATTLE_ROUTES = {
    "BATTLE_TUTORIAL_1": {
        "type": "random",
        "set_scene": "CH0_S3",
    },
    "BATTLE_SIAK_GATE": {
        "type": "story",
        "set_scene": "CH1_GATE_ALERT",
        "enemy": "GATE_SPIRIT",
        "return_scene": "CH1_GATE_AFTER",
    },
    "BATTLE_UMAR_HERB": {
        "type": "story",
        "set_scene": "SQ_UMAR_MINIDUNGEON",
        "enemy": "HERB_GUARDIAN",
        "return_scene": "SQ_UMAR_HEAL",
    },
    "BATTLE_RENGAT_GOLEM": {
        "type": "story",
        "set_scene": "CH2_GOLEM_ALERT",
        "enemy": "CORRUPTED_FOREST_GOLEM",
        "return_scene": "CH2_GOLEM_AFTER",
    },
    "BATTLE_REZA_SEAL": {
        "type": "story",
        "set_scene": "SQ_REZA_MASTER",
        "enemy": "SEAL_WARDEN",
        "return_scene": "SQ_REZA_RESOLVE",
    },
    "BATTLE_HOUND_OF_VOID": {
        "type": "story",
        "set_scene": "CH5_FLOOR2",
        "enemy": "HOUND_OF_VOID",
        "return_scene": "CH5_FLOOR2_AFTER",
    },
    "BATTLE_VOID_SENTINEL": {
        "type": "story",
        "set_scene": "CH5_FLOOR4",
        "enemy": "VOID_SENTINEL",
        "return_scene": "CH5_FLOOR4_AFTER",
    },
    "BATTLE_FEBRI": {
        "type": "story",
        "set_scene": "CH5_FLOOR5",
        "enemy": "FEBRI_LORD",
        "return_scene": "CH5_FINAL_WIN",
        "loss_scene": "BAD_ENDING",
    },
    "BATTLE_HARSAN_SENTINEL": {
        "type": "story",
        "set_scene": "SQ_HARSAN_SHRINE_CORE",
        "enemy": "LUMINAR_SENTINEL",
        "return_scene": "SQ_HARSAN_BLADE_VISION",
    },
    "BATTLE_ABYSS_SHADE": {
        "type": "story",
        "set_scene": "SQ_HARSAN_SHRINE_PILLARS",
        "enemy": "ABYSS_SHADE",
        "return_scene": "SQ_HARSAN_SHRINE_CORE",
    },
}


def apply_flags_from_data(
    state: GameState, flags: Optional[Dict[str, List[str]]]
) -> None:
    if not flags:
        return
    for flag in flags.get("set", []):
        state.flags[flag] = True
    for flag in flags.get("unset", []):
        state.flags[flag] = False


def highest_party_level(state: GameState) -> int:
    return max((c.level for c in state.party.values()), default=1)


def requirements_met(requirements: Optional[Dict[str, Any]], state: GameState) -> bool:
    if not requirements:
        return True
    req_flags = requirements.get("flags") or []
    for flag in req_flags:
        if not state.flags.get(flag):
            return False
    min_level = requirements.get("min_level")
    if isinstance(min_level, int) and highest_party_level(state) < min_level:
        return False
    return True


def find_choice_by_callback(
    scene_data: Optional[Dict[str, Any]], callback_data: str
) -> Optional[Dict[str, Any]]:
    if not scene_data:
        return None
    for choice in scene_data.get("choices", []):
        possible = {
            choice.get("callback_data"),
            choice.get("next_scene"),
            choice.get("next"),
            choice.get("command"),
            choice.get("battle"),
        }
        if callback_data in possible:
            return choice
    return None


def build_default_choice() -> Dict[str, Any]:
    return {
        "label": "Lanjut",
        "next_scene": "GO_TO_WORLD_MAP",
        "battle": None,
        "command": "GO_TO_WORLD_MAP",
        "flags": {"set": [], "unset": []},
        "requirements": {"flags": [], "min_level": None},
        "callback_data": "GO_TO_WORLD_MAP",
    }


async def send_scene_not_found(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    missing_scene_id: Optional[str] = None,
) -> None:
    scene_id = missing_scene_id or state.scene_id
    logger.error("Missing scene_id: %s untuk user %s", scene_id, state.user_id)
    text = (
        "Maaf, terjadi kesalahan pada cerita. Scene tidak ditemukan. "
        "Kamu akan dikembalikan ke peta dunia."
    )
    keyboard = make_keyboard([("Kembali ke map", "GO_TO_WORLD_MAP")])
    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=keyboard)
    await send_world_map(update, context, state)


async def execute_story_command(
    command: Optional[str],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    extra_text: str = "",
) -> bool:
    if not command:
        return False
    if command in {"GO_TO_WORLD_MAP", "WORLD_MAP"}:
        state.main_progress = "WORLD"
        await send_world_map(update, context, state)
        return True
    if command == "SIAK_CITY_MENU":
        state.location = "SIAK"
        await send_city_menu(update, context, state)
        return True
    if command == "SIAK_CITY_MENU_AFTER_UMAR":
        state.location = "SIAK"
        state.add_umar()
        await send_city_menu(
            update, context, state, extra_text="Umar kini menjadi anggota party."
        )
        return True
    if command == "SET_MAIN_RENGAT":
        state.main_progress = "Menuju Rengat (Lv 5+)"
        state.flags["SIAK_GATE_EVENT_DONE"] = True
        note = trigger_checkpoint_autosave(state, "chapter_unlock_rengat", notify=True)
        await send_world_map(update, context, state, extra_text=note or "")
        return True
    if command == "SET_MAIN_PEKANBARU":
        state.main_progress = "Menuju Pekanbaru (Lv 8+)"
        note = trigger_checkpoint_autosave(
            state, "chapter_unlock_pekanbaru", notify=True
        )
        await send_world_map(update, context, state, extra_text=note or "")
        return True
    if command == "SET_MAIN_KAMPAR":
        state.main_progress = "Menuju Kampar (Lv 12+)"
        state.flags["PEKANBARU_RUMOR_DONE"] = True
        aruna = state.party.get("ARUNA")
        if aruna:
            grant_skill_to_character(aruna, "ARUNA_CORE_AWAKENING")
        note = trigger_checkpoint_autosave(state, "chapter_unlock_kampar", notify=True)
        await send_world_map(update, context, state, extra_text=note or "")
        return True
    if command == "SQ_HARSAN_SHRINE":
        state.scene_id = "SQ_HARSAN_BLADE_SHRINE"
        await send_scene(update, context, state)
        return True
    if command == "ADD_REZA_PARTY":
        state.add_reza()
        state.scene_id = "CH2_REZA_JOINS"
        await send_scene(update, context, state)
        return True
    if command == "COMPLETE_UMAR_QUEST":
        state.flags["UMAR_QUEST_DONE"] = True
        umar = state.party.get("UMAR")
        if umar:
            grant_skill_to_character(umar, "SAFIYA_GRACE")
        state.scene_id = "SQ_UMAR_REWARD"
        quest_block = [
            "==== QUEST SELESAI ====",
            '"Warisan Safiya" telah diselesaikan.',
            "Umar mendapatkan skill Grace Safiya!",
        ]
        quest_text = extra_text or "\n".join(quest_block)
        autosave_note = trigger_checkpoint_autosave(
            state, "umar_quest_completed", notify=True
        )
        quest_text = append_optional_text(quest_text, autosave_note)
        await send_scene(
            update,
            context,
            state,
            extra_text=quest_text,
        )
        return True
    if command == "COMPLETE_REZA_QUEST":
        state.flags["REZA_QUEST_DONE"] = True
        reza = state.party.get("REZA")
        if reza:
            grant_skill_to_character(reza, "MASTER_LEGACY")
        state.scene_id = "SQ_REZA_REWARD"
        quest_block = [
            "==== QUEST SELESAI ====",
            '"Suara dari Segel" telah diselesaikan.',
            "Reza mendapatkan skill Warisan Sang Guru!",
        ]
        quest_text = extra_text or "\n".join(quest_block)
        autosave_note = trigger_checkpoint_autosave(
            state, "reza_quest_completed", notify=True
        )
        quest_text = append_optional_text(quest_text, autosave_note)
        await send_scene(
            update,
            context,
            state,
            extra_text=quest_text,
        )
        return True
    return False


async def handle_story_battle_trigger(
    battle_key: Optional[str],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    next_scene: Optional[str] = None,
) -> bool:
    if not battle_key:
        return False
    route = STORY_BATTLE_ROUTES.get(battle_key)
    if not route:
        return False
    set_scene = route.get("set_scene")
    if set_scene:
        state.scene_id = set_scene
    if route.get("type") == "random":
        # Untuk BATTLE_TUTORIAL_1, gunakan Shadow Slime secara spesifik
        if battle_key == "BATTLE_TUTORIAL_1":
            await start_fixed_battle(update, context, state, "SHADOW_SLIME")
        else:
            await start_random_battle(update, context, state)
        return True
    enemy = route.get("enemy")
    return_scene = next_scene or route.get("return_scene")
    if not enemy or not return_scene:
        return False
    await start_story_battle(
        update,
        context,
        state,
        enemy,
        return_scene,
        loss_scene=route.get("loss_scene"),
    )
    return True


def handle_scene_side_effects(state: GameState) -> str:
    extras: List[str] = []
    if state.scene_id == "SQ_HARSAN_BLADE_VISION" and not state.flags.get(
        "WEAPON_QUEST_DONE"
    ):
        state.flags["QUEST_WEAPON_DONE"] = True
        state.flags["WEAPON_QUEST_DONE"] = True
        state.flags["QUEST_WEAPON_STARTED"] = True
        state.flags["WEAPON_QUEST_STARTED"] = True
        adjust_inventory(state, "HARSAN_LEGACY_BLADE", 1)
        success, equip_msg = equip_item(state, "ARUNA", "HARSAN_LEGACY_BLADE")
        if not success:
            equip_msg = "Pedang baru tersimpan di tas."
        aruna = state.party.get("ARUNA")
        if aruna:
            grant_skill_to_character(aruna, "LEGACY_RADIANCE")
        quest_lines = [
            "==== QUEST SELESAI ====",
            '"Jejak Pedang Warisan" telah diselesaikan.',
            "Pedang leluhur kekaisaran beresonansi dengan kutukan abadi dalam dirimu!",
            equip_msg,
            "Skill baru diperoleh: Legacy Radiance.",
        ]
        quest_text = "\n".join(quest_lines)
        autosave_note = trigger_checkpoint_autosave(
            state, "weapon_quest_completed", notify=True
        )
        quest_text = append_optional_text(quest_text, autosave_note)
        extras.append(quest_text)
    if state.scene_id == "CH5_FLOOR5" and (
        state.flags.get("WEAPON_QUEST_DONE") or state.flags.get("QUEST_WEAPON_DONE")
    ):
        aruna = state.party.get("ARUNA")
        wielding = aruna and aruna.weapon_id == "HARSAN_LEGACY_BLADE"
        if wielding:
            extras.append(
                'Zabx menatap pedangmu: "Itu bilah kekaisaran... cahaya yang dulu mengkhianatiku." Aura iblisnya bergolak.'
            )
        else:
            extras.append(
                "Aura pedang warisan dalam tasmu membuat Zabx gelisah, seolah ia merasakan tatapan para kaisar masa lalu."
            )
    if state.scene_id == "CH5_FINAL_WIN":
        if state.flags.get("UMAR_QUEST_DONE") and state.flags.get("REZA_QUEST_DONE"):
            extras.append(
                "Kutukan abadi, doa Umar, dan segel Reza beresonansi. Jalan menuju TRUE ENDING terbuka."
            )
        else:
            extras.append(
                "Ada janji yang belum ditutup. Selesaikan Warisan Safiya dan Suara dari Segel untuk menemukan akhir sejati."
            )
    return "\n\n".join(extras)


def apply_story_tokens(text_lines: List[str], state: GameState) -> List[str]:
    hero_name = (
        state.player_name or state.party.get("ARUNA").name
        if state.party.get("ARUNA")
        else "Ksatria"
    )
    replacements = {
        "{PLAYER_NAME}": hero_name,
        "{HERO_NAME}": hero_name,
    }
    result: List[str] = []
    for line in text_lines:
        for src, dest in replacements.items():
            line = line.replace(src, dest)
        result.append(line)
    return result


async def send_scene(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    extra_text: str = "",
):
    reward_text = handle_scene_side_effects(state)
    data = get_scene(state.scene_id)
    if not data:
        await send_scene_not_found(
            update, context, state, missing_scene_id=state.scene_id
        )
        return

    apply_flags_from_data(state, data.get("flags"))

    if not requirements_met(data.get("requirements"), state):
        text = "Maaf, terjadi kesalahan pada cerita. Syarat scene belum terpenuhi."
        keyboard = make_keyboard([("Kembali ke map", "GO_TO_WORLD_MAP")])
        query = update.callback_query
        if query:
            await safe_edit_text(query, text=text, reply_markup=keyboard)
        elif update.message:
            await update.message.reply_text(text=text, reply_markup=keyboard)
        await send_world_map(update, context, state)
        return

    text_lines = data.get("text", []) or []
    if isinstance(text_lines, str):
        text_lines = text_lines.split("\n")
    if not text_lines:
        text_lines = ["Maaf, terjadi kesalahan pada cerita. Teks scene kosong."]
    text_lines = apply_story_tokens(text_lines, state)
    text = "\n".join(text_lines)
    if reward_text:
        extra_text = reward_text + ("\n\n" + extra_text if extra_text else "")
    if extra_text:
        text = extra_text + "\n\n" + text

    choices_raw = data.get("choices", []) or []
    visible_choices: List[Tuple[str, str]] = []
    for choice in choices_raw:
        if not requirements_met(choice.get("requirements"), state):
            continue
        callback_data = choice.get("callback_data")
        label = choice.get("label") or "Lanjut"
        if callback_data:
            visible_choices.append((label, callback_data))
    if not visible_choices:
        default_choice = build_default_choice()
        visible_choices.append(
            (default_choice["label"], default_choice["callback_data"])
        )
    keyboard = make_keyboard(visible_choices)
    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text=text, reply_markup=keyboard)


async def render_scene(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    scene_id: str,
    extra_text: str = "",
):
    state.scene_id = scene_id
    await send_scene(update, context, state, extra_text=extra_text)


async def handle_scene_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    choice_data: str,
):
    scene_data = get_scene(state.scene_id)
    selected_choice = find_choice_by_callback(scene_data, choice_data)

    if selected_choice:
        if not requirements_met(selected_choice.get("requirements"), state):
            await send_scene(
                update,
                context,
                state,
                extra_text="Pilihan ini belum bisa dipilih. Syarat belum terpenuhi.",
            )
            return
        apply_flags_from_data(state, selected_choice.get("flags"))
        battle_key = selected_choice.get("battle") or (
            choice_data if choice_data.startswith("BATTLE_") else None
        )
        next_scene = selected_choice.get("next_scene")
        command = selected_choice.get("command")

        handled_battle = await handle_story_battle_trigger(
            battle_key, update, context, state, next_scene=next_scene
        )
        if handled_battle:
            return

        handled_command = await execute_story_command(
            command or next_scene or choice_data, update, context, state
        )
        if handled_command:
            return

        target_scene = next_scene or choice_data
        if target_scene in SCENES:
            await render_scene(update, context, state, target_scene)
            return

        await send_scene_not_found(
            update, context, state, missing_scene_id=target_scene or choice_data
        )
        return

    battle_key = choice_data if choice_data.startswith("BATTLE_") else None
    if await handle_story_battle_trigger(battle_key, update, context, state):
        return

    if await execute_story_command(choice_data, update, context, state):
        return

    if choice_data == "TRUE_ENDING_TRIGGER":
        has_true = state.flags.get("UMAR_QUEST_DONE") and state.flags.get(
            "REZA_QUEST_DONE"
        )
        state.scene_id = "TRUE_ENDING" if has_true else "GOOD_ENDING"
        state.main_progress = "Epilog"
        await send_scene(update, context, state)
        return

    if choice_data in SCENES:
        await render_scene(update, context, state, choice_data)
        return

    await send_scene_not_found(update, context, state, missing_scene_id=choice_data)


# ==========================
# WORLD MAP & CITY MENU
# ==========================


async def send_world_map(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    extra_text: str = "",
):
    current_loc = LOCATIONS.get(state.location)
    if not current_loc:
        logger.warning(
            "Lokasi state tidak dikenal untuk user %s: %s",
            state.user_id,
            state.location,
        )
    loc_name = current_loc.get("name") if current_loc else state.location
    lines = [
        "=== PETA DUNIA ===",
        f"Lokasi saat ini: {loc_name}",
        f"Main Quest: {state.main_progress}",
        "",
        "Kota yang dikenal:",
    ]
    hero_level = highest_party_level(state)
    for loc_id, info in LOCATIONS.items():
        status = (
            "Siap dikunjungi"
            if hero_level >= info.get("min_level", 1)
            else f"Butuh Lv {info.get('min_level', 1)}"
        )
        lines.append(f"- {info['name']} (Lv {info['min_level']}+): {status}")
    lines.append("")
    lines.append("Area hunting utama:")
    for area_id, info in sorted(
        HUNTING_AREAS.items(), key=lambda item: item[1].get("min_level", 1)
    ):
        status = (
            "Terbuka"
            if hero_level >= info.get("min_level", 1)
            else f"Butuh Lv {info.get('min_level', 1)}"
        )
        lines.append(
            f"- {info['name']} ({info['level_range']}, elemen {info['element']}): {status}"
        )
    if extra_text:
        lines.append("")
        lines.append(extra_text)
    text = "\n".join(lines)
    choices: List[Tuple[str, str]] = []
    for loc_id, info in LOCATIONS.items():
        if hero_level < info.get("min_level", 1):
            continue
        label = info.get("name", loc_id)
        choices.append((label, f"GOTO_CITY|{loc_id}"))
    choices.append(("🗺️ Menu Hunting", "MENU_HUNTING"))
    keyboard = make_keyboard(choices)
    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=keyboard)
    else:
        await update.effective_chat.send_message(text, reply_markup=keyboard)


async def send_city_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    extra_text: str = "",
):
    loc = LOCATIONS.get(state.location)
    if not loc:
        logger.error(
            "Lokasi kota tidak dikenal untuk user %s: %s", state.user_id, state.location
        )
        await send_world_map(
            update,
            context,
            state,
            extra_text="Terjadi kesalahan lokasi. Kamu dikembalikan ke peta dunia.",
        )
        return
    features = CITY_FEATURES.get(state.location, {})
    lines = [
        f"=== {loc['name'].upper()} ===",
        f"Level minimum: {loc['min_level']}",
        "",
    ]
    description = features.get("description")
    if description:
        lines.append(description)
        lines.append("")
    lines.append(f"Gold: {state.gold}")
    if extra_text:
        lines.append("")
        lines.append(extra_text)
    lines.append("")
    lines.append("Pilih menu:")
    text = "\n".join(lines)

    choices = [
        ("Status Party", "MENU_STATUS"),
        ("Equipment", "MENU_EQUIPMENT"),
        ("Inventory", "MENU_INVENTORY"),
    ]
    if loc.get("has_shop"):
        choices.append(("Toko", "MENU_SHOP"))
    
    # Crafting tersedia di semua kota kecuali Kampar (cursed)
    if state.location != "KAMPAR":
        choices.append(("Crafting", "MENU_CRAFTING"))
    
    if loc.get("has_guild"):
        choices.append(("Guild Quest", "MENU_GUILD"))
        # Jobs hanya di kota dengan guild
        choices.append(("Pekerjaan", "MENU_JOBS"))
    
    if loc.get("has_inn"):
        choices.append(("Penginapan", "MENU_INN"))
    if loc.get("has_clinic"):
        choices.append(("Klinik", "MENU_CLINIC"))
    choices.append(("Hunting", "MENU_HUNTING"))

    # Event / side quest per kota
    if state.location == "SIAK":
        if state.flags.get("HAS_UMAR") and not state.flags.get("UMAR_QUEST_DONE"):
            choices.append(("Side Quest Umar: Warisan Safiya", "QUEST_UMAR"))
        if not state.flags.get("SIAK_GATE_EVENT_DONE"):
            choices.append(("Periksa gerbang kota", "EVENT_SIAK_GATE"))
    if (
        state.location == "RENGAT"
        and state.flags.get("HAS_REZA")
        and not state.flags.get("REZA_QUEST_DONE")
    ):
        choices.append(("Side Quest Reza: Suara dari Segel", "QUEST_REZA"))
    if state.location == "PEKANBARU" and not state.flags.get("PEKANBARU_RUMOR_DONE"):
        choices.append(("Cari rumor di kafe remang", "EVENT_PEKANBARU_CAFE"))
    if (
        state.location == "PEKANBARU"
        and state.flags.get("VISITED_PEKANBARU")
        and not (
            state.flags.get("WEAPON_QUEST_DONE") or state.flags.get("QUEST_WEAPON_DONE")
        )
    ):
        started = state.flags.get("QUEST_WEAPON_STARTED") or state.flags.get(
            "WEAPON_QUEST_STARTED"
        )
        label = (
            "Lanjutkan pencarian pedang Harsan"
            if started
            else "Jejak pedang warisan Harsan"
        )
        choices.append((label, "QUEST_HARSAN_BLADE"))
    if state.location == "KAMPAR":
        choices.append(("Menuju Kastil Febri", "EVENT_KASTIL_ENTRY"))

    choices.append(("Kembali ke world map", "GO_TO_WORLD_MAP"))

    keyboard = make_keyboard(choices)
    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=keyboard)
    elif update.effective_chat:
        await update.effective_chat.send_message(text, reply_markup=keyboard)


async def send_guild_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    loc = LOCATIONS.get(state.location, {})
    if not loc.get("has_guild"):
        text = "Tidak ada cabang guild di kota ini."
        keyboard = make_keyboard([("Kembali ke kota", "BACK_CITY_MENU")])
        query = update.callback_query
        if query:
            await safe_edit_text(query, text=text, reply_markup=keyboard)
        elif update.message:
            await update.message.reply_text(text=text, reply_markup=keyboard)
        else:
            await update.effective_chat.send_message(text, reply_markup=keyboard)
        return

    def describe_target(monster_id: Optional[str]) -> Tuple[str, Optional[str]]:
        if not monster_id:
            return "-", None
        monster = MONSTERS.get(monster_id)
        if not monster:
            return monster_id, None
        base = f"{monster['name']} (Lv {monster.get('level', '?')})"
        area_key = monster.get("area")
        area_text = None
        if area_key:
            for area_id, info in HUNTING_AREAS.items():
                if info.get("area_key") == area_key or area_id == area_key:
                    area_name = info.get("name", area_id)
                    level_range = info.get("level_range")
                    area_text = area_name
                    if level_range:
                        area_text += f" – {level_range}"
                    break
        return base, area_text

    loc_name = loc.get("name", state.location)
    quests = get_city_guild_quests(state.location)
    hero_level = highest_party_level(state)
    lines = [
        "=== GUILD QUESTS ===",
        f"Lokasi: {loc_name}",
        f"Level party tertinggi: {hero_level}",
        "",
        "-- Active Quests --",
    ]
    if state.quests_active:
        for quest in state.quests_active.values():
            target_desc, area_hint = describe_target(quest.target)
            lines.append(
                f"ID: {quest.id} | Type: {quest.type.title()} | Target: {target_desc} | Progress: {quest.progress}/{quest.required_amount}"
            )
            if area_hint:
                lines.append(f"   Area: {area_hint}")
            if quest.status == "COMPLETED":
                lines.append("   Status: ✔ Siap dilaporkan ke guild.")
    else:
        lines.append("Tidak ada quest aktif.")

    lines.append("")
    lines.append("-- Completed Quests --")
    if state.quests_completed:
        for quest in state.quests_completed[-5:]:
            target_desc, _ = describe_target(quest.target)
            reward_status = (
                "Hadiah diambil" if quest.reward_received else "Belum klaim hadiah"
            )
            lines.append(f"ID: {quest.id} | Target: {target_desc} | {reward_status}")
    else:
        lines.append("Belum ada quest yang selesai.")

    lines.append("")
    lines.append("-- Available Quests --")
    if not quests:
        lines.append("Belum ada kontrak berburu di papan pengumuman.")
    else:
        for quest_id, data in quests.items():
            quest_state = state.quests_active.get(quest_id)
            completed = find_completed_quest(state, quest_id)
            quest_type = data.get("type", "HUNT").title()
            target_desc, area_hint = describe_target(data.get("target"))
            lines.append(f"ID: {quest_id} | {data['name']} | Type: {quest_type}")
            lines.append(f"   Target: {target_desc}")
            if area_hint:
                lines.append(f"   Area: {area_hint}")
            reward_parts: List[str] = []
            if data.get("reward_gold"):
                reward_parts.append(f"{data['reward_gold']} Gold")
            for item_id, qty in data.get("reward_items", {}).items():
                item = ITEMS.get(item_id, {"name": item_id})
                reward_parts.append(f"{item['name']} x{qty}")
            reward_text = ", ".join(reward_parts) if reward_parts else "-"
            lines.append(
                f"   Hadiah: {reward_text} | Syarat level: Lv {data.get('min_level', 1)}+"
            )
            if quest_state:
                lines.append(
                    f"   Status: Sedang berlangsung ({quest_state.progress}/{quest_state.required_amount})"
                )
                if quest_state.status == "COMPLETED":
                    lines.append("   Catatan: Laporkan untuk klaim hadiah.")
            elif completed:
                lines.append("   Status: ✔ Sudah diselesaikan.")
            else:
                status_text = (
                    "Tersedia"
                    if hero_level >= data.get("min_level", 1)
                    else f"Butuh Lv {data.get('min_level', 1)}"
                )
                lines.append(f"   Status: {status_text}")

    buttons: List[List[InlineKeyboardButton]] = []
    for quest_id, data in quests.items():
        quest_state = state.quests_active.get(quest_id)
        completed = find_completed_quest(state, quest_id)
        if quest_state and quest_state.status == "COMPLETED":
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Klaim '{data['name']}'",
                        callback_data=f"GUILD_CLAIM|{quest_id}",
                    )
                ]
            )
        elif not quest_state and not completed:
            min_level = data.get("min_level", 1)
            if hero_level >= min_level:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"Ambil '{data['name']}'",
                            callback_data=f"GUILD_ACCEPT|{quest_id}",
                        )
                    ]
                )
    buttons.append([InlineKeyboardButton("⬅ Kembali", callback_data="BACK_CITY_MENU")])
    markup = InlineKeyboardMarkup(buttons)
    text = "\n".join(lines)
    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=markup)
    elif update.effective_chat:
        await update.effective_chat.send_message(text, reply_markup=markup)


async def handle_guild_accept(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, quest_id: str
):
    success, message = accept_guild_quest(state, quest_id)
    await update.callback_query.answer(message, show_alert=not success)
    await send_guild_menu(update, context, state)


async def handle_guild_claim(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, quest_id: str
):
    success, message = complete_guild_quest(state, quest_id)
    await update.callback_query.answer(message, show_alert=not success)
    await send_guild_menu(update, context, state)


async def send_shop_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    query = update.callback_query
    loc_info = LOCATIONS.get(state.location)
    if not loc_info or not loc_info.get("has_shop"):
        if query:
            await safe_edit_text(
                query,
                "Tidak ada toko di lokasi ini.",
                reply_markup=make_keyboard([("Kembali ke kota", "BACK_CITY_MENU")]),
            )
        elif update.message:
            await update.message.reply_text("Tidak ada toko di lokasi ini.")
        return
    features = CITY_FEATURES.get(state.location, {})
    shop_items = features.get("shop_items", [])
    lines = [
        f"=== TOKO {loc_info['name'].upper()} ===",
        f"Gold: {state.gold}",
        "",
        "Silakan pilih kategori barang:"
    ]
    buttons = [
        [InlineKeyboardButton("Beli Equipment", callback_data="SHOP_BUY_EQUIPMENT")],
        [InlineKeyboardButton("Beli Item Consumable", callback_data="SHOP_BUY_CONSUMABLE")],
        [InlineKeyboardButton("Jual Barang", callback_data="SHOP_SELL")],
        [InlineKeyboardButton("Kembali", callback_data="BACK_CITY_MENU")],
    ]
    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(buttons)
    if query:
        await safe_edit_text(query, text=text, reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=markup)


async def send_hunting_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    extra_text: str = "",
):
    hero_level = highest_party_level(state)
    lines = ["=== AREA HUNTING ===", f"Level tertinggi party: {hero_level}", ""]
    if extra_text:
        lines.append(extra_text)
        lines.append("")
    lines.append("Pilih area:")
    buttons: List[List[InlineKeyboardButton]] = []
    for area_id, info in sorted(
        HUNTING_AREAS.items(), key=lambda item: item[1].get("min_level", 1)
    ):
        status = (
            "Tersedia"
            if hero_level >= info.get("min_level", 1)
            else f"Butuh Lv {info.get('min_level', 1)}"
        )
        lines.append(
            f"- {info['name']} ({info['level_range']}, {info['element']}) - {status}"
        )
        if hero_level >= info.get("min_level", 1):
            buttons.append(
                [
                    InlineKeyboardButton(
                        info["name"], callback_data=f"HUNT_AREA|{area_id}"
                    )
                ]
            )
    buttons.append([InlineKeyboardButton("Kembali ke Kota", callback_data="BACK_CITY_MENU")])
    markup = InlineKeyboardMarkup(buttons)
    text = "\n".join(lines)
    query = update.callback_query
    if query:
        await safe_edit_text(query, text=text, reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=markup)


async def send_hunting_area_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    area_id: str,
    extra_text: str = "",
    *,
    force_new_message: bool = False,
    chat_id: Optional[int] = None,
):
    area = HUNTING_AREAS.get(area_id)
    if not area:
        await update.callback_query.answer(
            "Area hunting tidak dikenal.", show_alert=True
        )
        await send_hunting_menu(update, context, state)
        return
    hero_level = highest_party_level(state)
    if hero_level < area.get("min_level", 1):
        await update.callback_query.answer("Levelmu belum cukup.", show_alert=True)
        await send_hunting_menu(update, context, state)
        return
    state.flags["LAST_HUNT_AREA"] = area_id
    lines = [
        f"=== {area['name']} ===",
        f"Rekomendasi level: {area['level_range']} (min Lv {area['min_level']})",
        f"Elemen dominan: {area['element']}",
        area.get("description", ""),
    ]
    if extra_text:
        lines.append("")
        lines.append(extra_text)
    monsters = area.get("monsters", [])
    if monsters:
        lines.append("")
        lines.append("Monster yang sering muncul:")
        for mid in monsters:
            monster = MONSTERS.get(mid)
            if not monster:
                continue
            lines.append(f"- {monster['name']} [{monster.get('rank', 'MEDIUM')}]")
    auto_active = state.auto_hunt and state.auto_hunt_area == area_id
    buttons: List[List[InlineKeyboardButton]] = []
    buttons.append(
        [
            InlineKeyboardButton(
                "⚔️ Bertarung sekali", callback_data=f"HUNT_BATTLE|{area_id}"
            )
        ]
    )
    if auto_active:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Hentikan Auto Hunting", callback_data="AUTO_HUNT_OFF"
                )
            ]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Mulai Auto Hunting", callback_data=f"AUTO_HUNT_ON|{area_id}"
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton("Daftar Area", callback_data="MENU_HUNTING")]
    )
    buttons.append(
        [InlineKeyboardButton("Kembali ke Kota", callback_data="BACK_CITY_MENU")]
    )
    markup = InlineKeyboardMarkup(buttons)
    query = update.callback_query
    text = "\n".join(lines)
    if force_new_message:
        target_chat = chat_id
        if not target_chat and update.effective_chat:
            target_chat = update.effective_chat.id
        if target_chat:
            await context.bot.send_message(
                chat_id=target_chat, text=text, reply_markup=markup
            )
        return
    if query:
        await safe_edit_text(query, text=text, reply_markup=markup)
    else:
        await update.effective_chat.send_message(text, reply_markup=markup)


async def handle_auto_hunt_toggle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    area_id: Optional[str],
    enable: bool,
):
    query = update.callback_query
    if not AUTO_HUNT_RATE_LIMITER.is_allowed(state.user_id):
        if query:
            await query.answer(
                "Tunggu sebentar sebelum mengganti status auto hunting.", show_alert=True
            )
        else:
            await safe_reply(
                update,
                context,
                "Tunggu sebentar sebelum mengganti status auto hunting.",
            )
        return
    if enable:
        if state.auto_hunt:
            if query:
                await query.answer("Auto hunting sudah aktif.", show_alert=True)
            return
        if not area_id:
            if query:
                await query.answer("Area tidak valid.", show_alert=True)
            return
        area = HUNTING_AREAS.get(area_id)
        if not area:
            if query:
                await query.answer("Area hunting tidak dikenal.", show_alert=True)
            return
        hero_level = highest_party_level(state)
        if hero_level < area.get("min_level", 1):
            if query:
                await query.answer(
                    "Levelmu belum cukup untuk area ini.", show_alert=True
                )
            return
        if not living_party_members(state):
            if query:
                await query.answer(
                    "Seluruh party sedang tidak mampu bertarung.", show_alert=True
                )
            return
        state.auto_hunt = True
        state.auto_hunt_area = area_id
        state.flags["LAST_HUNT_AREA"] = area_id
        stats = {
            "session_area": area_id,
            "start_level": {
                cid: state.party[cid].level
                for cid in state.party_order
                if cid in state.party
            },
            "start_xp": {cid: state.xp_pool.get(cid, 0) for cid in state.party_order},
            "last_level_up_xp": {
                cid: state.xp_pool.get(cid, 0) for cid in state.party_order
            },
            "gained_xp": {cid: 0 for cid in state.party_order},
            "gained_gold": 0,
            "kills": 0,
            "items_gained": {},
            "stop_reason": "",
            "summary_sent": False,
            "loop_active": False,
        }
        if update.effective_chat:
            stats["auto_chat_id"] = update.effective_chat.id
        if query and query.message:
            stats["auto_message_id"] = query.message.message_id
        state.auto_hunt_stats = stats
        area_name = area.get("name", area_id)
        if query:
            await query.answer(
                f"Auto hunting dimulai di {area_name}.", show_alert=False
            )
        session = state.auto_hunt_session or AutoHuntSession()
        state.auto_hunt_session = session
        session.active = True
        runner = run_auto_hunt_loop(update, context, state)
        if context.application:
            task = context.application.create_task(runner)
        else:
            task = asyncio.create_task(runner)
        session.attach_task(task, area_id)
    else:
        if not state.auto_hunt:
            if query:
                await query.answer("Auto hunting tidak aktif.", show_alert=True)
            return
        reason_text = "Auto hunting dihentikan."
        if state.auto_hunt_stats:
            reason_text = "Dihentikan oleh pemain."
            state.auto_hunt_stats["stop_reason"] = reason_text
        finalize_now = not state.auto_hunt_stats or not state.auto_hunt_stats.get(
            "loop_active"
        )
        state.auto_hunt = False
        if state.auto_hunt_session:
            state.auto_hunt_session.active = False
        if query:
            await query.answer("Sedang menghentikan auto hunting...", show_alert=False)
        if finalize_now:
            runner = stop_auto_hunt(update, context, state, reason=reason_text)
            if context.application:
                context.application.create_task(runner)
            else:
                asyncio.create_task(runner)
        elif state.auto_hunt_session and state.auto_hunt_session.task:
            if context.application:
                context.application.create_task(
                    state.auto_hunt_session.stop(reason_text)
                )
            else:
                asyncio.create_task(state.auto_hunt_session.stop(reason_text))


def get_low_hp_allies(state: GameState, threshold: float) -> List[CharacterState]:
    allies: List[Tuple[float, CharacterState]] = []
    for cid in state.party_order:
        member = state.party.get(cid)
        if not member or member.hp <= 0:
            continue
        max_hp = max(1, get_effective_max_hp(member))
        ratio = member.hp / max_hp
        if ratio <= threshold:
            allies.append((ratio, member))
    allies.sort(key=lambda entry: entry[0])
    return [ally for _, ally in allies]


def select_auto_heal_skill(
    character: CharacterState, prefer_group: bool = False
) -> Optional[Tuple[str, Dict[str, Any]]]:
    best_single: Optional[Tuple[str, Dict[str, Any]]] = None
    best_single_power = -1.0
    best_group: Optional[Tuple[str, Dict[str, Any]]] = None
    best_group_power = -1.0
    for skill_id in character.skills:
        skill = SKILLS.get(skill_id)
        if not skill:
            continue
        skill_type = skill.get("type")
        if skill_type not in {"HEAL_SINGLE", "HEAL_ALL"}:
            continue
        mp_cost = skill.get("mp_cost", 0)
        if character.mp < mp_cost:
            continue
        power = skill.get("power", 0.3)
        if skill_type == "HEAL_ALL":
            if power > best_group_power:
                best_group = (skill_id, skill)
                best_group_power = power
        else:
            if power > best_single_power:
                best_single = (skill_id, skill)
                best_single_power = power
    if prefer_group and best_group:
        return best_group
    return best_single or best_group


def select_auto_damage_skill(
    character: CharacterState, enemy: Optional[Dict[str, Any]]
) -> Optional[Tuple[str, Dict[str, Any]]]:
    best_choice: Optional[Tuple[str, Dict[str, Any]]] = None
    best_score = 0.0
    for skill_id in character.skills:
        skill = SKILLS.get(skill_id)
        if not skill or skill.get("type") not in {"PHYS", "MAG"}:
            continue
        mp_cost = skill.get("mp_cost", 0)
        if character.mp < mp_cost:
            continue
        hits = max(1, int(skill.get("hits", 1)))
        score = skill.get("power", 1.0) * hits
        element = skill.get("element", "NETRAL")
        if enemy:
            weakness = enemy.get("weakness", [])
            resist = enemy.get("resist", [])
            if element and element in weakness:
                score *= 1.4
            if element and element in resist:
                score *= 0.7
        if score > best_score:
            best_score = score
            best_choice = (skill_id, skill)
    return best_choice


def perform_auto_player_action(
    state: GameState, character: CharacterState, enemy: Dict[str, Any]
) -> Tuple[List[str], bool]:
    logs: List[str] = []
    if character.hp <= 0 or enemy.get("hp", 0) <= 0:
        return logs, enemy.get("hp", 0) <= 0

    low_allies = get_low_hp_allies(state, 0.35)
    multi_low = get_low_hp_allies(state, 0.55)
    heal_choice = None
    if low_allies:
        heal_choice = select_auto_heal_skill(
            character, prefer_group=len(multi_low) >= 2
        )
    if heal_choice:
        skill_id, skill = heal_choice
        mp_cost = skill.get("mp_cost", 0)
        if character.mp >= mp_cost:
            character.mp -= mp_cost
            if skill.get("type") == "HEAL_ALL":
                healed_parts: List[str] = []
                for cid in state.party_order:
                    member = state.party.get(cid)
                    if not member or member.hp <= 0:
                        continue
                    heal_amount = calc_heal_amount(character, skill.get("power", 0.25))
                    before = member.hp
                    member.hp = min(
                        get_effective_max_hp(member), member.hp + heal_amount
                    )
                    diff = member.hp - before
                    healed_parts.append(f"{member.name}+{diff}")
                logs.append(
                    f"{character.name} menyalurkan {skill['name']} ke seluruh party."
                )
                if healed_parts:
                    logs.append("Pemulihan: " + ", ".join(healed_parts))
                else:
                    logs.append("Tidak ada ally yang butuh penyembuhan.")
                return logs, False
            target = low_allies[0]
            heal_amount = calc_heal_amount(character, skill.get("power", 0.3))
            before = target.hp
            target.hp = min(get_effective_max_hp(target), target.hp + heal_amount)
            healed = target.hp - before
            logs.append(
                f"{character.name} menggunakan {skill['name']} ke {target.name} (+{healed} HP)."
            )
            return logs, False

    damage_choice = select_auto_damage_skill(character, enemy)
    if damage_choice:
        skill_id, skill = damage_choice
        mp_cost = skill.get("mp_cost", 0)
        if character.mp >= mp_cost:
            character.mp -= mp_cost
            hits = max(1, int(skill.get("hits", 1)))
            skill_type = skill.get("type")
            element = skill.get("element", "NETRAL")
            total_damage = 0
            hit_logs: List[str] = []
            hit_weak = False
            hit_resist = False
            for hit in range(hits):
                if skill_type == "MAG":
                    dmg, h_weak, h_resist = calc_magic_damage(
                        character,
                        enemy["defense"],
                        skill.get("power", 1.0),
                        element,
                        enemy.get("weakness"),
                        enemy.get("resist"),
                        enemy.get("element"),
                    )
                else:
                    dmg, h_weak, h_resist = calc_physical_damage(
                        character,
                        enemy["defense"],
                        skill.get("power", 1.0),
                        element,
                        enemy.get("weakness"),
                        enemy.get("resist"),
                        enemy.get("element"),
                    )
                if element == "CAHAYA" and state.flags.get("LIGHT_BUFF_TURNS"):
                    dmg = int(dmg * 1.2)
                enemy["hp"] -= dmg
                total_damage += dmg
                hit_weak = hit_weak or h_weak
                hit_resist = hit_resist or h_resist
                if hits > 1:
                    hit_logs.append(f"Hit {hit + 1}: {dmg} damage")
            logs.append(f"{character.name} menggunakan {skill['name']}!")
            if hit_logs:
                logs.extend(hit_logs)
                logs.append(f"Total damage: {total_damage}.")
            else:
                logs.append(f"{enemy['name']} menerima {total_damage} damage.")
            if hit_weak:
                logs.append("Serangan ini mengenai kelemahan musuh!")
            if hit_resist:
                logs.append("Musuh menahan sebagian serangan ini.")
            return logs, enemy.get("hp", 0) <= 0

    damage, hit_weak, hit_resist = calc_physical_damage(
        character,
        enemy.get("defense", 0),
        power=1.0,
        element="NETRAL",
        target_weakness=enemy.get("weakness"),
        target_resist=enemy.get("resist"),
        target_element=enemy.get("element"),
    )
    enemy["hp"] -= damage
    logs.append(f"{character.name} menyerang {enemy['name']} → {damage} damage.")
    if hit_weak:
        logs.append("Serangan mengenai kelemahan musuh!")
    if hit_resist:
        logs.append("Musuh menahan sebagian damage.")
    return logs, enemy.get("hp", 0) <= 0


def perform_auto_enemy_attack(
    state: GameState, enemy: Dict[str, Any]
) -> Tuple[List[str], bool]:
    logs: List[str] = []
    target_id = choose_random_party_target(state)
    if not target_id:
        return logs, True
    target = state.party.get(target_id)
    if not target:
        return logs, True
    dmg = calc_enemy_basic_damage(
        enemy.get("atk", 1), get_effective_stat(target, "defense")
    )
    defending = state.flags.get("DEFENDING", {})
    if defending.get(target_id):
        dmg = max(1, dmg // 2)
        defending.pop(target_id, None)
        if not defending:
            state.flags.pop("DEFENDING", None)
    dmg = apply_mana_shield_absorption(state, target_id, dmg, logs)
    if dmg <= 0:
        return logs, False
    target.hp -= dmg
    logs.append(f"{enemy['name']} menyerang {target.name} dan memberikan {dmg} damage.")
    if target.hp <= 0:
        target.hp = 0
        logs.append(f"{target.name} tumbang!")
    party_dead = not any(
        state.party.get(cid) and state.party[cid].hp > 0 for cid in state.party_order
    )
    return logs, party_dead


async def send_auto_hunt_state(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    log_lines: List[str],
):
    stats = state.auto_hunt_stats or {}
    area_id = stats.get("session_area")
    area_name = HUNTING_AREAS.get(area_id, {}).get("name", area_id or "-")
    kills = stats.get("kills", 0)
    total_gold = stats.get("gained_gold", 0)
    gained_xp = stats.get("gained_xp", {})
    xp_parts: List[str] = []
    for cid in state.party_order:
        member = state.party.get(cid)
        if not member:
            continue
        xp_parts.append(f"{member.name} +{gained_xp.get(cid, 0)}")
    xp_line = "XP Didapat: " + (", ".join(xp_parts) if xp_parts else "0")
    lines = [
        f"=== AUTO HUNTING: {area_name} ===",
        f"Kills: {kills} | Total Gold: {total_gold}",
        xp_line,
        "",
        "Status Party:",
    ]
    for cid in state.party_order:
        member = state.party.get(cid)
        if not member:
            continue
        eff_hp = get_effective_max_hp(member)
        eff_mp = get_effective_max_mp(member)
        lines.append(
            f"- {member.name} Lv {member.level} HP {member.hp}/{eff_hp} MP {member.mp}/{eff_mp}"
        )
    lines.append("")
    lines.append("Musuh:")
    enemy = state.battle_enemies[0] if state.battle_enemies else None
    if enemy:
        lines.append(
            f"- {enemy['name']} HP {max(0, enemy.get('hp', 0))}/{enemy.get('max_hp', enemy.get('hp', 0))}"
        )
    else:
        lines.append("- (tidak ada)")
    if log_lines:
        lines.append("")
        lines.append("---- Aksi Terakhir ----")
        lines.extend(log_lines[-5:])
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Hentikan Auto Hunting", callback_data="AUTO_HUNT_OFF"
                )
            ]
        ]
    )
    chat_id = stats.get("auto_chat_id") or (
        update.effective_chat.id if update.effective_chat else None
    )
    message_id = stats.get("auto_message_id")
    try:
        if chat_id and message_id:
            await safe_edit_message(
                context,
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
            )
        elif update.callback_query and update.callback_query.message:
            await safe_edit_text(
                update.callback_query, text=text, reply_markup=keyboard
            )
            state.auto_hunt_stats["auto_chat_id"] = (
                update.callback_query.message.chat_id
            )
            state.auto_hunt_stats["auto_message_id"] = (
                update.callback_query.message.message_id
            )
        elif chat_id:
            message = await safe_send_message(
                context, chat_id=chat_id, text=text, reply_markup=keyboard
            )
            if message:
                state.auto_hunt_stats["auto_chat_id"] = message.chat_id
                state.auto_hunt_stats["auto_message_id"] = message.message_id
        elif update.effective_message:
            message = await safe_reply(update, context, text=text, reply_markup=keyboard)
            if message:
                state.auto_hunt_stats["auto_chat_id"] = message.chat_id
                state.auto_hunt_stats["auto_message_id"] = message.message_id
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        logger.warning(
            "Gagal memperbarui panel auto hunting user %s: %s", state.user_id, exc
        )


async def stop_auto_hunt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    *,
    reason: str = "",
) -> None:
    lock = get_user_lock(state.user_id)
    async with lock:
        stats = state.auto_hunt_stats or {}
        if not stats or stats.get("summary_sent"):
            reset_auto_hunt_state(state)
            return
        stats["summary_sent"] = True
        data = {
            "session_area": stats.get("session_area")
            or state.flags.get("LAST_HUNT_AREA"),
            "gained_xp": dict(stats.get("gained_xp", {})),
            "gained_gold": stats.get("gained_gold", 0),
            "kills": stats.get("kills", 0),
            "items": dict(stats.get("items_gained", {})),
            "start_xp": dict(stats.get("start_xp", {})),
            "last_level_up_xp": dict(stats.get("last_level_up_xp", {})),
            "chat_id": stats.get("auto_chat_id")
            or (update.effective_chat.id if update.effective_chat else None),
            "message_id": stats.get("auto_message_id"),
        }
        stored_reason = stats.get("stop_reason") or ""
        if not reason:
            reason = stored_reason or "Auto hunting selesai."
        state.auto_hunt = False
        state.auto_hunt_area = None
        state.in_battle = False
        state.battle_enemies = []
        reset_auto_hunt_state(state)
    chat_id = data["chat_id"]
    if data["message_id"] and chat_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=data["message_id"], reply_markup=None
            )
        except BadRequest:
            pass
    area_name = HUNTING_AREAS.get(data["session_area"], {}).get(
        "name", data["session_area"] or "-"
    )
    lines = ["=== Ringkasan Auto Hunting ===", f"Area : {area_name}"]
    if reason:
        lines.append(f"Catatan: {reason}")
    lines.append(f"Total monster dikalahkan : {data['kills']}")
    lines.append(f"Total Gold diperoleh     : {data['gained_gold']}")
    lines.append("")
    lines.append("EXP per karakter:")
    for cid in state.party_order:
        member = state.party.get(cid)
        if not member:
            continue
        gained = data["gained_xp"].get(cid, 0)
        last_level_xp = data["last_level_up_xp"].get(cid, data["start_xp"].get(cid, 0))
        since_last = max(0, state.xp_pool.get(cid, 0) - last_level_xp)
        lines.append(f"- {member.name}: +{gained} EXP sejak awal auto hunting")
        lines.append(f"  EXP sejak level up terakhir: +{since_last}")
    lines.append("")
    lines.append("Item yang didapat:")
    if data["items"]:
        for item_id, qty in data["items"].items():
            item = ITEMS.get(item_id)
            name = item["name"] if item else item_id
            lines.append(f"- {name} x{qty}")
    else:
        lines.append("- Tidak ada item langka yang ditemukan kali ini.")
    summary_text = "\n".join(lines)
    if chat_id:
        await context.bot.send_message(chat_id=chat_id, text=summary_text)
    elif update.effective_message:
        await update.effective_message.reply_text(summary_text)
    target_area = data["session_area"]
    if target_area:
        await send_hunting_area_menu(
            update,
            context,
            state,
            target_area,
            force_new_message=True,
            chat_id=chat_id,
        )
    else:
        await send_hunting_menu(update, context, state)


async def run_auto_hunt_loop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
):
    lock = get_user_lock(state.user_id)
    session = state.auto_hunt_session
    log_lines: List[str] = []
    stop_reason = ""
    try:
        async with lock:
            stats = state.auto_hunt_stats
            if not stats:
                return
            stats["loop_active"] = True
            if session:
                session.active = True
        while True:
            async with lock:
                stats = state.auto_hunt_stats
                if not stats:
                    stop_reason = stop_reason or "Auto hunting dihentikan."
                    break
                if session and not session.active:
                    stop_reason = "Auto hunting dihentikan (session reset)."
                    break
                if not (state.auto_hunt and state.auto_hunt_area):
                    stop_reason = stats.get("stop_reason") or "Auto hunting dihentikan."
                    break
                if not living_party_members(state):
                    state.auto_hunt = False
                    stop_reason = "Seluruh party tidak mampu bertarung."
                    break
                area_id = state.auto_hunt_area
                area_info = HUNTING_AREAS.get(area_id)
                if not area_info:
                    state.auto_hunt = False
                    stop_reason = "Area auto hunting tidak valid."
                    break
                battle_area = area_info.get("area_key", area_id)
                enemy = pick_random_monster_for_area(
                    battle_area, average_party_level(state)
                )
                state.in_battle = True
                state.battle_enemies = [enemy]
                reset_battle_flags(state)
                state.flags["CURRENT_BATTLE_AREA"] = battle_area
                state.flags["LAST_BATTLE_SOURCE"] = {
                    "type": "AUTO_HUNT",
                    "area": area_id,
                }
                intro_lines = [
                    f"{enemy['name']} Lv {enemy.get('level', '?')} muncul di {area_info.get('name', 'area liar')}!"
                ]
                if enemy.get("rarity") == "RARE":
                    intro_lines.append("Aura kuat menyelimuti udara. Monster langka!")
                log_lines = intro_lines[-5:]
            await send_auto_hunt_state(update, context, state, log_lines)
            await asyncio.sleep(CONFIG.auto_hunt_delay)
            battle_over = False
            enemy_defeated = False
            while not battle_over:
                for cid in state.party_order:
                    async with lock:
                        stats = state.auto_hunt_stats
                        if not stats or not state.auto_hunt:
                            stop_reason = (
                                stats.get("stop_reason")
                                or stop_reason
                                or "Auto hunting dihentikan."
                            )
                            state.in_battle = False
                            battle_over = True
                            action_logs: List[str] = []
                            break
                        if session and not session.active:
                            stop_reason = "Auto hunting dihentikan (session reset)."
                            state.in_battle = False
                            battle_over = True
                            action_logs = []
                            break
                        if not state.battle_enemies:
                            action_logs = []
                            battle_over = True
                            break
                        enemy = state.battle_enemies[0]
                        character = state.party.get(cid)
                        if not character or character.hp <= 0:
                            action_logs = []
                        else:
                            action_logs, defeated = perform_auto_player_action(
                                state, character, enemy
                            )
                            if defeated:
                                enemy_defeated = True
                                battle_over = True
                        if not living_party_members(state):
                            state.auto_hunt = False
                            state.in_battle = False
                            stop_reason = "Seluruh party tumbang saat auto hunting."
                            state.flags["LAST_BATTLE_RESULT"] = "LOSE"
                            battle_over = True
                    if action_logs:
                        log_lines.extend(action_logs)
                        log_lines = log_lines[-5:]
                        await send_auto_hunt_state(update, context, state, log_lines)
                        await asyncio.sleep(CONFIG.auto_hunt_delay)
                    if battle_over:
                        break
                if battle_over:
                    break
                async with lock:
                    stats = state.auto_hunt_stats
                    if not stats or not state.auto_hunt:
                        stop_reason = (
                            stats.get("stop_reason")
                            or stop_reason
                            or "Auto hunting dihentikan."
                        )
                        state.in_battle = False
                        battle_over = True
                        enemy_logs: List[str] = []
                    else:
                        if session and not session.active:
                            stop_reason = "Auto hunting dihentikan (session reset)."
                            state.in_battle = False
                            battle_over = True
                            enemy_logs = []
                        else:
                            enemy = state.battle_enemies[0]
                            enemy_logs, party_defeated = perform_auto_enemy_attack(
                                state, enemy
                            )
                            if party_defeated:
                                state.auto_hunt = False
                                state.in_battle = False
                                stop_reason = "Seluruh party tumbang saat auto hunting."
                                state.flags["LAST_BATTLE_RESULT"] = "LOSE"
                                battle_over = True
                if enemy_logs:
                    log_lines.extend(enemy_logs)
                    log_lines = log_lines[-5:]
                    await send_auto_hunt_state(update, context, state, log_lines)
                    await asyncio.sleep(CONFIG.auto_hunt_delay)
                if battle_over:
                    break
                if enemy.get("hp", 0) <= 0:
                    enemy_defeated = True
                    battle_over = True
            if not state.auto_hunt:
                break
            if enemy_defeated:
                async with lock:
                    stats = state.auto_hunt_stats
                    if not stats:
                        break
                    enemy_data = (
                        state.battle_enemies[0] if state.battle_enemies else enemy
                    )
                    total_xp = enemy_data.get("xp", 0)
                    total_gold = enemy_data.get("gold", 0)
                    stats["kills"] = stats.get("kills", 0) + 1
                    stats["gained_gold"] = stats.get("gained_gold", 0) + total_gold
                    before_levels = {
                        cid: state.party[cid].level
                        for cid in state.party_order
                        if state.party.get(cid)
                    }
                    for cid in state.party_order:
                        stats["gained_xp"].setdefault(cid, 0)
                        stats["gained_xp"][cid] += total_xp
                        state.xp_pool[cid] = state.xp_pool.get(cid, 0) + total_xp
                    state.gold += total_gold
                    check_level_up(state)
                    leveled = []
                    for cid in state.party_order:
                        character = state.party.get(cid)
                        if not character:
                            continue
                        prev = before_levels.get(cid, character.level)
                        if character.level > prev:
                            stats["last_level_up_xp"][cid] = state.xp_pool.get(cid, 0)
                            leveled.append(
                                f"{character.name} naik ke Level {character.level}!"
                            )
                    drop_logs, drop_details = grant_battle_drops(state)
                    for item_id, qty in drop_details:
                        stats["items_gained"][item_id] = (
                            stats["items_gained"].get(item_id, 0) + qty
                        )
                    quest_logs = update_hunt_quest_progress(
                        state, [enemy_data.get("id")]
                    )
                    state.flags["LAST_BATTLE_RESULT"] = "WIN"
                    state.in_battle = False
                    state.battle_enemies = []
                summary_lines = [
                    f"{enemy_data['name']} dikalahkan!",
                    f"EXP +{total_xp} / Gold +{total_gold}",
                ]
                if drop_logs:
                    summary_lines.append("Drop: " + ", ".join(drop_logs))
                if quest_logs:
                    summary_lines.extend(quest_logs)
                summary_lines.extend(leveled)
                log_lines.extend(summary_lines)
                log_lines = log_lines[-5:]
                await send_auto_hunt_state(update, context, state, log_lines)
                await asyncio.sleep(CONFIG.auto_hunt_delay)
                continue
            break
    except Exception:
        logger.exception("Terjadi error di auto hunting user %s", state.user_id)
        stop_reason = stop_reason or "Auto hunting dihentikan karena terjadi kesalahan."
    finally:
        async with lock:
            stats = state.auto_hunt_stats
            if stats:
                stats["loop_active"] = False
                if not stop_reason:
                    stop_reason = stats.get("stop_reason") or "Auto hunting selesai."
            if session:
                session.active = False
    await stop_auto_hunt(
        update, context, state, reason=stop_reason or "Auto hunting selesai."
    )


async def send_shop_buy_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, category: str = "ALL"
):
    """
    Tampilkan menu beli dengan kategori
    category: "EQUIPMENT", "CONSUMABLE", atau "ALL"
    """
    query = update.callback_query
    features = CITY_FEATURES.get(state.location, {})
    shop_items = features.get("shop_items", [])
    
    # Filter berdasarkan kategori
    filtered_items = []
    for item_id in shop_items:
        item = ITEMS.get(item_id)
        if not item:
            continue
        item_type = item.get("type", "")
        if category == "EQUIPMENT" and item_type in ["weapon", "armor"]:
            filtered_items.append((item_id, item))
        elif category == "CONSUMABLE" and item_type == "consumable":
            filtered_items.append((item_id, item))
        elif category == "ALL":
            filtered_items.append((item_id, item))
    
    # Header berdasarkan kategori
    if category == "EQUIPMENT":
        header = "=== EQUIPMENT ==="
    elif category == "CONSUMABLE":
        header = "=== ITEM CONSUMABLE ==="
    else:
        header = "=== SEMUA BARANG ==="
    
    lines = [header, f"Gold: {state.gold}", ""]
    buttons: List[List[InlineKeyboardButton]] = []
    
    if not filtered_items:
        lines.append("Tidak ada barang di kategori ini.")
    else:
        for item_id, item in filtered_items:
            item_type_label = ""
            if item.get("type") == "weapon":
                item_type_label = " [Senjata]"
            elif item.get("type") == "armor":
                item_type_label = " [Armor]"
            
            lines.append(f"- {item['name']}{item_type_label} - {item['buy_price']} Gold")
            if item.get("description"):
                lines.append(f"  {item['description']}")
            
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Beli {item['name']}", callback_data=f"BUY_ITEM|{item_id}"
                    )
                ]
            )
    
    buttons.append([InlineKeyboardButton("Kembali", callback_data="MENU_SHOP")])
    markup = InlineKeyboardMarkup(buttons)
    if query:
        await safe_edit_text(query, "\n".join(lines), reply_markup=markup)
    else:
        await update.message.reply_text("\n".join(lines), reply_markup=markup)


async def send_shop_sell_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    """Tampilkan menu jual dengan kategori terpisah"""
    query = update.callback_query
    lines = ["=== JUAL BARANG ===", f"Gold: {state.gold}", ""]
    
    # Kelompokkan berdasarkan kategori
    equipment_items = []
    consumable_items = []
    other_items = []
    
    for item_id, qty in sorted(state.inventory.items()):
        if qty <= 0:
            continue
        item = ITEMS.get(item_id)
        if not item:
            continue
        sell_price = item.get("sell_price", 0)
        if sell_price <= 0:
            continue
        
        item_type = item.get("type", "")
        if item_type in ["weapon", "armor"]:
            equipment_items.append((item_id, item, qty, sell_price))
        elif item_type == "consumable":
            consumable_items.append((item_id, item, qty, sell_price))
        else:
            other_items.append((item_id, item, qty, sell_price))
    
    buttons: List[List[InlineKeyboardButton]] = []
    any_item = False
    
    # Tampilkan Equipment
    if equipment_items:
        any_item = True
        lines.append("EQUIPMENT:")
        for item_id, item, qty, sell_price in equipment_items:
            type_label = "Senjata" if item.get("type") == "weapon" else "Armor"
            lines.append(f"- {item['name']} [{type_label}] x{qty} - {sell_price} Gold")
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Jual {item['name']}", callback_data=f"SELL_ITEM|{item_id}"
                    )
                ]
            )
        lines.append("")
    
    # Tampilkan Consumables
    if consumable_items:
        any_item = True
        lines.append("CONSUMABLE:")
        for item_id, item, qty, sell_price in consumable_items:
            lines.append(f"- {item['name']} x{qty} - {sell_price} Gold")
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Jual {item['name']}", callback_data=f"SELL_ITEM|{item_id}"
                    )
                ]
            )
        lines.append("")
    
    # Tampilkan Other
    if other_items:
        any_item = True
        lines.append("LAINNYA:")
        for item_id, item, qty, sell_price in other_items:
            lines.append(f"- {item['name']} x{qty} - {sell_price} Gold")
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Jual {item['name']}", callback_data=f"SELL_ITEM|{item_id}"
                    )
                ]
            )
    
    if not any_item:
        lines.append("Tidak ada item yang bisa dijual.")
    
    buttons.append([InlineKeyboardButton("Kembali", callback_data="MENU_SHOP")])
    markup = InlineKeyboardMarkup(buttons)
    if query:
        await safe_edit_text(query, "\n".join(lines), reply_markup=markup)
    else:
        await update.message.reply_text("\n".join(lines), reply_markup=markup)


async def handle_buy_item(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, item_id: str
):
    item = ITEMS.get(item_id)
    if not item:
        await update.callback_query.answer("Item tidak dikenal.", show_alert=True)
        return
    price = item.get("buy_price", 0)
    if state.gold < price:
        await update.callback_query.answer("Gold-mu tidak cukup.", show_alert=True)
        return
    state.gold -= price
    adjust_inventory(state, item_id, 1)
    await update.callback_query.answer(
        f"Kamu membeli {item['name']}!", show_alert=False
    )
    await send_shop_buy_menu(update, context, state)


async def handle_sell_item(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, item_id: str
):
    item = ITEMS.get(item_id)
    if not item:
        await update.callback_query.answer("Item tidak dikenal.", show_alert=True)
        return
    sell_price = item.get("sell_price", 0)
    if sell_price <= 0:
        await update.callback_query.answer(
            "Item itu tidak bisa dijual.", show_alert=True
        )
        return
    qty = state.inventory.get(item_id, 0)
    if qty <= 0:
        await update.callback_query.answer(
            "Kamu tidak memiliki item tersebut.", show_alert=True
        )
        return
    adjust_inventory(state, item_id, -1)
    state.gold += sell_price
    await update.callback_query.answer(
        f"Kamu menjual {item['name']} seharga {sell_price} Gold.", show_alert=False
    )
    await send_shop_sell_menu(update, context, state)


# ==========================
# CRAFTING HANDLERS
# ==========================

async def send_crafting_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    """Tampilkan menu crafting dengan resep yang tersedia"""
    query = update.callback_query
    
    # Pastikan player di kota
    if state.location not in CRAFTING_SYSTEM.city_tiers:
        await safe_edit_text(query, "Crafting hanya tersedia di kota.")
        return
    
    # Update energy sistem
    if state.energy_system:
        state.energy_system.update_energy()
    
    # Dapatkan resep yang tersedia di kota ini
    available_recipes = CRAFTING_SYSTEM.get_recipes_for_city(state.location)
    
    if not available_recipes:
        text = "Tidak ada resep crafting yang tersedia di kota ini."
        keyboard = make_keyboard([("Kembali ke kota", "BACK_CITY_MENU")])
        await safe_edit_text(query, text=text, reply_markup=keyboard)
        return
    
    lines = ["=== BENGKEL CRAFTING ==="]
    lines.append(f"Lokasi: {LOCATIONS[state.location]['name']}")
    lines.append(f"Gold: {state.gold}")
    lines.append("")
    lines.append("Resep yang tersedia:")
    
    buttons = []
    
    for recipe in available_recipes:
        # Cek apakah bisa craft
        can_craft, error_msg = CRAFTING_SYSTEM.can_craft(
            recipe.id, state.materials, state.gold, state.location
        )
        
        # Format material requirements
        mat_info = []
        for mat_id, req_qty in recipe.materials.items():
            player_qty = state.materials.get(mat_id, 0)
            mat = CRAFTING_SYSTEM.materials.get(mat_id)
            mat_name = mat.name if mat else mat_id
            status = "OK" if player_qty >= req_qty else f"KURANG"
            mat_info.append(f"{mat_name}: {player_qty}/{req_qty} [{status}]")
        
        lines.append(f"\n{recipe.name} - {recipe.rarity.value}")
        lines.append(f"  {recipe.description}")
        lines.append(f"  Biaya: {recipe.gold_cost} gold")
        lines.append("  Material:")
        for info in mat_info:
            lines.append(f"    - {info}")
        
        # Button
        button_text = f"Craft {recipe.name}" if can_craft else f"[X] {recipe.name}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=f"CRAFT_ITEM|{recipe.id}")])
    
    buttons.append([InlineKeyboardButton("Kembali ke kota", callback_data="BACK_CITY_MENU")])
    
    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(buttons)
    await safe_edit_text(query, text=text, reply_markup=markup)


async def handle_craft_item(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, recipe_id: str
):
    """Handle crafting item"""
    query = update.callback_query
    
    # Cek apakah bisa craft
    can_craft, error_msg = CRAFTING_SYSTEM.can_craft(
        recipe_id, state.materials, state.gold, state.location
    )
    
    if not can_craft:
        await query.answer(error_msg, show_alert=True)
        return
    
    # Craft item
    success, updated_materials, updated_gold, message = CRAFTING_SYSTEM.craft_item(
        recipe_id, state.materials, state.gold
    )
    
    if not success:
        await query.answer(message, show_alert=True)
        return
    
    # Update state
    state.materials = updated_materials
    state.gold = updated_gold
    
    # Tambahkan item hasil craft ke inventory
    recipe = CRAFTING_SYSTEM.recipes.get(recipe_id)
    if recipe:
        adjust_inventory(state, recipe.result_item_id, recipe.result_quantity)
        item = ITEMS.get(recipe.result_item_id)
        item_name = item.get("name") if item else recipe.result_item_id
        await query.answer(f"{message} Mendapat {item_name} x{recipe.result_quantity}", show_alert=True)
    
    # Refresh menu
    await send_crafting_menu(update, context, state)


# ==========================
# JOBS HANDLERS
# ==========================

async def send_jobs_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    """Tampilkan menu pekerjaan"""
    query = update.callback_query
    
    # Update energy
    if state.energy_system:
        state.energy_system.update_energy()
    
    # Cek apakah sedang bekerja
    if state.active_work_session and not state.active_work_session.check_completion():
        remaining_seconds = state.active_work_session.get_remaining_seconds()
        job = JOB_SYSTEM.jobs.get(state.active_work_session.job_id)
        job_name = job.name if job else state.active_work_session.job_id
        
        text = format_work_progress(
            job_name,
            remaining_seconds,
            state.active_work_session.energy_spent
        )
        
        buttons = [
            [InlineKeyboardButton("Cek Progress", callback_data="JOB_CHECK_WORK")],
            [InlineKeyboardButton("Kembali ke kota", callback_data="BACK_CITY_MENU")]
        ]
        markup = InlineKeyboardMarkup(buttons)
        await safe_edit_text(query, text=text, reply_markup=markup)
        return
    
    # Jika work session selesai, proses reward
    if state.active_work_session and state.active_work_session.check_completion():
        job_progress = state.job_progress.get(state.active_work_session.job_id)
        
        # Convert dict to JobProgress if needed
        if job_progress and isinstance(job_progress, dict):
            job_progress = JobProgress.from_dict(job_progress)
        
        gold, exp, updated_progress, logs = JOB_SYSTEM.complete_work(
            state.active_work_session, job_progress
        )
        
        state.gold += gold
        
        # Update job progress
        if updated_progress:
            state.job_progress[state.active_work_session.job_id] = updated_progress.to_dict()
            
            # Apply stat bonuses
            bonuses = JOB_SYSTEM.get_total_stat_bonus(
                state.active_work_session.job_id,
                updated_progress.level
            )
            
            # Update stats for Aruna (main character)
            aruna = state.party.get("ARUNA")
            if aruna:
                for stat_name, bonus in bonuses.items():
                    if stat_name == "atk":
                        aruna.atk = CHAR_BASE["ARUNA"]["atk"] + bonus
                    elif stat_name == "defense":
                        aruna.defense = CHAR_BASE["ARUNA"]["defense"] + bonus
                    elif stat_name == "mag":
                        aruna.mag = CHAR_BASE["ARUNA"]["mag"] + bonus
                    elif stat_name == "spd":
                        aruna.spd = CHAR_BASE["ARUNA"]["spd"] + bonus
                    elif stat_name == "max_hp":
                        aruna.max_hp = CHAR_BASE["ARUNA"]["max_hp"] + int(bonus)
                        aruna.hp = min(aruna.hp, aruna.max_hp)
                    elif stat_name == "max_mp":
                        aruna.max_mp = CHAR_BASE["ARUNA"]["max_mp"] + int(bonus)
                        aruna.mp = min(aruna.mp, aruna.max_mp)
            
            # Jika mencapai level max, keluar otomatis
            if updated_progress.level >= JOB_SYSTEM.jobs[state.active_work_session.job_id].max_level:
                state.current_job_id = None
        
        # Clear work session
        state.active_work_session = None
        
        # Show results
        result_text = "\n".join(logs)
        await query.answer(result_text, show_alert=True)
    
    # Tampilkan menu jobs
    lines = ["=== GUILD PEKERJAAN ==="]
    energy_current = state.energy_system.current_energy if state.energy_system else 0
    energy_max = state.energy_system.max_energy if state.energy_system else 100
    lines.append(f"Energy: {energy_current}/{energy_max}")
    lines.append("")
    
    buttons = []
    
    if state.current_job_id:
        # Player sudah punya job
        job = JOB_SYSTEM.jobs.get(state.current_job_id)
        if job:
            job_prog = state.job_progress.get(state.current_job_id, {})
            if isinstance(job_prog, dict):
                job_level = job_prog.get("level", 1)
                job_exp = job_prog.get("exp", 0)
            else:
                job_level = job_prog.level
                job_exp = job_prog.exp
            
            next_level_exp = JOB_SYSTEM.exp_for_next_level(job_level)
            
            lines.append(f"Pekerjaan saat ini: {job.name}")
            lines.append(f"Level: {job_level}/{job.max_level}")
            lines.append(f"EXP: {job_exp}/{next_level_exp}")
            lines.append("")
            lines.append("Pertumbuhan stat:")
            for stat, growth in job.stat_growth.items():
                total_bonus = int(growth * job_level)
                lines.append(f"  +{total_bonus} {stat.upper()}")
            lines.append("")
            
            # Tombol untuk bekerja atau keluar
            if energy_current >= 1:
                buttons.append([InlineKeyboardButton("Bekerja (1 energy)", callback_data=f"JOB_WORK|1")])
                if energy_current >= 5:
                    buttons.append([InlineKeyboardButton("Bekerja (5 energy)", callback_data=f"JOB_WORK|5")])
                if energy_current >= 10:
                    buttons.append([InlineKeyboardButton("Bekerja (10 energy)", callback_data=f"JOB_WORK|10")])
            
            buttons.append([InlineKeyboardButton("Keluar dari Pekerjaan", callback_data="JOB_QUIT")])
    else:
        # Player belum punya job, tampilkan pilihan
        lines.append("Pilih pekerjaan yang ingin kamu ambil:")
        lines.append("")
        
        # Get Aruna stats for requirements check
        aruna = state.party.get("ARUNA")
        player_stats = {
            "level": aruna.level if aruna else 1,
            "atk": aruna.atk if aruna else 0,
            "defense": aruna.defense if aruna else 0,
            "mag": aruna.mag if aruna else 0,
        }
        
        for job_id, job in JOB_SYSTEM.jobs.items():
            can_take, error = JOB_SYSTEM.can_start_job(job_id, player_stats, state.current_job_id)
            
            lines.append(f"{job.name}")
            lines.append(f"  {job.description}")
            
            if job.requirements:
                req_str = ", ".join([f"{k}: {v}" for k, v in job.requirements.items()])
                lines.append(f"  Persyaratan: {req_str}")
            
            growth_str = ", ".join([f"+{v} {k}/level" for k, v in job.stat_growth.items()])
            lines.append(f"  Pertumbuhan: {growth_str}")
            lines.append(f"  Reward: {job.base_gold_per_minute} gold/menit")
            lines.append("")
            
            button_text = f"Ambil: {job.name}" if can_take else f"[X] {job.name}"
            buttons.append([InlineKeyboardButton(button_text, callback_data=f"JOB_TAKE|{job_id}")])
    
    buttons.append([InlineKeyboardButton("Kembali ke kota", callback_data="BACK_CITY_MENU")])
    
    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(buttons)
    await safe_edit_text(query, text=text, reply_markup=markup)


async def handle_take_job(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, job_id: str
):
    """Handle pengambilan pekerjaan baru"""
    query = update.callback_query
    
    # Get Aruna stats for requirements check
    aruna = state.party.get("ARUNA")
    player_stats = {
        "level": aruna.level if aruna else 1,
        "atk": aruna.atk if aruna else 0,
        "defense": aruna.defense if aruna else 0,
        "mag": aruna.mag if aruna else 0,
    }
    
    can_start, error_msg = JOB_SYSTEM.can_start_job(job_id, player_stats, state.current_job_id)
    
    if not can_start:
        await query.answer(error_msg, show_alert=True)
        return
    
    # Set job
    state.current_job_id = job_id
    
    # Initialize job progress if not exists
    if job_id not in state.job_progress:
        state.job_progress[job_id] = JobProgress(
            job_id=job_id,
            level=1,
            exp=0,
            total_time_worked=0
        ).to_dict()
    
    job = JOB_SYSTEM.jobs.get(job_id)
    job_name = job.name if job else job_id
    await query.answer(f"Kamu sekarang bekerja sebagai {job_name}!", show_alert=True)
    
    # Refresh menu
    await send_jobs_menu(update, context, state)


async def handle_quit_job(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    """Handle keluar dari pekerjaan"""
    query = update.callback_query
    
    if not state.current_job_id:
        await query.answer("Kamu tidak memiliki pekerjaan saat ini.", show_alert=True)
        return
    
    job = JOB_SYSTEM.jobs.get(state.current_job_id)
    job_name = job.name if job else state.current_job_id
    
    state.current_job_id = None
    
    await query.answer(f"Kamu telah keluar dari pekerjaan {job_name}.", show_alert=True)
    
    # Refresh menu
    await send_jobs_menu(update, context, state)


async def handle_start_work(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, energy_amount: int
):
    """Handle memulai work session"""
    query = update.callback_query
    
    if not state.current_job_id:
        await query.answer("Kamu harus memiliki pekerjaan terlebih dahulu.", show_alert=True)
        return
    
    # Update energy
    if state.energy_system:
        state.energy_system.update_energy()
    
    success, work_session, message = JOB_SYSTEM.start_work(
        state.current_job_id,
        energy_amount,
        state.energy_system
    )
    
    if not success:
        await query.answer(message, show_alert=True)
        return
    
    # Set work session
    state.active_work_session = work_session
    
    await query.answer(f"{message}. Kamu tidak bisa melakukan aktivitas lain.", show_alert=True)
    
    # Refresh menu untuk menampilkan progress
    await send_jobs_menu(update, context, state)


async def handle_check_work_progress(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    """Handle pengecekan progress pekerjaan"""
    query = update.callback_query
    
    if not state.active_work_session:
        await query.answer("Kamu tidak sedang bekerja.", show_alert=True)
        return
    
    # Cek apakah sudah selesai
    if state.active_work_session.check_completion():
        await query.answer("Pekerjaan selesai! Kembali ke menu untuk mengambil reward.", show_alert=True)
    else:
        remaining_seconds = state.active_work_session.get_remaining_seconds()
        time_str = format_time_remaining(remaining_seconds)
        await query.answer(f"Sisa waktu: {time_str}", show_alert=True)
    
    # Refresh menu
    await send_jobs_menu(update, context, state)


async def send_equipment_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState
):
    lines = ["Kelola equipment party:"]
    buttons = []
    for cid in state.party_order:
        c = state.party.get(cid)
        if not c:
            continue
        weapon = ITEMS.get(c.weapon_id, {}).get("name") if c.weapon_id else "(Kosong)"
        armor = ITEMS.get(c.armor_id, {}).get("name") if c.armor_id else "(Kosong)"
        lines.append(f"- {c.name}: Senjata {weapon} | Armor {armor}")
        lines.append(f"  {format_effective_stat_summary(c)}")
        buttons.append(
            [InlineKeyboardButton(c.name, callback_data=f"EQUIP_CHAR|{cid}")]
        )
    buttons.append([InlineKeyboardButton("⬅ Kembali", callback_data="BACK_CITY_MENU")])
    markup = InlineKeyboardMarkup(buttons)
    query = update.callback_query
    text = "\n".join(lines)
    if query:
        await safe_edit_text(query, text=text, reply_markup=markup)
    else:
        await update.message.reply_text(text=text, reply_markup=markup)


async def send_character_equipment_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    char_id: str,
    extra_text: str = "",
):
    character = state.party.get(char_id)
    if not character:
        await send_equipment_menu(update, context, state)
        return
    weapon = (
        ITEMS.get(character.weapon_id, {}).get("name")
        if character.weapon_id
        else "(Kosong)"
    )
    armor = (
        ITEMS.get(character.armor_id, {}).get("name")
        if character.armor_id
        else "(Kosong)"
    )
    lines = [
        f"Kelola gear untuk {character.name}:",
        f"Senjata saat ini: {weapon}",
        f"Armor saat ini: {armor}",
        f"Stat efektif: {format_effective_stat_summary(character)}",
    ]
    if extra_text:
        lines.append("")
        lines.append(extra_text)
    buttons: List[List[InlineKeyboardButton]] = []
    weapon_choices = list_equippable_items(state, char_id, "weapon")
    if weapon_choices:
        lines.append("\nSenjata di tas:")
        for item_id, item, qty in weapon_choices:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Pasang {item['name']} (x{qty})",
                        callback_data=f"EQUIP_WEAPON|{char_id}|{item_id}",
                    )
                ]
            )
    if character.weapon_id:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Lepas senjata", callback_data=f"UNEQUIP|{char_id}|weapon"
                )
            ]
        )
    armor_choices = list_equippable_items(state, char_id, "armor")
    if armor_choices:
        lines.append("\nArmor di tas:")
        for item_id, item, qty in armor_choices:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"Pasang {item['name']} (x{qty})",
                        callback_data=f"EQUIP_ARMOR|{char_id}|{item_id}",
                    )
                ]
            )
    if character.armor_id:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Lepas armor", callback_data=f"UNEQUIP|{char_id}|armor"
                )
            ]
        )
    buttons.append([InlineKeyboardButton("⬅ Kembali", callback_data="MENU_EQUIPMENT")])
    markup = InlineKeyboardMarkup(buttons)
    query = update.callback_query
    text = "\n".join(lines)
    if query:
        await safe_edit_text(query, text=text, reply_markup=markup)
    else:
        await update.message.reply_text(text=text, reply_markup=markup)


async def handle_equip_item_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    char_id: str,
    item_id: str,
    slot_type: str,
):
    success, message = equip_item(state, char_id, item_id, expected_type=slot_type)
    await update.callback_query.answer(message, show_alert=not success)
    extra = message
    character = state.party.get(char_id)
    if character:
        extra += "\n" + format_effective_stat_summary(character)
    await send_character_equipment_menu(
        update, context, state, char_id, extra_text=extra
    )


async def handle_unequip_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    char_id: str,
    slot: str,
):
    success, message = unequip_item(state, char_id, slot)
    await update.callback_query.answer(message, show_alert=not success)
    extra = message
    character = state.party.get(char_id)
    if character:
        extra += "\n" + format_effective_stat_summary(character)
    await send_character_equipment_menu(
        update, context, state, char_id, extra_text=extra
    )


def apply_consumable_outside_battle(
    state: GameState, item_id: str
) -> Tuple[bool, List[str]]:
    item = ITEMS.get(item_id)
    if not item or item.get("type") != "consumable":
        return False, ["Item itu tidak bisa dipakai di luar battle."]
    if state.inventory.get(item_id, 0) <= 0:
        return False, ["Persediaan item itu sudah habis."]
    effects = item.get("effects", {})
    hp_restore = effects.get("hp_restore", 0)
    mp_restore = effects.get("mp_restore", 0)
    if not hp_restore and not mp_restore:
        return False, ["Hanya item pemulih yang bisa dipakai di luar battle."]

    target_mode = effects.get("target", "single")
    targets: List[CharacterState] = []
    if target_mode == "party":
        for cid in state.party_order:
            member = state.party.get(cid)
            if member and member.hp > 0:
                targets.append(member)
    else:
        aruna = state.party.get("ARUNA")
        if aruna and aruna.hp > 0:
            targets = [aruna]
    if not targets:
        return False, ["Tidak ada target yang bisa menerima efek item."]

    logs: List[str] = [f"Kamu menggunakan {item['name']}."]
    effect_logs: List[str] = []
    hp_targets: List[str] = []
    mp_targets: List[str] = []

    for target in targets:
        if hp_restore:
            before_hp = target.hp
            target.hp = min(get_effective_max_hp(target), target.hp + hp_restore)
            restored = target.hp - before_hp
            if restored > 0:
                hp_targets.append(target.name)
                effect_logs.append(f"HP {target.name} pulih {restored}.")
        if mp_restore:
            before_mp = target.mp
            target.mp = min(get_effective_max_mp(target), target.mp + mp_restore)
            restored_mp = target.mp - before_mp
            if restored_mp > 0:
                mp_targets.append(target.name)
                effect_logs.append(f"MP {target.name} pulih {restored_mp}.")

    def _format_names(names: List[str]) -> str:
        if len(names) <= 1:
            return names[0] if names else ""
        if len(names) == 2:
            return f"{names[0]} dan {names[1]}"
        return ", ".join(names[:-1]) + f", dan {names[-1]}"

    if len(hp_targets) > 1:
        effect_logs.append(f"HP {_format_names(hp_targets)} pulih sebagian.")
    if len(mp_targets) > 1:
        effect_logs.append(f"MP {_format_names(mp_targets)} pulih sebagian.")

    if not effect_logs:
        effect_logs.append("Tidak ada efek berarti.")

    adjust_inventory(state, item_id, -1)
    logs.extend(effect_logs)
    return True, logs


async def send_inventory_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: GameState,
    extra_text: str = "",
):
    lines = ["=== INVENTORY ==="]
    if extra_text:
        lines.append(extra_text)
        lines.append("")
    if not state.inventory:
        lines.append("Tas kamu kosong.")
    else:
        for item_id, qty in sorted(state.inventory.items()):
            if qty <= 0:
                continue
            item = ITEMS.get(item_id)
            if not item:
                continue
            owners = get_equipped_owners(state, item_id)
            owner_text = f" | Dipakai: {', '.join(owners)}" if owners else ""
            lines.append(f"- {item['name']} x{qty}{owner_text}")
            lines.append(f"  {item['description']}")
    lines.append("\nPerlengkapan terpasang:")
    for cid in state.party_order:
        c = state.party.get(cid)
        if not c:
            continue
        weapon = ITEMS.get(c.weapon_id, {}).get("name") if c.weapon_id else "(Kosong)"
        armor = ITEMS.get(c.armor_id, {}).get("name") if c.armor_id else "(Kosong)"
        lines.append(f"- {c.name}: Senjata {weapon} | Armor {armor}")
    buttons: List[List[InlineKeyboardButton]] = []
    for item_id, qty in sorted(state.inventory.items()):
        if qty <= 0:
            continue
        item = ITEMS.get(item_id)
        if not item or item.get("type") != "consumable":
            continue
        effects = item.get("effects", {})
        if not effects.get("hp_restore") and not effects.get("mp_restore"):
            continue
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Gunakan {item['name']}",
                    callback_data=f"USE_ITEM_OUTSIDE|{item_id}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton("⬅ Kembali", callback_data="BACK_CITY_MENU")])
    markup = InlineKeyboardMarkup(buttons)
    query = update.callback_query
    text = "\n".join(lines)
    if query:
        await safe_edit_text(query, text=text, reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text=text, reply_markup=markup)


async def handle_use_item_outside(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: GameState, item_id: str
):
    success, logs = apply_consumable_outside_battle(state, item_id)
    if not success:
        await update.callback_query.answer(" ".join(logs), show_alert=True)
        await send_inventory_menu(update, context, state)
        return
    await update.callback_query.answer("Berhasil menggunakan item.", show_alert=False)
    await send_inventory_menu(update, context, state, extra_text="\n".join(logs))


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (
        not update.message
        or not update.message.text
        or update.message.text.startswith("/")
    ):
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            awaiting_name = state.flags.get("AWAITING_PLAYER_NAME")
            if not awaiting_name:
                return
            if len(text) < 3:
                await update.message.reply_text("Nama minimal 3 karakter.")
                return
            if len(text) > 18:
                text = text[:18]
            state.player_name = text
            state.reset_for_new_journey()
            state.flags.pop("AWAITING_PLAYER_NAME", None)
            greeting = (
                f"Namamu tercatat sebagai {text}.\n"
                "500 tahun berlalu sejak kekaisaran runtuh, namun sumpahmu belum padam."
            )
        await update.message.reply_text(greeting)
        await send_scene(update, context, state)
    except Exception:
        logger.exception("Gagal memproses input nama untuk user %s", user_id)
        await update.message.reply_text(
            "Terjadi kesalahan saat menyimpan namamu. Coba lagi dengan /start."
        )


# ==========================
# HANDLER KOMANDO
# ==========================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            needs_name = not state.player_name
            if needs_name:
                state.flags["AWAITING_PLAYER_NAME"] = True
            else:
                state.flags.pop("AWAITING_PLAYER_NAME", None)
                state.ensure_aruna()
        if needs_name:
            prompt = (
                "Masukkan nama karaktermu: \n"
                "Kau adalah ksatria abadi yang menyaksikan runtuhnya kekaisaran 500 tahun lalu."
            )
            if update.message:
                await update.message.reply_text(prompt)
            elif update.effective_chat:
                await update.effective_chat.send_message(prompt)
            return
        logger.info("User %s melanjutkan petualangan dengan /start", user_id)
        if state.scene_id in SCENES:
            await send_scene(update, context, state)
        else:
            await send_world_map(update, context, state)
    except Exception:
        logger.exception("Error di handler /start untuk user %s", user_id)
        if update.message:
            await update.message.reply_text(
                "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin."
            )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lines = [
            "Legends of Aruna: Journey to Kampar",
            "RPG teks taktis tentang ksatria abadi yang menebus kegagalannya melawan Zabx.",
            "",
            "==============================",
            "Perintah Utama:",
            "/start - Mulai petualangan atau lanjutkan dari progress terakhir",
            "/status - Lihat status party dan kondisi terkini",
            "/map - Buka peta dunia dan pilih kota atau hutan",
            "/inventory - Lihat dan gunakan item di luar battle",
            "/stop_hunt - Hentikan auto hunting yang sedang berjalan",
            "/save - Simpan progress secara manual",
            "/load - Muat progress dari file save",
            "/quests - Lihat quest guild dan progres cerita",
            "/help - Lihat bantuan ini",
            "",
            "==============================",
            "Tips Singkat:",
            "Gunakan menu Hunting untuk memilih area dan menyalakan auto hunting.",
            "Kunjungi Guild di tiap kota besar untuk kontrak berburu dan hadiah tambahan.",
            "Kampar dan Kastil Zabx sangat berbahaya, selesaikan quest karakter dan pedang warisan sebelum maju.",
        ]
        text = "\n".join(lines)
        if update.message:
            await update.message.reply_text(text)
        elif update.effective_chat:
            await update.effective_chat.send_message(text)
    except Exception:
        logger.exception(
            "Error di handler /help untuk user %s", update.effective_user.id
        )
        if update.message:
            await update.message.reply_text(
                "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin."
            )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            apply_synergy_bonuses(state)
        lines = ["=== STATUS PARTY ==="]
        for cid in state.party_order:
            c = state.party.get(cid)
            if not c:
                continue
            lines.append(format_effective_stat_summary(c))
        loc_info = LOCATIONS.get(state.location)
        loc_name = loc_info.get("name") if loc_info else state.location
        lines.append(f"\nGold: {state.gold}")
        lines.append(f"Lokasi: {loc_name}")
        lines.append(f"Main Quest: {state.main_progress}")
        await update.message.reply_text("\n".join(lines))
    except Exception:
        logger.exception("Error di handler /status untuk user %s", user_id)
        if update.message:
            await update.message.reply_text(
                "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin."
            )


@PERFORMANCE_MONITOR.monitored("forge")
async def forge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            apply_synergy_bonuses(state)
            args = context.args or []
            if not args:
                lines = ["=== Forge & Upgrade ==="]
                for item_id, qty in state.inventory.items():
                    if item_id not in WEAPON_UPGRADE_RULES:
                        continue
                    upgrade = state.equipment_upgrades.get(item_id, WeaponUpgrade())
                    lines.append(
                        f"{item_id} x{qty} | Lv {upgrade.level} | Rate {int(upgrade.success_rate*100)}%"
                    )
                if len(lines) == 1:
                    lines.append("Tidak ada senjata yang bisa di-upgrade.")
                else:
                    lines.append("Gunakan /forge <ITEM_ID> untuk mencoba upgrade.")
                await update.message.reply_text("\n".join(lines))
                return

            item_id = args[0].upper()
            success, message = perform_weapon_upgrade(state, item_id, state.inventory)
            EVENT_BUS.publish(GameEvent(EventType.ITEM_UPGRADED, {"user_id": user_id, "item": item_id}))
            await update.message.reply_text(message)
            state.sync_models_from_fields()
            save_game_state(user_id, state)
    except Exception:
        logger.exception("Error di /forge untuk user %s", user_id)
        if update.message:
            await update.message.reply_text("Terjadi kesalahan pada forge.")


@PERFORMANCE_MONITOR.monitored("craft")
async def craft_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            args = context.args or []
            if not args:
                lines = ["=== Crafting ==="]
                for rid, recipe in CRAFTING_RECIPES.items():
                    mats = ", ".join([f"{mid} x{qty}" for mid, qty in recipe["materials"].items()])
                    res = ", ".join([f"{mid} x{qty}" for mid, qty in recipe["result"].items()])
                    lines.append(f"{rid}: {mats} -> {res} (Lv {recipe['required_level']})")
                lines.append("Gunakan /craft <RESEP_ID> untuk meracik.")
                await update.message.reply_text("\n".join(lines))
                return
            recipe_id = args[0].upper()
            result_text = craft_item(state, recipe_id)
            if "Berhasil" in result_text:
                EVENT_BUS.publish(GameEvent(EventType.ITEM_CRAFTED, {"user_id": user_id, "recipe": recipe_id}))
                save_game_state(user_id, state)
            await update.message.reply_text(result_text)
    except Exception:
        logger.exception("Error di /craft untuk user %s", user_id)
        if update.message:
            await update.message.reply_text("Terjadi kesalahan saat crafting.")


@PERFORMANCE_MONITOR.monitored("daily")
async def daily_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        challenge = DAILY_CHALLENGE_MANAGER.ensure_today()
        args = context.args or []
        if args and args[0].isdigit():
            score = int(args[0])
            DAILY_CHALLENGE_MANAGER.record_score(user_id, score)
            EVENT_BUS.publish(
                GameEvent(EventType.CHALLENGE_COMPLETED, {"user_id": user_id, "score": score})
            )
        top_scores = LEADERBOARD_MANAGER.top(challenge.id)
        my_score = LEADERBOARD_MANAGER.get_score(user_id, challenge.id)
        lines = [
            "=== Daily Challenge ===",
            f"ID: {challenge.id}",
            f"Tipe: {challenge.type.name}",
            f"Target: {challenge.params.get('target')}",
            f"Skor kamu: {my_score}",
            "Top 5:",
        ]
        for uid, score in top_scores[:5]:
            lines.append(f"{uid}: {score}")
        lines.append("Kirim /daily <skor> untuk mengirim progres hari ini.")
        await update.message.reply_text("\n".join(lines))
    except Exception:
        logger.exception("Error di /daily untuk user %s", user_id)
        if update.message:
            await update.message.reply_text("Daily challenge tidak bisa dimuat.")


@PERFORMANCE_MONITOR.monitored("events")
async def events_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            logs = EVENT_MANAGER.tick(state)
            if context.args and context.args[0].lower() == "start":
                event = EVENT_MANAGER.activate_random_event()
                logs.append(f"Event {event.id} dimulai!")
            active = EVENT_MANAGER.get_active_modifiers()
        lines = ["=== World Events ==="]
        if logs:
            lines.extend(logs)
        if not active:
            lines.append("Tidak ada event aktif.")
        else:
            for key, value in active.items():
                lines.append(f"{key}: x{value}")
        await update.message.reply_text("\n".join(lines))
    except Exception:
        logger.exception("Error di /events untuk user %s", user_id)
        if update.message:
            await update.message.reply_text("Gagal memuat event dunia.")


async def metrics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("Perintah khusus admin.")
        return
    summary = PERFORMANCE_MONITOR.summary()
    lines = ["=== Metrics ===", f"Total: {summary.get('count', 0)}", f"Avg: {summary.get('avg_duration', 0)}"]
    endpoints = summary.get("endpoints", {})
    if endpoints:
        lines.append("Endpoint Terlambat:")
        for name, data in endpoints.items():
            lines.append(f"- {name}: {data.get('avg', 0)}s ({data.get('count', 0)}x)")
    await update.message.reply_text("\n".join(lines))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = STATISTICS_PLUGIN.stats.get(str(user_id), {})
    lines = ["=== Statistik ==="]
    if not stats:
        lines.append("Belum ada aktivitas tercatat.")
    else:
        for key, value in stats.items():
            lines.append(f"{key}: {value}")
    await update.message.reply_text("\n".join(lines))


async def stop_hunt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command untuk menghentikan auto hunting"""
    user_id = update.effective_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            
            # Cek apakah sedang auto hunting
            if not state.auto_hunt:
                if update.message:
                    await update.message.reply_text(
                        "Auto hunting tidak sedang berjalan."
                    )
                return
            
            # Hentikan auto hunting
            logger.info("User %s menghentikan auto hunting via command", user_id)
            state.auto_hunt = False
            
            if state.auto_hunt_session:
                await state.auto_hunt_session.stop("Dihentikan via command /stop_hunt")
            
            # Reset state
            reset_auto_hunt_state(state)
            
            if update.message:
                await update.message.reply_text(
                    "Auto hunting telah dihentikan. Gunakan /map untuk kembali ke peta atau /status untuk melihat kondisi party."
                )
    except Exception:
        logger.exception("Error di handler /stop_hunt untuk user %s", user_id)
        if update.message:
            await update.message.reply_text(
                "Terjadi kesalahan saat menghentikan auto hunting."
            )


async def map_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if not MAP_RATE_LIMITER.is_allowed(user_id):
            if update.message:
                await safe_reply(
                    update,
                    context,
                    "Permintaan /map terlalu sering. Coba lagi sebentar lagi.",
                )
            return
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            # Hentikan auto hunting jika aktif saat membuka map
            if state.auto_hunt:
                reset_auto_hunt_state(state)
        await send_world_map(update, context, state)
    except Exception:
        logger.exception("Error di handler /map untuk user %s", user_id)
        if update.message:
            await safe_reply(
                update,
                context,
                "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin."
            )


async def save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if not SAVE_RATE_LIMITER.is_allowed(user_id):
            if update.message:
                await safe_reply(
                    update,
                    context,
                    "Permintaan /save terlalu sering. Coba beberapa detik lagi.",
                )
            return
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            success = save_game_state(user_id, state)
        if update.message:
            if success:
                logger.info("User %s melakukan manual save (berhasil)", user_id)
                await safe_reply(update, context, "Progress permainanmu telah disimpan.")
            else:
                logger.warning("User %s gagal manual save", user_id)
                await safe_reply(
                    update,
                    context,
                    "Gagal menyimpan progress. Silakan coba lagi atau cek izin folder saves."
                )
    except Exception:
        logger.exception("Error di handler /save untuk user %s", user_id)
        if update.message:
            await safe_reply(
                update,
                context,
                "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin."
            )


async def load_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if not LOAD_RATE_LIMITER.is_allowed(user_id):
            if update.message:
                await safe_reply(
                    update,
                    context,
                    "Permintaan /load terlalu sering. Coba beberapa detik lagi.",
                )
            return
        async with get_user_lock(user_id):
            save_exists = os.path.exists(get_save_path(user_id))
            loaded = load_game_state(user_id)
            if not loaded:
                if update.message:
                    if save_exists:
                        await safe_reply(
                            "Gagal memuat save. Coba lagi nanti atau periksa file di folder saves."
                        )
                    else:
                        await safe_reply(
                            "Tidak ada data save yang ditemukan untuk akunmu."
                        )
                logger.warning(
                    "User %s gagal /load (file ada: %s)", user_id, save_exists
                )
                return
            loaded.ensure_aruna()
            SESSION_MANAGER.set_state(user_id, loaded)
        if update.message:
            loc_name = LOCATIONS.get(loaded.location, {}).get("name", loaded.location)
            aruna = loaded.party.get("ARUNA")
            hero_name = loaded.player_name or (aruna.name if aruna else "Ksatria")
            aruna_level = aruna.level if aruna else "-"
            await safe_reply(
                (
                    "Progress berhasil dimuat!\n"
                    f"Lokasi: {loc_name}\n"
                    f"Level {hero_name}: {aruna_level}\n"
                    "Gunakan /status untuk melihat detail party."
                )
            )
            logger.info("User %s memuat save dan kembali ke %s", user_id, loc_name)
    except Exception:
        logger.exception("Error di handler /load untuk user %s", user_id)
        if update.message:
            await safe_reply(
                "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin."
            )


async def force_save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        if update.message:
            await update.message.reply_text("Perintah ini khusus admin.")
        logger.warning("User %s mencoba /force_save tanpa izin", user_id)
        return
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            success = save_game_state(user_id, state)
        if update.message:
            if success:
                await update.message.reply_text("Save paksa berhasil.")
            else:
                await update.message.reply_text(
                    "Save paksa gagal. Periksa log server atau folder saves."
                )
    except Exception:
        logger.exception("Error di handler /force_save untuk user %s", user_id)
        if update.message:
            await update.message.reply_text(
                "Terjadi kesalahan saat menjalankan force save."
            )


async def show_state_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        if update.message:
            await update.message.reply_text("Perintah ini khusus admin.")
        logger.warning("User %s mencoba /show_state tanpa izin", user_id)
        return
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
        loc_name = LOCATIONS.get(state.location, {}).get("name", state.location)
        lines = [
            "=== DEBUG STATE ===",
            f"Scene: {state.scene_id}",
            f"Lokasi: {loc_name}",
            f"Main Quest: {state.main_progress}",
            f"Gold: {state.gold}",
        ]
        lines.append("Party:")
        for cid in state.party_order:
            member = state.party.get(cid)
            if not member:
                continue
            lines.append(
                f"- {member.name} Lv{member.level} ({member.hp}/{get_effective_max_hp(member)} HP)"
            )
        quest_flags = [
            ("Warisan Safiya", state.flags.get("UMAR_QUEST_DONE")),
            ("Suara dari Segel", state.flags.get("REZA_QUEST_DONE")),
            (
                "Pedang warisan kekaisaran",
                state.flags.get("WEAPON_QUEST_DONE")
                or state.flags.get("QUEST_WEAPON_DONE"),
            ),
            ("Gerbang Siak", state.flags.get("SIAK_GATE_EVENT_DONE")),
            ("Rumor Pekanbaru", state.flags.get("PEKANBARU_RUMOR_DONE")),
            ("Kampar", state.flags.get("VISITED_KAMPAR")),
        ]
        lines.append("Quest Flag:")
        for label, value in quest_flags:
            indicator = "✅" if value else "❌"
            lines.append(f"- {label}: {indicator}")
        if update.message:
            await update.message.reply_text("\n".join(lines))
    except Exception:
        logger.exception("Error di handler /show_state untuk user %s", user_id)
        if update.message:
            await update.message.reply_text(
                "Terjadi kesalahan saat mengambil state pemain."
            )


async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
        await send_inventory_menu(update, context, state)
    except Exception:
        logger.exception("Error di handler /inventory untuk user %s", user_id)
        if update.message:
            await update.message.reply_text(
                "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin."
            )


async def quests_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
        lines = ["=== QUEST TRACKER ==="]
        if state.quests_active:
            lines.append("\nQuest Aktif:")
            for quest in state.quests_active.values():
                lines.append(
                    f"- {quest.id}: {quest.description or quest.type} ({quest.progress}/{quest.required_amount})"
                )
        else:
            lines.append("\nQuest Aktif: (tidak ada)")
        if state.quests_completed:
            lines.append("\nQuest Selesai:")
            for quest in state.quests_completed[-5:]:
                status = "Hadiah diambil" if quest.reward_received else "Belum klaim"
                lines.append(f"- {quest.id} ({status})")
        else:
            lines.append("\nQuest Selesai: (belum ada)")
        lines.append("\nStatus Cerita:")
        lines.append(f"• Main Quest: {state.main_progress}")
        lines.append(
            "• Warisan Umar: "
            + ("Selesai" if state.flags.get("UMAR_QUEST_DONE") else "Belum tuntas")
        )
        lines.append(
            "• Suara Reza: "
            + ("Selesai" if state.flags.get("REZA_QUEST_DONE") else "Belum tuntas")
        )
        lines.append(
            "• Pedang Warisan: "
            + (
                "Bangkit"
                if state.flags.get("WEAPON_QUEST_DONE")
                or state.flags.get("QUEST_WEAPON_DONE")
                else "Dalam pencarian"
            )
        )
        text = "\n".join(lines)
        if update.message:
            await update.message.reply_text(text)
    except Exception:
        logger.exception("Error di handler /quests untuk user %s", user_id)
        if update.message:
            await update.message.reply_text(
                "Terjadi kesalahan saat membaca daftar quest. Coba lagi nanti."
            )


# ==========================
# CALLBACK QUERY HANDLER
# ==========================


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        async with get_user_lock(user_id):
            state = get_game_state(user_id)
            data = query.data
            handled = False

            try:
                # BATTLE-related
                battle_action_keys = {
                    "BATTLE_ATTACK",
                    "BATTLE_DEFEND",
                    "BATTLE_RUN",
                    "BATTLE_ITEM",
                    "BATTLE_SKILL_MENU",
                    "BATTLE_MENU",
                    "BATTLE_BACK",
                }
                action_key = data.split("|", 1)[0]
                if action_key in battle_action_keys:
                    handled = True
                    if not state.in_battle:
                        await safe_edit_text(query, "Kamu tidak sedang dalam battle.")
                        return
                    if state.auto_hunt:
                        await query.answer(
                            "Kamu sedang auto hunting. Tekan 'Hentikan Auto Hunting' untuk kembali ke mode manual atau gunakan /stop_hunt.",
                            show_alert=True,
                        )
                        return
                    await process_battle_action(update, context, state, data)
                    return

                if data.startswith("USE_SKILL|"):
                    handled = True
                    # format: USE_SKILL|CHAR_ID|SKILL_ID
                    parts = parse_callback_parts(data, 3)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    _, char_id, skill_id = parts[:3]
                    if not state.in_battle:
                        await safe_edit_text(query, "Kamu tidak sedang dalam battle.")
                        return
                    if state.auto_hunt:
                        await query.answer(
                            "Kamu sedang auto hunting. Tekan 'Hentikan Auto Hunting' untuk kembali ke mode manual atau gunakan /stop_hunt.",
                            show_alert=True,
                        )
                        return
                    await process_use_skill(update, context, state, char_id, skill_id)
                    return

                if data.startswith("USE_ITEM|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    item_id = parts[1]
                    if not state.in_battle:
                        await safe_edit_text(query, "Kamu tidak sedang dalam battle.")
                        return
                    if state.auto_hunt:
                        await query.answer(
                            "Kamu sedang auto hunting. Tekan 'Hentikan Auto Hunting' untuk kembali ke mode manual atau gunakan /stop_hunt.",
                            show_alert=True,
                        )
                        return
                    await process_use_item(update, context, state, item_id)
                    return

                if data.startswith("TARGET_ENEMY|") or data.startswith("TARGET_ALLY|"):
                    handled = True
                    if not state.in_battle:
                        await safe_edit_text(query, "Kamu tidak sedang dalam battle.")
                        return
                    if state.auto_hunt:
                        await query.answer(
                            "Kamu sedang auto hunting. Tekan 'Hentikan Auto Hunting' untuk kembali ke mode manual atau gunakan /stop_hunt.",
                            show_alert=True,
                        )
                        return
                    await process_target_selection(update, context, state, data)
                    return

                if data == "RETURN_TO_CITY":
                    handled = True
                    reset_auto_hunt_state(state)
                    await send_city_menu(update, context, state)
                    return

                current_scene = get_scene(state.scene_id)
                if current_scene and find_choice_by_callback(current_scene, data):
                    handled = True
                    await handle_scene_choice(update, context, state, data)
                    return

                # WORLD MAP / TRAVEL
                if data.startswith("GOTO_CITY|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    _, loc_id = parts[:2]
                    loc_info = LOCATIONS.get(loc_id)
                    if not loc_info:
                        logger.warning(
                            "Lokasi callback tidak dikenal dari user %s: %s",
                            user_id,
                            loc_id,
                        )
                        await notify_unknown_callback(
                            update,
                            "Lokasi ini tidak dikenal. Kamu akan dikembalikan ke peta dunia.",
                        )
                        await send_world_map(update, context, state)
                        return
                    aruna = state.party.get("ARUNA")
                    if not aruna:
                        state.ensure_aruna()
                        aruna = state.party.get("ARUNA")
                    if not aruna:
                        logger.error(
                            "State user %s tidak memiliki Aruna saat cek level", user_id
                        )
                        await notify_unknown_callback(update)
                        return
                    if aruna.level < loc_info["min_level"]:
                        text = (
                            f"Level kamu ({aruna.level}) belum cukup untuk masuk ke {loc_info['name']} "
                            f"(butuh Lv {loc_info['min_level']})."
                        )
                        keyboard = make_keyboard(
                            [("Kembali ke map", "GO_TO_WORLD_MAP")]
                        )
                        await safe_edit_text(query, text=text, reply_markup=keyboard)
                        return
                    previous_location = state.location
                    state.location = loc_id
                    logger.info(
                        "User %s berpindah kota dari %s ke %s",
                        user_id,
                        previous_location,
                        loc_id,
                    )
                    if loc_id == "SIAK" and not state.flags.get("VISITED_SIAK"):
                        state.flags["VISITED_SIAK"] = True
                        note = trigger_checkpoint_autosave(
                            state, "visit_siak", notify=True
                        )
                        await render_scene(
                            update,
                            context,
                            state,
                            "CH1_SIAK_ENTRY",
                            extra_text=note or "",
                        )
                    elif loc_id == "RENGAT" and not state.flags.get("VISITED_RENGAT"):
                        state.flags["VISITED_RENGAT"] = True
                        note = trigger_checkpoint_autosave(
                            state, "visit_rengat", notify=True
                        )
                        await render_scene(
                            update,
                            context,
                            state,
                            "CH2_RENGAT_GATE",
                            extra_text=note or "",
                        )
                    elif loc_id == "PEKANBARU" and not state.flags.get(
                        "VISITED_PEKANBARU"
                    ):
                        state.flags["VISITED_PEKANBARU"] = True
                        note = trigger_checkpoint_autosave(
                            state, "visit_pekanbaru", notify=True
                        )
                        await render_scene(
                            update,
                            context,
                            state,
                            "CH3_PEKANBARU_ENTRY",
                            extra_text=note or "",
                        )
                    elif loc_id == "KAMPAR" and not state.flags.get("VISITED_KAMPAR"):
                        state.flags["VISITED_KAMPAR"] = True
                        note = trigger_checkpoint_autosave(
                            state, "visit_kampar", notify=True
                        )
                        await render_scene(
                            update,
                            context,
                            state,
                            "CH4_KAMPAR_ENTRY",
                            extra_text=note or "",
                        )
                    else:
                        await send_city_menu(update, context, state)
                    return

                if data == "MENU_GUILD":
                    handled = True
                    await send_guild_menu(update, context, state)
                    return
                if data.startswith("GUILD_ACCEPT|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    await handle_guild_accept(update, context, state, parts[1])
                    return
                if data.startswith("GUILD_CLAIM|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    await handle_guild_claim(update, context, state, parts[1])
                    return
                if data == "MENU_HUNTING":
                    handled = True
                    await send_hunting_menu(update, context, state)
                    return
                if data.startswith("HUNT_AREA|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    await send_hunting_area_menu(update, context, state, parts[1])
                    return
                if data.startswith("HUNT_BATTLE|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    await start_random_battle_in_area(update, context, state, parts[1])
                    return
                if data.startswith("AUTO_HUNT_ON|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    await handle_auto_hunt_toggle(
                        update, context, state, parts[1], True
                    )
                    return
                if data == "AUTO_HUNT_OFF":
                    handled = True
                    await handle_auto_hunt_toggle(
                        update, context, state, state.auto_hunt_area, False
                    )
                    return

                # MENU KOTA
                if data == "MENU_STATUS":
                    handled = True
                    lines = ["=== STATUS PARTY ==="]
                    for cid in state.party_order:
                        c = state.party.get(cid)
                        if not c:
                            continue
                        lines.append(format_effective_stat_summary(c))
                    loc_info = LOCATIONS.get(state.location)
                    loc_name = loc_info.get("name") if loc_info else state.location
                    lines.append(f"\nGold: {state.gold}")
                    lines.append(f"Lokasi: {loc_name}")
                    lines.append(f"Main Quest: {state.main_progress}")
                    text = "\n".join(lines)
                    keyboard = make_keyboard([("Kembali ke kota", "BACK_CITY_MENU")])
                    await safe_edit_text(query, text=text, reply_markup=keyboard)
                    return

                if data == "BACK_CITY_MENU":
                    handled = True
                    await send_city_menu(update, context, state)
                    return

                if data == "MENU_SHOP":
                    handled = True
                    await send_shop_menu(update, context, state)
                    return
                if data == "SHOP_BUY":
                    handled = True
                    await send_shop_buy_menu(update, context, state, "ALL")
                    return
                if data == "SHOP_BUY_EQUIPMENT":
                    handled = True
                    await send_shop_buy_menu(update, context, state, "EQUIPMENT")
                    return
                if data == "SHOP_BUY_CONSUMABLE":
                    handled = True
                    await send_shop_buy_menu(update, context, state, "CONSUMABLE")
                    return
                if data == "SHOP_SELL":
                    handled = True
                    await send_shop_sell_menu(update, context, state)
                    return
                if data.startswith("BUY_ITEM|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    item_id = parts[1]
                    await handle_buy_item(update, context, state, item_id)
                    return
                if data.startswith("SELL_ITEM|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    item_id = parts[1]
                    await handle_sell_item(update, context, state, item_id)
                    return
                
                # Crafting handlers
                if data == "MENU_CRAFTING":
                    handled = True
                    await send_crafting_menu(update, context, state)
                    return
                if data.startswith("CRAFT_ITEM|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    recipe_id = parts[1]
                    await handle_craft_item(update, context, state, recipe_id)
                    return
                
                # Jobs handlers
                if data == "MENU_JOBS":
                    handled = True
                    await send_jobs_menu(update, context, state)
                    return
                if data.startswith("JOB_TAKE|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    job_id = parts[1]
                    await handle_take_job(update, context, state, job_id)
                    return
                if data == "JOB_QUIT":
                    handled = True
                    await handle_quit_job(update, context, state)
                    return
                if data.startswith("JOB_WORK|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    energy_str = parts[1]
                    try:
                        energy_amount = int(energy_str)
                        await handle_start_work(update, context, state, energy_amount)
                    except ValueError:
                        await safe_edit_text(query, "Energy harus berupa angka.")
                    return
                if data == "JOB_CHECK_WORK":
                    handled = True
                    await handle_check_work_progress(update, context, state)
                    return

                if data == "MENU_INN":
                    handled = True
                    cost = CITY_FEATURES.get(state.location, {}).get("inn_cost", 0)
                    if cost > state.gold:
                        text = (
                            f"Biaya penginapan {cost} Gold, tapi Gold-mu tidak cukup."
                        )
                    else:
                        state.gold -= cost
                        for cid in state.party_order:
                            c = state.party.get(cid)
                            if not c:
                                continue
                            c.hp = get_effective_max_hp(c)
                            c.mp = get_effective_max_mp(c)
                        if cost == 0:
                            text = (
                                "Kamu beristirahat gratis. HP & MP seluruh party pulih."
                            )
                        else:
                            text = (
                                f"Kamu membayar {cost} Gold dan beristirahat di penginapan. "
                                "HP & MP seluruh party pulih."
                            )
                    keyboard = make_keyboard([("Kembali ke kota", "BACK_CITY_MENU")])
                    await safe_edit_text(query, text=text, reply_markup=keyboard)
                    return

                if data == "MENU_CLINIC":
                    handled = True
                    if state.location != "SIAK":
                        await safe_edit_text(
                            query,
                            "Klinik hanya ada di Siak.",
                            reply_markup=make_keyboard([("Kembali", "BACK_CITY_MENU")]),
                        )
                        return
                    if not state.flags.get("HAS_UMAR"):
                        await render_scene(update, context, state, "CH1_UMAR_CLINIC")
                    else:
                        hero_name = state.player_name or (
                            state.party.get("ARUNA").name
                            if state.party.get("ARUNA")
                            else "Ksatria"
                        )
                        text = f'Umar: "Jaga dirimu baik-baik, {hero_name}. Aku di sini kalau kau butuh bantuan."\n'
                        keyboard = make_keyboard(
                            [("Kembali ke kota", "BACK_CITY_MENU")]
                        )
                        await safe_edit_text(query, text=text, reply_markup=keyboard)
                    return

                if data == "MENU_EQUIPMENT":
                    handled = True
                    await send_equipment_menu(update, context, state)
                    return
                if data.startswith("EQUIP_CHAR|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    _, char_id = parts[:2]
                    await send_character_equipment_menu(update, context, state, char_id)
                    return
                if data.startswith("EQUIP_WEAPON|"):
                    handled = True
                    parts = parse_callback_parts(data, 3)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    _, char_id, item_id = parts[:3]
                    await handle_equip_item_selection(
                        update, context, state, char_id, item_id, slot_type="weapon"
                    )
                    return
                if data.startswith("EQUIP_ARMOR|"):
                    handled = True
                    parts = parse_callback_parts(data, 3)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    _, char_id, item_id = parts[:3]
                    await handle_equip_item_selection(
                        update, context, state, char_id, item_id, slot_type="armor"
                    )
                    return
                if data.startswith("EQUIP_ITEM|"):
                    handled = True
                    parts = parse_callback_parts(data, 3)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    _, char_id, item_id = parts[:3]
                    item = ITEMS.get(item_id)
                    slot_type = item.get("type") if item else "weapon"
                    await handle_equip_item_selection(
                        update, context, state, char_id, item_id, slot_type=slot_type
                    )
                    return
                if data.startswith("UNEQUIP|"):
                    handled = True
                    parts = parse_callback_parts(data, 3)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    _, char_id, slot = parts[:3]
                    await handle_unequip_selection(
                        update, context, state, char_id, slot
                    )
                    return

                if data == "MENU_INVENTORY":
                    handled = True
                    await send_inventory_menu(update, context, state)
                    return
                if data.startswith("USE_ITEM_OUTSIDE|"):
                    handled = True
                    parts = parse_callback_parts(data, 2)
                    if not parts:
                        await notify_unknown_callback(update)
                        return
                    item_id = parts[1]
                    await handle_use_item_outside(update, context, state, item_id)
                    return

                if data == "EVENT_SIAK_GATE":
                    handled = True
                    await render_scene(update, context, state, "CH1_GATE_ALERT")
                    return

                if data == "EVENT_PEKANBARU_CAFE":
                    handled = True
                    state.flags["PEKANBARU_RUMOR_DONE"] = True
                    await render_scene(update, context, state, "CH3_PEKANBARU_ENTRY")
                    return

                if data == "EVENT_KASTIL_ENTRY":
                    handled = True
                    await render_scene(update, context, state, "CH4_CASTLE_APPROACH")
                    return

                if data == "QUEST_UMAR":
                    handled = True
                    await render_scene(update, context, state, "SQ_UMAR_INTRO")
                    return

                if data == "QUEST_REZA":
                    handled = True
                    await render_scene(update, context, state, "SQ_REZA_INTRO")
                    return
                if data == "QUEST_HARSAN_BLADE":
                    handled = True
                    state.flags["QUEST_WEAPON_STARTED"] = True
                    state.flags["WEAPON_QUEST_STARTED"] = True
                    await render_scene(update, context, state, "SQ_HARSAN_BLADE_INTRO")
                    return

                if data == "GO_TO_WORLD_MAP":
                    handled = True
                    await send_world_map(update, context, state)
                    return

                if data in SCENES:
                    handled = True
                    await render_scene(update, context, state, data)
                    return

                # SCENE / STORY CHOICE
                handled = True
                await handle_scene_choice(update, context, state, data)
            except Exception:
                logger.exception(
                    "Error di callback handler untuk user %s dengan data %s",
                    user_id,
                    data,
                )
                await safe_edit_text(
                    query,
                    "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin.",
                )
                return

            if not handled:
                logger.warning("Callback tak dikenal dari user %s: %s", user_id, data)
                await safe_edit_text(
                    query,
                    "Maaf, terjadi kesalahan saat memproses pilihanmu. Kamu akan dikembalikan ke peta dunia.",
                )
                await send_world_map(update, context, state)
    except Exception:
        logger.exception("Error umum di callback handler untuk user %s", user_id)
        await safe_edit_text(
            query,
            "Terjadi kesalahan tak terduga. Silakan coba lagi. Jika masalah berlanjut, hubungi admin.",
        )


# ==========================
# MAIN
# ==========================


def main():
    try:
        # Penjagaan awal agar token tidak kosong atau konfigurasi Application gagal
        application = ApplicationBuilder().token(TOKEN_BOT).build()
    except Exception:
        logger.exception(
            "Gagal memulai Application Telegram, periksa TOKEN_BOT atau jaringan."
        )
        return

    loop = asyncio.get_event_loop()
    loop.create_task(TASK_QUEUE.start())
    loop.create_task(SCHEDULER.run_forever())

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("map", map_cmd))
    application.add_handler(CommandHandler("stop_hunt", stop_hunt_cmd))
    application.add_handler(CommandHandler("save", save_cmd))
    application.add_handler(CommandHandler("load", load_cmd))
    application.add_handler(CommandHandler("inventory", inventory_cmd))
    application.add_handler(CommandHandler("quests", quests_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("force_save", force_save_cmd))
    application.add_handler(CommandHandler("show_state", show_state_cmd))
    application.add_handler(CommandHandler("forge", forge_cmd))
    application.add_handler(CommandHandler("craft", craft_cmd))
    application.add_handler(CommandHandler("daily", daily_cmd))
    application.add_handler(CommandHandler("events", events_cmd))
    application.add_handler(CommandHandler("metrics", metrics_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))

    text_filter = filters.TEXT & (~filters.COMMAND)
    application.add_handler(MessageHandler(text_filter, handle_text_message))
    application.add_handler(CallbackQueryHandler(button))

    logger.info("Bot Legends of Aruna berjalan...")
    try:
        application.run_polling()
    except Exception:
        logger.exception("Aplikasi berhenti karena error tak terduga saat polling.")


# Ringkasan pembaruan Immortal Knight:
# - Story & Prologue: Seluruh scene utama kini mengikuti kisah ksatria abadi melawan Zabx, lengkap
#   dengan pengenalan kota modern, Umar, Reza, dan pedang warisan kekaisaran.
# - Guild quests: Job kota digantikan dengan sistem guild berburu yang melacak quest aktif dan
#   hadiah lewat QuestState baru.
# - Hunting maps & auto-hunting: Menu berburu area khusus memungkinkan battle manual maupun
#   auto-hunt berantai tanpa melanggar model async.
# - Scaling & monster labels: MONSTERS memakai rank serta helper power check supaya label WEAK/
#   MEDIUM/STRONG/BOSS tampil di UI dan skala statistik terasa konsisten antar area.
# - Quest tracking (/quests): Struktur GameState menyimpan quest aktif/selesai untuk cerita utama,
#   side quest Umar & Reza, pedang warisan, dan misi guild.
# - Elemental damage: Senjata, skill, dan monster memakai elemen (Api, Air, Netral, dll) dengan
#   matriks multiplier agar strategi unsur terasa penting.
# - Map display: /map menampilkan daftar kota & area berburu alih-alih ASCII lama sehingga mudah
#   diperluas dengan data hunting map baru.

if __name__ == "__main__":
    main()
