"""
Card Placement Analysis - Flask Web Application
Main Application File

Place this file as 'app.py' in your flask_card_analysis folder.
"""

import json
import os
import sys
import hashlib
import shutil
import tempfile
import threading
from pathlib import Path
import re
import ast
from collections import Counter
import base64
import io
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.animation import FuncAnimation
import matplotlib.pyplot as plt
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server

app = Flask(__name__)

from behavioral_app import bp as behavioral_bp
app.register_blueprint(behavioral_bp, url_prefix='/behavioral-app')
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'
# Cap upload size — CardsDataset.csv is ~1.7 MB; 32 MB leaves room for richer datasets
# without exposing the process to oversized payloads.
app.config.setdefault('MAX_CONTENT_LENGTH', 32 * 1024 * 1024)

# Soft import so a missing pandas / process_dataset doesn't block boot. The
# route returns 503 if the pipeline isn't importable instead of widening the
# except to swallow every error.
try:
    from process_dataset import (
        process_csv_to_json,
        atomic_write_json,
        ProcessingError as DatasetProcessingError,
    )
    _CSV_PIPELINE_AVAILABLE = True
except Exception as _proc_import_err:  # pragma: no cover
    process_csv_to_json = None
    atomic_write_json = None
    DatasetProcessingError = RuntimeError
    _CSV_PIPELINE_AVAILABLE = False
    print(f"[upload-dataset] WARNING: process_dataset import failed: {_proc_import_err}")

# Feature flag so deployments can lock the route without removing code.
def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ENABLE_DATASET_UPLOAD = _env_flag("ENABLE_DATASET_UPLOAD", True)

from behavioral_app import DATA_PATH as _ANALYSIS_DATA_PATH

# Paths for file regeneration / backup
_BASE_DIR = Path(__file__).resolve().parent
_DATA_DIR = _BASE_DIR / 'data'
_CSV_PATH = _DATA_DIR / 'CardsDataset.csv'
_EXCEL_PATH = _DATA_DIR / 'task1_B_condition_positioned_blank_cards.xlsx'
_JSON_PATH = _DATA_DIR / 'card_analysis_data.json'

# Global variables
df = None
visualizer = None
blank_patterns_df = None  # from blank_patterns part


class CardPlacementVisualizer:
    """
    Card placement visualizer adapted for Flask web application.
    Handles all visualization logic for card sorting trials.
    """

    def __init__(self, df, figure_size=(7, 7)):
        """
        Initialize the visualizer.

        Parameters:
        -----------
        df : pandas.DataFrame
            The preprocessed card sorting dataset
        figure_size : tuple
            Figure dimensions (width, height) in inches
        """
        self.df = df
        self.grid_size = 8
        self.figure_size = figure_size

        # Card color scheme
        self.card_colors = {
            'queen': '#FF6B6B',    # Red
            'king': '#4ECDC4',     # Teal
            'jack': '#45B7D1',     # Blue
            'blank': '#757575',    # Dark Gray (placeholder tone)
            'empty': '#F7F7F7'     # Light Gray
        }

        # Suit symbols
        self.suit_symbols = {
            'spades': '♠',
            'hearts': '♥',
            'diamonds': '♦',
            'clubs': '♣'
        }

    def parse_position(self, position_str):
        """
        Parse position string to grid coordinates.

        Parameters:
        -----------
        position_str : str
            Position in format 'A1', 'B3', etc. or 'Off Grid'

        Returns:
        --------
        tuple or None : (row, col) coordinates or None if off-grid
        """
        if pd.isna(position_str) or 'Off Grid' in str(position_str):
            return None

        try:
            position_str = str(position_str).strip()
            if len(position_str) >= 2:
                col_letter = position_str[0]
                row_num = position_str[1:]
                col = ord(col_letter.upper()) - ord('A')
                row = int(row_num) - 1

                if 0 <= row < self.grid_size and 0 <= col < self.grid_size:
                    return (row, col)
        except:
            pass
        return None

    def extract_card_info(self, move_str):
        """
        Extract card information from movement string.
        Handles cards with suits (queen_spades_A1) and without suits (blank_A1).

        Parameters:
        -----------
        move_str : str
            Movement string like 'queen_spades_A1' or 'blank_A1'

        Returns:
        --------
        dict or None : Card information dictionary
        """
        if pd.isna(move_str) or move_str == '':
            return None

        try:
            parts = str(move_str).split('_')
            if len(parts) >= 2:
                card_rank = parts[0].lower()

                # Check if this is a blank card (only 2 parts: blank_position)
                if card_rank == 'blank' and len(parts) == 2:
                    suit = ''
                    position = parts[1]
                # Regular card with suit (3+ parts: rank_suit_position)
                elif len(parts) >= 3:
                    suit = parts[1]
                    position = parts[-1]
                # Card with suit but no position (2 parts: rank_suit)
                else:
                    suit = parts[1] if len(parts) > 1 else ''
                    position = 'Off Grid'

                color = self.card_colors.get(
                    card_rank, self.card_colors['empty'])

                # Blank cards don't have suit symbols
                symbol = '' if card_rank == 'blank' else self.suit_symbols.get(
                    suit, '')

                return {
                    'rank': card_rank,
                    'suit': suit,
                    'position': position,
                    'color': color,
                    'symbol': symbol
                }
        except:
            pass
        return None

    def create_grid_state(self, movements, step):
        """
        Create grid state at a specific step in the trial.

        Parameters:
        -----------
        movements : list
            List of movement strings
        step : int
            Step number (0 to len(movements))

        Returns:
        --------
        numpy.ndarray : Grid state with card information
        """
        grid = np.empty((self.grid_size, self.grid_size), dtype=object)
        card_positions = {}

        for i in range(min(step, len(movements))):
            card_info = self.extract_card_info(movements[i])
            if card_info:
                position_coords = self.parse_position(card_info['position'])
                card_key = f"{card_info['rank']}_{card_info['suit']}"

                # Remove card from previous position ONLY if it's still there
                if card_key in card_positions:
                    old_row, old_col = card_positions[card_key]
                    if old_row is not None:
                        # Only remove if this card is still at that position
                        if grid[old_row, old_col] is not None:
                            old_card_key = f"{grid[old_row, old_col]['rank']}_{grid[old_row, old_col]['suit']}"
                            if old_card_key == card_key:
                                grid[old_row, old_col] = None

                # Place card at new position (overwrites whatever was there)
                if position_coords:
                    row, col = position_coords
                    grid[row, col] = card_info
                    card_positions[card_key] = (row, col)
                else:
                    card_positions[card_key] = (None, None)

        return grid

    def add_blank_cards_to_grid(self, grid, final_positions):
        """
        Add blank cards from final_card_position_codes_1 to the grid.
        This handles cases where blank cards appear in final position but weren't moved.

        Parameters:
        -----------
        grid : numpy.ndarray
            Current grid state
        final_positions : list
            List of final position strings (including blanks)

        Returns:
        --------
        numpy.ndarray : Grid with blank cards added
        """
        if not final_positions:
            return grid

        for position_str in final_positions:
            card_info = self.extract_card_info(position_str)
            if card_info and card_info['rank'] == 'blank':
                position_coords = self.parse_position(card_info['position'])
                if position_coords:
                    row, col = position_coords
                    # Only add blank if position is currently empty
                    if grid[row, col] is None:
                        grid[row, col] = card_info

        return grid

    def plot_grid(self, grid, ax, step, total_steps, trial_info):
        """
        Plot the grid state on a matplotlib axis.
        Thread-safe implementation. Renders realistic playing-card faces
        on a dark board matching the behavioural analysis aesthetic.
        """
        from matplotlib.patches import Rectangle

        ax.clear()
        ax.set_facecolor("#0D1B2A")

        # Dark navy board background
        board_bg = Rectangle((-0.5, -0.5), 8, 8, facecolor="#0D1B2A", zorder=0)
        ax.add_patch(board_bg)

        # Draw empty cells: dark green
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if grid[i, j] is None:
                    cell = Rectangle((j - 0.46, i - 0.46), 0.92, 0.92,
                                     facecolor="#1C4C3C", edgecolor="#2D6B55",
                                     linewidth=0.5, alpha=0.7, zorder=1)
                    ax.add_patch(cell)

        # Draw gridlines -- subtle teal
        for i in range(self.grid_size + 1):
            ax.axhline(i - 0.5, color="#146C94", linewidth=0.5, alpha=0.35, zorder=2)
            ax.axvline(i - 0.5, color="#146C94", linewidth=0.5, alpha=0.35, zorder=2)

        # Draw cards
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if grid[i, j] is not None:
                    card_info = grid[i, j]
                    is_blank = card_info.get("rank", "") == "blank"

                    # Card face
                    face_color = "#9CA3AF" if is_blank else "#FFFFFF"
                    card_rect = Rectangle((j - 0.44, i - 0.44), 0.88, 0.88,
                                          facecolor=face_color,
                                          edgecolor="#3B4F6B" if not is_blank else "#64748B",
                                          linewidth=1.2,
                                          zorder=3)
                    ax.add_patch(card_rect)

                    # Suit symbol color
                    suit = card_info.get("suit", "").lower()
                    symbol = card_info.get("symbol", "")
                    is_red_suit = suit in ("hearts", "diamonds")
                    text_color = "#DC2626" if is_red_suit else "#111827"

                    if is_blank:
                        # Blank card: show "?" in white on gray
                        ax.text(j, i, "?", ha="center", va="center",
                                fontsize=12, fontweight="bold",
                                color="#FFFFFF", zorder=4)
                    else:
                        # Rank letter (top-left of card)
                        rank_text = card_info["rank"][0].upper() if card_info.get("rank") else "?"
                        ax.text(j - 0.28, i - 0.30, rank_text,
                                ha="center", va="center",
                                fontsize=11, fontweight="bold",
                                color=text_color, zorder=4)
                        # Suit symbol (centered on card)
                        if symbol:
                            ax.text(j, i + 0.04, symbol,
                                    ha="center", va="center",
                                    fontsize=15, fontweight="bold",
                                    color=text_color, zorder=4)

        # Row labels (1-8) on left
        for i in range(self.grid_size):
            ax.text(-0.75, i, str(i + 1),
                    ha="center", va="center",
                    fontsize=8, fontweight="bold",
                    color="#146C94", zorder=4)

        # Column labels (A-H) on top
        for j in range(self.grid_size):
            ax.text(j, -0.75, chr(65 + j),
                    ha="center", va="center",
                    fontsize=8, fontweight="bold",
                    color="#146C94", zorder=4)

        ax.set_xlim(-1, self.grid_size)
        ax.set_ylim(self.grid_size, -1)
        ax.axis("off")

        # Title -- success green / fail red / neutral slate
        participant = trial_info.get("participant", "N/A")
        trial_n = trial_info.get("trialN", "N/A")
        condition = trial_info.get("condition", "N/A")
        success = trial_info.get("overall_correct", 0)
        is_pattern = trial_info.get("is_pattern", False)

        success_text = "Success" if success == 1 else "Failed"
        title_color = "#059669" if success == 1 else "#DC2626"

        if is_pattern:
            title = f"Pattern {participant}  |  Frequency: {trial_n}  |  {condition}  |  {success_text}"
        else:
            title = f"Participant {participant}  |  Trial {trial_n}  |  {condition}  |  {success_text}"
            if step is not None and total_steps is not None:
                title += f"  |  Step {step}/{total_steps}"

        ax.set_title(title, fontsize=10, fontweight="bold", pad=12,
                     color=title_color if (is_pattern or step == total_steps) else "#F8FAFC")

    def generate_static_image(self, participant, trial_n, step=None):
        """
        Generate a static PNG image of the trial grid.
        Thread-safe implementation using Figure instead of global plt.

        Parameters:
        -----------
        participant : int
            Participant ID
        trial_n : int
            Trial number
        step : int, optional
            Specific step to show (default: final step)

        Returns:
        --------
        io.BytesIO : PNG image bytes
        """
        trial_data = self.df[(self.df['participant'] == participant) &
                             (self.df['trialN'] == trial_n)]

        if trial_data.empty:
            return None

        trial_data = trial_data.iloc[0]
        movements = trial_data['movement_codes']

        if not movements:
            return None

        if step is None:
            step = len(movements)

        # Create isolated figure (thread-safe)
        fig = Figure(figsize=self.figure_size, facecolor='white')
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

        trial_info = {
            'participant': participant,
            'trialN': trial_n,
            'condition': trial_data.get('condition', 'N/A'),
            'overall_correct': trial_data.get('overall_correct', 0)
        }

        grid = self.create_grid_state(movements, step)

        # Add blank cards from final position if showing final state
        if step == len(movements):
            final_positions = trial_data.get('final_card_position_codes_1', [])
            grid = self.add_blank_cards_to_grid(grid, final_positions)

        self.plot_grid(grid, ax, step, len(movements), trial_info)

        fig.tight_layout()

        # Save to bytes using canvas (thread-safe)
        img_bytes = io.BytesIO()
        canvas.print_png(img_bytes)
        img_bytes.seek(0)

        return img_bytes

