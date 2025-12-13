"""
Enhanced Combat Engine - Turn-Based RPG System
Sistem pertarungan yang ditingkatkan dengan initiative, skills, status effects, dan AI musuh
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class ActionType(Enum):
    """Tipe aksi dalam pertempuran"""
    ATTACK = "ATTACK"
    SKILL = "SKILL"
    DEFEND = "DEFEND"
    ITEM = "ITEM"
    RUN = "RUN"


class TargetType(Enum):
    """Tipe target dari skill atau item"""
    SINGLE_ENEMY = "SINGLE_ENEMY"
    ALL_ENEMIES = "ALL_ENEMIES"
    SINGLE_ALLY = "SINGLE_ALLY"
    ALL_ALLIES = "ALL_ALLIES"
    SELF = "SELF"


@dataclass
class CombatStats:
    """Statistik tempur untuk karakter atau musuh"""
    max_hp: int
    hp: int
    max_mp: int
    mp: int
    atk: int
    defense: int
    mag: int
    spd: int
    luck: int
    accuracy: int = 95  # Base accuracy percentage
    evasion: int = 5    # Base evasion percentage
    crit_chance: int = 5  # Base crit chance percentage
    crit_multiplier: float = 1.5


@dataclass
class StatusEffect:
    """Efek status yang diterapkan pada entity"""
    id: str
    name: str
    duration: int  # Jumlah turn tersisa
    potency: float  # Kekuatan efek (damage%, stat modifier, dll)
    tick_on_start: bool = False  # Apakah efek berlaku di awal turn
    
    def apply_turn_effect(self, target: CombatEntity) -> str:
        """Terapkan efek per turn dan return log"""
        if self.id == "POISON":
            damage = max(1, int(target.stats.max_hp * self.potency))
            target.stats.hp = max(0, target.stats.hp - damage)
            return f"{target.name} menerima {damage} damage racun"
        elif self.id == "BURN":
            damage = max(1, int(target.stats.max_hp * self.potency))
            target.stats.hp = max(0, target.stats.hp - damage)
            return f"{target.name} terbakar dan kehilangan {damage} HP"
        elif self.id == "REGEN":
            heal = max(1, int(target.stats.max_hp * self.potency))
            old_hp = target.stats.hp
            target.stats.hp = min(target.stats.max_hp, target.stats.hp + heal)
            actual_heal = target.stats.hp - old_hp
            if actual_heal > 0:
                return f"{target.name} regenerasi {actual_heal} HP"
        return ""


@dataclass
class Skill:
    """Definisi skill yang dapat digunakan dalam pertempuran"""
    id: str
    name: str
    description: str
    mp_cost: int
    cooldown: int  # Jumlah turn cooldown setelah digunakan
    power: float  # Multiplier damage (1.0 = 100% ATK/MAG)
    element: str = "NETRAL"
    target_type: TargetType = TargetType.SINGLE_ENEMY
    is_physical: bool = True  # True = gunakan ATK, False = gunakan MAG
    status_effect: Optional[Dict[str, Any]] = None  # {"id": "POISON", "chance": 30, "duration": 3, "potency": 0.05}
    hits: int = 1  # Jumlah hit untuk multi-hit skills


@dataclass 
class CombatEntity:
    """Entitas dalam pertempuran (karakter atau musuh)"""
    id: str
    name: str
    stats: CombatStats
    is_player: bool
    skills: List[Skill] = field(default_factory=list)
    status_effects: List[StatusEffect] = field(default_factory=list)
    skill_cooldowns: Dict[str, int] = field(default_factory=dict)
    defending: bool = False
    initiative_roll: int = 0
    
    def is_alive(self) -> bool:
        """Cek apakah entity masih hidup"""
        return self.stats.hp > 0
    
    def can_act(self) -> bool:
        """Cek apakah entity bisa bertindak (tidak stunned)"""
        if not self.is_alive():
            return False
        # Cek status effect yang memblokir aksi
        for effect in self.status_effects:
            if effect.id == "STUN":
                return False
        return True
    
    def add_status_effect(self, effect: StatusEffect) -> str:
        """Tambahkan status effect, return log"""
        # Cek apakah sudah ada efek yang sama
        existing = next((e for e in self.status_effects if e.id == effect.id), None)
        if existing:
            # Refresh duration jika efek baru lebih kuat
            if effect.potency >= existing.potency:
                existing.duration = max(existing.duration, effect.duration)
                existing.potency = effect.potency
                return f"{self.name} - efek {effect.name} diperpanjang"
            return ""
        else:
            self.status_effects.append(effect)
            return f"{self.name} terkena {effect.name}"
    
    def tick_status_effects(self) -> List[str]:
        """Proses semua status effect dan kurangi duration, return logs"""
        logs = []
        to_remove = []
        
        for effect in self.status_effects:
            # Terapkan efek
            log = effect.apply_turn_effect(self)
            if log:
                logs.append(log)
            
            # Kurangi duration
            effect.duration -= 1
            if effect.duration <= 0:
                to_remove.append(effect)
                logs.append(f"{effect.name} pada {self.name} berakhir")
        
        # Hapus efek yang habis
        for effect in to_remove:
            self.status_effects.remove(effect)
        
        return logs
    
    def tick_skill_cooldowns(self):
        """Kurangi cooldown semua skill"""
        for skill_id in list(self.skill_cooldowns.keys()):
            self.skill_cooldowns[skill_id] -= 1
            if self.skill_cooldowns[skill_id] <= 0:
                del self.skill_cooldowns[skill_id]


class CombatEngine:
    """Engine utama untuk mengelola pertempuran"""
    
    def __init__(self):
        self.turn_order: List[CombatEntity] = []
        self.current_turn_index: int = 0
        self.battle_logs: List[str] = []
    
    def initialize_battle(self, allies: List[CombatEntity], enemies: List[CombatEntity]):
        """Inisialisasi pertempuran dan tentukan turn order berdasarkan initiative"""
        all_entities = allies + enemies
        
        # Roll initiative untuk setiap entity (SPD + random)
        for entity in all_entities:
            entity.initiative_roll = entity.stats.spd + random.randint(0, 10)
        
        # Sort berdasarkan initiative (tertinggi duluan)
        self.turn_order = sorted(all_entities, key=lambda e: e.initiative_roll, reverse=True)
        self.current_turn_index = 0
        self.battle_logs = []
        
        # Log turn order
        order_log = "Urutan giliran: " + " -> ".join([e.name for e in self.turn_order])
        self.battle_logs.append(order_log)
    
    def get_current_entity(self) -> Optional[CombatEntity]:
        """Dapatkan entity yang sedang giliran"""
        if 0 <= self.current_turn_index < len(self.turn_order):
            return self.turn_order[self.current_turn_index]
        return None
    
    def advance_turn(self) -> Optional[CombatEntity]:
        """Lanjut ke giliran berikutnya, skip entity yang mati"""
        self.current_turn_index += 1
        
        # Jika sudah satu putaran penuh, reset dan tick status effects
        if self.current_turn_index >= len(self.turn_order):
            self.current_turn_index = 0
            self._process_end_of_round()
        
        # Skip entity yang mati atau tidak bisa act
        current = self.get_current_entity()
        while current and not current.can_act():
            if not current.is_alive():
                # Hapus dari turn order jika mati
                self.turn_order.remove(current)
                if self.current_turn_index >= len(self.turn_order):
                    self.current_turn_index = 0
                    if len(self.turn_order) == 0:
                        return None
            else:
                # Jika stunned, skip turn
                self.battle_logs.append(f"{current.name} tidak bisa bergerak!")
                self.current_turn_index += 1
                if self.current_turn_index >= len(self.turn_order):
                    self.current_turn_index = 0
                    self._process_end_of_round()
            
            current = self.get_current_entity()
        
        return current
    
    def _process_end_of_round(self):
        """Proses akhir ronde: tick status effects dan cooldowns"""
        self.battle_logs.append("--- Akhir Ronde ---")
        
        for entity in self.turn_order:
            if entity.is_alive():
                # Tick status effects
                effect_logs = entity.tick_status_effects()
                self.battle_logs.extend(effect_logs)
                
                # Tick skill cooldowns
                entity.tick_skill_cooldowns()
                
                # Reset defending
                if entity.defending:
                    entity.defending = False
    
    def calculate_damage(
        self,
        attacker: CombatEntity,
        target: CombatEntity,
        base_power: float,
        is_physical: bool,
        element: str = "NETRAL"
    ) -> Tuple[int, bool, str]:
        """
        Hitung damage dengan formula lengkap
        Returns: (damage, is_crit, damage_type)
        """
        # Cek accuracy vs evasion
        hit_chance = attacker.stats.accuracy - target.stats.evasion
        hit_chance = max(10, min(95, hit_chance))  # Cap antara 10-95%
        
        if random.randint(1, 100) > hit_chance:
            return 0, False, "MISS"
        
        # Base damage
        if is_physical:
            base_stat = attacker.stats.atk
        else:
            base_stat = attacker.stats.mag
        
        raw_damage = base_stat * base_power
        
        # Defense reduction
        defense_value = target.stats.defense
        if target.defending:
            defense_value = int(defense_value * 1.5)  # +50% def saat bertahan
        
        damage_reduction = defense_value * 0.5
        damage = max(1, raw_damage - damage_reduction)
        
        # Cek critical hit
        is_crit = False
        crit_chance = attacker.stats.crit_chance + (attacker.stats.luck // 5)
        if random.randint(1, 100) <= crit_chance:
            is_crit = True
            damage *= attacker.stats.crit_multiplier
        
        # Element weakness/resistance (placeholder - bisa diperluas)
        # TODO: Implement element system jika diperlukan
        
        damage = int(damage)
        damage_type = "CRIT" if is_crit else "NORMAL"
        
        return damage, is_crit, damage_type
    
    def execute_attack(self, attacker: CombatEntity, target: CombatEntity) -> List[str]:
        """Eksekusi serangan dasar"""
        logs = []
        
        damage, is_crit, damage_type = self.calculate_damage(
            attacker, target, base_power=1.0, is_physical=True
        )
        
        if damage_type == "MISS":
            logs.append(f"{attacker.name} menyerang {target.name} tapi meleset!")
        else:
            target.stats.hp = max(0, target.stats.hp - damage)
            crit_text = " KRITIS!" if is_crit else ""
            logs.append(f"{attacker.name} menyerang {target.name} sebesar {damage} damage{crit_text}")
            
            if not target.is_alive():
                logs.append(f"{target.name} telah dikalahkan!")
        
        self.battle_logs.extend(logs)
        return logs
    
    def execute_skill(
        self,
        attacker: CombatEntity,
        skill: Skill,
        targets: List[CombatEntity]
    ) -> List[str]:
        """Eksekusi skill"""
        logs = []
        
        # Cek MP
        if attacker.stats.mp < skill.mp_cost:
            logs.append(f"{attacker.name} tidak punya cukup MP untuk {skill.name}!")
            return logs
        
        # Cek cooldown
        if skill.id in attacker.skill_cooldowns:
            remaining = attacker.skill_cooldowns[skill.id]
            logs.append(f"{skill.name} masih cooldown ({remaining} turn lagi)!")
            return logs
        
        # Kurangi MP
        attacker.stats.mp -= skill.mp_cost
        logs.append(f"{attacker.name} menggunakan {skill.name}!")
        
        # Set cooldown
        if skill.cooldown > 0:
            attacker.skill_cooldowns[skill.id] = skill.cooldown
        
        # Aplikasikan skill ke semua target
        for target in targets:
            for _ in range(skill.hits):
                damage, is_crit, damage_type = self.calculate_damage(
                    attacker, target, skill.power, skill.is_physical, skill.element
                )
                
                if damage_type == "MISS":
                    logs.append(f"  {skill.name} meleset pada {target.name}!")
                else:
                    target.stats.hp = max(0, target.stats.hp - damage)
                    crit_text = " KRITIS!" if is_crit else ""
                    logs.append(f"  {target.name} menerima {damage} damage{crit_text}")
                    
                    # Aplikasikan status effect jika ada
                    if skill.status_effect and target.is_alive():
                        chance = skill.status_effect.get("chance", 100)
                        if random.randint(1, 100) <= chance:
                            effect = StatusEffect(
                                id=skill.status_effect["id"],
                                name=skill.status_effect.get("name", skill.status_effect["id"]),
                                duration=skill.status_effect["duration"],
                                potency=skill.status_effect["potency"]
                            )
                            effect_log = target.add_status_effect(effect)
                            if effect_log:
                                logs.append(f"  {effect_log}")
                    
                    if not target.is_alive():
                        logs.append(f"  {target.name} telah dikalahkan!")
        
        self.battle_logs.extend(logs)
        return logs
    
    def execute_defend(self, entity: CombatEntity) -> List[str]:
        """Eksekusi aksi bertahan"""
        entity.defending = True
        logs = [f"{entity.name} mengambil posisi bertahan! (DEF +50%)"]
        self.battle_logs.extend(logs)
        return logs
    
    def try_run(self, runner: CombatEntity, enemies: List[CombatEntity]) -> Tuple[bool, str]:
        """
        Coba melarikan diri dari pertempuran
        Success chance berdasarkan perbandingan speed
        """
        if not enemies:
            return True, f"{runner.name} berhasil kabur!"
        
        avg_enemy_spd = sum(e.stats.spd for e in enemies if e.is_alive()) / len([e for e in enemies if e.is_alive()])
        speed_ratio = runner.stats.spd / max(1, avg_enemy_spd)
        
        # Base 50% chance, +/- based on speed
        base_chance = 50
        speed_bonus = int((speed_ratio - 1.0) * 30)
        final_chance = max(20, min(80, base_chance + speed_bonus))
        
        success = random.randint(1, 100) <= final_chance
        
        if success:
            log = f"{runner.name} berhasil kabur dari pertempuran!"
        else:
            log = f"{runner.name} gagal kabur!"
        
        self.battle_logs.append(log)
        return success, log
    
    def check_battle_end(self, allies: List[CombatEntity], enemies: List[CombatEntity]) -> Optional[str]:
        """
        Cek apakah pertempuran sudah selesai
        Returns: "WIN", "LOSE", atau None jika masih berlanjut
        """
        allies_alive = any(a.is_alive() for a in allies)
        enemies_alive = any(e.is_alive() for e in enemies)
        
        if not allies_alive:
            return "LOSE"
        elif not enemies_alive:
            return "WIN"
        return None


def create_skill_database() -> Dict[str, Skill]:
    """Database skill yang tersedia dalam game"""
    return {
        # Physical Skills
        "SLASH": Skill(
            id="SLASH",
            name="Tebasan",
            description="Serangan fisik dasar",
            mp_cost=0,
            cooldown=0,
            power=1.0,
            is_physical=True
        ),
        "POWER_STRIKE": Skill(
            id="POWER_STRIKE",
            name="Pukulan Kuat",
            description="Serangan fisik dengan damage tinggi",
            mp_cost=8,
            cooldown=2,
            power=1.8,
            is_physical=True
        ),
        "DOUBLE_SLASH": Skill(
            id="DOUBLE_SLASH",
            name="Tebasan Ganda",
            description="Menyerang dua kali berturut-turut",
            mp_cost=10,
            cooldown=3,
            power=0.7,
            is_physical=True,
            hits=2
        ),
        "ARMOR_BREAK": Skill(
            id="ARMOR_BREAK",
            name="Hancurkan Armor",
            description="Serangan yang melemahkan pertahanan musuh",
            mp_cost=12,
            cooldown=4,
            power=1.3,
            is_physical=True,
            status_effect={
                "id": "WEAKEN",
                "name": "Lemah",
                "chance": 70,
                "duration": 3,
                "potency": 0.3
            }
        ),
        
        # Magical Skills
        "LIGHT_BURST": Skill(
            id="LIGHT_BURST",
            name="Ledakan Cahaya",
            description="Serangan sihir cahaya",
            mp_cost=10,
            cooldown=0,
            power=1.5,
            is_physical=False,
            element="CAHAYA"
        ),
        "FIRE_BALL": Skill(
            id="FIRE_BALL",
            name="Bola Api",
            description="Serangan api yang dapat membakar musuh",
            mp_cost=15,
            cooldown=2,
            power=1.6,
            is_physical=False,
            element="API",
            status_effect={
                "id": "BURN",
                "name": "Terbakar",
                "chance": 40,
                "duration": 3,
                "potency": 0.05
            }
        ),
        "POISON_DART": Skill(
            id="POISON_DART",
            name="Panah Racun",
            description="Serangan yang meracuni musuh",
            mp_cost=12,
            cooldown=3,
            power=1.0,
            is_physical=False,
            status_effect={
                "id": "POISON",
                "name": "Racun",
                "chance": 60,
                "duration": 4,
                "potency": 0.06
            }
        ),
        "STUN_BOLT": Skill(
            id="STUN_BOLT",
            name="Petir Setrum",
            description="Serangan petir yang dapat membuat musuh stun",
            mp_cost=18,
            cooldown=5,
            power=1.4,
            is_physical=False,
            element="PETIR",
            status_effect={
                "id": "STUN",
                "name": "Stun",
                "chance": 50,
                "duration": 1,
                "potency": 0.0
            }
        ),
        
        # Support Skills
        "HEAL": Skill(
            id="HEAL",
            name="Penyembuhan",
            description="Memulihkan HP sekutu",
            mp_cost=15,
            cooldown=2,
            power=-1.0,  # Negative untuk heal
            is_physical=False,
            target_type=TargetType.SINGLE_ALLY
        ),
        "REGENERATE": Skill(
            id="REGENERATE",
            name="Regenerasi",
            description="Memberikan efek regenerasi HP",
            mp_cost=20,
            cooldown=4,
            power=0.0,
            is_physical=False,
            target_type=TargetType.SINGLE_ALLY,
            status_effect={
                "id": "REGEN",
                "name": "Regenerasi",
                "chance": 100,
                "duration": 3,
                "potency": 0.08
            }
        ),
    }


def ai_choose_action(
    entity: CombatEntity,
    allies: List[CombatEntity],
    enemies: List[CombatEntity]
) -> Tuple[ActionType, Optional[Skill], List[CombatEntity]]:
    """
    AI sederhana untuk memilih aksi musuh
    Returns: (action_type, skill_or_none, targets)
    """
    # Strategi sederhana berdasarkan HP
    hp_percent = entity.stats.hp / entity.stats.max_hp
    
    # Jika HP rendah dan punya heal skill, gunakan heal
    if hp_percent < 0.3:
        heal_skills = [s for s in entity.skills if s.target_type == TargetType.SINGLE_ALLY]
        if heal_skills:
            skill = random.choice(heal_skills)
            if entity.stats.mp >= skill.mp_cost and skill.id not in entity.skill_cooldowns:
                return ActionType.SKILL, skill, [entity]
    
    # Jika MP cukup dan ada skill available, gunakan skill (50% chance)
    available_skills = [
        s for s in entity.skills
        if s.mp_cost <= entity.stats.mp
        and s.id not in entity.skill_cooldowns
        and s.target_type in [TargetType.SINGLE_ENEMY, TargetType.ALL_ENEMIES]
    ]
    
    if available_skills and random.random() < 0.5:
        skill = random.choice(available_skills)
        alive_enemies = [e for e in enemies if e.is_alive()]
        if alive_enemies:
            if skill.target_type == TargetType.ALL_ENEMIES:
                return ActionType.SKILL, skill, alive_enemies
            else:
                # Prioritas target HP tertinggi
                target = max(alive_enemies, key=lambda e: e.stats.hp)
                return ActionType.SKILL, skill, [target]
    
    # Default: serangan biasa ke target random
    alive_enemies = [e for e in enemies if e.is_alive()]
    if alive_enemies:
        target = random.choice(alive_enemies)
        return ActionType.ATTACK, None, [target]
    
    return ActionType.DEFEND, None, []

