import copy
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from flask import Blueprint, jsonify, make_response, render_template, request, send_file
from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent
# Data lives in azure-deploy/data/ — override with CARD_DATA_PATH env var on Azure
DEFAULT_DATA_PATH = BASE_DIR / "data" / "card_analysis_data.json"
DATA_PATH = Path(os.getenv("CARD_DATA_PATH", str(DEFAULT_DATA_PATH))).resolve()
DB_PATH = Path(os.getenv("PLAY_DB_PATH", str(BASE_DIR / "play_sessions.db"))).resolve()
# Set PARENT_ORIGIN to your portfolio domain in Azure App Settings, e.g. https://yoursite.netlify.app
PARENT_ORIGIN = os.getenv("PARENT_ORIGIN", "")

allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ENABLE_PLAYGROUND = env_flag("ENABLE_PLAYGROUND", True)
ENABLE_GIF_EXPORT = env_flag("ENABLE_GIF_EXPORT", True)
ENABLE_HISTORY_TAB = env_flag("ENABLE_HISTORY_TAB", True)

bp = Blueprint(
    "behavioral",
    __name__,
    template_folder="templates_behavioral",
    static_folder="static_behavioral",
    static_url_path="/behavioral-static",
)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS visitors (
            visitor_id TEXT PRIMARY KEY,
            display_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS play_sessions (
            session_id TEXT PRIMARY KEY,
            visitor_id TEXT NOT NULL,
            condition TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            result_json TEXT,
            user_agent TEXT,
            device_type TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            move_index INTEGER NOT NULL,
            row INTEGER NOT NULL,
            col INTEGER NOT NULL,
            value TEXT,
            suit_symbol TEXT,
            color TEXT,
            is_blank INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def _read_json():
    if not DATA_PATH.exists():
        return {"statistics": {}, "analysis_types": []}
    with DATA_PATH.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _is_blank(card):
    if not isinstance(card, dict):
        return False
    if card.get("is_blank") is True:
        return True
    return str(card.get("value", "")).upper() == "BLANK"


def _trial_identity_key(trial):
    trial_number = trial.get("trial_number")
    if trial_number is not None:
        return (
            str(trial.get("participant") or ""),
            str(trial.get("condition") or ""),
            str(trial_number),
        )

    moves = list(trial.get("moves") or [])
    first = moves[0] if moves else {}
    last = moves[-1] if moves else {}
    return (
        str(trial.get("participant") or ""),
        str(trial.get("condition") or ""),
        str(trial.get("outcome") or ""),
        int(trial.get("move_count") or len(moves) or 0),
        f"{first.get('row')}-{first.get('col')}-{first.get('value')}",
        f"{last.get('row')}-{last.get('col')}-{last.get('value')}",
    )


def _dedupe_trials(trials):
    deduped = {}
    for trial in trials:
        deduped[_trial_identity_key(trial)] = trial
    return list(deduped.values())


MIN_VALID_MOVES = 6

ANALYSIS_DEFINITIONS = {
    1: {
        "title": "Successful Clean Patterns (Many Moves)",
        "explanation": "Successful participants with many exploratory moves while keeping structure.",
    },
    2: {
        "title": "Failed Messy Patterns (Few Moves)",
        "explanation": "Failed trials where organization breaks down early.",
    },
    3: {
        "title": "All Successful Trials",
        "explanation": "All success outcomes to compare multiple winning paths.",
    },
    4: {
        "title": "In-Trial Progression (Early vs Late)",
        "explanation": "Shows the strongest within-trial shifts: trials that became more organized versus trials that became less organized.",
    },
    5: {
        "title": "Opening Strategies (First 5 Moves)",
        "explanation": "First moves that shape final outcomes.",
    },
    6: {
        "title": "Repeated Attempts and Success Recovery",
        "explanation": "Only participants with multiple usable trials are shown, so retry/progression comparisons are meaningful.",
    },
    7: {
        "title": "Extreme Cases by Outcome",
        "explanation": "Cleanest and messiest successful trials plus cleanest and messiest failed trials.",
    },
    8: {
        "title": "Speed Comparison (Quick vs Slow Solvers)",
        "explanation": "Quickest successful solvers compared with slowest successful solvers.",
    },
    9: {
        "title": "Card Repetition Patterns",
        "explanation": "Repeated placements and revisits during sorting.",
    },
}


def _valid_cards(cards):
    return [
        copy.deepcopy(card)
        for card in cards or []
        if isinstance(card, dict) and isinstance(card.get("row"), int) and isinstance(card.get("col"), int)
    ]


def _normalize_trial(trial):
    normalized = copy.deepcopy(trial)
    moves = _valid_cards(normalized.get("moves"))
    final_state = _valid_cards(normalized.get("final_state"))
    normalized["moves"] = moves
    normalized["final_state"] = final_state
    normalized["move_count"] = int(normalized.get("move_count") or len(moves) or 0)
    normalized["blank_card_count"] = int(
        normalized.get("blank_card_count")
        or sum(1 for card in final_state if _is_blank(card))
        or sum(1 for card in moves if _is_blank(card))
        or 0
    )
    return normalized


def _source_trials(raw):
    analyses = raw.get("analysis_types", [])
    analysis_six = next((a for a in analyses if a.get("id") == 6 and a.get("trials")), None)
    source = analysis_six.get("trials", []) if analysis_six else [t for a in analyses for t in a.get("trials", [])]
    return [_normalize_trial(t) for t in source if _normalize_trial(t).get("moves")]


def _numeric(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _repetition_ratio(trial):
    moves = trial.get("moves") or []
    if not moves:
        return 0.0
    unique = {
        f"{m.get('value', '')}-{m.get('suit_symbol', '')}-{m.get('row')}-{m.get('col')}"
        for m in moves
    }
    return 1.0 - (len(unique) / len(moves))


def _progression_delta(trial):
    moves = trial.get("moves") or []
    if len(moves) < 4:
        return 0.0
    segment_size = max(2, len(moves) // 3)
    return _messiness_from_moves(moves[-segment_size:]) - _messiness_from_moves(moves[:segment_size])


def _participant_sort_key(participant):
    raw = str(participant)
    try:
        return (0, int(raw))
    except ValueError:
        return (1, raw)


def _group_by_participant(trials):
    groups = {}
    for trial in trials:
        key = str(trial.get("participant") or "N/A")
        groups.setdefault(key, []).append(trial)
    return groups


def _repeated_participant_trials(trials):
    groups = _group_by_participant(trials)
    repeated = []
    for participant in sorted(groups, key=_participant_sort_key):
        group = groups[participant]
        if len(group) > 1:
            repeated.extend(group)
    return repeated


def _recovery_score(trials):
    failed = [t for t in trials if t.get("outcome") != "success"]
    success = [t for t in trials if t.get("outcome") == "success"]
    if not failed or not success:
        return None
    worst_fail_mess = max(_numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or [])) for t in failed)
    best_success_mess = min(_numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or [])) for t in success)
    worst_fail_moves = max(_numeric(t.get("move_count")) for t in failed)
    best_success_moves = max(_numeric(t.get("move_count")) for t in success)
    return (worst_fail_mess - best_success_mess) + max(0.0, best_success_moves - worst_fail_moves) * 0.12