# Data preprocessing functions


def safe_literal_eval(x):
    """Safely evaluate string representations of Python literals."""
    if pd.isna(x) or x == '' or x == '[]':
        return []
    try:
        return ast.literal_eval(x) if isinstance(x, str) else x
    except:
        return []


def clean_card_positions(movement_list):
    """Clean card position labels by removing extraneous characters."""
    if not isinstance(movement_list, list):
        return movement_list

    cleaned_list = []
    for move in movement_list:
        if not isinstance(move, str):
            cleaned_list.append(move)
            continue

        parts = move.split('_')

        # Remove lowercase prefix from last part
        if re.match(r'^[a-z]', parts[-1]):
            parts[-1] = parts[-1][1:]
        # Remove standalone 'c' in second-to-last position
        elif len(parts) >= 2 and parts[-2] == 'c':
            parts.pop(-2)

        cleaned_list.append("_".join(parts))

    return cleaned_list


# Cache for pattern counters to avoid recomputing
_pattern_cache = {'success': None, 'failure': None}


def get_pattern_counter(pattern_type):
    """
    Get pattern counter with caching to avoid recomputing.
    Returns Counter object with all patterns.
    """
    global _pattern_cache

    # Check cache
    if _pattern_cache[pattern_type] is not None:
        return _pattern_cache[pattern_type]

    # Compute patterns
    if pattern_type == 'success':
        subset_df = df[df['overall_correct'] == 1]
    else:
        subset_df = df[df['overall_correct'] == 0]

    position_counter = Counter()
    for _, row in subset_df.iterrows():
        final_positions = row['final_card_position_codes_1']
        if final_positions and len(final_positions) > 0:
            position_tuple = tuple(sorted(final_positions))
            position_counter[position_tuple] += 1

    # Cache it
    _pattern_cache[pattern_type] = position_counter

    return position_counter


def load_data():
    """Load and preprocess the dataset."""
    global df, visualizer, _pattern_cache
    _pattern_cache = {'success': None, 'failure': None}

    if not _CSV_PATH.exists():
        return False

    # Load CSV
    df = pd.read_csv(_CSV_PATH)

    # Preprocess movement columns
    df['movement_codes'] = df['movement_codes'].apply(safe_literal_eval)
    df['movement_codes'] = df['movement_codes'].apply(clean_card_positions)
    df['final_card_position_codes_1'] = df['final_card_position_codes_1'].apply(
        safe_literal_eval)
    df['final_card_position_codes_1'] = df['final_card_position_codes_1'].apply(
        clean_card_positions)

    # Create visualizer
    visualizer = CardPlacementVisualizer(df)

    return True

###################################################################################
###################################################################################

# =========================================================
# BLANK PATTERNS HELPERS
# =========================================================


SUIT_SYMBOL = {
    "spades": "♠",
    "diamonds": "♦",
    "hearts": "♥",
    "clubs": "♣"
}

RANK_SYMBOL = {
    "king": "K",
    "queen": "Q",
    "jack": "J",
    "blank": "B"
}

ROWS = {letter: idx for idx, letter in enumerate("ABCDEFGH")}


def regenerate_excel_from_csv(source_df):
    """Recreate the Blank Patterns Excel from the in-memory CSV.

    The Excel shipped with the app is simply the full CardsDataset.csv
    filtered to the two B‑condition tasks (KQJB, KQB).  Regenerating it
    here keeps the Blank Patterns tab in sync with whatever CSV is
    currently loaded — no separate Excel upload needed.
    """
    if source_df is None or source_df.empty:
        return False
    try:
        _B_CONDITIONS = {'KQJB', 'KQB'}
        subset = source_df[source_df['condition'].isin(_B_CONDITIONS)]
        if subset.empty:
            print("[Excel regen] No KQJB/KQB rows found — skipping Excel write.")
            return False
        subset.to_excel(
            _EXCEL_PATH,
            sheet_name='B_condition_blank_cards',
            index=False,
        )
        print(f"[Excel regen] Wrote {len(subset)} rows to {_EXCEL_PATH.name}")
        return True
    except Exception as exc:
        print(f"[Excel regen] FAILED: {exc}")
        return False


