"""Alert engine: turn fixtures and results into deduplicated, sendable alerts.

The engine is delivery-agnostic — it produces :class:`Alert` objects with a stable
``dedup_key``. Phase 4 formats and sends them; Phase 5 schedules them. Dedup is enforced
against the SQLite state store so an alert fires exactly once, even across restarts.
"""

from .engine import AlertEngine
from .models import Alert, AlertType
from .shock import UpsetEvaluation, evaluate_upset

__all__ = ["Alert", "AlertType", "AlertEngine", "UpsetEvaluation", "evaluate_upset"]
