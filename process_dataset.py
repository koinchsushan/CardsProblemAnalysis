"""
process_dataset.py
==================

CSV → card_analysis_data.json converter for the /behavioural-analysis Behavioural Analysis tab.

The existing `data/card_analysis_data.json` is the schema for the output. The
behavioural Blueprint in `behavioral_app.py` re-derives analyses #1–#5 and #7–#9
on every request from the trials stored in `analysis_types[6]`, so we MUST keep
slot #6 fully populated with every parsed trial (`moves`, `final_state`,
`messiness_score`, `_source_trials` cannot use anything else).

Slots #1–#5 and #7–#9 are emitted as curated subsets that mirror what the BP
would derive; even when the BP runs `_derive_analysis` it reads these slots
only as fallback. Their presence keeps the JSON externally valid for any
tool that consumes the file directly without re-deriving.

Usage from the CLI:
    python process_dataset.py data/CardsDataset.csv data/card_analysis_data.json

Usage from Python:
    from process_dataset import process_csv_to_json
    payload = process_csv_to_json("data/CardsDataset.csv")
    json.dump(payload, open("data/card_analysis_data.json", "w"), indent=2)
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from statistics import mean
from typing import Any


# ---------------------------------------------------------------------------
# Required schema
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "participant",
    "trialN",
    "condition",
    "overall_correct",
    "movement_codes",
    "final_card_position_codes_1",
]


class ProcessingError(Exception):
    """Raised when the uploaded CSV is structurally invalid."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


# ---------------------------------------------------------------------------
# Card vocabulary — mirrors what app.py / behavioral_app.py render
# ---------------------------------------------------------------------------

RANK_PREFIXES = ("king", "queen", "jack", "blank")
SUIT_NAMES = ("spades", "hearts", "diamonds", "clubs")

SUIT_SYMBOLS = {
    "spades": "♠",
    "hearts": "♥",
    "diamonds": "♦",
    "clubs": "♣",
}

# Suit → CSS-friendly colour token used by the renderer
SUIT_COLORS = {
    "spades": "black",
    "clubs": "black",
    "hearts": "red",
    "diamonds": "red",
}

RANK_LETTERS = {"king": "K", "queen": "Q", "jack": "J", "blank": "B"}

GRID_ROWS = 8
GRID_COLS = 8
MIN_VALID_MOVES_FOR_ANALYSIS = 6  # mirrors MIN_VALID_MOVES in behavioral_app.py


# ---------------------------------------------------------------------------
# Move / position parsing
# ---------------------------------------------------------------------------

