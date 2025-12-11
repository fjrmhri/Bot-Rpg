# Legends of Aruna – Prioritas Perbaikan & Rencana Teknis

## 1. Ringkasan Prioritas Perbaikan

### a) Concurrency & State Safety
- **Risiko:** race condition pada auto-hunt dan battle menyebabkan state corrupt (battle_state `None`, quest progress hilang), crash, atau aksi ganda.
- **Dampak:** pemain kehilangan progres, UI membingungkan, crash saat callback, auto-hunt berhenti tanpa cleanup.
- **Prioritas:** **High**.

### b) Memory & Session Management
- **Risiko:** `USER_STATES`/lock tidak pernah dibersihkan → memory leak; session lama tetap hidup dengan state usang.
- **Dampak:** konsumsi memori jangka panjang, potensi state lama dipakai ulang tanpa sengaja.
- **Prioritas:** **High**.

### c) Error Handling & I/O Safety
- **Risiko:** error Telegram (FloodWait, TimedOut) tidak di-handle; save/load bisa corrupt jika crash di tengah operasi.
- **Dampak:** bot down, pesan hilang, file save rusak atau hilang.
- **Prioritas:** **High**.

### d) Arsitektur & Pemisahan Concern
- **Risiko:** god object `GameState` dan logika domain bercampur UI; sulit diuji, perubahan kecil memicu regressi.
- **Dampak:** maintainability rendah, sulit menambah fitur (battle AI, quest baru) tanpa efek samping.
- **Prioritas:** **Medium–High**.

### e) Performa & Efisiensi I/O
- **Risiko:** N+1 load/save, full-serialization besar, tidak ada caching/delta → I/O berat; data musuh disalin penuh ke state.
- **Dampak:** latency tinggi, pemakaian disk/CPU berlebihan saat banyak user atau spam `/save`.
- **Prioritas:** **Medium**.

### f) Security & Abuse Protection
- **Risiko:** token hard-coded, tidak ada rate limiting untuk command berat.
- **Dampak:** kebocoran kredensial, spam/save storm membuat I/O bottleneck.
- **Prioritas:** **High** (token); **Medium** (rate limit).

## 2. Desain Solusi Teknis Per Kategori

### a) Concurrency & State Safety
- Terapkan `AutoHuntSession` per user + `asyncio.Lock` untuk setiap akses state yang mengubah battle/quest/inventory.
- Bungkus semua interaksi `battle_state` (aksi player, auto-hunt tick, drop) dengan lock pengguna.
- Quest progress update dibuat atomic (gunakan lock + operasi terpusat di quest service).

### b) Memory & Session Management
- Tambahkan `SessionManager` dengan TTL + LRU untuk `USER_STATES` dan `USER_LOCKS`; cleanup otomatis untuk user idle.
- Auto-hunt/battle loop memeriksa flag aktif dan berhenti rapi saat session dibersihkan.

### c) Error Handling & I/O Safety
- Bungkus call Telegram via `safe_send_message`/`safe_edit_message` yang handle `RetryAfter`, `TimedOut`, `TelegramError` (log + fallback).
- Implementasi penyimpanan aman: tulis ke temp file, flush+fsync, backup lama, lalu `os.replace`. Pulihkan dari backup bila gagal.

### d) Arsitektur & Pemisahan Concern
- Pecah `GameState` ke domain model (`PlayerProfile`, `Party`, `QuestLog`, `Inventory`, `BattleState`).
- Pisahkan layer: **Domain** (battle engine, quest rules), **Service/Application** (orchestrate + enforce lock/transaction), **Presentation** (handler Telegram).

### e) Performa & Efisiensi I/O
- Lazy load state per user + cache (LRU) dengan asynchronous save queue; hindari load massal.
- Terapkan delta update / incremental save untuk perubahan kecil; kompakkan berkala.
- Gunakan flyweight untuk data musuh (template + instance) agar battle state ringan.

### f) Security & Abuse Protection
- Ambil token dari environment (.env) dan fail-fast jika kosong.
- Rate limiter per user untuk command berat (mis. `/save`, `/load`, auto-hunt toggle) dan fallback pesan jika limit tercapai.