def _sort_trials_for_recovery_participant(trials):
    failed = [t for t in trials if t.get("outcome") != "success"]
    success = [t for t in trials if t.get("outcome") == "success"]

    if failed and success:
        worst_fail_mess = max(_numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or [])) for t in failed)
        success_ranked = sorted(
            success,
            key=lambda t: (worst_fail_mess - _numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or [])))
            + _numeric(t.get("move_count")) * 0.04,
            reverse=True,
        )
        failed_ranked = sorted(
            failed,
            key=lambda t: _numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or [])),
            reverse=True,
        )
        return success_ranked + failed_ranked

    if success:
        return sorted(success, key=lambda t: _numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or [])))
    return sorted(failed, key=lambda t: _numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or [])), reverse=True)


def _sort_trials_for_recovery(trials):
    groups = _group_by_participant(trials)
    meta = []
    for participant, group in groups.items():
        has_fail = any(t.get("outcome") != "success" for t in group)
        has_success = any(t.get("outcome") == "success" for t in group)
        score = _recovery_score(group)
        meta.append((participant, has_fail and has_success, score))

    meta.sort(
        key=lambda row: (
            0 if row[1] else 1,
            -row[2] if row[2] is not None else 0,
            _participant_sort_key(row[0]),
        )
    )

    ordered = []
    for participant, _, _ in meta:
        ordered.extend(_sort_trials_for_recovery_participant(groups.get(participant, [])))
    return ordered


def _with_meta(trial, **meta):
    enriched = copy.deepcopy(trial)
    enriched.update(meta)
    return enriched