def load_blank_patterns_data():
    """
    Load and preprocess the Excel file for blank pattern analysis.
    """
    global blank_patterns_df

    excel_path = _EXCEL_PATH
    sheet_name = "B_condition_blank_cards"

    if not excel_path.exists():
        print(f"[Blank Patterns] File not found: {excel_path}")
        blank_patterns_df = pd.DataFrame()
        return

    try:
        bp_df = pd.read_excel(excel_path, sheet_name=sheet_name)

        # Clean / normalize
        bp_df["participant"] = bp_df["participant"].astype(str)
        bp_df["trialN"] = bp_df["trialN"].astype(str)
        bp_df["condition"] = bp_df["condition"].astype(str)

        # Convert stringified NaNs to actual NaN
        bp_df["condition"] = bp_df["condition"].replace({
            "nan": np.nan,
            "NaN": np.nan,
            "": np.nan
        })

        # Build pattern column
        bp_df["pattern"] = bp_df["final_card_position_codes_1"].apply(
            blank_pattern)

        blank_patterns_df = bp_df
        print(
            f"[Blank Patterns] Loaded {len(blank_patterns_df)} rows from {excel_path}")

    except Exception as e:
        print(f"[Blank Patterns] Error loading Excel file: {e}")
        blank_patterns_df = pd.DataFrame()


def split_tokens_blank(cell):
    if pd.isna(cell):
        return []

    if isinstance(cell, str):
        s = cell.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                tokens = ast.literal_eval(s)
            except Exception:
                tokens = re.split(r"[;,]", s.strip("[]"))
        else:
            tokens = re.split(r"[;,]", s)
    else:
        tokens = [str(cell)]

    return [t.strip() for t in tokens if str(t).strip()]


def parse_cards_blank(cell):
    """
    Parse tokens and extract:
      position, rank, suit, symbol, raw
    """
    tokens = split_tokens_blank(cell)
    cards = []

    for tok in tokens:
        t = tok.lower().replace(" ", "")
        m = re.search(r'([a-h]\d{1,2})$', t)
        if not m:
            continue

        pos = m.group(1).upper()
        prefix = t[:m.start()]

        if prefix.startswith("blank"):
            rank = "blank"
        elif prefix.startswith("king"):
            rank = "king"
        elif prefix.startswith("queen"):
            rank = "queen"
        elif prefix.startswith("jack"):
            rank = "jack"
        else:
            rank = "other"

        suit = None
        for s in ["spades", "diamonds", "hearts", "clubs"]:
            if s in prefix:
                suit = s
                break

        if rank == "blank":
            sym = "B"
        elif rank in ["king", "queen", "jack"]:
            sym = f"{RANK_SYMBOL.get(rank, '?')}{SUIT_SYMBOL.get(suit, '')}"
        else:
            sym = "?"

        cards.append({
            "pos": pos,
            "rank": rank,
            "suit": suit,
            "sym": sym,
            "raw": tok
        })

    return cards


def blank_pattern(cell):
    cards = parse_cards_blank(cell)
    positions = [c["pos"] for c in cards if c["rank"] == "blank"]
    return "-".join(sorted(positions))


def df_for_condition_blank(cond):
    if blank_patterns_df is None or blank_patterns_df.empty:
        return pd.DataFrame()

    if cond == "All":
        return blank_patterns_df.copy()

    return blank_patterns_df[blank_patterns_df["condition"] == cond].copy()


def compute_sr_n_for_condition_blank(cond):
    d = df_for_condition_blank(cond)

    if d.empty:
        return {}, {}

    d = d[d["pattern"].astype(str).str.strip() != ""]

    if d.empty:
        return {}, {}

    stats = (
        d.groupby("pattern")["overall_correct"]
        .agg(SR="mean", N="count")
        .reset_index()
    )

    sr_map = dict(zip(stats["pattern"], stats["SR"]))
    n_map = dict(zip(stats["pattern"], stats["N"]))
    return sr_map, n_map


def get_blank_conditions():
    if blank_patterns_df is None or blank_patterns_df.empty:
        return ["All"]

    real_conditions = sorted([
        c for c in blank_patterns_df["condition"].dropna().unique().tolist()
        if str(c).strip() != ""
    ])
    return ["All"] + real_conditions


def patterns_for_condition_blank(cond):
    d = df_for_condition_blank(cond)
    if d.empty:
        return []

    vals = sorted([
        p for p in d["pattern"].unique().tolist()
        if p and str(p).strip() != ""
    ])
    return vals


def participants_for_condition_pattern_blank(cond, pat):
    d = df_for_condition_blank(cond)
    if d.empty or not pat:
        return []

    d = d[d["pattern"] == pat]
    return sorted(d["participant"].unique().tolist())


def trials_for_condition_pattern_participant_blank(cond, pat, part):
    d = df_for_condition_blank(cond)
    if d.empty or not pat or not part:
        return []

    d = d[(d["pattern"] == pat) & (d["participant"] == part)]
    return sorted(d["trialN"].unique().tolist())


def build_grid_payload_blank(cards):
    grid_map = {}

    for idx, c in enumerate(cards, start=1):
        pos = c["pos"]
        row_letter = pos[0]
        try:
            col_num = int(pos[1:])
        except Exception:
            continue

        if row_letter not in ROWS or not (1 <= col_num <= 8):
            continue

        value_type = "blank" if c["rank"] == "blank" else "other"

        grid_map[pos] = {
            "pos": pos,
            "row": row_letter,
            "col": col_num,
            "value_type": value_type,
            "index": idx,
            "sym": c["sym"],
            "raw": c["raw"],
            "rank": c["rank"],
            "suit": c["suit"]
        }

    return grid_map


def get_trial_payload_blank(condition, pattern, participant, trial):
    d = df_for_condition_blank(condition)

    if d.empty:
        return None

    subset = d[
        (d["pattern"] == pattern) &
        (d["participant"] == participant) &
        (d["trialN"] == trial)
    ]

    if subset.empty:
        return None

    row = subset.iloc[0]

    sr_map, n_map = compute_sr_n_for_condition_blank(condition)
    sr = float(sr_map.get(pattern, np.nan))
    n = int(n_map.get(pattern, 0))

    cards = parse_cards_blank(row["final_card_position_codes_1"])
    grid_map = build_grid_payload_blank(cards)

    legend = [f"{idx} = {c['raw']}" for idx, c in enumerate(cards, start=1)]

    return {
        "selected_condition": condition,
        "actual_condition": str(row["condition"]),
        "participant": str(row["participant"]),
        "trial": str(row["trialN"]),
        "pattern": pattern,
        "overall_correct": float(row["overall_correct"]),
        "outcome": "Success" if float(row["overall_correct"]) == 1 else "Fail",
        "SR": sr,
        "N": n,
        "grid_map": grid_map,
        "legend": legend
    }

#############################################################################
# Documentation part


def build_documentation_rows():
    """
    Build rows for the documentation table:
    N, Condition, Pattern, Participant, Trial, Status
    """
    if blank_patterns_df is None or blank_patterns_df.empty:
        return []

    d = blank_patterns_df.copy()
    d = d[d["pattern"].astype(str).str.strip() != ""]

    # Count how many times each pattern appears within each condition
    counts = (
        d.groupby(["condition", "pattern"])
         .size()
         .reset_index(name="N")
    )

    d = d.merge(counts, on=["condition", "pattern"], how="left")

    d["Status"] = d["overall_correct"].apply(
        lambda x: "S" if float(x) == 1 else "F")

    rows = d[["N", "condition", "pattern",
              "participant", "trialN", "Status"]].copy()
    rows.columns = ["N", "Condition", "Pattern",
                    "Participant", "Trial", "Status"]

    rows = rows.sort_values(
        by=["Condition", "Pattern", "Participant", "Trial"])

    return rows.to_dict(orient="records")
##############################################################################
#############################################################################
# load_blank_patterns_data()
# ============================================================================
# FLASK ROUTES
# ============================================================================


@app.route('/')
def index():
    """Homepage with overview statistics."""
    if df is None:
        return render_template('error.html',
                               message="Dataset not loaded. Please ensure CardsDataset.csv is in the data/ folder.")

    stats = {
        'total_trials': len(df),
        'unique_participants': df['participant'].nunique(),
        'success_trials': len(df[df['overall_correct'] == 1]),
        'failed_trials': len(df[df['overall_correct'] == 0]),
        'success_rate': (df['overall_correct'] == 1).mean() * 100,
        'avg_moves': df['movement_codes'].apply(len).mean(),
        'conditions': df['condition'].unique().tolist()
    }

    return render_template('index.html', stats=stats)


