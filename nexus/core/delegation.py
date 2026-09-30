from __future__ import annotations

from dataclasses import dataclass


class DelegationBudgetError(RuntimeError):
    """Raised when a parent cannot safely start another child session."""


@dataclass(frozen=True)
class DelegationReservation:
    amount_usd: float
    slot: int


class DelegationBudget:
    """Small in-process ledger shared by a parent and its child sessions.

    A reservation is conservative when the provider does not report cost: the
    reserved amount is then treated as spent. This prevents unknown provider
    pricing from silently enabling unlimited child calls.
    """

    def __init__(self, total_usd: float, max_children: int = 2) -> None:
        self.total_usd = max(0.0, float(total_usd))
        self.max_children = max(0, int(max_children))
        self.reserved_usd = 0.0
        self.spent_usd = 0.0
        self.children_started = 0

    def reserve(self, requested_usd: float) -> DelegationReservation:
        if self.children_started >= self.max_children:
            raise DelegationBudgetError("maximum child-session count reached")
        available = self.total_usd - self.reserved_usd - self.spent_usd
        amount = min(max(0.0, float(requested_usd)), max(0.0, available))
        if amount <= 0:
            raise DelegationBudgetError("delegation budget exhausted")
        self.children_started += 1
        self.reserved_usd += amount
        return DelegationReservation(amount_usd=amount, slot=self.children_started)

    def settle(
        self,
        reservation: DelegationReservation,
        *,
        actual_cost_usd: float | None,
        cost_available: bool,
    ) -> None:
        self.reserved_usd = max(0.0, self.reserved_usd - reservation.amount_usd)
        if cost_available and actual_cost_usd is not None:
            self.spent_usd += max(0.0, float(actual_cost_usd))
        else:
            self.spent_usd += reservation.amount_usd

    def snapshot(self) -> dict[str, float | int]:
        return {
            "total_usd": self.total_usd,
            "reserved_usd": self.reserved_usd,
            "spent_usd": self.spent_usd,
            "children_started": self.children_started,
            "max_children": self.max_children,
        }

    def restore(self, snapshot: dict[str, float | int]) -> None:
        """Restore the durable portion of the ledger after a process restart."""
        self.total_usd = float(snapshot.get("total_usd", self.total_usd))
        self.reserved_usd = float(snapshot.get("reserved_usd", 0.0))
        self.spent_usd = float(snapshot.get("spent_usd", 0.0))
        self.children_started = int(
            snapshot.get("children_started", self.children_started)
        )
        self.max_children = int(snapshot.get("max_children", self.max_children))
