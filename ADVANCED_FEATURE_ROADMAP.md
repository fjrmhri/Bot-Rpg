# Legends of Aruna – Roadmap & Desain Fitur Lanjutan

## 1. Roadmap Implementasi Fitur (Per Fase)

### Fase 1 – Dampak Gameplay Cepat & Terisolasi
- **Tujuan desain:** memperdalam loop combat tanpa mengubah fondasi arsitektur besar.
- **Fitur:** Weapon affinity & upgrade, dasar status effects, crafting dasar.
- **Ketergantungan:**
  - Membutuhkan perluasan `GameState` (inventori menyimpan bahan, progres affinity per senjata).
  - Status effect membutuhkan hook di battle loop (tick per turn, gate aksi `can_act`).
- **Perubahan data model & API:**
  - `CharacterState`/inventory menampung `weapon_affinity` dan `equipment_upgrades` keyed by `item_id`.
  - `BattleState` menampung `StatusEffectManager` per battle.
  - API internal damage calc menerima modifier event/status.

### Fase 2 – Sistem Meta & Engagement
- **Tujuan desain:** menambah alasan login harian dan eksplorasi sosial ringan.
- **Fitur:** Daily challenge + leaderboard, dynamic world/merchant events, party composition synergies.
- **Ketergantungan:**
  - Memerlukan penyimpanan skor global (sementara di file/Redis; final di PostgreSQL).
  - Event Manager terhubung ke battle reward dan encounter generator.
  - Synergy memerlukan daftar anggota party dan hook di kalkulasi stat/skill.
- **Perubahan data model & API:**
  - `DailyChallenge` entity + `LeaderboardManager` global.
  - `WorldEvent` aktif disimpan di `GameState` global/tenant; modifier diterapkan ke damage/drop.
  - `calculate_party_synergies` dipanggil saat membangun snapshot stat untuk battle.

### Fase 3 – Arsitektur & Skalabilitas
- **Tujuan desain:** memisah concern, siap skala pengguna, dan tambah extensibility.
- **Fitur:** Event Bus, plugin system, PostgreSQL + Redis (HybridStorage), task queue/scheduler.
- **Ketergantungan:**
  - Event Bus menjadi kontrak utama antar modul; plugin memakai event hook.
  - Hybrid storage menggantikan save-file; task queue dipakai untuk background save/reset challenge.
- **Perubahan data model & API:**
  - `PlayerState` SQLAlchemy dengan kolom primitif + JSON.
  - `RedisCache` untuk state ephemeral; metode `get_state/save_state` menjadi async dan idempotent.
  - Handler utama memakai `event_bus.publish` untuk semua milestone (battle, item, quest).

### Fase 4 – Polishing & Intelligence
- **Tujuan desain:** meningkatkan rasa hidup dunia dan kualitas layanan.
- **Fitur:** Advanced enemy AI, monitoring & analytics, balancing lanjutan, personality-driven encounters.
- **Ketergantungan:**
  - Memakai Event Bus untuk statistik, achievement, dan alert performa.
  - Monitoring butuh decorator `@monitored` di handler kritis.
- **Perubahan data model & API:**
  - Monster template menyimpan `ai_personality` dan daftar skill.
  - Log performa (in-memory + opsi persist) untuk admin command.

## 2. Desain Teknis Detail per Fitur Utama

### Equipment System Lanjutan
- **Data:**
  - `WeaponAffinity` per `item_id` dengan `kills_with_weapon`, `mastery_level`, `gain_exp(kills)` menghasilkan pesan level-up.
  - `WeaponUpgrade` per `item_id` dengan `level`, `bonus_stats` (atk, crit_chance, opsional element scaling), `_success_rate` menurun per level.
- **Integrasi:**
  - Inventory menampung bahan upgrade; `CharacterState` menyimpan `equipment_upgrades` & `weapon_affinity` dict.
  - Battle calc: `effective_atk = base_atk + upgrade_bonus + mastery_bonus`.
- **UI Telegram:** menu `/forge` atau `MENU_FORGE` menampilkan senjata di inventory + level + success rate, inline button `UPGRADE|{item_id}`; hasil kirim pesan sukses/gagal dan update inventory.

### Dynamic World Events
- **Representasi:** `WorldEvent` berisi `id`, `trigger`, `duration`, `effects` (modifier multiplicative), `is_active`.
- **Manager:** `EventManager.tick(state)` memeriksa trigger & kadaluarsa; `get_active_modifiers()` dipakai battle/drop generator.
- **Integrasi:**
  - Damage calc memakai `enemy_atk_multiplier`, drop memakai `drop_rate_multiplier`, encounter memakai `rare_encounter_boost`.
  - Notifikasi event start/end via Event Bus → UI broadcast.