@app.route('/explorer')
def explorer():
    """Interactive trial explorer page."""
    if df is None:
        return render_template('error.html', message="Dataset not loaded")

    participants = sorted(df['participant'].unique().tolist())
    conditions = sorted(df['condition'].unique().tolist())
    return render_template('explorer.html',
                           participants=participants,
                           conditions=conditions)


@app.route('/patterns')
def patterns():
    """Pattern analysis page."""
    if df is None:
        return render_template('error.html', message="Dataset not loaded")

    success_df = df[df['overall_correct'] == 1]
    failure_df = df[df['overall_correct'] == 0]

    return render_template('patterns.html',
                           success_count=len(success_df),
                           failure_count=len(failure_df))


@app.route('/blank-card-paradox')
def blank_card_paradox():
    """Blank Card Paradox research page with embedded viewer."""
    return render_template('blank_card_paradox.html')


# ============================================================================
# BLANK CARD PARADOX DYNAMIC DATA API
# ============================================================================

def _parse_movement_to_viewer(move_str, step_idx):
    """Parse a movement_code string into the viewer's expected format.

    Examples: 'queen_spades_A1' → {card:'queen',label:'Q',row:0,col:0,...}
              'blank_B3'       → {card:'blank',label:'?',row:1,col:2,...}
    """
    if pd.isna(move_str) or not isinstance(move_str, str) or move_str == '':
        return {'card': 'unknown', 'label': '?', 'offGrid': True, 'step': step_idx + 1}
    parts = move_str.split('_')
    rank = parts[0].lower()
    label_map = {'king': 'K', 'queen': 'Q', 'jack': 'J', 'blank': '?'}
    label = label_map.get(rank, '?')

    # Extract position (last segment)
    pos_str = parts[-1]
    m = re.match(r'^([A-H])(\d{1,2})$', pos_str.upper())
    if m:
        col = ord(m.group(1)) - ord('A')
        row = int(m.group(2)) - 1
        if 0 <= row < 8 and 0 <= col < 8:
            return {
                'card': rank, 'label': label, 'row': row, 'col': col,
                'cell': pos_str.upper(), 'offGrid': False, 'step': step_idx + 1,
            }
    return {'card': rank, 'label': label, 'offGrid': True, 'step': step_idx + 1}


def _parse_final_positions_to_layout(final_positions):
    """Parse final_card_position_codes_1 into the viewer's finalLayout format."""
    layout = []
    if not final_positions or not isinstance(final_positions, list):
        return layout
    for fp in final_positions:
        if not isinstance(fp, str):
            continue
        parts = fp.split('_')
        rank = parts[0].lower()
        label_map = {'king': 'K', 'queen': 'Q', 'jack': 'J', 'blank': '?'}
        label = label_map.get(rank, '?')
        pos_str = parts[-1]
        m = re.match(r'^([A-H])(\d{1,2})$', pos_str.upper())
        off_grid = not bool(m)
        layout.append({'card': rank, 'label': label, 'offGrid': off_grid})
    return layout


def _has_blank_in_moves(movements):
    """Check if any movement contains a blank card."""
    if not movements or not isinstance(movements, list):
        return False
    return any('blank' in str(m).lower() for m in movements)


@app.route('/api/bcp/summary')
def api_bcp_summary():
    """Return Blank Card Paradox summary statistics computed from the live CSV."""
    if df is None:
        return jsonify({'error': 'Dataset not loaded'}), 503

    d = df.copy()
    # Determine blank-card usage across all trials
    d['_used_blank'] = d['movement_codes'].apply(_has_blank_in_moves)
    # Also check final positions
    d['_final_has_blank'] = d['final_card_position_codes_1'].apply(
        lambda fps: any('blank' in str(f).lower() for f in (fps or [])))
    d['_has_blank'] = d['_used_blank'] | d['_final_has_blank']

    total = len(d)
    blank_users_count = d['participant'][d['_has_blank']].nunique()
    non_blank_users_count = d['participant'].nunique() - blank_users_count
    eligible_participants = d['participant'][d['condition'].isin(['KQJB', 'KQB'])].nunique()

    blank_user_success = d[d['_has_blank']]['overall_correct'].mean() * 100 if blank_users_count > 0 else 0
    non_blank_user_success = d[~d['_has_blank']]['overall_correct'].mean() * 100 if non_blank_users_count > 0 else 0

    conditions = {}
    for cond in ['KQ', 'KQB', 'KQJ', 'KQJB']:
        cd = d[d['condition'] == cond]
        if cd.empty:
            conditions[cond] = {'participants': 0, 'usedBlankCount': 0,
                                'successRate': 0, 'successWithBlank': None, 'successWithoutBlank': None}
            continue
        has_blank = cd[cd['_has_blank']]
        no_blank = cd[~cd['_has_blank']]
        conditions[cond] = {
            'participants': int(cd['participant'].nunique()),
            'usedBlankCount': int(has_blank['participant'].nunique()),
            'successRate': round(cd['overall_correct'].mean() * 100, 1),
            'successWithBlank': round(has_blank['overall_correct'].mean() * 100, 1) if len(has_blank) > 0 else None,
            'successWithoutBlank': round(no_blank['overall_correct'].mean() * 100, 1) if len(no_blank) > 0 else None,
        }

    return jsonify({
        'summary': {
            'participants': int(d['participant'].nunique()),
            'blankUsers': blank_users_count,
            'blankUsageRateOverall': round(blank_users_count / total * 100, 1) if total > 0 else 0,
            'blankUsageRateEligible': round(blank_users_count / eligible_participants * 100, 1) if eligible_participants > 0 else 0,
            'successWithBlank': round(blank_user_success, 1),
            'successWithoutBlank': round(non_blank_user_success, 1),
            'conditions': conditions,
        }
    })


@app.route('/api/bcp/participants')
def api_bcp_participants():
    """Return participant-grid data for the Blank Card Paradox page."""
    if df is None:
        return jsonify({'error': 'Dataset not loaded'}), 503

    d = df.copy()
    d['_has_blank'] = d['movement_codes'].apply(_has_blank_in_moves) | \
                      d['final_card_position_codes_1'].apply(
                          lambda fps: any('blank' in str(f).lower() for f in (fps or [])))

    # Aggregate per participant: one row = last successful trial or first trial
    participants = []
    for pid, group in d.groupby('participant'):
        row = group.iloc[0]
        success = bool(row['overall_correct'] == 1)
        # Check if ANY trial for this participant used blanks
        used_blank = bool(group['_has_blank'].any())
        participants.append({
            'id': int(pid),
            'condition': str(row['condition']),
            'usedBlank': used_blank,
            'success': success,
        })

    participants.sort(key=lambda p: p['id'])
    return jsonify({'participants': participants})


@app.route('/api/bcp/viewer-data')
def api_bcp_viewer_data():
    """Return viewer-compatible participant data from the live CSV.

    Each participant gets a single entry with:
      - Moves parsed from movement_codes (simplified — no per-step timestamps)
      - Final layout parsed from final_card_position_codes_1
      - Single-trial representation (the CSV has one row per trial)
    """
    if df is None:
        return jsonify({'error': 'Dataset not loaded'}), 503

    viewers = []
    for pid, group in df.groupby('participant'):
        row = group.iloc[0]
        movements_raw = row.get('movement_codes', [])
        if not isinstance(movements_raw, list):
            movements_raw = []

        moves = [_parse_movement_to_viewer(m, i) for i, m in enumerate(movements_raw)]
        final_layout = _parse_final_positions_to_layout(row.get('final_card_position_codes_1', []))

        used_blank = _has_blank_in_moves(movements_raw) or \
                     any(f.get('card') == 'blank' for f in final_layout)
        success = bool(row['overall_correct'] == 1)
        move_count = len(movements_raw)

        viewers.append({
            'id': int(pid),
            'condition': str(row['condition']),
            'usedBlank': used_blank,
            'success': success,
            'rowCorrect': True,
            'colCorrect': True,
            'trialsToCorrect': 1 if success else 0,
            'finalTrialIndex': 0,
            'defaultTrialNumber': 1,
            'trialCount': 1,
            'moveCount': move_count,
            'moves': moves,
            'finalLayout': final_layout,
            'trials': [{
                'trialNumber': 1,
                'trialIndex': 0,
                'endType': 'submit',
                'success': success,
                'rowCorrect': True,
                'colCorrect': True,
                'usedBlank': used_blank,
                'moveCount': move_count,
                'moves': moves,
                'finalLayout': final_layout,
            }],
        })

    viewers.sort(key=lambda p: p['id'])
    return jsonify({'participants': viewers})


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/api/get-trials/<int:participant>')
def get_trials(participant):
    """Get all trials for a specific participant, optionally filtered by condition."""
    condition = request.args.get('condition', '')

    # Filter by participant
    participant_df = df[df['participant'] == participant]

    # Optionally filter by condition
    if condition:
        participant_df = participant_df[participant_df['condition'] == condition]

    trials = participant_df['trialN'].unique().tolist()
    return jsonify(sorted(trials))