def _progression_cases(trials, limit_per_side=12):
    enriched = [(t, _progression_delta(t)) for t in trials if len(t.get("moves") or []) >= 6]
    improved = sorted(
        [(t, delta) for t, delta in enriched if delta < 0],
        key=lambda item: item[1],
    )[:limit_per_side]
    deteriorated = sorted(
        [(t, delta) for t, delta in enriched if delta > 0],
        key=lambda item: item[1],
        reverse=True,
    )[:limit_per_side]
    return [
        _with_meta(t, progression_label="Became more organized", progression_delta=round(delta, 3))
        for t, delta in improved
    ] + [
        _with_meta(t, progression_label="Became less organized", progression_delta=round(delta, 3))
        for t, delta in deteriorated
    ]


def _extreme_cases_by_outcome(success_trials, failed_trials, limit_per_group=6):
    cases = []
    for label, pool in (("successful", success_trials), ("failed", failed_trials)):
        sorted_pool = sorted(pool, key=lambda t: _numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or [])))
        cases.extend(_with_meta(t, extreme_label=f"Cleanest {label}") for t in sorted_pool[:limit_per_group])
        cases.extend(_with_meta(t, extreme_label=f"Messiest {label}") for t in sorted_pool[-limit_per_group:])
    return cases


def _speed_cases(success_trials, limit_per_side=8):
    sorted_success = sorted(success_trials, key=lambda t: int(t.get("move_count") or 0))
    pick = max(1, min(limit_per_side, len(sorted_success) // 2))
    quickest = [_with_meta(t, speed_label="Quick successful solver") for t in sorted_success[:pick]]
    slowest = [_with_meta(t, speed_label="Slow successful solver") for t in sorted_success[-pick:]]
    return quickest + slowest


def _derive_analysis(raw, analysis_id):
    analyses = raw.get("analysis_types", [])
    by_id = {a.get("id"): a for a in analyses}
    base = copy.deepcopy(by_id.get(analysis_id, {"id": analysis_id, "trials": []}))
    base.update(ANALYSIS_DEFINITIONS.get(analysis_id, {}))

    all_raw = _source_trials(raw)
    valid_raw = [t for t in all_raw if int(t.get("move_count") or 0) >= MIN_VALID_MOVES]
    valid = _dedupe_trials(valid_raw)
    success = [t for t in valid if t.get("outcome") == "success"]
    failed = [t for t in valid if t.get("outcome") != "success"]

    def _mess(t):
        return _numeric(t.get("messiness_score"), _messiness_from_moves(t.get("moves") or []))

    derived = {
        1: sorted(
            (t for t in success if int(t.get("move_count") or 0) >= 15),
            key=lambda t: (_mess(t), -int(t.get("move_count") or 0)),
        )[:80],
        2: sorted(
            (t for t in failed if int(t.get("move_count") or 0) < 15),
            key=lambda t: (-_mess(t), int(t.get("move_count") or 0)),
        )[:80],
        3: success,
        4: _progression_cases(valid),
        5: [
            {**t, "moves": (t.get("moves") or [])[:5], "move_count": len((t.get("moves") or [])[:5])}
            for t in valid
            if (t.get("moves") or [])[:5]
        ][:32],
        6: _sort_trials_for_recovery(_repeated_participant_trials(all_raw)),
        7: _extreme_cases_by_outcome(success, failed, limit_per_group=20),
        8: _speed_cases(success, limit_per_side=60),
        9: (lambda sorted_trials: sorted_trials[:16] + sorted_trials[-16:])(
            sorted(valid, key=_repetition_ratio, reverse=True)
        ),
    }

    fallback = [_normalize_trial(t) for t in base.get("trials", []) if _normalize_trial(t).get("moves")]
    trials = derived.get(analysis_id) or fallback
    base["trials"] = trials if analysis_id == 6 else _dedupe_trials(trials)
    return base


def _json_response(payload, no_store=True):
    response = make_response(jsonify(payload))
    response.headers["Cache-Control"] = "no-store" if no_store else "public, max-age=60"
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


def _all_trials_for_condition(condition: str):
    data = _read_json()
    trials = []
    for analysis in data.get("analysis_types", []):
        for t in analysis.get("trials", []):
            if t.get("condition") == condition:
                trials.append(copy.deepcopy(t))
    return _dedupe_trials(trials)


def _messiness_from_moves(moves):
    points = [(m.get("row"), m.get("col")) for m in moves if isinstance(m.get("row"), int) and isinstance(m.get("col"), int)]
    if not points:
        return 0.0
    x_bar = mean([p[0] for p in points])
    y_bar = mean([p[1] for p in points])
    dists = [((x - x_bar) ** 2 + (y - y_bar) ** 2) for x, y in points]
    return sum(dists) / len(dists)


def _deterioration_slope(moves):
    if len(moves) < 3:
        return 0.0
    running = []
    for i in range(1, len(moves) + 1):
        running.append(_messiness_from_moves(moves[:i]))
    xs = list(range(1, len(running) + 1))
    x_mean = mean(xs)
    y_mean = mean(running)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, running))
    den = sum((x - x_mean) ** 2 for x in xs)
    return (num / den) if den else 0.0