## 3. Rencana Implementasi Bertahap

1) **Infra Locking & Session Manager**
   - Ubah modul state/handler (`LEGENDS_OF_ARUNA_JOURNEY_TO_KAMPAR.py`): tambahkan `SessionManager` (TTL, LRU) menggantikan dict global; expose `get_state(user_id)` dan `get_lock(user_id)` baru.
   - Risiko: deadlock jika lock ganda; mitigasi dengan pola "satu lock per user" dan dokumentasi pemakaian.
   - Tes: unit test SessionManager (expiry, reuse), integration test concurrency (simulasi dua aksi paralel tidak saling tubruk).

2) **Lindungi Battle & Auto-Hunt**
   - Bungkus seluruh akses `state.battle_state` dan quest progression di dalam `async with get_lock(user_id)`.
   - Introduce `AutoHuntSession` (flag aktif, lock internal) dan pastikan cleanup saat berhenti/exception.
   - Risiko: kemungkinan blok lama; mitigasi dengan menjaga critical section kecil.
   - Tes: integration test auto-hunt stop/start, simulasi spam callback saat battle.

3) **Safe Persistence Layer**
   - Tambahkan modul `safe_storage` atau fungsi penyimpanan aman dengan temp+backup+fsync.
   - Adaptasi `save_game_state`/`load_game_state` untuk memakai wrapper; tambahkan error handling dan logging.
   - Risiko: path permission; mitigasi dengan pengecekan directory + fallback ke backup.
   - Tes: unit test save/load dengan fault injection (paksa exception di tengah), verifikasi backup dipakai.

4) **Error Handling Wrapper untuk Telegram**
   - Implement `safe_send_message`/`safe_edit_message` di util; ganti pemanggilan `bot.send_*` kritis di handler.
   - Risiko: lupa mengganti semua call; mitigasi dengan grep audit dan lint check.
   - Tes: mock Telegram API untuk memicu `RetryAfter`/`TimedOut` dan pastikan retry/backoff berjalan.

5) **Rate Limiting & Abuse Protection**
   - Tambah `RateLimiter` sederhana per user; terapkan di command berat (`/save`, `/load`, auto-hunt toggle, map spam).
   - Risiko: UX sedikit tertahan; mitigasi dengan pesan jelas.
   - Tes: unit test limiter window/reset; integration test command spam.

6) **Refactor Domain Model**
   - Perkenalkan model `PlayerProfile`, `Party`, `QuestLog`, `Inventory`, `BattleState`; migrasi `GameState` menjadi komposisi.
   - Update serialization/deserialization sesuai struktur baru.
   - Risiko: regression luas; mitigasi dengan bertahap (compat layer), tambahkan unit test untuk tiap model.

7) **Layering: Domain vs UI**
   - Ekstrak battle logic ke `BattleEngine` (domain) + `BattleService` (application); handler hanya formatting UI.
   - Migrasi quest handler/story ke service layer yang pure data.
   - Risiko: refactor besar; lakukan modular (battle dulu, lalu quest/story).
   - Tes: unit test battle engine (damage, buff), integration test handler end-to-end.

8) **Performa & I/O Optimasi**
   - Implement LRU cache + lazy load + async save queue; tambahkan delta save pipeline.
   - Ganti musuh ke pola template+instance (flyweight) dan hapus data immutable dari state.
   - Risiko: konsistensi cache vs disk; mitigasi dengan flush saat shutdown dan pada interval.
   - Tes: benchmark save/load; profiling memory battle.

9) **Konfigurasi & Type Safety**
   - Tambah modul config dengan konstanta (crit rate, drop, auto-hunt delay) dan pakai di seluruh battle/quest.
   - Gunakan pydantic/dataclass untuk entity (Monster, Skill, Item) untuk validasi.
   - Tes: unit test validasi config/entity.

## 4. Contoh Kode / Snippet Implementasi Inti

### AutoHuntSession + Lock Per User
```python
class AutoHuntSession:
    def __init__(self, state):
        self.state = state
        self.active = True
        self.lock = asyncio.Lock()

    async def stop(self):
        async with self.lock:
            self.active = False

    async def run(self, user_lock):
        while True:
            async with self.lock:
                if not self.active:
                    return
            async with user_lock:  # serialize with other actions
                await self._run_single_battle()
            await asyncio.sleep(CONFIG.AUTO_HUNT_DELAY)
```

