"""
Crafting System - Material & Recipe Management
Sistem crafting dengan material drop, recipes, dan city-tier restrictions
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class ItemRarity(Enum):
    """Tingkat kelangkaan item dan material"""
    COMMON = "COMMON"
    UNCOMMON = "UNCOMMON"
    RARE = "RARE"
    EPIC = "EPIC"
    LEGENDARY = "LEGENDARY"


class MaterialType(Enum):
    """Jenis material"""
    ORE = "ORE"  # Bijih logam
    HERB = "HERB"  # Tanaman/herbal
    MONSTER_PART = "MONSTER_PART"  # Bagian monster
    ESSENCE = "ESSENCE"  # Esensi magic
    CRYSTAL = "CRYSTAL"  # Kristal


@dataclass
class Material:
    """Definisi material untuk crafting"""
    id: str
    name: str
    description: str
    type: MaterialType
    rarity: ItemRarity
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "type": self.type.value,
            "rarity": self.rarity.value
        }


@dataclass
class Recipe:
    """Resep crafting"""
    id: str
    name: str
    description: str
    result_item_id: str
    result_quantity: int
    materials: Dict[str, int]  # material_id -> quantity
    min_city_tier: int  # Tier kota minimum (1=Selatpanjang, 2=Siak, 3=Rengat, 4=Pekanbaru, 5=Kampar)
    rarity: ItemRarity
    gold_cost: int = 0  # Biaya tambahan untuk craft
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "result_item_id": self.result_item_id,
            "result_quantity": self.result_quantity,
            "materials": self.materials,
            "min_city_tier": self.min_city_tier,
            "rarity": self.rarity.value,
            "gold_cost": self.gold_cost
        }


@dataclass
class MaterialDrop:
    """Konfigurasi drop material dari monster"""
    material_id: str
    chance: float  # 0.0 - 1.0
    min_quantity: int
    max_quantity: int


class CraftingSystem:
    """Sistem utama untuk crafting"""
    
    def __init__(self):
        self.materials = create_material_database()
        self.recipes = create_recipe_database()
        self.city_tiers = {
            "SELATPANJANG": 1,
            "SIAK": 2,
            "RENGAT": 3,
            "PEKANBARU": 4,
            "KAMPAR": 5
        }
    
    def can_craft(
        self,
        recipe_id: str,
        player_materials: Dict[str, int],
        player_gold: int,
        current_city: str
    ) -> Tuple[bool, str]:
        """
        Cek apakah player bisa craft item
        Returns: (can_craft, error_message)
        """
        recipe = self.recipes.get(recipe_id)
        if not recipe:
            return False, "Resep tidak ditemukan"
        
        # Cek apakah di kota
        if current_city not in self.city_tiers:
            return False, "Crafting hanya bisa dilakukan di kota"
        
        # Cek tier kota
        city_tier = self.city_tiers.get(current_city, 0)
        if city_tier < recipe.min_city_tier:
            required_city = self._get_city_name_by_tier(recipe.min_city_tier)
            return False, f"Resep ini membutuhkan akses ke {required_city} atau lebih tinggi"
        
        # Cek material
        for mat_id, required_qty in recipe.materials.items():
            player_qty = player_materials.get(mat_id, 0)
            if player_qty < required_qty:
                mat = self.materials.get(mat_id)
                mat_name = mat.name if mat else mat_id
                return False, f"Material tidak cukup: {mat_name} ({player_qty}/{required_qty})"
        
        # Cek gold
        if player_gold < recipe.gold_cost:
            return False, f"Gold tidak cukup: {player_gold}/{recipe.gold_cost}"
        
        return True, ""
    
    def craft_item(
        self,
        recipe_id: str,
        player_materials: Dict[str, int],
        player_gold: int
    ) -> Tuple[bool, Dict[str, int], int, str]:
        """
        Craft item dan kembalikan material/gold yang digunakan
        Returns: (success, updated_materials, updated_gold, message)
        """
        recipe = self.recipes.get(recipe_id)
        if not recipe:
            return False, player_materials, player_gold, "Resep tidak ditemukan"
        
        # Konsumsi material
        updated_materials = dict(player_materials)
        for mat_id, qty in recipe.materials.items():
            updated_materials[mat_id] = updated_materials.get(mat_id, 0) - qty
            if updated_materials[mat_id] <= 0:
                updated_materials.pop(mat_id, None)
        
        # Konsumsi gold
        updated_gold = player_gold - recipe.gold_cost
        
        message = f"Berhasil membuat {recipe.name}!"
        return True, updated_materials, updated_gold, message
    
    def get_recipes_for_city(self, city: str) -> List[Recipe]:
        """Dapatkan semua resep yang tersedia di kota tertentu"""
        city_tier = self.city_tiers.get(city, 0)
        return [r for r in self.recipes.values() if r.min_city_tier <= city_tier]
    
    def _get_city_name_by_tier(self, tier: int) -> str:
        """Helper untuk mendapatkan nama kota berdasarkan tier"""
        tier_to_city = {
            1: "Selatpanjang",
            2: "Siak",
            3: "Rengat",
            4: "Pekanbaru",
            5: "Kampar"
        }
        return tier_to_city.get(tier, "Unknown")


def create_material_database() -> Dict[str, Material]:
    """Database semua material dalam game"""
    return {
        # COMMON Materials
        "WOOD_SCRAP": Material(
            id="WOOD_SCRAP",
            name="Pecahan Kayu",
            description="Serpihan kayu dari monster hutan",
            type=MaterialType.MONSTER_PART,
            rarity=ItemRarity.COMMON
        ),
        "SLIME_GEL": Material(
            id="SLIME_GEL",
            name="Gel Slime",
            description="Gel kenyal dari slime",
            type=MaterialType.MONSTER_PART,
            rarity=ItemRarity.COMMON
        ),
        "WOLF_FANG": Material(
            id="WOLF_FANG",
            name="Taring Serigala",
            description="Taring tajam dari serigala",
            type=MaterialType.MONSTER_PART,
            rarity=ItemRarity.COMMON
        ),
        "IRON_ORE": Material(
            id="IRON_ORE",
            name="Bijih Besi",
            description="Bijih besi mentah",
            type=MaterialType.ORE,
            rarity=ItemRarity.COMMON
        ),
        "HEALING_HERB": Material(
            id="HEALING_HERB",
            name="Herbal Penyembuh",
            description="Tanaman dengan khasiat penyembuhan",
            type=MaterialType.HERB,
            rarity=ItemRarity.COMMON
        ),
        
        # UNCOMMON Materials
        "BRONZE_INGOT": Material(
            id="BRONZE_INGOT",
            name="Batangan Perunggu",
            description="Perunggu yang telah ditempa",
            type=MaterialType.ORE,
            rarity=ItemRarity.UNCOMMON
        ),
        "GOLEM_CORE": Material(
            id="GOLEM_CORE",
            name="Inti Golem",
            description="Inti energi dari golem",
            type=MaterialType.MONSTER_PART,
            rarity=ItemRarity.UNCOMMON
        ),
        "SHADOW_ESSENCE": Material(
            id="SHADOW_ESSENCE",
            name="Esensi Bayangan",
            description="Esensi gelap yang terkonsentrasi",
            type=MaterialType.ESSENCE,
            rarity=ItemRarity.UNCOMMON
        ),
        "MAGIC_THREAD": Material(
            id="MAGIC_THREAD",
            name="Benang Sihir",
            description="Benang yang ditenun dengan sihir",
            type=MaterialType.ESSENCE,
            rarity=ItemRarity.UNCOMMON
        ),
        
        # RARE Materials
        "STEEL_INGOT": Material(
            id="STEEL_INGOT",
            name="Batangan Baja",
            description="Baja berkualitas tinggi",
            type=MaterialType.ORE,
            rarity=ItemRarity.RARE
        ),
        "MANA_CRYSTAL": Material(
            id="MANA_CRYSTAL",
            name="Kristal Mana",
            description="Kristal yang mengandung mana murni",
            type=MaterialType.CRYSTAL,
            rarity=ItemRarity.RARE
        ),
        "DRAGON_SCALE": Material(
            id="DRAGON_SCALE",
            name="Sisik Naga",
            description="Sisik keras dari makhluk naga",
            type=MaterialType.MONSTER_PART,
            rarity=ItemRarity.RARE
        ),
        "LIGHT_ESSENCE": Material(
            id="LIGHT_ESSENCE",
            name="Esensi Cahaya",
            description="Esensi cahaya murni",
            type=MaterialType.ESSENCE,
            rarity=ItemRarity.RARE
        ),
        
        # EPIC Materials
        "MITHRIL_ORE": Material(
            id="MITHRIL_ORE",
            name="Bijih Mithril",
            description="Logam langka yang sangat kuat dan ringan",
            type=MaterialType.ORE,
            rarity=ItemRarity.EPIC
        ),
        "VOID_FRAGMENT": Material(
            id="VOID_FRAGMENT",
            name="Fragmen Kehampaan",
            description="Pecahan dari dimensi void",
            type=MaterialType.ESSENCE,
            rarity=ItemRarity.EPIC
        ),
        "PHOENIX_FEATHER": Material(
            id="PHOENIX_FEATHER",
            name="Bulu Phoenix",
            description="Bulu yang bercahaya dengan api abadi",
            type=MaterialType.MONSTER_PART,
            rarity=ItemRarity.EPIC
        ),
        
        # LEGENDARY Materials
        "ADAMANTITE": Material(
            id="ADAMANTITE",
            name="Adamantit",
            description="Logam terkuat yang diketahui",
            type=MaterialType.ORE,
            rarity=ItemRarity.LEGENDARY
        ),
        "DIVINE_CRYSTAL": Material(
            id="DIVINE_CRYSTAL",
            name="Kristal Ilahi",
            description="Kristal dengan kekuatan dewa",
            type=MaterialType.CRYSTAL,
            rarity=ItemRarity.LEGENDARY
        ),
    }


def create_recipe_database() -> Dict[str, Recipe]:
    """Database semua resep crafting"""
    return {
        # COMMON Recipes - Tier 1-2 (Selatpanjang, Siak)
        "CRAFT_WOODEN_SWORD": Recipe(
            id="CRAFT_WOODEN_SWORD",
            name="Pedang Kayu",
            description="Pedang latihan dari kayu",
            result_item_id="WOODEN_SWORD",
            result_quantity=1,
            materials={"WOOD_SCRAP": 5, "IRON_ORE": 2},
            min_city_tier=1,
            rarity=ItemRarity.COMMON,
            gold_cost=20
        ),
        "CRAFT_POTION_SMALL": Recipe(
            id="CRAFT_POTION_SMALL",
            name="Potion Kecil",
            description="Ramuan penyembuh dasar",
            result_item_id="POTION_SMALL",
            result_quantity=2,
            materials={"HEALING_HERB": 3, "SLIME_GEL": 2},
            min_city_tier=1,
            rarity=ItemRarity.COMMON,
            gold_cost=10
        ),
        "CRAFT_LEATHER_ARMOR": Recipe(
            id="CRAFT_LEATHER_ARMOR",
            name="Baju Kulit",
            description="Armor kulit ringan",
            result_item_id="LEATHER_ARMOR",
            result_quantity=1,
            materials={"WOLF_FANG": 4, "SLIME_GEL": 3},
            min_city_tier=2,
            rarity=ItemRarity.COMMON,
            gold_cost=30
        ),
        
        # UNCOMMON Recipes - Tier 2-3 (Siak, Rengat)
        "CRAFT_BRONZE_SWORD": Recipe(
            id="CRAFT_BRONZE_SWORD",
            name="Pedang Perunggu",
            description="Pedang dari perunggu berkualitas",
            result_item_id="BRONZE_SWORD",
            result_quantity=1,
            materials={"BRONZE_INGOT": 3, "WOLF_FANG": 5, "IRON_ORE": 4},
            min_city_tier=2,
            rarity=ItemRarity.UNCOMMON,
            gold_cost=80
        ),
        "CRAFT_CHAIN_ARMOR": Recipe(
            id="CRAFT_CHAIN_ARMOR",
            name="Zirah Rantai",
            description="Armor rantai besi",
            result_item_id="CHAIN_ARMOR",
            result_quantity=1,
            materials={"BRONZE_INGOT": 4, "IRON_ORE": 6, "MAGIC_THREAD": 2},
            min_city_tier=2,
            rarity=ItemRarity.UNCOMMON,
            gold_cost=100
        ),
        "CRAFT_LIGHT_ROBE": Recipe(
            id="CRAFT_LIGHT_ROBE",
            name="Jubah Ringan",
            description="Jubah untuk pengguna sihir",
            result_item_id="LIGHT_ROBE",
            result_quantity=1,
            materials={"MAGIC_THREAD": 5, "HEALING_HERB": 4, "SHADOW_ESSENCE": 2},
            min_city_tier=3,
            rarity=ItemRarity.UNCOMMON,
            gold_cost=90
        ),
        "CRAFT_POTION_MEDIUM": Recipe(
            id="CRAFT_POTION_MEDIUM",
            name="Potion Sedang",
            description="Ramuan penyembuh tingkat menengah",
            result_item_id="POTION_MEDIUM",
            result_quantity=2,
            materials={"HEALING_HERB": 6, "GOLEM_CORE": 1, "SLIME_GEL": 4},
            min_city_tier=3,
            rarity=ItemRarity.UNCOMMON,
            gold_cost=40
        ),
        
        # RARE Recipes - Tier 3-4 (Rengat, Pekanbaru)
        "CRAFT_STEEL_SWORD": Recipe(
            id="CRAFT_STEEL_SWORD",
            name="Pedang Baja",
            description="Pedang baja yang tajam",
            result_item_id="STEEL_SWORD",
            result_quantity=1,
            materials={"STEEL_INGOT": 5, "GOLEM_CORE": 2, "WOLF_FANG": 8},
            min_city_tier=3,
            rarity=ItemRarity.RARE,
            gold_cost=250
        ),
        "CRAFT_MYSTIC_CLOAK": Recipe(
            id="CRAFT_MYSTIC_CLOAK",
            name="Jubah Mistik",
            description="Jubah dengan perlindungan magis",
            result_item_id="MYSTIC_CLOAK",
            result_quantity=1,
            materials={"MAGIC_THREAD": 8, "MANA_CRYSTAL": 3, "LIGHT_ESSENCE": 2},
            min_city_tier=4,
            rarity=ItemRarity.RARE,
            gold_cost=300
        ),
        "CRAFT_GUARDIAN_PLATE": Recipe(
            id="CRAFT_GUARDIAN_PLATE",
            name="Armor Pelindung",
            description="Armor berat untuk pertahanan maksimal",
            result_item_id="GUARDIAN_PLATE",
            result_quantity=1,
            materials={"STEEL_INGOT": 7, "DRAGON_SCALE": 4, "GOLEM_CORE": 3},
            min_city_tier=4,
            rarity=ItemRarity.RARE,
            gold_cost=350
        ),
        
        # EPIC Recipes - Tier 4-5 (Pekanbaru, Kampar)
        "CRAFT_MITHRIL_BLADE": Recipe(
            id="CRAFT_MITHRIL_BLADE",
            name="Pedang Mithril",
            description="Pedang legendaris dari mithril",
            result_item_id="MITHRIL_BLADE",
            result_quantity=1,
            materials={"MITHRIL_ORE": 6, "LIGHT_ESSENCE": 4, "DRAGON_SCALE": 5, "MANA_CRYSTAL": 3},
            min_city_tier=4,
            rarity=ItemRarity.EPIC,
            gold_cost=800
        ),
        "CRAFT_VOID_ARMOR": Recipe(
            id="CRAFT_VOID_ARMOR",
            name="Armor Kehampaan",
            description="Armor yang diperkuat energi void",
            result_item_id="VOID_ARMOR",
            result_quantity=1,
            materials={"VOID_FRAGMENT": 5, "STEEL_INGOT": 8, "SHADOW_ESSENCE": 6},
            min_city_tier=5,
            rarity=ItemRarity.EPIC,
            gold_cost=1000
        ),
    }


def generate_material_drops(monster_level: int, monster_rarity: str = "COMMON") -> List[MaterialDrop]:
    """
    Generate material drops berdasarkan level dan rarity monster
    Monster level lebih tinggi = chance material lebih bagus
    """
    drops = []
    
    # Common materials - selalu ada chance
    if monster_level <= 3:
        drops.extend([
            MaterialDrop("WOOD_SCRAP", 0.6, 1, 2),
            MaterialDrop("SLIME_GEL", 0.5, 1, 3),
            MaterialDrop("HEALING_HERB", 0.4, 1, 2),
        ])
    
    if monster_level >= 2:
        drops.extend([
            MaterialDrop("WOLF_FANG", 0.5, 1, 2),
            MaterialDrop("IRON_ORE", 0.4, 1, 3),
        ])
    
    # Uncommon materials - level 4+
    if monster_level >= 4:
        drops.extend([
            MaterialDrop("BRONZE_INGOT", 0.3, 1, 2),
            MaterialDrop("SHADOW_ESSENCE", 0.25, 1, 1),
            MaterialDrop("MAGIC_THREAD", 0.3, 1, 2),
        ])
    
    # Rare materials - level 7+
    if monster_level >= 7:
        drops.extend([
            MaterialDrop("GOLEM_CORE", 0.25, 1, 1),
            MaterialDrop("STEEL_INGOT", 0.2, 1, 2),
            MaterialDrop("MANA_CRYSTAL", 0.15, 1, 1),
        ])
    
    # Epic materials - level 10+
    if monster_level >= 10:
        drops.extend([
            MaterialDrop("DRAGON_SCALE", 0.2, 1, 1),
            MaterialDrop("LIGHT_ESSENCE", 0.15, 1, 1),
            MaterialDrop("MITHRIL_ORE", 0.1, 1, 1),
        ])
    
    # Legendary materials - level 13+ atau boss
    if monster_level >= 13 or monster_rarity in ["EPIC", "LEGENDARY"]:
        drops.extend([
            MaterialDrop("VOID_FRAGMENT", 0.15, 1, 1),
            MaterialDrop("PHOENIX_FEATHER", 0.08, 1, 1),
        ])
    
    # Rarity multiplier
    rarity_multipliers = {
        "COMMON": 1.0,
        "UNCOMMON": 1.3,
        "RARE": 1.6,
        "EPIC": 2.0,
        "LEGENDARY": 2.5
    }
    multiplier = rarity_multipliers.get(monster_rarity, 1.0)
    
    # Apply multiplier to quantity
    for drop in drops:
        drop.max_quantity = int(drop.max_quantity * multiplier)
    
    return drops


def roll_material_drops(drops: List[MaterialDrop]) -> Dict[str, int]:
    """
    Roll untuk mendapatkan material dari list drops
    Returns: dict material_id -> quantity
    """
    import random
    
    result = {}
    for drop in drops:
        if random.random() <= drop.chance:
            qty = random.randint(drop.min_quantity, drop.max_quantity)
            result[drop.material_id] = result.get(drop.material_id, 0) + qty
    
    return result

