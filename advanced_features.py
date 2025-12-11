from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

from safe_storage import safe_load_json, safe_save_json


# =====================
# Status Effects
# =====================


class StatusType(Enum):
    STUN = "STUN"
    FREEZE = "FREEZE"
    POISON = "POISON"
    BURN = "BURN"
    REGEN = "REGEN"
    SHIELD = "SHIELD"


@dataclass
class StatusEffect:
    type: StatusType
    duration: int
    potency: float
    source: str = ""

    def on_apply(self, target: Any) -> List[str]:
        logs: List[str] = []
        if self.type in {StatusType.SHIELD, StatusType.REGEN}:
            target.temporary_bonuses["max_hp"] = target.temporary_bonuses.get("max_hp", 0) + int(
                self.potency
            )
            logs.append(f"{target.name} mendapatkan efek {self.type.value}!")
        elif self.type in {StatusType.POISON, StatusType.BURN}:
            logs.append(f"{target.name} terkena {self.type.value.lower()}!")
        elif self.blocks_action:
            logs.append(f"{target.name} tidak bisa bergerak!")
        return logs

    def tick(self, target: Any) -> List[str]:
        logs: List[str] = []
        if self.type == StatusType.POISON:
            dmg = max(1, int(target.max_hp * self.potency))
            target.hp = max(0, target.hp - dmg)
            logs.append(f"{target.name} menerima {dmg} damage racun.")
        elif self.type == StatusType.BURN:
            dmg = max(1, int(target.max_hp * self.potency))
            target.hp = max(0, target.hp - dmg)
            logs.append(f"{target.name} terbakar {dmg} damage.")
        elif self.type == StatusType.REGEN:
            heal = max(1, int(target.max_hp * self.potency))
            target.hp = min(target.max_hp, target.hp + heal)
            logs.append(f"{target.name} memulihkan {heal} HP.")
        if self.duration > 0:
            self.duration -= 1
        return logs

    def on_remove(self, target: Any) -> List[str]:
        logs: List[str] = []
        if self.type in {StatusType.SHIELD, StatusType.REGEN}:
            target.temporary_bonuses["max_hp"] = max(
                0, target.temporary_bonuses.get("max_hp", 0) - int(self.potency)
            )
            logs.append(f"Efek {self.type.value} pada {target.name} berakhir.")
        return logs

    @property
    def blocks_action(self) -> bool:
        return self.type in {StatusType.STUN, StatusType.FREEZE}


class StatusEffectManager:
    def __init__(self):
        self.active_effects: Dict[str, List[StatusEffect]] = {}

    def add_effect(self, target_key: str, effect: StatusEffect, target: Any) -> List[str]:
        bucket = self.active_effects.setdefault(target_key, [])
        existing = next((e for e in bucket if e.type == effect.type), None)
        logs: List[str] = []
        if existing and effect.potency >= existing.potency:
            existing.duration = max(existing.duration, effect.duration)
            existing.potency = max(existing.potency, effect.potency)
            logs.append(f"Status {effect.type.value} diperpanjang pada {getattr(target, 'name', target_key)}!")
        elif not existing:
            bucket.append(effect)
            logs.extend(effect.on_apply(target))
        return logs

    def tick_effects(self, targets: Dict[str, Any]) -> List[str]:
        logs: List[str] = []
        for target_key, effects in list(self.active_effects.items()):
            target = targets.get(target_key)
            if not target:
                continue
            remaining: List[StatusEffect] = []
            for effect in effects:
                if effect.blocks_action:
                    if effect.duration > 0:
                        logs.append(f"{target.name} tidak bisa bertindak!")
                        effect.duration -= 1
                        remaining.append(effect)
                    else:
                        logs.extend(effect.on_remove(target))
                else:
                    logs.extend(effect.tick(target))
                    if effect.duration > 0:
                        remaining.append(effect)
                    else:
                        logs.extend(effect.on_remove(target))
            self.active_effects[target_key] = remaining
        return logs


# =====================
# Weapon Affinity & Upgrade
# =====================


@dataclass
class WeaponAffinity:
    kills_with_weapon: int = 0
    mastery_level: int = 0

    def gain_exp(self, kills: int) -> List[str]:
        self.kills_with_weapon += kills
        logs: List[str] = []
        if self.kills_with_weapon >= (self.mastery_level + 1) * 5:
            self.mastery_level += 1
            logs.append(f"Affinity senjata naik ke level {self.mastery_level}!")
        return logs

    @property
    def atk_bonus(self) -> int:
        return self.mastery_level