### Party Synergies
- **Data:** `PARTY_SYNERGIES` map kombinasi -> efek (stat flat/percent, skill boost).
- **Logika:** `calculate_party_synergies(state)` memeriksa anggota hidup; hasil bonus diaplikasi saat build stat snapshot atau sebelum turn.
- **Integrasi skill:** buff seperti `mag_bonus` menambah multiplier di kalkulasi damage; `all_stats_bonus` menaikkan base stat sebelum gear.

### Daily Challenges & Leaderboards
- **Entity:** `DailyChallenge(date, type, params, leaderboard)`; `CHALLENGE_TYPES` mendefinisikan scoring.
- **Sistem skor:** `LeaderboardManager.update_score(user_id, challenge_id, score)` menyimpan top-N; gunakan Redis sorted set bila tersedia.
- **Reset:** scheduler/task queue menjalankan reset harian, membuat challenge baru, membersihkan leaderboard.
- **UX:** command `/daily` menampilkan challenge aktif, skor pengguna, dan top 10; publish event saat selesai challenge.

### Crafting System
- **Data:** `CRAFTING_RECIPES` dengan `materials`, `result`, `required_level`.
- **Logika:** `can_craft` validasi level tertinggi party + stok; `craft` konsumsi material dan memberi item.
- **Progression:** resep tier tinggi buka lewat quest/flag; gating bahan langka dari event/boss.

### Plugin System & Event Bus
- **Plugin:** `GamePlugin` dengan `on_load`, hook battle/progression; plugin didaftarkan di `GameApplication.load_plugin`.
- **Event Bus:** `EventType`, `GameEvent`, `EventBus.subscribe/publish` + middleware.
- **Contoh plugin:**
  - **AchievementPlugin**: subscribe `BATTLE_WON`, `ENEMY_DEFEATED`, `LEVEL_UP`; grant reward dan publish event baru.
  - **StatisticsTracker**: simpan agregat per user (xp, gold, kills, item). Provides `/stats` output.

### Database & Caching (PostgreSQL + Redis)
- **Model:** `PlayerState` SQLAlchemy: `user_id` (PK), `name`, `location`, `gold`, `party_data` JSON, `inventory` JSON, `flags` JSON, `last_active`.
- **HybridStorage:** `get_state` cek Redis dulu; fallback PostgreSQL; setelah load, cache TTL 1h. `save_state` menulis ke DB + cache paralel.
- **Konsistensi:** invalidasi cache setelah perubahan besar (equip, craft, quest complete); gunakan version/etag optional untuk detect stale.

### Monitoring & Analytics
- **Komponen:** `PerformanceMonitor` menyimpan `RequestLog` deque; `monitored(endpoint)` decorator menulis metrik (latency, error rate, RPS, active users, mem/cpu via psutil).
- **Admin command:** `/metrics` menampilkan metrik dan 5 endpoint paling lambat.
- **Event hook:** middleware Event Bus untuk logging event volume dan outliers.

### Status Effects System
- **Entity:** `StatusType`, `StatusEffect(type, duration, potency, source)`, `StatusEffectManager`.
- **Perilaku:** `on_apply` (adjust stat/status), `tick` (per turn damage/heal), `on_remove` (revert buff). `can_act` blokir aksi saat stun/freeze.
- **Integrasi skill:** skill memiliki blok `status_effect` (type, chance, duration, potency); diterapkan saat skill hit atau buff.

### Background Job System / Task Queue
- **Komponen:** `Task(priority, retries)`, `TaskQueue` berbasis `asyncio.PriorityQueue`, worker paralel, retry hingga `max_retries`.
- **Contoh job:** `background_save`, `reset_daily_challenges`, `broadcast_world_event`, `analytics_flush`.
- **Scheduler:** `ScheduledTaskManager` tidur sampai midnight untuk reset harian; enqueue job ke queue.

### Advanced Enemy AI
- **Personality:** `AIPersonality` (Aggressive, Defensive, Tactical, Healer, Random).
- **Keputusan:** `EnemyAI.choose_action(enemy, party, battle_state)` mengembalikan aksi & target; strategi berbeda per personality (focus kill, heal, buff, aoe, randomizer).
- **Integrasi:** monster template menambahkan `ai_personality` + skill list; battle loop memanggil `enemy_take_turn_with_ai` dan mencatat log.

## 3. Contoh Kode / Snippet Implementasi Inti