### SessionManager dengan TTL
```python
class SessionManager:
    def __init__(self, ttl=3600, max_size=1000):
        self.states = OrderedDict()
        self.locks = {}
        self.last_access = {}
        self.ttl = ttl
        self.max_size = max_size

    def get_lock(self, user_id):
        self.last_access[user_id] = time.time()
        self._cleanup()
        return self.locks.setdefault(user_id, asyncio.Lock())

    def get_state(self, user_id):
        self.last_access[user_id] = time.time()
        self._cleanup()
        state = self.states.get(user_id)
        if not state:
            state = GameState(user_id)
            self.states[user_id] = state
        return state

    def _cleanup(self):
        now = time.time()
        expired = [uid for uid, ts in self.last_access.items() if now - ts > self.ttl]
        for uid in expired:
            self.states.pop(uid, None)
            self.locks.pop(uid, None)
            self.last_access.pop(uid, None)
        while len(self.states) > self.max_size:
            uid, _ = self.states.popitem(last=False)
            self.locks.pop(uid, None)
            self.last_access.pop(uid, None)
```

### Safe Telegram Wrapper
```python
async def safe_send_message(context, chat_id, **kwargs):
    for attempt in range(3):
        try:
            return await context.bot.send_message(chat_id=chat_id, **kwargs)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TimedOut:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
        except TelegramError as e:
            logger.warning("send_message failed: %s", e)
            return None
```

### Safe Save/Load
```python
def safe_save(path: str, data: dict):
    tmp = f"{path}.tmp.{uuid.uuid4()}"
    backup = f"{path}.bak"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        shutil.copy2(path, backup)
    os.replace(tmp, path)
```

### Rate Limiter
```python
class RateLimiter:
    def __init__(self, max_calls, period):
        self.calls = defaultdict(list)
        self.max_calls = max_calls
        self.period = period

    def is_allowed(self, user_id):
        now = time.time()
        self.calls[user_id] = [t for t in self.calls[user_id] if now - t < self.period]
        if len(self.calls[user_id]) >= self.max_calls:
            return False
        self.calls[user_id].append(now)
        return True
```

### Refactor GameState → Domain Model (contoh)
```python
@dataclass
class PlayerProfile:
    user_id: int
    name: str
    location: str
    gold: int

@dataclass
class GameState:
    profile: PlayerProfile
    party: Party
    quests: QuestLog
    inventory: Inventory
    battle: Optional[BattleState] = None
```

### Battle Engine Terpisah
```python
class BattleEngine:
    def execute_attack(self, attacker, target):
        dmg = self._calc_damage(attacker, target)
        target.hp = max(0, target.hp - dmg)
        return BattleResult(log=[f"{attacker.name} hits {target.name} for {dmg}"],
                            battle_over=target.hp == 0)

class BattleService:
    def __init__(self, engine):
        self.engine = engine

    async def player_action(self, state, action):
        result = self.engine.execute_attack(action.attacker, action.target)
        return result
```

## 5. Checklist Validasi & Pengujian

- [ ] Semua akses `battle_state`/quest update berada dalam user-level lock; auto-hunt menggunakan `AutoHuntSession`.
- [ ] SessionManager membersihkan state/lock idle; tidak ada growth tak terbatas pada dict global.
- [ ] `safe_send_message`/`safe_edit_message` digunakan di handler utama; retry/backoff diuji.
- [ ] Safe save/load memakai temp+backup+fsync; uji corrupt recovery.
- [ ] Rate limiter aktif untuk command berat; pesan limit jelas.
- [ ] Token dibaca dari environment; tidak ada hard-coded token di repo.
- [ ] Config terpusat untuk magic numbers; entitas utama memakai dataclass/pydantic.
- [ ] Battle engine & quest logic terpisah dari UI; unit test domain tersedia.
- [ ] Performa: lazy load + cache + delta save; flyweight untuk monster data.
- [ ] Logging & monitoring: error Telegram, failure save, eviction session tercatat; alert jika retry habis.