@app.route('/api/trial-info/<int:participant>/<int:trial_n>')
def trial_info(participant, trial_n):
    """Get detailed information about a specific trial."""
    trial_data = df[(df['participant'] == participant) &
                    (df['trialN'] == trial_n)]

    if trial_data.empty:
        return jsonify({'error': 'Trial not found'}), 404

    trial = trial_data.iloc[0]

    return jsonify({
        'participant': int(participant),
        'trial': int(trial_n),
        'condition': str(trial['condition']),
        'success': bool(trial['overall_correct'] == 1),
        'total_moves': len(trial['movement_codes']),
        'movements': trial['movement_codes']
    })


@app.route('/api/animation-info/<int:participant>/<int:trial_n>')
def animation_info(participant, trial_n):
    """
    Get animation metadata without generating frames.
    Fast and lightweight - returns trial information.
    Memory: <1MB, Time: <0.1s
    """
    try:
        trial_data = df[(df['participant'] == participant) &
                        (df['trialN'] == trial_n)]

        if trial_data.empty:
            return jsonify({'error': 'Trial not found'}), 404

        trial_data = trial_data.iloc[0]
        movements = trial_data['movement_codes']

        if not movements:
            return jsonify({'error': 'No movements in trial'}), 404

        info = {
            'participant': participant,
            'trial': trial_n,
            'total_frames': len(movements) + 1,
            'condition': trial_data.get('condition', 'N/A'),
            'success': bool(trial_data.get('overall_correct', 0)),
            'total_moves': len(movements)
        }

        print(f"✓ Animation info: {info['total_frames']} frames")
        return jsonify(info)

    except Exception as e:
        print(f"✗ Animation info error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/animation-frame/<int:participant>/<int:trial_n>/<int:frame_index>')
def animation_frame(participant, trial_n, frame_index):
    """
    Generate and return a single animation frame as PNG.
    Production-safe: Memory ~10MB (vs 200MB for full animation)
    Time: ~0.3-0.8 seconds per frame
    """
    try:
        trial_data = df[(df['participant'] == participant) &
                        (df['trialN'] == trial_n)]

        if trial_data.empty:
            return "Trial not found", 404

        trial_data = trial_data.iloc[0]
        movements = trial_data['movement_codes']

        if not movements:
            return "No movements", 404

        max_frame = len(movements)
        if frame_index < 0 or frame_index > max_frame:
            return f"Invalid frame. Must be 0-{max_frame}", 400

        # Create grid state for this specific frame only
        grid = visualizer.create_grid_state(movements, frame_index)

        # Add blank cards on final frame
        if frame_index == len(movements):
            final_positions = trial_data.get('final_card_position_codes_1', [])
            grid = visualizer.add_blank_cards_to_grid(grid, final_positions)

        # Create figure for single frame (thread-safe)
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        fig = Figure(figsize=(7, 7), facecolor='white')
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

        trial_info = {
            'participant': participant,
            'trialN': trial_n,
            'condition': trial_data.get('condition', 'N/A'),
            'overall_correct': trial_data.get('overall_correct', 0)
        }

        # Plot this frame
        visualizer.plot_grid(grid, ax, frame_index, len(movements), trial_info)
        fig.tight_layout()

        # Render to PNG in memory (no disk writes)
        img_bytes = io.BytesIO()
        canvas.print_png(img_bytes)
        img_bytes.seek(0)

        # Cleanup
        del fig, ax, canvas

        # Return with caching headers
        response = send_file(img_bytes, mimetype='image/png')
        response.headers['Cache-Control'] = 'public, max-age=3600'
        response.headers['ETag'] = f'frame-{participant}-{trial_n}-{frame_index}'

        return response

    except Exception as e:
        print(f"✗ Frame {frame_index} error: {str(e)}")
        import traceback
        traceback.print_exc()
        return str(e), 500


@app.route('/api/trial-image/<int:participant>/<int:trial_n>')
def trial_image(participant, trial_n):
    """Get static image of trial's final state."""
    img_bytes = visualizer.generate_static_image(participant, trial_n)

    if img_bytes is None:
        return "Image not found", 404

    return send_file(img_bytes, mimetype='image/png')


def trial_image(participant, trial_n):
    """Get static image of trial's final state."""
    img_bytes = visualizer.generate_static_image(participant, trial_n)


@app.route('/api/trial-grid/<int:participant>/<int:trial_n>')
def trial_grid(participant, trial_n):
    """Get trial grid data as JSON for client-side HTML card rendering."""
    import pandas as pd
    import numpy as np

    if df is None:
        return jsonify({'error': 'Dataset not loaded'}), 503

    trial_data = df[(df['participant'] == participant) & (df['trialN'] == trial_n)]
    if trial_data.empty:
        return jsonify({'error': 'Trial not found'}), 404

    row = trial_data.iloc[0]
    movements_raw = row.get('movement_codes', []) or []
    movements = _parse_movement_codes(movements_raw)

    grid = visualizer.create_grid_state(movements, len(movements))
    final_positions = row.get('final_card_position_codes_1', [])
    if final_positions and len(final_positions) > 0:
        grid = visualizer.add_blank_cards_to_grid(grid, final_positions)

    # Convert grid to JSON-safe format: list of {row, col, value, suit}
    cells = []
    for r in range(8):
        for c in range(8):
            val = grid[r][c]
            if val and val != ' ':
                cells.append({
                    'row': r,
                    'col': c,
                    'value': str(val),
                    'suit': SUIT_SYMBOLS.get(str(val), ''),
                })

    result = {
        'participant': int(row['participant']),
        'trial': int(row['trialN']),
        'condition': str(row.get('condition', '')),
        'success': bool(row.get('overall_correct', 0) == 1),
        'total_moves': len(movements),
        'cells': cells,
    }

    # Convert numpy types
    import json as _json
    class NpEncoder(_json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer,)): return int(obj)
            if isinstance(obj, (np.floating,)): return float(obj)
            if isinstance(obj, (np.ndarray,)): return obj.tolist()
            return super().default(obj)

    return app.response_class(
        response=_json.dumps(result, cls=NpEncoder),
        status=200,
        mimetype='application/json'
    )


# Add suit symbol mapping at module level if not present
if 'SUIT_SYMBOLS' not in dir():
    SUIT_SYMBOLS = {'K': 'spades', 'Q': 'hearts', 'J': 'diamonds', 'B': 'blank'}

@app.route('/api/analyze-patterns/<pattern_type>')
def analyze_patterns(pattern_type):
    """Analyze patterns for success or failure trials with optional limit."""
    # Get limit parameter (default 5, 0 means all)
    limit = request.args.get('limit', '5')
    try:
        limit = int(limit)
    except:
        limit = 5

    # Use cached pattern counter
    position_counter = get_pattern_counter(pattern_type)

    # Get patterns (limited or all)
    if limit > 0:
        top_patterns = position_counter.most_common(limit)
    else:
        top_patterns = position_counter.most_common()

    patterns_data = []
    for idx, (pattern, count) in enumerate(top_patterns):
        patterns_data.append({
            'id': idx,
            'pattern': list(pattern),
            'count': count,
            'cards': len(pattern)
        })

    return jsonify({
        'patterns': patterns_data,
        'total_unique': len(position_counter),
        'showing': len(patterns_data)
    })


@app.route('/api/pattern-image/<pattern_type>/<int:pattern_id>')
def pattern_image(pattern_type, pattern_id):
    """Generate visualization image for a specific pattern."""
    # Use cached pattern counter
    position_counter = get_pattern_counter(pattern_type)

    # Get ALL patterns, not just top 5
    all_patterns = position_counter.most_common()

    if pattern_id >= len(all_patterns):
        return "Pattern not found", 404

    pattern, count = all_patterns[pattern_id]

    # Create isolated figure (thread-safe)
    fig = Figure(figsize=(7, 7), facecolor='white')
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)

    grid = np.empty((visualizer.grid_size, visualizer.grid_size), dtype=object)

    for card_str in pattern:
        card_info = visualizer.extract_card_info(card_str)
        if card_info:
            position_coords = visualizer.parse_position(card_info['position'])
            if position_coords:
                row, col = position_coords
                grid[row, col] = card_info

    trial_info = {
        'participant': f'#{pattern_id + 1}',
        'trialN': f'{count} trials',
        'condition': f'{len(pattern)} cards',
        'overall_correct': 1 if pattern_type == 'success' else 0,
        'is_pattern': True  # Flag to format differently
    }

    visualizer.plot_grid(grid, ax, len(pattern), len(pattern), trial_info)
    fig.tight_layout()

    # Save using canvas (thread-safe)
    img_bytes = io.BytesIO()
    canvas.print_png(img_bytes)
    img_bytes.seek(0)

    return send_file(img_bytes, mimetype='image/png')