def safe_literal_eval(value: Any) -> list:
    """Accept a Python list/tuple OR a stringified list, otherwise return []."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "[]":
            return []
        try:
            return list(ast.literal_eval(text))
        except (ValueError, SyntaxError):
            return []
    return []


def _parse_position_text(raw: str) -> tuple[int, int] | None:
    """Parse 'A1'..'H8' (with optional suffixes like 'A1c') into (row, col)."""
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip().upper()
    # Strip a leading lowercase letter that occasionally prefixes the cell code
    if text and text[0].islower():
        text = text[1:]
    match = re.match(r"^([A-H])(\d{1,2})$", text)
    if not match:
        return None
    col_letter, row_digits = match.groups()
    col = ord(col_letter) - ord("A")
    row = int(row_digits) - 1
    if not (0 <= row < GRID_ROWS and 0 <= col < GRID_COLS):
        return None
    return row, col


def _clean_token(token: str) -> str | None:
    """Strip lowercase prefixes and standalone 'c' pieces, like clean_card_positions."""
    if not isinstance(token, str):
        return None
    parts = token.split("_")
    if not parts:
        return None
    # 'queen_spades_A1c' → 'queen_spades_A1'
    if re.match(r"^[a-z]", parts[-1]):
        parts[-1] = parts[-1][1:]
    elif len(parts) >= 2 and parts[-2] == "c":
        parts.pop(-2)
    return "_".join(parts)


def _parse_move_token(token: str) -> dict | None:
    """Convert one movement token (e.g. 'queen_spades_A1', 'blank_A1', 'blank2_cB2') into a move dict."""
    cleaned = _clean_token(token)
    if not cleaned:
        return None
    parts = cleaned.split("_")
    if len(parts) < 2:
        return None

    rank = parts[0].lower()
    is_blank_rank = rank == "blank" or (rank.startswith("blank") and rank[5:].isdigit())
    if not is_blank_rank and rank not in RANK_PREFIXES:
        return None

    suit = ""
    if is_blank_rank:
        position_code = parts[1]
    elif len(parts) >= 3:
        suit = parts[1].lower()
        position_code = parts[-1]
    else:
        return None

    if suit and suit not in SUIT_NAMES:
        return None

    coords = _parse_position_text(position_code)
    if coords is None:
        return None
    row, col = coords

    is_blank = is_blank_rank
    if is_blank:
        suit_symbol = ""
        color = "gray"
    else:
        suit_symbol = SUIT_SYMBOLS.get(suit, "")
        color = SUIT_COLORS.get(suit, "black")

    return {
        "row": row,
        "col": col,
        "value": RANK_LETTERS.get(rank, rank[0].upper()),
        "suit_symbol": suit_symbol,
        "color": color,
        "is_blank": is_blank,
    }


def _parse_move_list(raw: Any) -> list[dict]:
    """Parse a movement_codes cell into a list of move dicts (skips malformed tokens)."""
    items = safe_literal_eval(raw)
    moves = []
    for item in items:
        if not isinstance(item, str):
            continue
        move = _parse_move_token(item)
        if move is not None:
            moves.append(move)
    return moves


# ---------------------------------------------------------------------------
# Trial- and dataset-level helpers
# ---------------------------------------------------------------------------

def _messiness_from_moves(moves: list[dict]) -> float:
    """Average squared distance from centroid (2D spatial variance).
        Threshold 4.09 = mean. Clean ≤ 4.09, Messy > 4.09"""
    points = [(m["row"], m["col"]) for m in moves
              if isinstance(m.get("row"), int) and isinstance(m.get("col"), int)]
    if not points:
        return 0.0
    cx = mean(p[0] for p in points)
    cy = mean(p[1] for p in points)
    distances = [((x - cx) ** 2 + (y - cy) ** 2) for x, y in points]
    return sum(distances) / len(distances)


def _blank_card_count(final_state: list[dict], moves: list[dict]) -> int:
    blanks = sum(1 for card in final_state if card.get("is_blank"))
    if blanks:
        return blanks
    return sum(1 for m in moves if m.get("is_blank"))


def _derive_trial(row: Any, index: int) -> dict:
    """Translate one pandas/DataFrame row into the JSON trial shape."""
    moves = _parse_move_list(row.get("movement_codes"))
    final_state = _parse_move_list(row.get("final_card_position_codes_1"))

    outcome_value = row.get("overall_correct", 0)
    try:
        outcome_value = float(outcome_value)
    except (TypeError, ValueError):
        outcome_value = 0.0
    outcome = "success" if outcome_value == 1.0 else "failed"

    try:
        trial_number = int(row.get("trialN"))
    except (TypeError, ValueError):
        trial_number = index

    try:
        participant_value = row.get("participant")
        participant = str(participant_value)
    except Exception:
        participant = ""

    try:
        condition_value = row.get("condition")
        condition = "" if condition_value is None else str(condition_value)
    except Exception:
        condition = ""

    return {
        "participant": participant,
        "condition": condition,
        "outcome": outcome,
        "moves": moves,
        "final_state": final_state,
        "move_count": len(moves),
        "messiness_score": round(_messiness_from_moves(moves), 4),
        "trial_number": trial_number,
        "blank_card_count": _blank_card_count(final_state, moves),
    }


def _compute_statistics(trials: list[dict]) -> dict:
    """Top-level summary block — keys must match the existing JSON for downstream consumers."""
    total = len(trials)
    if total == 0:
        return {
            "total_trials": 0,
            "success_count": 0,
            "success_rate": 0.0,
            "avg_moves": 0.0,
            "blank_card_success_rate": 0.0,
            "no_blank_success_rate": 0.0,
            "trials_with_blank_cards": 0,
        }
    success_trials = [t for t in trials if t["outcome"] == "success"]
    success_count = len(success_trials)
    success_rate = round(success_count / total * 100, 2)
    avg_moves = round(sum(t["move_count"] for t in trials) / total, 2)

    blank_trials = [t for t in trials if t["blank_card_count"] > 0]
    no_blank_trials = [t for t in trials if t["blank_card_count"] == 0]

    blank_sr = round(
        sum(1 for t in blank_trials if t["outcome"] == "success") / max(1, len(blank_trials)) * 100,
        2,
    )
    nob_sr = round(
        sum(1 for t in no_blank_trials if t["outcome"] == "success") / max(1, len(no_blank_trials)) * 100,
        2,
    )

    return {
        "total_trials": total,
        "success_count": success_count,
        "success_rate": success_rate,
        "avg_moves": avg_moves,
        "blank_card_success_rate": blank_sr,
        "no_blank_success_rate": nob_sr,
        "trials_with_blank_cards": len(blank_trials),
    }


# ---------------------------------------------------------------------------
# Curated slots — mirror what behavioral_app._derive_analysis emits so the file
# looks consistent even if a downstream tool reads it without re-deriving.
# ---------------------------------------------------------------------------

ANALYSIS_DEFS = [
    (1, "Successful Clean Patterns (Many Moves)",
     "Top successful trials with many exploratory moves while keeping a tidy layout."),
    (2, "Failed Messy Patterns (Few Moves)",
     "Failed trials that scattered early moves without producing a stable outcome."),
    (3, "All Successful Trials",
     "Every trial the dataset labelled as a success so winning strategies can be compared."),
    (4, "In-Trial Progression (Early vs Late)",
     "Trials with the strongest within-trial shifts between an early and a late half."),
    (5, "Opening Strategies (First 5 Moves)",
     "The first five moves of every usable trial, surfacing the anchors that shape the rest."),
    (6, "Retry and Recovery Patterns",
     "Every usable trial with full movement history — the Blueprint uses this as the source of truth."),
    (7, "Extreme Cases (Cleanest vs Messiest)",
     "The cleanest and messiest trials within the success and failure populations."),
    (8, "Speed Comparison (Quick vs Slow Solvers)",
     "Successful solvers split into the fastest and slowest halves to compare strategy."),
    (9, "Card Repetition Patterns",
     "Trials ranked by how often the same card or position was revisited."),
]


def _progression_delta(trial: dict) -> float:
    """Mirror behavioral_app._progression_delta — measures internal change within a trial."""
    moves = trial.get("moves", [])
    if len(moves) < 4:
        return 0.0
    segment = max(2, len(moves) // 3)
    return _messiness_from_moves(moves[-segment:]) - _messiness_from_moves(moves[:segment])


def _repetition_ratio(trial: dict) -> float:
    """Mirror behavioral_app._repetition_ratio."""
    moves = trial.get("moves", [])
    if not moves:
        return 0.0
    unique = {(m.get("value", ""), m.get("suit_symbol", ""), m.get("row"), m.get("col"))
              for m in moves}
    return 1.0 - (len(unique) / len(moves))


def _build_slot_1(success: list[dict]) -> list[dict]:
    chosen = [t for t in success if t["move_count"] >= 15]
    chosen.sort(key=lambda t: (t["messiness_score"], -t["move_count"]))
    return chosen[:80]


def _build_slot_2(failed: list[dict]) -> list[dict]:
    chosen = [t for t in failed if t["move_count"] < 15]
    chosen.sort(key=lambda t: (-t["messiness_score"], t["move_count"]))
    return chosen[:80]


def _build_slot_3(success: list[dict]) -> list[dict]:
    return list(success)


def _build_slot_5(valid: list[dict]) -> list[dict]:
    opened = []
    for t in valid:
        first_five = t.get("moves", [])[:5]
        if first_five:
            opened.append({**t, "moves": first_five, "move_count": len(first_five)})
    return opened[:32]


def _build_slot_4(valid: list[dict], limit_per_side: int = 12) -> list[dict]:
    enriched = [(t, _progression_delta(t)) for t in valid if len(t.get("moves", [])) >= MIN_VALID_MOVES_FOR_ANALYSIS]
    improved = sorted([(t, d) for t, d in enriched if d < 0], key=lambda x: x[1])[:limit_per_side]
    deteriorated = sorted([(t, d) for t, d in enriched if d > 0], key=lambda x: x[1], reverse=True)[:limit_per_side]
    out = []
    for t, d in improved:
        out.append({**t, "progression_label": "Became more organized", "progression_delta": round(d, 3)})
    for t, d in deteriorated:
        out.append({**t, "progression_label": "Became less organized", "progression_delta": round(d, 3)})
    return out


def _build_slot_7(success: list[dict], failed: list[dict], limit_per_group: int = 20) -> list[dict]:
    out = []
    for label, pool in (("successful", success), ("failed", failed)):
        if not pool:
            continue
        ordered = sorted(pool, key=lambda t: t["messiness_score"])
        out.extend({**t, "extreme_label": f"Cleanest {label}"} for t in ordered[:limit_per_group])
        out.extend({**t, "extreme_label": f"Messiest {label}"} for t in ordered[-limit_per_group:])
    return out


def _build_slot_8(success: list[dict], limit_per_side: int = 60) -> list[dict]:
    if not success:
        return []
    ordered = sorted(success, key=lambda t: t["move_count"])
    pick = max(1, min(limit_per_side, len(ordered) // 2))
    out = []
    out.extend({**t, "speed_label": "Quick successful solver"} for t in ordered[:pick])
    out.extend({**t, "speed_label": "Slow successful solver"} for t in ordered[-pick:])
    return out


def _build_slot_9(valid: list[dict]) -> list[dict]:
    ordered = sorted(valid, key=_repetition_ratio, reverse=True)
    out = []
    out.extend(ordered[:16])
    out.extend(ordered[-16:])
    return out


def _build_analysis_types(all_trials: list[dict]) -> list[dict]:
    """Assemble the 9 analysis slots. Slot #6 carries every trial as the source of truth."""
    valid = [t for t in all_trials if t["move_count"] >= MIN_VALID_MOVES_FOR_ANALYSIS]
    success = [t for t in valid if t["outcome"] == "success"]
    failed = [t for t in valid if t["outcome"] != "success"]

    builders = {
        1: lambda: _build_slot_1(success),
        2: lambda: _build_slot_2(failed),
        3: lambda: _build_slot_3(success),
        4: lambda: _build_slot_4(valid),
        5: lambda: _build_slot_5(valid),
        6: lambda: list(all_trials),
        7: lambda: _build_slot_7(success, failed),
        8: lambda: _build_slot_8(success),
        9: lambda: _build_slot_9(valid),
    }

    analyses = []
    for analysis_id, title, description in ANALYSIS_DEFS:
        try:
            trials_slot = builders[analysis_id]()
        except Exception as exc:  # pragma: no cover — defensive
            trials_slot = []
            print(f"[process_dataset] slot {analysis_id} build error: {exc}")
        analyses.append({
            "id": analysis_id,
            "title": title,
            "description": description,
            "trials": trials_slot,
        })
    return analyses