### Weapon Affinity & Upgrade
```python
@dataclass
class WeaponAffinity:
    kills_with_weapon: int = 0
    mastery_level: int = 0

    def gain_exp(self, kills: int):
        self.kills_with_weapon += kills
        new_level = min(5, self.kills_with_weapon // 50)
        if new_level > self.mastery_level:
            self.mastery_level = new_level
            return f"Weapon Mastery increased to {new_level}!"
        return None

class WeaponUpgrade:
    def __init__(self, item_id, upgrade_level=0):
        self.item_id = item_id
        self.level = upgrade_level

    @property
    def bonus_stats(self):
        return {
            "atk": self.level * 2,
            "crit_chance": self.level * 0.01,
        }

    def _success_rate(self):
        return max(0.5, 1.0 - (self.level * 0.08))

    def upgrade(self, materials: Dict[str, int]) -> Tuple[bool, str]:
        required = self._get_required_materials()
        if not self._has_materials(materials, required):
            return False, "Material tidak cukup"
        if random.random() < self._success_rate():
            self.level += 1
            return True, f"Upgrade berhasil! Weapon sekarang +{self.level}"
        return False, f"Upgrade gagal. Weapon tetap +{self.level}"
```

### World Event Manager
```python
class WorldEvent:
    def __init__(self, event_id, trigger_conditions, duration, effects):
        self.id = event_id
        self.triggers = trigger_conditions
        self.duration = duration
        self.effects = effects
        self.is_active = False
        self.expires_at = 0

    def check_trigger(self, state: GameState) -> bool:
        return all(condition(state) for condition in self.triggers)

class EventManager:
    def __init__(self):
        self.active_events = []

    def activate_event(self, event_id, event_data):
        self.active_events.append({
            "id": event_id,
            "effects": event_data["effects"],
            "expires_at": time.time() + event_data["duration"],
        })

    def tick(self, state: GameState):
        now = time.time()
        self.active_events = [e for e in self.active_events if now < e["expires_at"]]
        for event_id, event_data in WORLD_EVENTS.items():
            if event_data["trigger"](state):
                self.activate_event(event_id, event_data)

    def get_active_modifiers(self):
        modifiers = {}
        for event in self.active_events:
            for k, v in event["effects"].items():
                modifiers[k] = modifiers.get(k, 1.0) * v
        return modifiers
```

### Party Synergy Calculation
```python
def calculate_party_synergies(state: GameState) -> Dict[str, float]:
    living = [c.id for c in state.party.living_members()]
    bonuses = defaultdict(float)
    for combo, synergy in PARTY_SYNERGIES.items():
        if all(char_id in living for char_id in combo):
            for effect, value in synergy["effects"].items():
                bonuses[effect] += value
    return bonuses
```

### Daily Challenge & Leaderboard
```python
class DailyChallenge:
    def __init__(self, date, challenge_type, params):
        self.date = date
        self.type = challenge_type
        self.params = params
        self.leaderboard = []

class LeaderboardManager:
    async def update_score(self, user_id, challenge_id, score):
        lb = self.global_leaderboard.setdefault(challenge_id, [])
        existing = next((e for e in lb if e["user_id"] == user_id), None)
        if existing:
            if score > existing["score"]:
                existing["score"] = score
                existing["timestamp"] = time.time()
        else:
            lb.append({"user_id": user_id, "score": score, "timestamp": time.time()})
        lb.sort(key=lambda x: x["score"], reverse=True)
        self.global_leaderboard[challenge_id] = lb[:100]
```

### Crafting Core
```python
class CraftingSystem:
    def can_craft(self, state: GameState, recipe_id: str) -> Tuple[bool, str]:
        recipe = CRAFTING_RECIPES.get(recipe_id)
        if not recipe:
            return False, "Recipe tidak ditemukan"
        if highest_party_level(state) < recipe["required_level"]:
            return False, f"Butuh level {recipe['required_level']}"
        for mat_id, qty in recipe["materials"].items():
            if state.inventory.get(mat_id, 0) < qty:
                return False, f"Kurang {ITEMS[mat_id]['name']}"
        return True, "OK"

    def craft(self, state: GameState, recipe_id: str) -> Tuple[bool, str]:
        can, msg = self.can_craft(state, recipe_id)
        if not can:
            return False, msg
        for mat_id, qty in CRAFTING_RECIPES[recipe_id]["materials"].items():
            adjust_inventory(state, mat_id, -qty)
        result = CRAFTING_RECIPES[recipe_id]["result"]
        adjust_inventory(state, result["item_id"], result["quantity"])
        return True, f"Berhasil membuat {ITEMS[result['item_id']]['name']}!"
```

### Plugin System & Event Bus
```python
class GamePlugin:
    def __init__(self, app):
        self.app = app
    def on_load(self):
        pass

class GameApplication:
    def __init__(self):
        self.plugins = []
        self.event_bus = EventBus()
    def load_plugin(self, plugin_class):
        plugin = plugin_class(self)
        plugin.on_load()
        self.plugins.append(plugin)
```

