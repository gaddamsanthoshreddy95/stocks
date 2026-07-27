"""
Trade Plan Model
"""

from dataclasses import dataclass, field


@dataclass
class TradePlan:

    entry: float

    stop_loss: float

    target1: float

    target2: float

    target3: float

    risk: float

    reward: float

    risk_reward: float

    quality: str

    expected_reward: float = 0.0

    nearest_target_reward: float = 0.0

    target_basis: str = "NEAREST_RESISTANCE"

    breakout_probability: float = 0.0

    diagnostics: list[str] = field(default_factory=list)

    scenarios: dict = field(default_factory=dict)
