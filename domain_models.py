from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PlayerProfile:
    user_id: int
    name: Optional[str] = None
    location: str = "SELATPANJANG"
    main_progress: str = "PROLOG"
    scene_id: str = "CH0_S1"
    gold: int = 0


@dataclass
class Inventory:
    items: Dict[str, int] = field(default_factory=dict)

    def add(self, item_id: str, qty: int = 1) -> None:
        self.items[item_id] = self.items.get(item_id, 0) + qty

    def remove(self, item_id: str, qty: int = 1) -> bool:
        if self.items.get(item_id, 0) < qty:
            return False
        self.items[item_id] -= qty
        if self.items[item_id] <= 0:
            self.items.pop(item_id, None)
        return True


@dataclass
class QuestLog:
    active: Dict[str, dict] = field(default_factory=dict)
    completed: List[dict] = field(default_factory=list)


@dataclass
class Party:
    members: Dict[str, object] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)


@dataclass
class BattleSnapshot:
    in_battle: bool = False
    enemies: List[dict] = field(default_factory=list)
    turn: str = "PLAYER"