### Hybrid Storage (PostgreSQL + Redis)
```python
class HybridStorage:
    async def get_state(self, user_id):
        cached = await self.cache.get_state(user_id)
        if cached:
            return cached
        state = await self.db.load_state(user_id)
        if state:
            await self.cache.set_state(user_id, state)
        return state

    async def save_state(self, state):
        await asyncio.gather(
            self.db.save_state(state),
            self.cache.set_state(state.user_id, state)
        )
```

### Monitoring Decorator
```python
monitor = PerformanceMonitor()

def monitored(endpoint_name: str):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start = time.time(); success = True; err = ""
            update = args[0] if args else None
            user_id = getattr(getattr(update, "effective_user", None), "id", 0)
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                success = False; err = str(e); raise
            finally:
                monitor.record_request(RequestLog(time.time(), time.time()-start, endpoint_name, user_id, success, err))
        return wrapper
    return decorator
```

### Status Effect Manager (Inti)
```python
class StatusEffectManager:
    def add_effect(self, target_key: str, effect: StatusEffect, target) -> List[str]:
        bucket = self.active_effects.setdefault(target_key, [])
        existing = next((e for e in bucket if e.type == effect.type), None)
        logs = []
        if existing and effect.potency >= existing.potency:
            existing.duration = max(existing.duration, effect.duration)
            existing.potency = max(existing.potency, effect.potency)
            logs.append(f"Status {effect.type.value} diperpanjang pada {target.name}!")
        elif not existing:
            bucket.append(effect)
            logs.extend(effect.on_apply(target))
        return logs

    def tick_effects(self, state: GameState) -> List[str]:
        logs = []
        for target_key, effects in list(self.active_effects.items()):
            target = get_buff_target(state, target_key)
            remaining = []
            for effect in effects:
                if effect.type in [StatusType.STUN, StatusType.FREEZE]:
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
```

### Task Queue & Scheduler
```python
class TaskQueue:
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
```

### Enemy AI Turn
```python
def enemy_take_turn_with_ai(state: GameState, enemy_index: int) -> List[str]:
    enemy = state.battle_state.enemies[enemy_index]
    ai = EnemyAI(enemy.get("ai_personality", AIPersonality.RANDOM))
    party = [state.party[cid] for cid in state.party_order if state.party[cid].hp > 0]
    action, target = ai.choose_action(enemy, party, state.battle_state)
    logs = []
    if action == "ATTACK" and target:
        target_char = state.party[target]
        dmg = calc_enemy_basic_damage(enemy["atk"], get_effective_stat(target_char, "defense"))
        target_char.hp = max(0, target_char.hp - dmg)
        logs.append(f"{enemy['name']} menyerang {target_char.name} dan memberikan {dmg} damage!")
        if target_char.hp == 0:
            logs.append(f"{target_char.name} tumbang!")
    elif action == "DEFEND":
        enemy["defending"] = True
        logs.append(f"{enemy['name']} mengambil posisi bertahan!")
    return logs
```

## 4. Strategi Migrasi & Deployment Bertahap
- Tambah field baru dengan default di deserializer (contoh `equipment_upgrades: {}`); lakukan fallback jika kunci tidak ada.
- Gunakan feature flag/config (`ENABLE_EVENT_SYSTEM`, `ENABLE_STATUS_EFFECTS`) agar dapat rollout incremental.
- Urutan deployment:
  1. Event Bus + plugin skeleton (no-op) untuk kompatibilitas ke depan.
  2. HybridStorage (DB+Redis) berjalan paralel dengan file save; simpan ganda sampai valid.
  3. Aktifkan status effects & equipment upgrade di battle kalkulasi setelah tes regresi.
  4. Rilis daily challenge + leaderboard setelah scheduler stabil.
  5. Tambah AI cerdas & monitoring terakhir (mudah rollback).
- Buat migrasi data: script satu kali untuk memindah file save → PostgreSQL; simpan versi schema per user (`state.version`).

## 5. Saran Balancing & UX
- **Balancing:**
  - Batasi upgrade success rate menurun; tambah biaya bahan eksponensial untuk level tinggi.
  - Status effect ofensif (poison/burn) scale dengan max HP kecil (5%) agar tidak instakill bos; beri immunities pada bos tertentu.
  - Crafting resep kuat butuh bahan dari event/challenge agar tidak trivial.
  - AI cerdas: kurangi damage dasar atau frekuensi skill agar tidak frustasi; pakai personality sesuai tier area.
- **UX & Komunikasi:**
  - Tambahkan tutorial singkat saat fitur dibuka (pesan satu kali + command `help` khusus).
  - Notifikasi in-game untuk world event mulai/berakhir dan daily challenge reset.
  - Menu terpisah: `Forge`, `Craft`, `Challenges`, `Events`; jelaskan requirement jelas (bahan, level, success rate).
  - Leaderboard tampilkan waktu reset dan hadiahnya; beri badge visual (emoji) pada top pemain.