# ---------------------------------------------------------------------------
# Public entry point used by the upload endpoint
# ---------------------------------------------------------------------------

def process_csv_to_json(csv_path: str | Path) -> dict:
    """Read a CardsDataset-style CSV and return the JSON-ready dict.

    Raises ProcessingError for missing/invalid columns; pandas errors propagate
    unchanged so the caller can surface them verbatim.
    """
    import pandas as pd  # local import keeps the module lightweight for CLI use

    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise ProcessingError(f"CSV file not found: {csv_path}")

    df = pd.read_csv(csv_path)
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ProcessingError(
            "CSV is missing required columns: " + ", ".join(missing),
            {
                "missing_columns": missing,
                "required_columns": list(REQUIRED_COLUMNS),
            },
        )

    rows = df.to_dict("records")
    trials: list[dict] = []
    for index, row in enumerate(rows):
        try:
            trial = _derive_trial(row, index)
        except Exception as exc:
            # Skip a misbehaving row but keep processing the rest.
            print(f"[process_dataset] skipped row {index}: {exc}")
            continue
        if not trial["moves"]:
            # Keep rows even with empty moves so trial counts reflect the upload.
            pass
        trials.append(trial)

    if not trials:
        raise ProcessingError("CSV produced zero trials — check the rows and try again.")

    return {
        "statistics": _compute_statistics(trials),
        "analysis_types": _build_analysis_types(trials),
    }


def atomic_write_json(path: str | Path, payload: dict, indent: int = 2) -> None:
    """Write JSON atomically: temp file in same dir, fsync, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=indent, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Clean up the temp file if anything went wrong before os.replace
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except Exception:
                pass
        raise


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: python process_dataset.py <input.csv> <output.json>", flush=True)
        return 2
    csv_in, json_out = argv[1], argv[2]
    try:
        payload = process_csv_to_json(csv_in)
    except ProcessingError as exc:
        print(f"Validation error: {exc}", flush=True)
        return 1
    except Exception as exc:
        print(f"Processing failed: {exc}", flush=True)
        return 1
    atomic_write_json(json_out, payload)
    stats = payload["statistics"]
    print(
        f"Wrote {json_out} — {stats['total_trials']} trials, "
        f"{stats['success_count']} successes "
        f"({stats['success_rate']}% SR), "
        f"{stats['trials_with_blank_cards']} used blank cards.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli(_sys.argv))
