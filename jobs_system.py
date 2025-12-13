"""
Jobs System - Time-Based Work with Energy
Sistem pekerjaan dengan energy, job levels, dan stat bonuses
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple


class JobType(Enum):
    """Jenis pekerjaan yang tersedia"""
    WARRIOR = "WARRIOR"
    GUARD = "GUARD"
    MAGIC_TEACHER = "MAGIC_TEACHER"


@dataclass
class JobDefinition:
    """Definisi pekerjaan"""
    id: str
    name: str
    description: str
    requirements: Dict[str, int]  # stat requirements: {"level": 5, "atk": 20}
    base_gold_per_minute: int
    base_exp_per_minute: int
    stat_growth: Dict[str, float]  # stat yang tumbuh per level: {"atk": 0.5, "spd": 0.3}
    max_level: int = 100


@dataclass
class JobProgress:
    """Progress player pada suatu job"""
    job_id: str
    level: int
    exp: int
    total_time_worked: int  # dalam menit
    
    def to_dict(self) -> Dict:
        return {
            "job_id": self.job_id,
            "level": self.level,
            "exp": self.exp,
            "total_time_worked": self.total_time_worked
        }
    
    @staticmethod
    def from_dict(data: Dict) -> JobProgress:
        return JobProgress(
            job_id=data["job_id"],
            level=data["level"],
            exp=data["exp"],
            total_time_worked=data["total_time_worked"]
        )


@dataclass
class WorkSession:
    """Sesi kerja yang sedang berlangsung"""
    job_id: str
    energy_spent: int  # Energy yang dihabiskan (1 energy = 1 minute)
    start_timestamp: float  # Unix timestamp
    end_timestamp: float  # Unix timestamp
    is_complete: bool = False
    
    def get_remaining_seconds(self) -> int:
        """Dapatkan sisa waktu dalam detik"""
        if self.is_complete:
            return 0
        current_time = time.time()
        remaining = max(0, self.end_timestamp - current_time)
        return int(remaining)
    
    def check_completion(self) -> bool:
        """Cek apakah work session sudah selesai"""
        if self.is_complete:
            return True
        current_time = time.time()
        if current_time >= self.end_timestamp:
            self.is_complete = True
            return True
        return False
    
    def to_dict(self) -> Dict:
        return {
            "job_id": self.job_id,
            "energy_spent": self.energy_spent,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "is_complete": self.is_complete
        }
    
    @staticmethod
    def from_dict(data: Dict) -> WorkSession:
        return WorkSession(
            job_id=data["job_id"],
            energy_spent=data["energy_spent"],
            start_timestamp=data["start_timestamp"],
            end_timestamp=data["end_timestamp"],
            is_complete=data.get("is_complete", False)
        )


@dataclass
class EnergySystem:
    """Sistem energy untuk pekerjaan"""
    max_energy: int = 100
    current_energy: int = 100
    regen_rate: float = 1.0  # Energy per menit
    last_regen_timestamp: float = field(default_factory=time.time)
    
    def update_energy(self):
        """Update energy berdasarkan waktu yang lewat"""
        current_time = time.time()
        minutes_passed = (current_time - self.last_regen_timestamp) / 60.0
        
        if minutes_passed >= 1.0:
            energy_to_add = int(minutes_passed * self.regen_rate)
            self.current_energy = min(self.max_energy, self.current_energy + energy_to_add)
            self.last_regen_timestamp = current_time
    
    def consume_energy(self, amount: int) -> bool:
        """
        Konsumsi energy
        Returns: True jika berhasil, False jika tidak cukup
        """
        if self.current_energy < amount:
            return False
        self.current_energy -= amount
        return True
    
    def to_dict(self) -> Dict:
        return {
            "max_energy": self.max_energy,
            "current_energy": self.current_energy,
            "regen_rate": self.regen_rate,
            "last_regen_timestamp": self.last_regen_timestamp
        }
    
    @staticmethod
    def from_dict(data: Dict) -> EnergySystem:
        return EnergySystem(
            max_energy=data.get("max_energy", 100),
            current_energy=data.get("current_energy", 100),
            regen_rate=data.get("regen_rate", 1.0),
            last_regen_timestamp=data.get("last_regen_timestamp", time.time())
        )


class JobSystem:
    """Sistem utama untuk manajemen pekerjaan"""
    
    def __init__(self):
        self.jobs = create_job_database()
    
    def can_start_job(
        self,
        job_id: str,
        player_stats: Dict[str, int],
        current_job_id: Optional[str]
    ) -> Tuple[bool, str]:
        """
        Cek apakah player bisa memulai job
        Returns: (can_start, error_message)
        """
        if current_job_id is not None:
            return False, "Kamu harus keluar dari pekerjaan saat ini terlebih dahulu"
        
        job = self.jobs.get(job_id)
        if not job:
            return False, "Pekerjaan tidak ditemukan"
        
        # Cek requirements
        for stat_name, required_value in job.requirements.items():
            player_value = player_stats.get(stat_name, 0)
            if player_value < required_value:
                return False, f"Persyaratan tidak terpenuhi: {stat_name} minimum {required_value} (kamu: {player_value})"
        
        return True, ""
    
    def start_work(
        self,
        job_id: str,
        energy_amount: int,
        energy_system: EnergySystem
    ) -> Tuple[bool, Optional[WorkSession], str]:
        """
        Mulai work session
        Returns: (success, work_session, message)
        """
        if energy_amount < 1:
            return False, None, "Minimal 1 energy untuk bekerja"
        
        if energy_amount > energy_system.current_energy:
            return False, None, f"Energy tidak cukup. Kamu punya {energy_system.current_energy}, butuh {energy_amount}"
        
        job = self.jobs.get(job_id)
        if not job:
            return False, None, "Pekerjaan tidak ditemukan"
        
        # Konsumsi energy
        if not energy_system.consume_energy(energy_amount):
            return False, None, "Gagal mengonsumsi energy"
        
        # Create work session (1 energy = 1 minute)
        start_time = time.time()
        duration_seconds = energy_amount * 60
        end_time = start_time + duration_seconds
        
        session = WorkSession(
            job_id=job_id,
            energy_spent=energy_amount,
            start_timestamp=start_time,
            end_timestamp=end_time,
            is_complete=False
        )
        
        return True, session, f"Mulai bekerja selama {energy_amount} menit"
    
    def complete_work(
        self,
        session: WorkSession,
        job_progress: Optional[JobProgress]
    ) -> Tuple[int, int, Optional[JobProgress], List[str]]:
        """
        Selesaikan work session dan hitung reward
        Returns: (gold_earned, job_exp_earned, updated_progress, logs)
        """
        job = self.jobs.get(session.job_id)
        if not job:
            return 0, 0, job_progress, ["Pekerjaan tidak ditemukan"]
        
        logs = []
        
        # Hitung reward berdasarkan waktu
        minutes_worked = session.energy_spent
        
        # Base reward
        gold_earned = job.base_gold_per_minute * minutes_worked
        exp_earned = job.base_exp_per_minute * minutes_worked
        
        # Update job progress
        if job_progress is None:
            job_progress = JobProgress(
                job_id=session.job_id,
                level=1,
                exp=0,
                total_time_worked=0
            )
        
        job_progress.exp += exp_earned
        job_progress.total_time_worked += minutes_worked
        
        # Cek level up
        level_ups = 0
        while job_progress.exp >= self.exp_for_next_level(job_progress.level) and job_progress.level < job.max_level:
            job_progress.exp -= self.exp_for_next_level(job_progress.level)
            job_progress.level += 1
            level_ups += 1
            logs.append(f"Job Level Up! Sekarang level {job_progress.level} di {job.name}")
        
        # Jika mencapai level max, keluar otomatis
        if job_progress.level >= job.max_level:
            logs.append(f"Kamu telah mencapai level maksimum di {job.name}! Pekerjaan selesai.")
            # Job progress tetap disimpan untuk history
        
        logs.insert(0, f"Pekerjaan selesai! Mendapat {gold_earned} gold dan {exp_earned} job EXP")
        
        return gold_earned, exp_earned, job_progress, logs
    
    def exp_for_next_level(self, current_level: int) -> int:
        """Hitung EXP yang dibutuhkan untuk level berikutnya"""
        # Formula: 100 * level^1.5
        return int(100 * (current_level ** 1.5))
    
    def get_total_stat_bonus(self, job_id: str, job_level: int) -> Dict[str, int]:
        """
        Hitung total bonus stat dari job berdasarkan level
        Returns: dict stat_name -> bonus_value
        """
        job = self.jobs.get(job_id)
        if not job:
            return {}
        
        bonuses = {}
        for stat_name, growth_per_level in job.stat_growth.items():
            total_bonus = int(growth_per_level * job_level)
            bonuses[stat_name] = total_bonus
        
        return bonuses


def create_job_database() -> Dict[str, JobDefinition]:
    """Database semua pekerjaan yang tersedia"""
    return {
        "WARRIOR": JobDefinition(
            id="WARRIOR",
            name="Prajurit",
            description="Latihan bertarung meningkatkan ATK dan Speed",
            requirements={"level": 1},  # Tidak ada requirement khusus
            base_gold_per_minute=10,
            base_exp_per_minute=5,
            stat_growth={
                "atk": 0.8,  # +0.8 ATK per job level
                "spd": 0.5   # +0.5 SPD per job level
            },
            max_level=100
        ),
        "GUARD": JobDefinition(
            id="GUARD",
            name="Penjaga",
            description="Berjaga meningkatkan DEF dan HP",
            requirements={"level": 1},
            base_gold_per_minute=12,
            base_exp_per_minute=5,
            stat_growth={
                "defense": 0.9,  # +0.9 DEF per job level
                "max_hp": 3.0    # +3 HP per job level
            },
            max_level=100
        ),
        "MAGIC_TEACHER": JobDefinition(
            id="MAGIC_TEACHER",
            name="Pengajar Akademi Sihir",
            description="Mengajar sihir meningkatkan MAG dan MP",
            requirements={"level": 3, "mag": 10},  # Butuh level 3 dan MAG 10
            base_gold_per_minute=15,
            base_exp_per_minute=6,
            stat_growth={
                "mag": 1.0,     # +1.0 MAG per job level
                "max_mp": 2.0   # +2 MP per job level
            },
            max_level=100
        ),
    }


def format_time_remaining(seconds: int) -> str:
    """Format waktu tersisa menjadi string yang mudah dibaca"""
    if seconds <= 0:
        return "Selesai"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours} jam")
    if minutes > 0:
        parts.append(f"{minutes} menit")
    if secs > 0 and hours == 0:  # Hanya tampilkan detik jika < 1 jam
        parts.append(f"{secs} detik")
    
    return " ".join(parts)