@dataclass
class WeaponUpgrade:
    level: int = 0
    bonus_stats: Dict[str, int] = field(default_factory=dict)
    base_success_rate: float = 0.8

    @property
    def success_rate(self) -> float:
        return max(0.2, self.base_success_rate - (self.level * 0.1))

    def apply_bonus(self) -> Dict[str, int]:
        bonus: Dict[str, int] = {}
        for key, value in self.bonus_stats.items():
            bonus[key] = value * max(1, self.level)
        return bonus


WEAPON_UPGRADE_RULES: Dict[str, Dict[str, Any]] = {
    "WOODEN_SWORD": {"bonus_stats": {"atk_bonus": 1}},
    "IRON_SWORD": {"bonus_stats": {"atk_bonus": 2, "crit": 1}},
    "HARSAN_BLADE": {"bonus_stats": {"atk_bonus": 3, "mag_bonus": 1}},
}


def perform_weapon_upgrade(
    state: Any, item_id: str, inventory: Dict[str, int], cost_material: str = "IRON_ORE"
) -> Tuple[bool, str]:
    upgrade_data: WeaponUpgrade = state.equipment_upgrades.setdefault(item_id, WeaponUpgrade())
    rule = WEAPON_UPGRADE_RULES.get(item_id)
    if not rule:
        return False, "Senjata ini tidak bisa di-upgrade."
    required = upgrade_data.level + 1
    if inventory.get(cost_material, 0) < required:
        return False, f"Butuh {required} {cost_material} untuk upgrade."
    inventory[cost_material] -= required
    if inventory.get(cost_material, 0) <= 0:
        inventory.pop(cost_material, None)
    roll = random.random()
    if roll <= upgrade_data.success_rate:
        upgrade_data.level += 1
        upgrade_data.bonus_stats = rule.get("bonus_stats", {})
        return True, f"Upgrade berhasil! {item_id} kini level {upgrade_data.level}."
    return False, "Upgrade gagal, bahan habis."


# =====================
# Crafting
# =====================


CRAFTING_RECIPES: Dict[str, Dict[str, Any]] = {
    "HEALING_POTION": {
        "materials": {"HERB": 3},
        "result": {"SMALL_POTION": 1},
        "required_level": 1,
    },
    "MANA_POTION": {
        "materials": {"BLUE_HERB": 2, "CRYSTAL_SHARD": 1},
        "result": {"SMALL_ETHER": 1},
        "required_level": 2,
    },
}


def can_craft(state: Any, recipe_id: str) -> Tuple[bool, str]:
    recipe = CRAFTING_RECIPES.get(recipe_id)
    if not recipe:
        return False, "Resep tidak ditemukan."
    highest_level = max([ch.level for ch in state.party.values()]) if state.party else 1
    if highest_level < recipe.get("required_level", 1):
        return False, "Level party belum cukup."
    for item_id, qty in recipe.get("materials", {}).items():
        if state.inventory.get(item_id, 0) < qty:
            return False, "Bahan tidak cukup."
    return True, "OK"


def craft_item(state: Any, recipe_id: str) -> str:
    allowed, msg = can_craft(state, recipe_id)
    if not allowed:
        return msg
    recipe = CRAFTING_RECIPES[recipe_id]
    for item_id, qty in recipe["materials"].items():
        state.inventory[item_id] = state.inventory.get(item_id, 0) - qty
        if state.inventory[item_id] <= 0:
            state.inventory.pop(item_id, None)
    for item_id, qty in recipe["result"].items():
        state.inventory[item_id] = state.inventory.get(item_id, 0) + qty
    return "Berhasil meracik item!"


# =====================
# World Events & Synergy
# =====================


@dataclass
class WorldEvent:
    id: str
    trigger: str
    duration: int
    effects: Dict[str, float]
    is_active: bool = False
    started_at: Optional[float] = None

    def activate(self):
        self.is_active = True
        self.started_at = time.time()

    def expired(self) -> bool:
        if not self.is_active:
            return False
        if self.started_at is None:
            return True
        return (time.time() - self.started_at) >= self.duration


