"""ScanResult — typed return for pipeline phases."""

from dataclasses import dataclass, field
from typing import Literal

ScanStatus = Literal[
    "alerts_found",
    "no_setups",
    "suppressed",
    "api_failure",
    "no_watchlist",
    "no_confirmations",
    "error",
]


@dataclass
class ScanResult:
    """Result of a single pipeline phase run.

    Every scan produces a ScanResult. On success it contains Pump Alerts;
    on empty or failure it carries a status explaining why.
    """
    status: ScanStatus
    alerts: list[dict] = field(default_factory=list)
    detail: str = ""
    candidate_symbols: list[str] = field(default_factory=list)
    phase: str = ""  # "phase1" or "phase2"

    def __bool__(self) -> bool:
        """True when the scan produced actionable alerts."""
        return len(self.alerts) > 0

    def __iter__(self):
        """Allow `for alert in result:` iteration over alerts."""
        return iter(self.alerts)

    def __len__(self) -> int:
        return len(self.alerts)