@app.route('/api/pattern-trials/<pattern_type>/<int:pattern_id>')
def pattern_trials(pattern_type, pattern_id):
    """Get all trials that match a specific pattern."""
    if pattern_type == 'success':
        subset_df = df[df['overall_correct'] == 1]
    else:
        subset_df = df[df['overall_correct'] == 0]

    # Use cached pattern counter
    position_counter = get_pattern_counter(pattern_type)

    # Get ALL patterns, not just top 5
    all_patterns = position_counter.most_common()

    if pattern_id >= len(all_patterns):
        return jsonify([])

    target_pattern, _ = all_patterns[pattern_id]
    target_sorted = tuple(sorted(target_pattern))

    matching_trials = []
    for _, row in subset_df.iterrows():
        final_positions = row['final_card_position_codes_1']
        if final_positions and len(final_positions) > 0:
            current_sorted = tuple(sorted(final_positions))
            if current_sorted == target_sorted:
                matching_trials.append({
                    'participant': int(row['participant']),
                    'trial': int(row['trialN']),
                    'condition': str(row['condition']),
                    'moves': len(row['movement_codes'])
                })

    return jsonify(matching_trials)


##############################################################################
####### documentation####################################################


@app.route('/api/blank-patterns/doc-options')
def api_blank_patterns_doc_options():
    rows = build_documentation_rows()

    conditions = sorted(set(r["Condition"]
                        for r in rows if str(r["Condition"]).strip()))
    patterns = sorted(set(r["Pattern"]
                      for r in rows if str(r["Pattern"]).strip()))
    statuses = sorted(set(r["Status"]
                      for r in rows if str(r["Status"]).strip()))

    return jsonify({
        "conditions": ["All"] + conditions,
        "patterns": ["All"] + patterns,
        "statuses": ["All"] + statuses
    })


@app.route('/api/blank-patterns/doc-table')
def api_blank_patterns_doc_table():
    condition = request.args.get("condition", "All")
    pattern = request.args.get("pattern", "All")
    status = request.args.get("status", "All")
    limit = int(request.args.get("limit", 10))

    all_rows = get_filtered_documentation_rows(
        condition=condition,
        pattern=pattern,
        status=status,
        limit=None
    )

    visible_rows = all_rows[:limit]

    return jsonify({
        "rows": visible_rows,
        "total": len(all_rows),
        "shown": len(visible_rows)
    })


@app.route('/api/blank-patterns/doc-table/download')
def api_blank_patterns_doc_table_download():
    condition = request.args.get("condition", "All")
    pattern = request.args.get("pattern", "All")
    status = request.args.get("status", "All")

    rows = get_filtered_documentation_rows(
        condition=condition,
        pattern=pattern,
        status=status,
        limit=None   # export ALL filtered rows
    )

    df_export = pd.DataFrame(
        rows,
        columns=["N", "Condition", "Pattern", "Participant", "Trial", "Status"]
    )

    output = io.StringIO()
    df_export.to_csv(output, index=False)
    output.seek(0)

    filename = f"blank_patterns_documentation_{condition}_{pattern}_{status}.csv"
    filename = filename.replace("/", "-").replace("\\", "-").replace(" ", "_")

    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename
    )


@app.route('/api/blank-patterns/doc-pattern-options')
def api_blank_patterns_doc_pattern_options():
    condition = request.args.get("condition", "All")

    rows = build_documentation_rows()

    if condition != "All":
        rows = [r for r in rows if r["Condition"] == condition]

    patterns = sorted(set(r["Pattern"]
                      for r in rows if str(r["Pattern"]).strip()))

    return jsonify({
        "patterns": ["All"] + patterns
    })


@app.route('/api/blank-patterns/doc-status-options')
def api_blank_patterns_doc_status_options():
    condition = request.args.get("condition", "All")
    pattern = request.args.get("pattern", "All")

    rows = build_documentation_rows()

    if condition != "All":
        rows = [r for r in rows if r["Condition"] == condition]

    if pattern != "All":
        rows = [r for r in rows if r["Pattern"] == pattern]

    statuses = sorted(set(r["Status"]
                      for r in rows if str(r["Status"]).strip()))

    return jsonify({
        "statuses": ["All"] + statuses
    })


def get_filtered_documentation_rows(condition="All", pattern="All", status="All", limit=None):
    rows = build_documentation_rows()

    if condition != "All":
        rows = [r for r in rows if r["Condition"] == condition]

    if pattern != "All":
        rows = [r for r in rows if r["Pattern"] == pattern]

    if status != "All":
        rows = [r for r in rows if r["Status"] == status]

    if limit is not None:
        rows = rows[:limit]

    return rows
# documentation summary


def get_documentation_summary(condition="All", pattern="All", status="All"):
    rows = get_filtered_documentation_rows(
        condition=condition,
        pattern=pattern,
        status=status,
        limit=None
    )

    total = len(rows)
    success = sum(1 for r in rows if r["Status"] == "S")
    fail = sum(1 for r in rows if r["Status"] == "F")
    unique_participants = len(set(r["Participant"] for r in rows))
    unique_patterns = len(set(r["Pattern"] for r in rows))

    success_rate = 0.0
    if total > 0:
        success_rate = (success / total) * 100

    return {
        "total": total,
        "success": success,
        "fail": fail,
        "success_rate": round(success_rate, 1),
        "unique_participants": unique_participants,
        "unique_patterns": unique_patterns
    }


@app.route('/api/blank-patterns/doc-summary')
def api_blank_patterns_doc_summary():
    condition = request.args.get("condition", "All")
    pattern = request.args.get("pattern", "All")
    status = request.args.get("status", "All")

    summary = get_documentation_summary(
        condition=condition,
        pattern=pattern,
        status=status
    )

    return jsonify(summary)
##############################################################################


@app.route('/powerbi')
def powerbi():
    """Power BI dashboard page."""
    return render_template('powerbi.html')


# Team roster for the About Us page. Photos live in static/img/team/<photo>.jpg
# (optimised from the originals). Grouped by affiliation per the project's
# "About our teams" brief.
ABOUT_INTRO = (
    "We are a multidisciplinary group of researchers and academics working "
    "across psychology, data science, and data analytics, focused on advancing "
    "research and innovation."
)

PSYCHOLOGY_TEAM = [
    {"name": "Dr. Wendy Ross", "role": "Senior Lecturer in Psychology",
     "org": "London Metropolitan University", "photo": "wendy"},
    {"name": "Thomas Ormerod", "role": "Professor of Psychology",
     "org": "University of Sussex", "photo": "tom"},
]

SCDM_TEAM = [
    {"name": "Dr. Subeksha Shrestha", "role": "Course Leader & Lecturer, MSc Data Analytics",
     "org": "London Metropolitan University", "photo": "subeksha"},
    {"name": "Sushan Sunuwar", "role": "MSc Data Analytics",
     "org": "London Metropolitan University", "photo": "sushan"},
    {"name": "Athapaththulage Lahiru Ruchira Samarajeewa", "role": "MSc Data Analytics",
     "org": "London Metropolitan University", "photo": "lahiru"},
    {"name": "Nikunjkumar Manubhai Prajapati", "role": "MSc Data Analytics",
     "org": "London Metropolitan University", "photo": "nikunj"},
    {"name": "Shana Precilla Iruthayaraj", "role": "MSc Data Analytics",
     "org": "London Metropolitan University", "photo": "shana"},
    {"name": "Bodiya Baduge Shavini Nadeesha Fernando", "role": "MSc Data Analytics",
     "org": "London Metropolitan University", "photo": "shavini"},
    {"name": "Mahesh Kohar Prajapati", "role": "MSc Data Analytics",
     "org": "London Metropolitan University", "photo": "mahesh"},
]


@app.route('/about')
def about():
    """About Us — project contributors."""
    return render_template(
        'about.html',
        intro=ABOUT_INTRO,
        psychology_team=PSYCHOLOGY_TEAM,
        scdm_team=SCDM_TEAM,
    )


###############################################################################
##########Behavioral patterns-Grid Analysis###################################
@app.route('/behavioral_patterns')
def behavioral_patterns():
    return render_template('behavioral_patterns.html')