class EventManager:
    def __init__(self):
        self.events: List[WorldEvent] = []

    def tick(self, state: Any) -> List[str]:
        logs: List[str] = []
        for event in list(self.events):
            if event.expired():
                event.is_active = False
                logs.append(f"Event {event.id} berakhir.")
        state.active_world_events = [e for e in self.events if e.is_active]
        return logs

    def activate_random_event(self) -> WorldEvent:
        pool = [
            WorldEvent(
                id="FESTIVAL_GOLD",
                trigger="CITY",
                duration=3600,
                effects={"drop_rate_multiplier": 1.2},
            ),
            WorldEvent(
                id="BLOOD_MOON",
                trigger="DUNGEON",
                duration=1800,
                effects={"enemy_atk_multiplier": 1.1},
            ),
        ]
        event = random.choice(pool)
        event.activate()
        self.events.append(event)
        return event

    def get_active_modifiers(self) -> Dict[str, float]:
        modifiers: Dict[str, float] = {}
        for event in self.events:
            if event.is_active:
                for key, value in event.effects.items():
                    modifiers[key] = modifiers.get(key, 1.0) * value
        return modifiers


PARTY_SYNERGIES: Dict[Tuple[str, ...], Dict[str, int]] = {
    ("ARUNA", "UMAR"): {"atk": 1, "defense": 1},
    ("ARUNA", "REZA"): {"mag": 2},
    ("UMAR", "REZA"): {"max_hp": 5},
}


def calculate_party_synergies(party_order: List[str]) -> Dict[str, Dict[str, int]]:
    bonuses: Dict[str, Dict[str, int]] = {}
    for combo, bonus in PARTY_SYNERGIES.items():
        if all(member in party_order for member in combo):
            for member in combo:
                char_bonus = bonuses.setdefault(member, {})
                for attr, value in bonus.items():
                    char_bonus[attr] = char_bonus.get(attr, 0) + value
    return bonuses


# =====================
# Daily Challenge & Leaderboard
# =====================


class ChallengeType(Enum):
    DAMAGE = auto()
    KILL = auto()
    GOLD = auto()


@dataclass
class DailyChallenge:
    id: str
    date: str
    type: ChallengeType
    params: Dict[str, Any] = field(default_factory=dict)
    leaderboard_id: str = "global"


class LeaderboardManager:
    def __init__(self, path: str = "data/leaderboard.json"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.scores: Dict[str, Dict[str, int]] = safe_load_json(path) or {}

    def update_score(self, user_id: int, challenge_id: str, score: int) -> None:
        board = self.scores.setdefault(challenge_id, {})
        board[str(user_id)] = max(score, board.get(str(user_id), 0))
        safe_save_json(self.path, self.scores)

    def top(self, challenge_id: str, limit: int = 10) -> List[Tuple[str, int]]:
        board = self.scores.get(challenge_id, {})
        return sorted(board.items(), key=lambda x: x[1], reverse=True)[:limit]

    def get_score(self, user_id: int, challenge_id: str) -> int:
        return int(self.scores.get(challenge_id, {}).get(str(user_id), 0))


class DailyChallengeManager:
    def __init__(self, leaderboard: LeaderboardManager, path: str = "data/daily_challenge.json"):
        self.leaderboard = leaderboard
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.current: Optional[DailyChallenge] = None
        self.load()

    def load(self) -> None:
        data = safe_load_json(self.path)
        if data:
            self.current = DailyChallenge(
                id=data.get("id", ""),
                date=data.get("date", ""),
                type=ChallengeType[data.get("type", "DAMAGE")],
                params=data.get("params", {}),
                leaderboard_id=data.get("leaderboard_id", "global"),
            )

    def ensure_today(self) -> DailyChallenge:
        today = date.today().isoformat()
        if self.current and self.current.date == today:
            return self.current
        self.current = DailyChallenge(
            id=f"DAILY-{today}",
            date=today,
            type=random.choice(list(ChallengeType)),
            params={"target": random.randint(50, 150)},
            leaderboard_id="global",
        )
        safe_save_json(
            self.path,
            {
                "id": self.current.id,
                "date": self.current.date,
                "type": self.current.type.name,
                "params": self.current.params,
                "leaderboard_id": self.current.leaderboard_id,
            },
        )
        return self.current

    def record_score(self, user_id: int, score: int) -> None:
        if not self.current:
            self.ensure_today()
        if not self.current:
            return
        self.leaderboard.update_score(user_id, self.current.id, score)


# =====================
# Event Bus & Plugin
# =====================


class EventType(Enum):
    BATTLE_WON = auto()
    ENEMY_DEFEATED = auto()
    LEVEL_UP = auto()
    ITEM_UPGRADED = auto()
    ITEM_CRAFTED = auto()
    CHALLENGE_COMPLETED = auto()


@dataclass
class GameEvent:
    type: EventType
    payload: Dict[str, Any]


class EventBus:
    def __init__(self):
        self.subscribers: Dict[EventType, List[Callable[[GameEvent], None]]] = {}

    def subscribe(self, event_type: EventType, handler: Callable[[GameEvent], None]) -> None:
        self.subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event: GameEvent) -> None:
        for handler in self.subscribers.get(event.type, []):
            try:
                handler(event)
            except Exception:
                continue


