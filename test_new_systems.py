"""
Test Suite untuk sistem baru: Combat Engine, Crafting, dan Jobs
Memastikan semua fitur utama bekerja dengan benar
"""
import sys
import time
from combat_engine import (
    ActionType,
    CombatEngine,
    CombatEntity,
    CombatStats,
    Skill,
    TargetType,
    StatusEffect,
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


def test_combat_engine():
    """Test sistem combat engine"""
    print("=== Testing Combat Engine ===")
    
    # Create combat entities
    player_stats = CombatStats(
        max_hp=100,
        hp=100,
        max_mp=50,
        mp=50,
        atk=20,
        defense=10,
        mag=15,
        spd=12,
        luck=5
    )
    
    player = CombatEntity(
        id="PLAYER",
        name="Hero",
        stats=player_stats,
        is_player=True,
        skills=[]
    )
    
    enemy_stats = CombatStats(
        max_hp=60,
        hp=60,
        max_mp=20,
        mp=20,
        atk=15,
        defense=8,
        mag=10,
        spd=8,
        luck=3
    )
    
    enemy = CombatEntity(
        id="GOBLIN",
        name="Goblin",
        stats=enemy_stats,
        is_player=False,
        skills=[]
    )
    
    # Initialize combat
    engine = CombatEngine()
    engine.initialize_battle([player], [enemy])
    
    print(f"  Turn order: {[e.name for e in engine.turn_order]}")
    assert len(engine.turn_order) == 2, "Turn order harus ada 2 entity"
    
    # Test attack
    print("  Testing attack...")
    current = engine.get_current_entity()
    print(f"  Current turn: {current.name}")
    
    # Execute attack
    if current == player:
        logs = engine.execute_attack(player, enemy)
        print(f"  Attack logs: {logs}")
        assert enemy.stats.hp < 60, "Enemy harus kehilangan HP"
    
    # Test status effects
    print("  Testing status effects...")
    poison = StatusEffect(
        id="POISON",
        name="Racun",
        duration=3,
        potency=0.1
    )
    
    log = enemy.add_status_effect(poison)
    print(f"  Status effect log: {log}")
    assert len(enemy.status_effects) == 1, "Enemy harus punya 1 status effect"
    
    # Tick status effect
    tick_logs = enemy.tick_status_effects()
    print(f"  Tick logs: {tick_logs}")
    assert enemy.status_effects[0].duration == 2, "Duration harus berkurang"
    
    print("  Combat Engine: PASSED\n")


def test_crafting_system():
    """Test sistem crafting"""
    print("=== Testing Crafting System ===")
    
    crafting = CraftingSystem()
    
    # Test material database
    print("  Testing material database...")
    assert "WOOD_SCRAP" in crafting.materials, "Material WOOD_SCRAP harus ada"
    assert "IRON_ORE" in crafting.materials, "Material IRON_ORE harus ada"
    print(f"  Total materials: {len(crafting.materials)}")
    
    # Test recipe database
    print("  Testing recipe database...")
    assert "CRAFT_WOODEN_SWORD" in crafting.recipes, "Recipe pedang kayu harus ada"
    assert "CRAFT_POTION_SMALL" in crafting.recipes, "Recipe potion kecil harus ada"
    print(f"  Total recipes: {len(crafting.recipes)}")
    
    # Test crafting validation
    print("  Testing crafting validation...")
    player_materials = {
        "WOOD_SCRAP": 10,
        "IRON_ORE": 5
    }
    player_gold = 100
    
    can_craft, error = crafting.can_craft(
        "CRAFT_WOODEN_SWORD",
        player_materials,
        player_gold,
        "SIAK"
    )
    print(f"  Can craft wooden sword: {can_craft}")
    assert can_craft, f"Harus bisa craft pedang kayu: {error}"
    
    # Test crafting execution
    print("  Testing crafting execution...")
    success, updated_materials, updated_gold, message = crafting.craft_item(
        "CRAFT_WOODEN_SWORD",
        player_materials,
        player_gold
    )
    print(f"  Craft result: {message}")
    assert success, "Crafting harus berhasil"
    assert updated_materials["WOOD_SCRAP"] == 5, "Material harus berkurang"
    assert updated_gold == 80, "Gold harus berkurang"
    
    # Test material drops
    print("  Testing material drops...")
    drops = generate_material_drops(monster_level=5, monster_rarity="COMMON")
    print(f"  Generated {len(drops)} drop configs")
    assert len(drops) > 0, "Harus ada material drops"
    
    rolled = roll_material_drops(drops)
    print(f"  Rolled materials: {rolled}")
    
    print("  Crafting System: PASSED\n")


def test_jobs_system():
    """Test sistem pekerjaan"""
    print("=== Testing Jobs System ===")
    
    job_system = JobSystem()
    
    # Test job database
    print("  Testing job database...")
    assert "WARRIOR" in job_system.jobs, "Job WARRIOR harus ada"
    assert "GUARD" in job_system.jobs, "Job GUARD harus ada"
    assert "MAGIC_TEACHER" in job_system.jobs, "Job MAGIC_TEACHER harus ada"
    print(f"  Total jobs: {len(job_system.jobs)}")
    
    # Test energy system
    print("  Testing energy system...")
    energy = EnergySystem(max_energy=100, current_energy=50)
    assert energy.current_energy == 50, "Energy harus 50"
    
    success = energy.consume_energy(10)
    assert success, "Harus bisa konsumsi energy"
    assert energy.current_energy == 40, "Energy harus berkurang jadi 40"
    
    # Test taking job
    print("  Testing job taking...")
    player_stats = {"level": 1, "atk": 10, "defense": 10, "mag": 10}
    can_take, error = job_system.can_start_job("WARRIOR", player_stats, None)
    print(f"  Can take WARRIOR job: {can_take}")
    assert can_take, f"Harus bisa ambil job WARRIOR: {error}"
    
    # Test work session
    print("  Testing work session...")
    success, work_session, message = job_system.start_work(
        "WARRIOR",
        5,  # 5 energy = 5 minutes
        energy
    )
    print(f"  Start work result: {message}")
    assert success, "Harus bisa mulai bekerja"
    assert work_session is not None, "Work session harus dibuat"
    assert work_session.energy_spent == 5, "Energy spent harus 5"
    
    # Test work completion
    print("  Testing work completion...")
    # Force completion untuk testing
    work_session.is_complete = True
    job_progress = JobProgress(
        job_id="WARRIOR",
        level=1,
        exp=0,
        total_time_worked=0
    )
    
    gold, exp, updated_progress, logs = job_system.complete_work(
        work_session,
        job_progress
    )
    print(f"  Work completion: {gold} gold, {exp} exp")
    assert gold > 0, "Harus dapat gold"
    assert exp > 0, "Harus dapat exp"
    assert updated_progress.total_time_worked == 5, "Total time worked harus 5"
    
    # Test stat bonuses
    print("  Testing stat bonuses...")
    bonuses = job_system.get_total_stat_bonus("WARRIOR", 10)
    print(f"  Level 10 bonuses: {bonuses}")
    assert "atk" in bonuses, "Harus ada bonus ATK"
    assert "spd" in bonuses, "Harus ada bonus SPD"
    assert bonuses["atk"] > 0, "Bonus ATK harus > 0"
    
    # Test time formatting
    print("  Testing time formatting...")
    time_str = format_time_remaining(3665)
    print(f"  3665 seconds = {time_str}")
    assert "jam" in time_str or "menit" in time_str, "Format waktu harus benar"
    
    print("  Jobs System: PASSED\n")


def test_integration():
    """Test integrasi antar sistem"""
    print("=== Testing Integration ===")
    
    # Scenario: Player berburu, dapat material, craft equipment, ambil job
    
    # 1. Battle dan material drops
    print("  1. Simulating battle and material drops...")
    drops_config = generate_material_drops(monster_level=3, monster_rarity="COMMON")
    materials = roll_material_drops(drops_config)
    print(f"     Got materials: {materials}")
    
    # 2. Coba crafting dengan materials
    print("  2. Attempting to craft with materials...")
    crafting = CraftingSystem()
    
    # Tambahkan material yang dibutuhkan
    test_materials = {
        "WOOD_SCRAP": 10,
        "IRON_ORE": 5,
        "HEALING_HERB": 10,
        "SLIME_GEL": 10
    }
    test_gold = 100
    
    # Craft potion
    can_craft, error = crafting.can_craft(
        "CRAFT_POTION_SMALL",
        test_materials,
        test_gold,
        "SIAK"
    )
    print(f"     Can craft potion: {can_craft}")
    
    if can_craft:
        success, updated_mat, updated_gold, msg = crafting.craft_item(
            "CRAFT_POTION_SMALL",
            test_materials,
            test_gold
        )
        print(f"     Craft result: {msg}")
        print(f"     Remaining materials: {updated_mat}")
    
    # 3. Ambil job dan kerja
    print("  3. Taking job and working...")
    job_system = JobSystem()
    energy = EnergySystem(current_energy=20)
    
    player_stats = {"level": 5, "atk": 15, "defense": 12, "mag": 10}
    can_take, _ = job_system.can_start_job("WARRIOR", player_stats, None)
    
    if can_take:
        success, work_session, msg = job_system.start_work("WARRIOR", 10, energy)
        print(f"     Work started: {msg}")
        print(f"     Remaining energy: {energy.current_energy}")
        
        # Simulate completion
        work_session.is_complete = True
        job_prog = JobProgress("WARRIOR", 1, 0, 0)
        gold, exp, prog, logs = job_system.complete_work(work_session, job_prog)
        print(f"     Work completed: +{gold} gold, +{exp} exp")
        print(f"     Job level: {prog.level}, exp: {prog.exp}")
    
    print("  Integration: PASSED\n")


def run_all_tests():
    """Jalankan semua test"""
    print("\n" + "="*60)
    print("RUNNING ALL TESTS")
    print("="*60 + "\n")
    
    try:
        test_combat_engine()
        test_crafting_system()
        test_jobs_system()
        test_integration()
        
        print("="*60)
        print("ALL TESTS PASSED!")
        print("="*60)
        return True
    except AssertionError as e:
        print(f"\n!!! TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"\n!!! UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