def _percentile(value, population, reverse=False):
    if not population:
        return 50.0
    arr = sorted(population, reverse=reverse)
    lower_or_equal = sum(1 for v in arr if v <= value) if not reverse else sum(1 for v in arr if v >= value)
    return round((lower_or_equal / len(arr)) * 100.0, 1)


def _insight_label(move_count, messiness, slope, blank_count):
    if blank_count > 0 and messiness < 6.9:
        return "Blank-Strategic Solver"
    if slope <= 0.12 and messiness < 5.8:
        return "Structured Explorer"
    if move_count < 8 and messiness > 10.7:
        return "Fast but Chaotic"
    return "Persistent Organizer"


def _device_type(ua: str):
    ua_l = (ua or "").lower()
    return "mobile" if any(k in ua_l for k in ["android", "iphone", "mobile"]) else "desktop"


def _trial_features(trial):
    moves = trial.get("moves") or []
    move_count = int(trial.get("move_count") or len(moves) or 0)
    messiness = float(trial.get("messiness_score") or _messiness_from_moves(moves))
    slope = float(trial.get("organization_deterioration_rate") or _deterioration_slope(moves))
    final_state = trial.get("final_state") or []
    blank_count = int(
        trial.get("blank_card_count")
        or sum(1 for card in final_state if _is_blank(card))
        or sum(1 for m in moves if _is_blank(m))
    )
    return {
        "participant": str(trial.get("participant") or "N/A"),
        "outcome": str(trial.get("outcome") or "unknown"),
        "condition": str(trial.get("condition") or "N/A"),
        "move_count": move_count,
        "messiness_score": messiness,
        "organization_deterioration_rate": slope,
        "blank_cards_used": blank_count,
    }


def _feature_bounds(features):
    if not features:
        return {
            "move_count": (0.0, 1.0),
            "messiness_score": (0.0, 1.0),
            "organization_deterioration_rate": (0.0, 1.0),
            "blank_cards_used": (0.0, 1.0),
        }

    def bounds(key):
        vals = [float(f.get(key, 0.0)) for f in features]
        return (min(vals), max(vals))

    return {
        "move_count": bounds("move_count"),
        "messiness_score": bounds("messiness_score"),
        "organization_deterioration_rate": bounds("organization_deterioration_rate"),
        "blank_cards_used": bounds("blank_cards_used"),
    }


def _norm(value, low, high):
    if high <= low:
        return 0.0
    return (float(value) - float(low)) / (float(high) - float(low))


def _distance(a, b, bounds):
    a_move = _norm(a["move_count"], *bounds["move_count"])
    b_move = _norm(b["move_count"], *bounds["move_count"])
    a_mess = _norm(a["messiness_score"], *bounds["messiness_score"])
    b_mess = _norm(b["messiness_score"], *bounds["messiness_score"])
    a_slope = _norm(a["organization_deterioration_rate"], *bounds["organization_deterioration_rate"])
    b_slope = _norm(b["organization_deterioration_rate"], *bounds["organization_deterioration_rate"])
    a_blank = _norm(a["blank_cards_used"], *bounds["blank_cards_used"])
    b_blank = _norm(b["blank_cards_used"], *bounds["blank_cards_used"])
    return ((a_move - b_move) ** 2 + (a_mess - b_mess) ** 2 + (a_slope - b_slope) ** 2 + (a_blank - b_blank) ** 2) ** 0.5


def _percentile_context(feature, baseline_features):
    baseline_moves = [max(1, int(f["move_count"])) for f in baseline_features] or [1]
    baseline_mess = [float(f["messiness_score"]) for f in baseline_features] or [0.0]
    baseline_blank = [int(f["blank_cards_used"]) for f in baseline_features] or [0]
    baseline_eff = [1.0 / m for m in baseline_moves]
    eff = 1.0 / max(1, int(feature["move_count"]))
    return {
        "messiness": _percentile(float(feature["messiness_score"]), baseline_mess, reverse=True),
        "efficiency": _percentile(eff, baseline_eff, reverse=False),
        "blank_usage": _percentile(int(feature["blank_cards_used"]), baseline_blank, reverse=False),
    }


def _performance_score(percentiles):
    return round(0.6 * float(percentiles.get("messiness", 50.0)) + 0.4 * float(percentiles.get("efficiency", 50.0)), 2)