@app.route('/api/behavioral-csv')
def behavioral_csv():
    """Serve the active CSV dataset for client-side processing by the
    Behavioral Patterns dashboard.  When the main upload widget replaces
    CardsDataset.csv on disk this endpoint automatically returns the new file."""
    if not _CSV_PATH.exists():
        return jsonify({'error': 'CSV not found'}), 404
    return send_file(str(_CSV_PATH), mimetype='text/csv')

###############################################################################
########### Behavioural Analysis — 9 statistical perspectives on Cards task #####
@app.route('/behavioural-analysis')
def behavioural_analysis():
    """Interactive Card Sorting Analysis: the original animation viewer,
    statistics, and 'Try Yourself' playground. Wrapped in the main site layout
    so it keeps the shared navbar, upload widget, and footer."""
    return render_template('behavioural_analysis.html', parent_origin='')


# ---------------------------------------------------------------------------
# CSV upload — re-process the behavioural dataset from a user-supplied file
# ---------------------------------------------------------------------------
@app.route('/behavioural-analysis/upload-dataset', methods=['POST'])
def behavioural_upload_dataset():
    """Accept a CardsDataset-style CSV or Excel file and overwrite card_analysis_data.json.

    Replaces the static JSON with a freshly-processed version derived from the
    uploaded CSV. The behavioural Blueprint (analysis_types[6]) re-uses these
    trials to derive all nine analyses on the next page load, so no
    in-process invalidation is required. Writes are atomic to avoid serving a
    half-written file if the request is interrupted.
    """
    if not ENABLE_DATASET_UPLOAD:
        return jsonify({"error": "Dataset upload is disabled on this server."}), 403
    if not _CSV_PIPELINE_AVAILABLE or process_csv_to_json is None:
        return jsonify({"error": "Server is missing the process_dataset module."}), 503

    upload = request.files.get('dataset')
    if upload is None or not upload.filename:
        return jsonify({
            "error": "No dataset file uploaded. Pick a CSV or Excel file via the upload widget (multipart field name 'dataset')."
        }), 400

    safe_filename = upload.filename.lower()
    if not (safe_filename.endswith('.csv') or safe_filename.endswith('.xlsx')):
        return jsonify({"error": "Only .csv or .xlsx files are accepted."}), 400

    # Reject oversized requests before buffering the body.
    max_size = app.config.get('MAX_CONTENT_LENGTH') or 32 * 1024 * 1024
    if request.content_length is not None and request.content_length > max_size:
        return jsonify({"error": "Upload too large for the configured limit."}), 413

    tmp_path = None
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="behavioural_upload_")
        # Sanitise + cap filename so attackers can't pin disk with a 32 MB
        # filename (still in the tempdir, never served).
        original_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(upload.filename).name)[:120]
        tmp_path = os.path.join(tmp_dir, original_name)
        upload.save(tmp_path)

        # Excel uploads need to be normalised to CSV before the pipeline
        # can read them, while still validating their original columns.
        process_path = tmp_path
        if safe_filename.endswith(('.xlsx', '.xls')):
            excel_df = pd.read_excel(tmp_path)
            csv_path = os.path.join(tmp_dir, "converted.csv")
            excel_df.to_csv(csv_path, index=False)
            process_path = csv_path

        payload = process_csv_to_json(process_path)
        stats = payload.get("statistics", {}) or {}
        if not stats.get("total_trials"):
            return jsonify({"error": "No valid trials found in the uploaded CSV."}), 400

        with dataset_lock:
            atomic_write_json(_ANALYSIS_DATA_PATH, payload)

            # Only overwrite the canonical CSV AFTER both processing and JSON write
            # succeed — a bad CSV won't corrupt the running dataset. Use the path
            # that was actually processed (the converted CSV for Excel uploads).
            shutil.copy2(process_path, _CSV_PATH)

            # Reload the global df so other tabs pick up the new CSV immediately.
            load_data()

            # Regenerate the Blank Patterns Excel from the freshly loaded CSV so
            # all tabs — including Blank Patterns — reflect the uploaded dataset.
            regenerate_excel_from_csv(df)
            load_blank_patterns_data()

            # Invalidate all caches so the Blank Card Paradox page and
            # pattern analysis recalculate from the new dataset on next load.
            global _pattern_cache
            _pattern_cache = {'success': None, 'failure': None}

            return jsonify({
                "ok": True,
                "data_path": str(_ANALYSIS_DATA_PATH),
                "statistics": stats,
                "analysis_count": len(payload.get("analysis_types", [])),
            })
    except DatasetProcessingError as exc:
        details = getattr(exc, 'details', {}) or {}
        missing = details.get('missing_columns')
        required = details.get('required_columns')
        if missing and required:
            return jsonify({
                "error": "Missing required columns",
                "missing_columns": missing,
                "required_columns": required,
                "message": (
                    "The uploaded file is missing the following required columns: "
                    + ", ".join(missing) + "."
                ),
                "next_steps": (
                    "Please add the missing columns listed above to your file and "
                    "upload it again. Every row must include a value for each required column."
                ),
            }), 400
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        # Surface enough context to debug env-level failures (stale
        # __pycache__, partial pandas upgrade, etc.) WITHOUT leaking
        # arbitrary filesystem layout to API consumers: scrub paths
        # down to filenames, cap depth, and only suggest pandas reinstall
        # when the error is actually pandas-related.
        import traceback
        tb_text_full = traceback.format_exc()
        traceback.print_exc()  # always log full traceback server-side
        # Scrub absolute paths in traceback lines: "File "X:/path/to/foo.py", ..."
        # becomes "File "foo.py", ...". Keep server-side log unsanitized.
        tb_text = re.sub(r'File ".*[\\/]', 'File "', tb_text_full)
        tb_lines = tb_text.splitlines()[-15:]  # last 15 lines is plenty
        exc_type = type(exc).__name__

        # Friendly suggested action ONLY for pandas-related errors — a
        # missing openpyxl or numpy should NOT be answered with
        # "reinstall pandas".
        hint = None
        msg = str(exc)
        if 'pandas' in msg.lower():
            hint = (
                "This looks like a pandas environment issue. "
                "Try: pip install --force-reinstall pandas==2.1.4 "
                "and restart the server."
            )

        response = {
            "error": f"Processing failed: {exc}",
            "exception_type": exc_type,
            "exception_message": msg,
            "traceback": tb_lines,
            "diagnostics": {
                "pandas_version": pd.__version__,
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            }
        }
        if hint:
            response["hint"] = hint
        return jsonify(response), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        if tmp_dir and os.path.isdir(tmp_dir):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Revert dataset — restore CardsDataset.csv + card_analysis_data.json
# from their .orig.bak backups (created once at first startup).
# ---------------------------------------------------------------------------
@app.route('/behavioural-analysis/revert-dataset', methods=['POST'])
def behavioural_revert_dataset():
    if not ENABLE_DATASET_UPLOAD:
        return jsonify({"error": "Dataset upload is disabled on this server."}), 403

    with dataset_lock:
        _base = _DATA_DIR
        _shipped_dir = _base / '.shipped'
        _stems = ('CardsDataset.csv', 'card_analysis_data.json',
                  'task1_B_condition_positioned_blank_cards.xlsx')
        restored = []

        for stem in _stems:
            target = _base / stem
            # Prefer the bulletproof .shipped/ copy — never corrupted.
            shipped = _shipped_dir / stem
            if shipped.exists():
                shutil.copy2(shipped, target)
                restored.append(stem)
                continue
            # Fall back to .orig.bak for older deployments.
            bak = _base / (stem + '.orig.bak')
            if bak.exists():
                shutil.copy2(bak, target)
                restored.append(stem)

        if not restored:
            return jsonify({"error": "No backups found in data/.shipped/ or data/*.orig.bak . Ensure the app started at least once with the original dataset."}), 404

        # Reload the global df from the restored CSV.
        load_data()

        # Regenerate the Excel so it matches the restored CSV (handles case where
        # the uploaded CSV was a different dataset and the Excel drifted).
        regenerate_excel_from_csv(df)
        load_blank_patterns_data()

        return jsonify({
            "ok": True,
            "restored": restored,
            "message": f"Restored {len(restored)} file(s): {', '.join(restored)}. Reloading…",
        })