class GamePlugin:
    def on_load(self, bus: EventBus) -> None:
        raise NotImplementedError


class PluginManager:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.plugins: List[GamePlugin] = []

    def load_plugin(self, plugin: GamePlugin) -> None:
        plugin.on_load(self.bus)
        self.plugins.append(plugin)


class AchievementPlugin(GamePlugin):
    def __init__(self, path: str = "data/achievements.json"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.achievements = safe_load_json(path) or {}

    def on_load(self, bus: EventBus) -> None:
        bus.subscribe(EventType.ITEM_UPGRADED, self._on_upgrade)
        bus.subscribe(EventType.ITEM_CRAFTED, self._on_craft)

    def _save(self):
        safe_save_json(self.path, self.achievements)

    def _grant(self, user_id: int, key: str) -> None:
        badges = self.achievements.setdefault(str(user_id), [])
        if key not in badges:
            badges.append(key)
            self._save()

    def _on_upgrade(self, event: GameEvent) -> None:
        user_id = event.payload.get("user_id")
        self._grant(user_id, "UPGRADER")

    def _on_craft(self, event: GameEvent) -> None:
        user_id = event.payload.get("user_id")
        self._grant(user_id, "ALCHEMIST")


class StatisticsTracker(GamePlugin):
    def __init__(self, path: str = "data/statistics.json"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.stats = safe_load_json(path) or {}

    def on_load(self, bus: EventBus) -> None:
        bus.subscribe(EventType.ITEM_UPGRADED, self._update_upgrade)
        bus.subscribe(EventType.ITEM_CRAFTED, self._update_craft)
        bus.subscribe(EventType.CHALLENGE_COMPLETED, self._update_challenge)

    def _save(self):
        safe_save_json(self.path, self.stats)

    def _increment(self, user_id: int, key: str, amount: int = 1) -> None:
        bucket = self.stats.setdefault(str(user_id), {})
        bucket[key] = bucket.get(key, 0) + amount
        self._save()

    def _update_upgrade(self, event: GameEvent) -> None:
        user_id = event.payload.get("user_id")
        self._increment(user_id, "upgrades")

    def _update_craft(self, event: GameEvent) -> None:
        user_id = event.payload.get("user_id")
        self._increment(user_id, "crafts")

    def _update_challenge(self, event: GameEvent) -> None:
        user_id = event.payload.get("user_id")
        self._increment(user_id, "challenges")


# =====================
# Hybrid Storage Stub
# =====================


class HybridStorage:
    def __init__(self, path: str = "data/player_db.json"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.db = safe_load_json(path) or {}

    async def save_state(self, user_id: int, payload: Dict[str, Any]) -> None:
        self.cache[str(user_id)] = payload
        self.db[str(user_id)] = payload
        safe_save_json(self.path, self.db)

    async def get_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        if str(user_id) in self.cache:
            return self.cache[str(user_id)]
        return self.db.get(str(user_id))

    def save_state_sync(self, user_id: int, payload: Dict[str, Any]) -> None:
        asyncio.get_event_loop().create_task(self.save_state(user_id, payload))

    def load_state_sync(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self.db.get(str(user_id))


# =====================
# Monitoring
# =====================


@dataclass
class RequestLog:
    timestamp: float
    duration: float
    endpoint: str
    user_id: int
    success: bool
    error: str = ""


class PerformanceMonitor:
    def __init__(self, window: int = 100):
        self.window = window
        self.logs: List[RequestLog] = []

    def record_request(self, log: RequestLog) -> None:
        self.logs.append(log)
        if len(self.logs) > self.window:
            self.logs.pop(0)

    def summary(self) -> Dict[str, Any]:
        if not self.logs:
            return {"count": 0}
        total = len(self.logs)
        avg_duration = sum(log.duration for log in self.logs) / total
        failures = sum(1 for log in self.logs if not log.success)
        return {
            "count": total,
            "avg_duration": round(avg_duration, 3),
            "failures": failures,
            "endpoints": self._endpoint_stats(),
        }

    def _endpoint_stats(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {}
        for log in self.logs:
            ep = stats.setdefault(log.endpoint, {"count": 0, "total": 0.0})
            ep["count"] += 1
            ep["total"] += log.duration
        for ep in stats:
            stats[ep]["avg"] = round(stats[ep]["total"] / stats[ep]["count"], 3)
        return stats

    def monitored(self, endpoint_name: str) -> Callable:
        def decorator(func: Callable):
            async def wrapper(*args, **kwargs):
                start = time.time()
                success = True
                err = ""
                update = args[0] if args else None
                user_id = getattr(getattr(update, "effective_user", None), "id", 0)
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # pragma: no cover - defensive
                    success = False
                    err = str(exc)
                    raise
                finally:
                    self.record_request(
                        RequestLog(time.time(), time.time() - start, endpoint_name, user_id, success, err)
                    )

            return wrapper

        return decorator


# =====================
# Task Queue & Scheduler
# =====================


class TaskPriority(Enum):
    LOW = 3
    MEDIUM = 2
    HIGH = 1


@dataclass(order=True)
class Task:
    priority: TaskPriority
    func: Callable
    args: Tuple[Any, ...] = field(default_factory=tuple, compare=False)
    kwargs: Dict[str, Any] = field(default_factory=dict, compare=False)
    max_retries: int = 3
    retries: int = 0


class TaskQueue:
    def __init__(self, workers: int = 1):
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.workers = workers
        self.running = False
        self.tasks_failed = 0

    async def enqueue(self, task: Task):
        await self.queue.put((task.priority.value, task))

    async def _worker(self, worker_id: int):
        while self.running:
            try:
                _, task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                if asyncio.iscoroutinefunction(task.func):
                    await task.func(*task.args, **task.kwargs)
                else:
                    task.func(*task.args, **task.kwargs)
            except Exception:
                if task.retries < task.max_retries:
                    task.retries += 1
                    await self.queue.put((task.priority.value, task))
                else:
                    self.tasks_failed += 1
            finally:
                self.queue.task_done()

    async def start(self):
        self.running = True
        for idx in range(self.workers):
            asyncio.create_task(self._worker(idx))

    async def stop(self):
        self.running = False


class ScheduledTaskManager:
    def __init__(self, queue: TaskQueue, challenge_manager: DailyChallengeManager):
        self.queue = queue
        self.challenge_manager = challenge_manager

    async def run_forever(self):
        while True:
            now = datetime.now()
            tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
            await asyncio.sleep((tomorrow - now).total_seconds())
            self.challenge_manager.ensure_today()
            await self.queue.enqueue(Task(TaskPriority.MEDIUM, lambda: None))


# =====================
# Enemy AI
# =====================


class AIPersonality(Enum):
    RANDOM = auto()
    AGGRESSIVE = auto()
    DEFENSIVE = auto()


class EnemyAI:
    def __init__(self, personality: AIPersonality):
        self.personality = personality

    def choose_action(self, enemy: Dict[str, Any], party: List[Any], battle_state: Any) -> Tuple[str, Optional[str]]:
        if not party:
            return "DEFEND", None
        if self.personality == AIPersonality.AGGRESSIVE:
            target = max(party, key=lambda c: getattr(c, "atk", 1))
            return "ATTACK", target.id
        if self.personality == AIPersonality.DEFENSIVE and enemy.get("hp", 1) < enemy.get("max_hp", 1) * 0.3:
            return "DEFEND", None
        target = random.choice(party)
        return "ATTACK", target.id


__all__ = [
    "AchievementPlugin",
    "AIPersonality",
    "DailyChallenge",
    "DailyChallengeManager",
    "EventBus",
    "EventManager",
    "EventType",
    "GameEvent",
    "HybridStorage",
    "LeaderboardManager",
    "PARTY_SYNERGIES",
    "PerformanceMonitor",
    "RequestLog",
    "ScheduledTaskManager",
    "StatisticsTracker",
    "StatusEffect",
    "StatusEffectManager",
    "StatusType",
    "Task",
    "TaskPriority",
    "TaskQueue",
    "WeaponAffinity",
    "WeaponUpgrade",
    "calculate_party_synergies",
    "can_craft",
    "craft_item",
    "perform_weapon_upgrade",
    "WEAPON_UPGRADE_RULES",
    "CRAFTING_RECIPES",
    "EnemyAI",
]