def _predict_outcome(user_feature, baseline_features):
    if not baseline_features:
        return {"predicted_outcome": "unknown", "confidence": 0.0}

    bounds = _feature_bounds(baseline_features + [user_feature])
    ranked = sorted(
        baseline_features,
        key=lambda f: _distance(user_feature, f, bounds),
    )
    k = min(7, len(ranked))
    neighbors = ranked[:k]
    success_votes = sum(1 for n in neighbors if n.get("outcome") == "success")
    success_prob = (success_votes / k) if k else 0.0
    predicted = "success" if success_prob >= 0.5 else "fail"
    confidence = round(abs(success_prob - 0.5) * 2, 3)
    return {
        "predicted_outcome": predicted,
        "confidence": confidence,
        "success_probability": round(success_prob, 3),
    }


def _build_trial_comparisons(user_feature, baseline_features):
    if not baseline_features:
        return {"peer_rank": {"rank": 1, "total": 1, "percentile": 100.0}, "nearest_trials": [], "better_trials": []}

    bounds = _feature_bounds(baseline_features + [user_feature])
    user_pct = _percentile_context(user_feature, baseline_features)
    user_score = _performance_score(user_pct)

    enriched = []
    for f in baseline_features:
        pct = _percentile_context(f, baseline_features)
        score = _performance_score(pct)
        enriched.append(
            {
                **f,
                "distance": round(_distance(user_feature, f, bounds), 4),
                "performance_score": score,
                "percentiles": pct,
            }
        )

    sorted_by_score = sorted(enriched, key=lambda x: x["performance_score"], reverse=True)
    # only consider baseline trials with at least 7 moves (lighter/noise trials filter)
    better = [t for t in sorted_by_score if t["performance_score"] > user_score and t.get("move_count", 0) >= 7][:10]
    nearest = sorted(enriched, key=lambda x: x["distance"])[:5]
    rank = sum(1 for t in sorted_by_score if t["performance_score"] > user_score) + 1
    total = len(sorted_by_score) + 1
    percentile = round(((total - rank + 1) / total) * 100.0, 1)

    return {
        "user_performance_score": user_score,
        "peer_rank": {"rank": rank, "total": total, "percentile": percentile},
        "nearest_trials": [
            {
                "participant": t["participant"],
                "outcome": t["outcome"],
                "move_count": t["move_count"],
                "messiness_score": round(float(t["messiness_score"]), 3),
                "condition": t["condition"],
                "distance": t["distance"],
                "performance_score": t["performance_score"],
            }
            for t in nearest
        ],
        "better_trials": [
            {
                "participant": t["participant"],
                "outcome": t["outcome"],
                "move_count": t["move_count"],
                "messiness_score": round(float(t["messiness_score"]), 3),
                "condition": t["condition"],
                "performance_score": t["performance_score"],
            }
            for t in better
        ],
    }


def _require_playground():
    if not ENABLE_PLAYGROUND:
        return _json_response({"error": "Playground disabled"}, no_store=True), 404
    return None


# ── Main routes ──────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    return render_template("behavioral/index.html", parent_origin=PARENT_ORIGIN)


# Allow /cards-analysis/ and /cards-analysis/index.html as aliases
@bp.route("/cards-analysis/")
@bp.route("/cards-analysis/index.html")
def cards_analysis():
    return render_template("behavioral/index.html", parent_origin=PARENT_ORIGIN)


@bp.route("/play")
def play():
    if not ENABLE_PLAYGROUND:
        return render_template("behavioral/play_disabled.html"), 404
    return render_template(
        "behavioral/play.html",
        parent_origin=PARENT_ORIGIN,
        enable_history=ENABLE_HISTORY_TAB,
        enable_gif=ENABLE_GIF_EXPORT,
    )


# Serve raw data file so /data/card_analysis_data.json works as a static URL
@bp.route("/data/card_analysis_data.json")
def data_file():
    return _json_response(_read_json(), no_store=False)


# ── API routes ───────────────────────────────────────────────────────────────

@bp.route("/api/data")
def api_data():
    return _json_response(_read_json(), no_store=True)


@bp.route("/api/statistics")
def api_statistics():
    return _json_response(_read_json().get("statistics", {}), no_store=True)


@bp.route("/api/analysis/<int:analysis_id>")
def api_analysis(analysis_id):
    raw = _read_json()
    if 1 <= analysis_id <= 9:
        return _json_response(_derive_analysis(raw, analysis_id), no_store=True)
    return _json_response({"error": "Analysis not found"}, no_store=True), 404