# ---------------------------------------------------------------------------
# Dataset status — compare current data files against .orig.bak hashes so the
# frontend badge knows whether the active dataset is original or custom.
#
# Only checks CSV + JSON (the authoritative data sources).  The Excel is a
# derived file that gets regenerated on each upload; including it would
# produce false "custom" flags whenever the hand-curated original Excel
# (152 rows) differs from the auto-regenerated version (~357 rows).
# ---------------------------------------------------------------------------
@app.route('/behavioural-analysis/dataset-status', methods=['GET'])
def behavioural_dataset_status():
    """Return per-file custom status by comparing sha256 against .orig.bak."""
    def _sha(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception:
            return None

    # Compare against .shipped/ first (bulletproof), fall back to .orig.bak.
    _shipped_dir = _DATA_DIR / '.shipped'
    stems = ('CardsDataset.csv', 'card_analysis_data.json')
    files_status = {}
    any_custom = False

    for stem in stems:
        live = _DATA_DIR / stem
        ref = _shipped_dir / stem
        if not ref.exists():
            ref = _DATA_DIR / (stem + '.orig.bak')

        live_hash = _sha(live) if live.exists() else None
        ref_hash = _sha(ref) if ref.exists() else None

        if live_hash is None:
            files_status[stem] = 'missing'
        elif ref_hash is None:
            files_status[stem] = 'custom'
            any_custom = True
        elif live_hash != ref_hash:
            files_status[stem] = 'custom'
            any_custom = True
        else:
            files_status[stem] = 'original'

    return jsonify({
        'is_custom': any_custom,
        'files': files_status,
    })

# ---------------------------------------------------------------------------
# dataset_lock serialises the upload + revert handlers so two simultaneous
# POSTs can't corrupt the data files. We no longer auto-revert on page load,
# so uploaded datasets persist across navigation and refresh until the user
# clicks "Revert to Original".
# ---------------------------------------------------------------------------
dataset_lock = threading.Lock()


###############################################################################
# APPLICATION STARTUP - Load data when module is imported
# ============================================================================
# Load data at module level (works with gunicorn)
print("=" * 60)
print("Initializing Card Placement Analysis Application")
print("=" * 60)

# Sanity check pandas: a stale __pycache__ or partial upgrade can break
# DataFrame.to_dict() even when import succeeds. Catch it at boot so the
# FIRST upload doesn't fail with a mystery ModuleNotFoundError.
try:
    _pd_smoke = pd.DataFrame({"a": [1]}).to_dict("records")
    assert _pd_smoke == [{"a": 1}], f"unexpected: {_pd_smoke}"
    print(f"[OK] pandas {pd.__version__} sanity check passed")
except Exception as _pd_err:
    print(f"[ERROR] pandas sanity check FAILED: {_pd_err}")
    print(f"[ERROR] pandas version: {pd.__version__}")
    print(f"[ERROR] python version: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print("[ERROR] Likely cause: stale __pycache__ or partial pandas install.")
    print("[ERROR] Try: pip install --force-reinstall pandas==2.1.4, then restart.")
    # Don't crash — let the upload endpoint surface the same hint on first use.

print("\nLoading dataset...")

if not load_data():
    print("\n[WARN] Could not load dataset")
    print("Application will start but show error page")
    print("Please ensure CardsDataset.csv is in the data/ folder")
else:
    print(f"[OK] Dataset loaded successfully")
    print(f"  - Total trials: {len(df)}")
    print(f"  - Participants: {df['participant'].nunique()}")
    print(
        f"  - Success rate: {(df['overall_correct'] == 1).mean() * 100:.1f}%")

    # ── Shipped-originals directory ──────────────────────────────────
    # data/.shipped/ is created ONCE (first server start) and is NEVER
    # touched by uploads, reverts, or tests.  It is the bulletproof
    # source-of-truth that the revert button always falls back on.
    #
    # We also maintain .orig.bak files alongside the live files for
    # backwards-compatibility with older test suites and direct
    # inspection, but they are regenerated from .shipped/ when missing.
    # ─────────────────────────────────────────────────────────────────
    _SHIPPED_DIR = _DATA_DIR / '.shipped'
    _SHIPPED_DIR.mkdir(parents=True, exist_ok=True)

    _STEMS = (
        'CardsDataset.csv',
        'card_analysis_data.json',
        'task1_B_condition_positioned_blank_cards.xlsx',
    )

    _shipped_created = 0
    for _stem in _STEMS:
        _shipped_path = _SHIPPED_DIR / _stem
        _live_path    = _DATA_DIR   / _stem
        if not _shipped_path.exists():
            if _live_path.exists():
                # Safety check: don't ship a tiny CSV — that means the
                # live file was already corrupted before the first start.
                if _stem == 'CardsDataset.csv' and _live_path.stat().st_size < 50_000:
                    print(f"  [WARN] {_stem} is only {_live_path.stat().st_size} bytes — "
                          f"SKIPPING .shipped/ creation (file appears corrupted). "
                          f"Please restore the original CSV manually.")
                    continue
                shutil.copy2(_live_path, _shipped_path)
                _shipped_created += 1

    if _shipped_created:
        print(f"  [OK] Seeded data/.shipped/ with {_shipped_created} file(s)")

    # Regenerate .orig.bak from .shipped/ on every boot so the revert
    # always has a correct secondary fallback (in case .shipped/ is
    # accidentally deleted).  .shipped/ is the canonical source.
    for _stem in _STEMS:
        _bak_path     = _DATA_DIR / (_stem + '.orig.bak')
        _shipped_path = _SHIPPED_DIR / _stem
        if _shipped_path.exists():
            shutil.copy2(_shipped_path, _bak_path)
    print(f"  [OK] Regenerated .orig.bak files from data/.shipped/")

print("=" * 60)

###############################################################
#############################################################
load_blank_patterns_data()
################################################################
################################################################
# =========================================================
# BLANK PATTERNS PAGE
# =========================================================


@app.route('/blank-patterns')
def blank_patterns_page():
    return render_template('blank_patterns.html')


# =========================================================
# BLANK PATTERNS API
# =========================================================

@app.route('/api/blank-patterns/options')
def blank_patterns_options():
    conditions = get_blank_conditions()

    default_condition = "All"
    patterns = patterns_for_condition_blank(default_condition)
    default_pattern = patterns[0] if patterns else ""

    participants = participants_for_condition_pattern_blank(
        default_condition, default_pattern) if default_pattern else []
    default_participant = participants[0] if participants else ""

    trials = trials_for_condition_pattern_participant_blank(
        default_condition,
        default_pattern,
        default_participant
    ) if default_pattern and default_participant else []

    return jsonify({
        "conditions": conditions,
        "patterns": patterns,
        "participants": participants,
        "trials": trials
    })


@app.route('/api/blank-patterns/patterns')
def api_blank_patterns_patterns():
    condition = request.args.get('condition', 'All')
    patterns = patterns_for_condition_blank(condition)
    return jsonify({"patterns": patterns})


@app.route('/api/blank-patterns/participants')
def api_blank_patterns_participants():
    condition = request.args.get('condition', 'All')
    pattern = request.args.get('pattern', '')
    participants = participants_for_condition_pattern_blank(condition, pattern)
    return jsonify({"participants": participants})


@app.route('/api/blank-patterns/trials')
def api_blank_patterns_trials():
    condition = request.args.get('condition', 'All')
    pattern = request.args.get('pattern', '')
    participant = request.args.get('participant', '')
    trials = trials_for_condition_pattern_participant_blank(
        condition, pattern, participant)
    return jsonify({"trials": trials})


@app.route('/api/blank-patterns/plot-data')
def api_blank_patterns_plot_data():
    condition = request.args.get('condition', 'All')
    pattern = request.args.get('pattern', '')
    participant = request.args.get('participant', '')
    trial = request.args.get('trial', '')

    payload = get_trial_payload_blank(condition, pattern, participant, trial)

    if payload is None:
        return jsonify({
            "error": "No record found for the selected Condition → Pattern → Participant → Trial combination."
        }), 404

    return jsonify(payload)
#######################################################################################
#########################################################################################
# ============================================================================
# DEVELOPMENT SERVER (only when running python app.py directly)
# ============================================================================


if __name__ == '__main__':
    print("\nStarting Flask development server...")
    print("=" * 60)

    if df is not None:
        print("\n[SERVER] Application running at: http://0.0.0.0:5001")
        print("\nPress Ctrl+C to stop the server\n")

        # Get port from environment (for deployment) or use 5001 for local
        port = int(os.environ.get('PORT', 5001))

        debug_mode = os.environ.get('FLASK_ENV') != 'production'

        app.run(debug=debug_mode, host='0.0.0.0', port=port)
    else:
        print("\n✗ Error: Could not load dataset")
        print("\nPlease ensure:")
        print("  1. CardsDataset.csv is in the data/ folder")
        print("  2. The CSV file has the correct format")
        print("  3. File permissions allow reading")
        print("\n" + "=" * 60)
