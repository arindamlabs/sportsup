"""Explainable "upset" detection.

Produces an ``upset_index`` in [0,1] plus a human-readable reason, trying signals in
the configured ``signal_priority`` order and using the first that has data:

1. **odds** — winner's pre-match implied win-probability. Flag if the winner was a
   genuine underdog: implied chance <= (1 - sensitivity) AND below the opponent's.
2. **standings** — league-table position gap. Flag if the winner was ranked at least
   ``min_position_gap`` places *below* the loser.
3. **form** — recent-results points over the last ``form_window`` games. Flag if the
   winner beat a markedly in-form opponent despite worse recent form.

Limitations: odds aren't always published (esp. early World Cup group games); early-season
tables are noisy (few games played); "upset" is inherently subjective — hence the tunable
``sensitivity``/``min_position_gap`` and the logged reason so you can calibrate.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..providers import MatchOdds, MatchResult, Standing
from ..providers.teams import canonical_name

_FORM_POINTS = {"W": 3, "D": 1, "L": 0}


@dataclass
class UpsetEvaluation:
    is_upset: bool
    upset_index: float          # 0..1 severity (0 when not an upset / no data)
    signal_used: str | None     # 'odds' | 'standings' | 'form' | None
    reason: str


def _form_points(form: str | None, window: int) -> int | None:
    if not form:
        return None
    chars = [c for c in form.upper() if c in _FORM_POINTS][-window:]
    return sum(_FORM_POINTS[c] for c in chars) if chars else None


def _winner_loser(result: MatchResult) -> tuple[str, str, str, str]:
    fx = result.fixture
    if result.winner == "HOME":
        return fx.home.name, fx.away.name, "HOME", "AWAY"
    return fx.away.name, fx.home.name, "AWAY", "HOME"


def evaluate_upset(
    result: MatchResult,
    *,
    config,
    odds: MatchOdds | None = None,
    standings: list[Standing] | None = None,
) -> UpsetEvaluation:
    """Decide whether a finished match was an upset, per the configured heuristic."""
    if result.winner in (None, "DRAW"):
        return UpsetEvaluation(False, 0.0, None, "no decisive winner (draw or unknown)")

    winner_name, loser_name, winner_key, loser_key = _winner_loser(result)
    sd = config.shock_detection
    table = (
        {canonical_name(s.team.name): s for s in standings} if standings else {}
    )

    for signal in sd.signal_priority:
        if signal == "odds" and odds is not None:
            implied = odds.implied_probabilities()
            wp, lp = implied[winner_key], implied[loser_key]
            idx = round(1.0 - wp, 3)
            is_upset = (wp <= 1.0 - sd.sensitivity) and (wp < lp)
            reason = (
                f"{winner_name} won with only ~{wp*100:.0f}% pre-match implied chance "
                f"(vs {loser_name} ~{lp*100:.0f}%)"
            )
            return UpsetEvaluation(is_upset, idx, "odds", reason)

        if signal == "standings" and table:
            w, l = table.get(canonical_name(winner_name)), table.get(canonical_name(loser_name))
            if w and l:
                gap = w.position - l.position  # positive => winner ranked lower (worse)
                idx = round(max(0.0, min(1.0, gap / max(1, len(standings) - 1))), 3)
                is_upset = gap >= sd.min_position_gap
                reason = (
                    f"{winner_name} (#{w.position}) beat {loser_name} (#{l.position}) "
                    f"— a {gap}-place gap (threshold {sd.min_position_gap})"
                )
                return UpsetEvaluation(is_upset, idx, "standings", reason)

        if signal == "form" and table:
            w, l = table.get(canonical_name(winner_name)), table.get(canonical_name(loser_name))
            if w and l:
                wpts, lpts = _form_points(w.form, sd.form_window), _form_points(l.form, sd.form_window)
                if wpts is not None and lpts is not None:
                    gap = lpts - wpts  # positive => loser was in better recent form
                    idx = round(max(0.0, min(1.0, gap / (3 * sd.form_window))), 3)
                    is_upset = gap >= sd.form_window
                    reason = (
                        f"{winner_name} (form {wpts}pts) beat in-form {loser_name} "
                        f"({lpts}pts) over last {sd.form_window} games"
                    )
                    return UpsetEvaluation(is_upset, idx, "form", reason)

    return UpsetEvaluation(False, 0.0, None, "insufficient data for shock detection")