@bp.route("/api/play/session/start", methods=["POST"])
def api_play_start():
    blocked = _require_playground()
    if blocked:
        return blocked

    body = request.get_json(silent=True) or {}
    condition = body.get("condition", "KQJB")
    display_name = (body.get("display_name") or "").strip()[:80] or None
    visitor_id = (body.get("visitor_id") or request.cookies.get("cards_visitor_token") or str(uuid.uuid4())).strip()
    session_id = str(uuid.uuid4())
    now = utc_now_iso()
    ua = request.headers.get("User-Agent", "")

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT visitor_id FROM visitors WHERE visitor_id = ?", (visitor_id,))
    exists = cur.fetchone()
    if exists:
        cur.execute(
            "UPDATE visitors SET display_name = COALESCE(?, display_name), updated_at = ?, last_seen_at = ? WHERE visitor_id = ?",
            (display_name, now, now, visitor_id),
        )
    else:
        cur.execute(
            "INSERT INTO visitors(visitor_id, display_name, created_at, updated_at, last_seen_at) VALUES(?,?,?,?,?)",
            (visitor_id, display_name, now, now, now),
        )

    cur.execute(
        """
        INSERT INTO play_sessions(session_id, visitor_id, condition, status, started_at, user_agent, device_type)
        VALUES(?,?,?,?,?,?,?)
        """,
        (session_id, visitor_id, condition, "in_progress", now, ua, _device_type(ua)),
    )
    conn.commit()
    conn.close()

    payload = {
        "session_id": session_id,
        "visitor_id": visitor_id,
        "condition": condition,
        "started_at": now,
    }
    response = _json_response(payload, no_store=True)
    response.set_cookie("cards_visitor_token", visitor_id, max_age=60 * 60 * 24 * 365, httponly=False, samesite="Lax")
    return response


