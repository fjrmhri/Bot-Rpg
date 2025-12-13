"""
Combat UI - Enhanced Battle Display dengan HP Bars
UI pertempuran yang ditingkatkan dengan HP bar ASCII
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def create_hp_bar(current: int, maximum: int, length: int = 10) -> str:
    """
    Buat HP bar ASCII
    Contoh: HP: 65/100 [██████▒▒▒▒]
    """
    if maximum <= 0:
        return f"HP: 0/{maximum} [{'▒' * length}]"
    
    percentage = current / maximum
    filled = int(percentage * length)
    empty = length - filled
    
    bar = '█' * filled + '▒' * empty
    return f"HP: {current}/{maximum} [{bar}]"


def create_mp_bar(current: int, maximum: int, length: int = 10) -> str:
    """
    Buat MP bar ASCII
    Contoh: MP: 35/50 [███████▒▒▒]
    """
    if maximum <= 0:
        return f"MP: 0/{maximum} [{'▒' * length}]"
    
    percentage = current / maximum
    filled = int(percentage * length)
    empty = length - filled
    
    bar = '█' * filled + '▒' * empty
    return f"MP: {current}/{maximum} [{bar}]"


def format_battle_header(turn_number: int, current_actor: str) -> str:
    """Format header pertempuran"""
    return f"=== PERTEMPURAN (Turn {turn_number}) ===\nGiliran: {current_actor}\n"


def format_party_status(party_members: List[Dict[str, Any]]) -> str:
    """
    Format status party dengan HP/MP bars
    Input: list of dicts dengan keys: name, hp, max_hp, mp, max_mp, status_effects
    """
    lines = ["--- PARTY ---"]
    
    for member in party_members:
        name = member.get("name", "Unknown")
        hp = member.get("hp", 0)
        max_hp = member.get("max_hp", 1)
        mp = member.get("mp", 0)
        max_mp = member.get("max_mp", 1)
        status_effects = member.get("status_effects", [])
        
        hp_bar = create_hp_bar(hp, max_hp, length=10)
        mp_bar = create_mp_bar(mp, max_mp, length=10)
        
        lines.append(f"\n{name}")
        lines.append(f"  {hp_bar}")
        lines.append(f"  {mp_bar}")
        
        if status_effects:
            status_str = ", ".join([f"{s['name']}({s['duration']})" for s in status_effects])
            lines.append(f"  Status: {status_str}")
    
    return "\n".join(lines)


def format_enemy_status(enemies: List[Dict[str, Any]]) -> str:
    """
    Format status musuh dengan HP bars
    Input: list of dicts dengan keys: name, hp, max_hp, status_effects
    """
    lines = ["--- MUSUH ---"]
    
    for idx, enemy in enumerate(enemies):
        name = enemy.get("name", "Unknown")
        hp = enemy.get("hp", 0)
        max_hp = enemy.get("max_hp", 1)
        status_effects = enemy.get("status_effects", [])
        
        hp_bar = create_hp_bar(hp, max_hp, length=10)
        
        lines.append(f"\n[{idx+1}] {name}")
        lines.append(f"  {hp_bar}")
        
        if status_effects:
            status_str = ", ".join([f"{s['name']}({s['duration']})" for s in status_effects])
            lines.append(f"  Status: {status_str}")
    
    return "\n".join(lines)


def format_battle_state(
    turn_number: int,
    current_actor: str,
    party_members: List[Dict[str, Any]],
    enemies: List[Dict[str, Any]],
    action_log: Optional[List[str]] = None
) -> str:
    """
    Format keseluruhan state battle dengan semua informasi
    """
    sections = []
    
    # Header
    sections.append(format_battle_header(turn_number, current_actor))
    
    # Party status
    sections.append(format_party_status(party_members))
    
    # Enemy status
    sections.append(format_enemy_status(enemies))
    
    # Action log (3 baris terakhir)
    if action_log:
        sections.append("\n--- LOG ---")
        recent_logs = action_log[-3:] if len(action_log) > 3 else action_log
        for log in recent_logs:
            sections.append(f"  {log}")
    
    return "\n".join(sections)


def format_action_menu(actor_name: str, available_skills: List[str]) -> str:
    """Format menu aksi untuk player"""
    lines = [f"\n{actor_name}, pilih aksi:"]
    lines.append("1. Serang - Serangan dasar")
    lines.append("2. Skill - Gunakan skill khusus")
    lines.append("3. Item - Gunakan item")
    lines.append("4. Bertahan - Pertahanan +50%")
    lines.append("5. Kabur - Coba melarikan diri")
    
    if available_skills:
        lines.append(f"\nSkill tersedia: {', '.join(available_skills)}")
    
    return "\n".join(lines)


def format_skill_list(skills: List[Dict[str, Any]]) -> str:
    """
    Format daftar skill untuk ditampilkan
    Input: list of dicts dengan keys: name, mp_cost, cooldown, description
    """
    lines = ["Skill yang bisa digunakan:"]
    
    for idx, skill in enumerate(skills):
        name = skill.get("name", "Unknown")
        mp_cost = skill.get("mp_cost", 0)
        cooldown = skill.get("cooldown", 0)
        description = skill.get("description", "")
        on_cooldown = skill.get("on_cooldown", 0)
        
        status = ""
        if on_cooldown > 0:
            status = f" (Cooldown: {on_cooldown} turn)"
        
        lines.append(f"\n{idx+1}. {name} (MP: {mp_cost}){status}")
        lines.append(f"   {description}")
    
    return "\n".join(lines)


def format_victory_message(
    gold_earned: int,
    exp_earned: int,
    materials_earned: Dict[str, int],
    items_earned: Dict[str, int]
) -> str:
    """Format pesan kemenangan dengan reward"""
    lines = ["=== KEMENANGAN ==="]
    lines.append(f"\nMendapat {gold_earned} gold")
    lines.append(f"Mendapat {exp_earned} EXP")
    
    if materials_earned:
        lines.append("\nMaterial yang didapat:")
        for mat_id, qty in materials_earned.items():
            lines.append(f"  - {mat_id} x{qty}")
    
    if items_earned:
        lines.append("\nItem yang didapat:")
        for item_id, qty in items_earned.items():
            lines.append(f"  - {item_id} x{qty}")
    
    return "\n".join(lines)


def format_defeat_message() -> str:
    """Format pesan kekalahan"""
    return "=== KEKALAHAN ===\nParty kamu telah dikalahkan...\nKamu kembali ke kota terdekat."


def format_city_menu(
    city_name: str,
    player_gold: int,
    has_shop: bool,
    has_inn: bool,
    has_crafting: bool,
    has_jobs: bool
) -> str:
    """Format menu kota"""
    lines = [f"=== {city_name.upper()} ==="]
    lines.append(f"Gold: {player_gold}")
    lines.append("\nApa yang ingin kamu lakukan?")
    
    options = []
    if has_shop:
        options.append("1. Toko - Beli/jual item")
    if has_inn:
        options.append("2. Penginapan - Istirahat dan pulihkan HP/MP")
    if has_crafting:
        options.append("3. Bengkel - Craft equipment")
    if has_jobs:
        options.append("4. Guild Pekerjaan - Ambil pekerjaan")
    
    options.append("5. Status - Lihat status party")
    options.append("6. Keluar Kota - Pergi berburu")
    
    lines.extend(options)
    
    return "\n".join(lines)


def format_crafting_menu(
    recipes: List[Dict[str, Any]],
    player_materials: Dict[str, int],
    player_gold: int
) -> str:
    """
    Format menu crafting
    Input recipes: list of dicts dengan keys: name, materials (dict), gold_cost, description
    """
    lines = ["=== BENGKEL CRAFTING ==="]
    lines.append(f"Gold: {player_gold}\n")
    
    if not recipes:
        lines.append("Tidak ada resep yang tersedia di kota ini.")
        return "\n".join(lines)
    
    lines.append("Resep yang tersedia:")
    
    for idx, recipe in enumerate(recipes):
        name = recipe.get("name", "Unknown")
        materials = recipe.get("materials", {})
        gold_cost = recipe.get("gold_cost", 0)
        description = recipe.get("description", "")
        
        lines.append(f"\n{idx+1}. {name}")
        lines.append(f"   {description}")
        lines.append(f"   Biaya: {gold_cost} gold")
        lines.append("   Material:")
        
        for mat_id, required_qty in materials.items():
            player_qty = player_materials.get(mat_id, 0)
            status = "OK" if player_qty >= required_qty else "KURANG"
            lines.append(f"     - {mat_id}: {player_qty}/{required_qty} [{status}]")
    
    return "\n".join(lines)


def format_job_menu(
    available_jobs: List[Dict[str, Any]],
    current_job: Optional[Dict[str, Any]],
    energy: int,
    max_energy: int
) -> str:
    """
    Format menu pekerjaan
    Input jobs: list of dicts dengan keys: name, description, requirements, stat_growth
    current_job: dict dengan keys: name, level, exp
    """
    lines = ["=== GUILD PEKERJAAN ==="]
    lines.append(f"Energy: {energy}/{max_energy}\n")
    
    if current_job:
        lines.append(f"Pekerjaan saat ini: {current_job['name']} (Level {current_job['level']})")
        lines.append(f"EXP: {current_job['exp']}/{current_job['next_level_exp']}\n")
        lines.append("1. Mulai Bekerja")
        lines.append("2. Keluar dari Pekerjaan")
    else:
        lines.append("Kamu belum memiliki pekerjaan.\n")
        lines.append("Pekerjaan yang tersedia:")
        
        for idx, job in enumerate(available_jobs):
            name = job.get("name", "Unknown")
            description = job.get("description", "")
            requirements = job.get("requirements", {})
            stat_growth = job.get("stat_growth", {})
            
            lines.append(f"\n{idx+1}. {name}")
            lines.append(f"   {description}")
            
            if requirements:
                req_str = ", ".join([f"{k}: {v}" for k, v in requirements.items()])
                lines.append(f"   Persyaratan: {req_str}")
            
            if stat_growth:
                growth_str = ", ".join([f"+{v} {k}/level" for k, v in stat_growth.items()])
                lines.append(f"   Pertumbuhan: {growth_str}")
    
    return "\n".join(lines)


def format_work_progress(
    job_name: str,
    time_remaining_seconds: int,
    energy_spent: int
) -> str:
    """Format progress pekerjaan yang sedang berlangsung"""
    hours = time_remaining_seconds // 3600
    minutes = (time_remaining_seconds % 3600) // 60
    seconds = time_remaining_seconds % 60
    
    time_str = ""
    if hours > 0:
        time_str = f"{hours}j {minutes}m {seconds}d"
    elif minutes > 0:
        time_str = f"{minutes}m {seconds}d"
    else:
        time_str = f"{seconds}d"
    
    lines = [f"=== SEDANG BEKERJA: {job_name} ==="]
    lines.append(f"Energy digunakan: {energy_spent}")
    lines.append(f"Waktu tersisa: {time_str}")
    lines.append("\nKamu tidak bisa melakukan aktivitas lain saat bekerja.")
    lines.append("Tunggu hingga pekerjaan selesai.")
    
    return "\n".join(lines)