@bp.route("/api/play/session/<session_id>/move", methods=["POST"])
def api_play_move(session_id):
    blocked = _require_playground()
    if blocked:
        return blocked

    body = request.get_json(silent=True) or {}
    try:
        move_index = int(body.get("move_index"))
        row = int(body.get("row"))
        col = int(body.get("col"))
    except (TypeError, ValueError):
        return _json_response({"error": "Invalid move fields"}, no_store=True), 400

    if not (0 <= row <= 7 and 0 <= col <= 7 and move_index >= 0):
        return _json_response({"error": "Move out of bounds"}, no_store=True), 400

    value = body.get("value")
    suit_symbol = body.get("suit_symbol")
    color = body.get("color")
    is_blank = 1 if _is_blank(body) else 0
    now = utc_now_iso()

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT status FROM play_sessions WHERE session_id = ?", (session_id,))
    row_status = cur.fetchone()
    if not row_status:
        conn.close()
        return _json_response({"error": "Session not found"}, no_store=True), 404
    if row_status["status"] == "completed":
        conn.close()
        return _json_response({"error": "Session already completed"}, no_store=True), 409

    cur.execute(
        """
        INSERT INTO moves(session_id, move_index, row, col, value, suit_symbol, color, is_blank, created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (session_id, move_index, row, col, value, suit_symbol, color, is_blank, now),
    )
    conn.commit()
    conn.close()
    return _json_response({"ok": True}, no_store=True)


@bp.route("/api/play/session/<session_id>/complete", methods=["POST"])
def api_play_complete(session_id):
    blocked = _require_playground()
    if blocked:
        return blocked

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM play_sessions WHERE session_id = ?", (session_id,))
    session = cur.fetchone()
    if not session:
        conn.close()
        return _json_response({"error": "Session not found"}, no_store=True), 404

    if session["status"] == "completed":
        result = json.loads(session["result_json"] or "{}")
        conn.close()
        return _json_response({"session_id": session_id, "result": result}, no_store=True)

    cur.execute("SELECT * FROM moves WHERE session_id = ? ORDER BY move_index ASC", (session_id,))
    rows = cur.fetchall()
    moves = [
        {
            "move_index": r["move_index"],
            "row": r["row"],
            "col": r["col"],
            "value": r["value"],
            "suit_symbol": r["suit_symbol"],
            "color": r["color"],
            "is_blank": bool(r["is_blank"]),
        }
        for r in rows
    ]

    condition = session["condition"]
    baseline_trials = _all_trials_for_condition(condition)
    baseline_features = [_trial_features(t) for t in baseline_trials]

    move_count = len(moves)
    messiness = _messiness_from_moves(moves)
    slope = _deterioration_slope(moves)
    blank_count = sum(1 for m in moves if _is_blank(m))
    current_feature = {
        "participant": "YOU",
        "outcome": "unknown",
        "condition": condition,
        "move_count": move_count,
        "messiness_score": messiness,
        "organization_deterioration_rate": slope,
        "blank_cards_used": blank_count,
    }
    percentiles = _percentile_context(current_feature, baseline_features)
    score = _performance_score(percentiles)
    outcome_prediction = _predict_outcome(current_feature, baseline_features)
    comparisons = _build_trial_comparisons(current_feature, baseline_features)

    result = {
        "move_count": move_count,
        "messiness_score": round(messiness, 4),
        "organization_deterioration_rate": round(slope, 4),
        "blank_cards_used": blank_count,
        "condition": condition,
        "condition_matched_percentile": percentiles,
        "performance_score": score,
        "trial_outcome": outcome_prediction["predicted_outcome"],
        "outcome_confidence": outcome_prediction["confidence"],
        "success_probability": outcome_prediction["success_probability"],
        "peer_rank": comparisons["peer_rank"],
        "nearest_trials": comparisons["nearest_trials"],
        "better_trials": comparisons["better_trials"],
        "insight_label": _insight_label(move_count, messiness, slope, blank_count),
    }

    now = utc_now_iso()
    cur.execute(
        "UPDATE play_sessions SET status = ?, completed_at = ?, result_json = ? WHERE session_id = ?",
        ("completed", now, json.dumps(result), session_id),
    )
    conn.commit()
    conn.close()

    return _json_response({"session_id": session_id, "result": result}, no_store=True)


@bp.route("/api/play/session/<session_id>/result")
def api_play_result(session_id):
    blocked = _require_playground()
    if blocked:
        return blocked

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT result_json, status FROM play_sessions WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return _json_response({"error": "Session not found"}, no_store=True), 404
    if row["status"] != "completed":
        return _json_response({"error": "Session not completed"}, no_store=True), 409
    return _json_response({"session_id": session_id, "result": json.loads(row["result_json"] or "{}")}, no_store=True)


@bp.route("/api/play/session/<session_id>/compare-participant/<participant_id>")
def api_play_compare_participant(session_id, participant_id):
    blocked = _require_playground()
    if blocked:
        return blocked

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT result_json, condition, status FROM play_sessions WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return _json_response({"error": "Session not found"}, no_store=True), 404
    if row["status"] != "completed":
        return _json_response({"error": "Session not completed"}, no_store=True), 409

    user_result = json.loads(row["result_json"] or "{}")
    condition = user_result.get("condition") or row["condition"]
    baseline_features = [_trial_features(t) for t in _all_trials_for_condition(condition)]
    target = [f for f in baseline_features if str(f.get("participant")) == str(participant_id)]
    if not target:
        return _json_response({"error": "Participant not found in this condition"}, no_store=True), 404

    user_feature = {
        "participant": "YOU",
        "outcome": user_result.get("trial_outcome", "unknown"),
        "condition": condition,
        "move_count": int(user_result.get("move_count", 0)),
        "messiness_score": float(user_result.get("messiness_score", 0.0)),
        "organization_deterioration_rate": float(user_result.get("organization_deterioration_rate", 0.0)),
        "blank_cards_used": int(user_result.get("blank_cards_used", 0)),
    }

    def avg(key):
        vals = [float(t.get(key, 0.0)) for t in target]
        return (sum(vals) / len(vals)) if vals else 0.0

    participant_summary = {
        "participant": str(participant_id),
        "condition": condition,
        "trial_count": len(target),
        "success_rate": round(100.0 * sum(1 for t in target if t.get("outcome") == "success") / len(target), 1),
        "avg_move_count": round(avg("move_count"), 2),
        "avg_messiness_score": round(avg("messiness_score"), 3),
        "avg_deterioration_rate": round(avg("organization_deterioration_rate"), 4),
        "avg_blank_cards_used": round(avg("blank_cards_used"), 2),
    }

    compare = {
        "move_count_delta": round(user_feature["move_count"] - participant_summary["avg_move_count"], 2),
        "messiness_delta": round(user_feature["messiness_score"] - participant_summary["avg_messiness_score"], 3),
        "deterioration_delta": round(
            user_feature["organization_deterioration_rate"] - participant_summary["avg_deterioration_rate"], 4
        ),
        "blank_cards_delta": round(user_feature["blank_cards_used"] - participant_summary["avg_blank_cards_used"], 2),
    }

    return _json_response({"participant_summary": participant_summary, "comparison": compare}, no_store=True)


@bp.route("/api/play/history")
def api_play_history():
    blocked = _require_playground()
    if blocked:
        return blocked
    if not ENABLE_HISTORY_TAB:
        return _json_response({"sessions": []}, no_store=True)

    visitor_id = (request.args.get("visitor_id") or request.cookies.get("cards_visitor_token") or "").strip()
    if not visitor_id:
        return _json_response({"sessions": []}, no_store=True)

    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.session_id, s.condition, s.status, s.started_at, s.completed_at, s.result_json, v.display_name
        FROM play_sessions s
        LEFT JOIN visitors v ON v.visitor_id = s.visitor_id
        WHERE s.visitor_id = ?
        ORDER BY s.started_at DESC
        LIMIT 100
        """,
        (visitor_id,),
    )
    rows = cur.fetchall()
    conn.close()
    sessions = []
    for r in rows:
        sessions.append(
            {
                "session_id": r["session_id"],
                "condition": r["condition"],
                "status": r["status"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "display_name": r["display_name"],
                "result": json.loads(r["result_json"] or "{}") if r["result_json"] else None,
            }
        )
    return _json_response({"sessions": sessions}, no_store=True)


def _build_board_states(moves):
    states = []
    board = {}
    for i, m in enumerate(moves):
        key = (m.get("row"), m.get("col"))
        board[key] = m
        states.append((i, dict(board)))
    return states


def _draw_frame(state, current_idx):
    size = 420
    padding = 20
    cell = 44
    header = 20
    img = Image.new("RGB", (size, size), color=(13, 27, 42))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    for i in range(8):
        x = padding + header + i * cell
        y = padding
        draw.rectangle([x, y, x + cell - 2, y + header - 2], fill=(20, 108, 148))
        draw.text((x + 14, y + 5), str(i), fill="white", font=font)
        x2 = padding
        y2 = padding + header + i * cell
        draw.rectangle([x2, y2, x2 + header - 2, y2 + cell - 2], fill=(20, 108, 148))
        draw.text((x2 + 5, y2 + 14), str(i), fill="white", font=font)

    for r in range(8):
        for c in range(8):
            x = padding + header + c * cell
            y = padding + header + r * cell
            card = state.get((r, c))
            if card:
                blank = _is_blank(card)
                fill = (158, 158, 158) if blank else (255, 255, 255)
                draw.rectangle([x, y, x + cell - 2, y + cell - 2], fill=fill, outline=(180, 180, 180))
                txt = "\u25a1" if blank else f"{card.get('value','')}{card.get('suit_symbol','')}"
                clr = (255, 255, 255) if blank else ((220, 38, 38) if card.get("color") == "red" else (17, 24, 39))
                draw.text((x + 7, y + 14), txt, fill=clr, font=font)
                if card.get("move_index") == current_idx:
                    draw.rectangle([x, y, x + cell - 2, y + cell - 2], outline=(244, 162, 97), width=3)
            else:
                draw.rectangle([x, y, x + cell - 2, y + cell - 2], fill=(28, 76, 60), outline=(60, 120, 95))

    return img


@bp.route("/api/play/export/gif", methods=["POST"])
def api_export_gif():
    blocked = _require_playground()
    if blocked:
        return blocked
    if not ENABLE_GIF_EXPORT:
        return _json_response({"error": "GIF export disabled"}, no_store=True), 404

    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    if not session_id:
        return _json_response({"error": "session_id required"}, no_store=True), 400

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT row, col, value, suit_symbol, color, is_blank, move_index FROM moves WHERE session_id = ? ORDER BY move_index ASC", (session_id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return _json_response({"error": "No moves found"}, no_store=True), 404

    moves = [
        {
            "row": int(r["row"]),
            "col": int(r["col"]),
            "value": r["value"],
            "suit_symbol": r["suit_symbol"],
            "color": r["color"],
            "is_blank": bool(r["is_blank"]),
            "move_index": int(r["move_index"]),
        }
        for r in rows
    ][:180]

    frames = []
    for idx, board_state in _build_board_states(moves):
        frames.append(_draw_frame(board_state, idx))
    if not frames:
        return _json_response({"error": "No frames generated"}, no_store=True), 500

    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=350, loop=0)
    output.seek(0)
    return send_file(output, mimetype="image/gif", as_attachment=True, download_name=f"cards-session-{session_id}.gif")


@bp.route("/health")
def health():
    return _json_response(
        {
            "status": "ok",
            "data_path": str(DATA_PATH),
            "db_path": str(DB_PATH),
            "enable_playground": ENABLE_PLAYGROUND,
            "enable_gif_export": ENABLE_GIF_EXPORT,
            "enable_history_tab": ENABLE_HISTORY_TAB,
        },
        no_store=False,
    )


ensure_db()
