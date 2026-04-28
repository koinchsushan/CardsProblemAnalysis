"""
Card Placement Analysis - Flask Web Application
Main Application File

Place this file as 'app.py' in your flask_card_analysis folder.
"""

import json
import os
import re
import ast
from collections import Counter
import base64
import io
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.animation import FuncAnimation
import matplotlib.pyplot as plt
from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'

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
        Thread-safe implementation.

        Parameters:
        -----------
        grid : numpy.ndarray
            Current grid state
        ax : matplotlib.axes.Axes
            Axis to plot on
        step : int
            Current step number
        total_steps : int
            Total steps in trial
        trial_info : dict
            Trial metadata
        """
        from matplotlib.patches import Rectangle

        ax.clear()

        # Set white background
        ax.set_facecolor('white')

        # Create light gray background
        color_grid = np.zeros((self.grid_size, self.grid_size, 3))
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                color_grid[i, j] = [0.97, 0.97, 0.97]

        ax.imshow(color_grid, aspect='auto')

        # Draw gridlines
        for i in range(self.grid_size + 1):
            ax.axhline(i - 0.5, color='gray', linewidth=0.8, alpha=0.3)
            ax.axvline(i - 0.5, color='gray', linewidth=0.8, alpha=0.3)

        # Draw cards
        for i in range(self.grid_size):
            for j in range(self.grid_size):
                if grid[i, j] is not None:
                    card_info = grid[i, j]

                    # Draw card rectangle
                    rect = Rectangle((j - 0.45, i - 0.45), 0.9, 0.9,
                                     facecolor=card_info['color'],
                                     edgecolor='black',
                                     linewidth=1.5,
                                     alpha=0.9)
                    ax.add_patch(rect)

                    # Add rank text - show "B" for blank cards
                    if card_info['rank'] == 'blank':
                        rank_text = 'B'
                        # Blank cards don't show suit symbols
                        suit_symbol = ''
                    else:
                        rank_text = card_info['rank'][0].upper()
                        suit_symbol = card_info['symbol']

                    ax.text(j, i, rank_text,
                            ha='center', va='center',
                            fontsize=14, fontweight='bold',
                            color='white')

                    # Add suit symbol (only for non-blank cards)
                    if suit_symbol:
                        ax.text(j, i + 0.25, suit_symbol,
                                ha='center', va='center',
                                fontsize=9,
                                color='white')

        # Add row labels
        for i in range(self.grid_size):
            ax.text(-0.7, i, str(i + 1),
                    ha='center', va='center',
                    fontsize=9, fontweight='bold')

        # Add column labels
        for j in range(self.grid_size):
            ax.text(j, -0.7, chr(65 + j),
                    ha='center', va='center',
                    fontsize=9, fontweight='bold')

        # Set axis properties
        ax.set_xlim(-1, self.grid_size)
        ax.set_ylim(self.grid_size, -1)
        ax.axis('off')

        # Create title
        participant = trial_info.get('participant', 'N/A')
        trial_n = trial_info.get('trialN', 'N/A')
        condition = trial_info.get('condition', 'N/A')
        success = trial_info.get('overall_correct', 0)
        is_pattern = trial_info.get('is_pattern', False)

        success_text = '✓ Success' if success == 1 else '✗ Failed'
        title_color = 'green' if success == 1 else 'red'

        # Different format for patterns vs regular trials
        if is_pattern:
            # Pattern format: "Pattern #1 | Frequency: 3 trials | Cards: 4 | ✓ Success"
            title = f'Pattern {participant} | Frequency: {trial_n} | {condition} | {success_text}'
        else:
            # Regular trial format
            title = f'Participant {participant} | Trial {trial_n} | Condition: {condition} | {success_text}\n'
            title += f'Step {step}/{total_steps}'

        ax.set_title(title, fontsize=11, fontweight='bold', pad=15,
                     color=title_color if (is_pattern or step == total_steps) else 'black')

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

    def generate_animation_html(self, participant, trial_n):
        """
        Generate HTML5 animation of the trial.
        Server-compatible implementation with proper error handling.

        Parameters:
        -----------
        participant : int
            Participant ID
        trial_n : int
            Trial number

        Returns:
        --------
        str : File path to the generated animation
        """
        try:
            trial_data = self.df[(self.df['participant'] == participant) &
                                 (self.df['trialN'] == trial_n)]

            if trial_data.empty:
                print(
                    f"⚠ No data for participant {participant}, trial {trial_n}")
                return None

            trial_data = trial_data.iloc[0]
            movements = trial_data['movement_codes']

            if not movements:
                print(
                    f"⚠ No movements for participant {participant}, trial {trial_n}")
                return None

            # Use Figure instead of plt.subplots (server-safe)
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg

            fig = Figure(figsize=(7, 7))
            ax = fig.add_subplot(111)

            trial_info = {
                'participant': participant,
                'trialN': trial_n,
                'condition': trial_data.get('condition', 'N/A'),
                'overall_correct': trial_data.get('overall_correct', 0)
            }

            total_steps = len(movements)
            final_positions = trial_data.get('final_card_position_codes_1', [])

            def update(frame):
                ax.clear()
                grid = self.create_grid_state(movements, frame)
                if frame == total_steps:
                    grid = self.add_blank_cards_to_grid(grid, final_positions)
                self.plot_grid(grid, ax, frame, total_steps, trial_info)
                fig.tight_layout()
                return ax,

            # Create animation
            anim = FuncAnimation(fig, update, frames=total_steps + 1,
                                 interval=500, repeat=True, blit=False)

            # Save animation to file
            anim_filename = f'animation_{participant}_{trial_n}.html'
            anim_path = os.path.join('static', 'animations', anim_filename)

            # Ensure directory exists (important on ephemeral filesystems like Render)
            # NOTE: On Render free tier, this directory and files are ephemeral
            # They're regenerated on-demand and lost when container restarts
            # This is OK - animations are cached temporarily for performance
            os.makedirs(os.path.join('static', 'animations'), exist_ok=True)

            # Generate HTML content
            html_content = anim.to_jshtml()

            # Inject CSS to scale the matplotlib figure
            css_injection = """
<style>
    body { margin: 0; padding: 0; overflow-x: hidden; }
    div.animation { max-width: 550px !important; width: 100% !important; margin: 0 auto !important; text-align: center !important; }
    div.animation img { max-width: 100% !important; width: auto !important; height: auto !important; display: block !important; margin: 0 auto !important; }
    div.anim-controls { max-width: 550px !important; margin: 0 auto !important; }
</style>
"""
            if '</head>' in html_content:
                html_content = html_content.replace(
                    '</head>', css_injection + '</head>')
            else:
                html_content = css_injection + html_content

            # Write to file
            with open(anim_path, 'w') as f:
                f.write(html_content)

            print(
                f"✓ Animation generated: participant {participant}, trial {trial_n}")

            # Clean up matplotlib objects
            del fig, ax, anim

            return f'/static/animations/{anim_filename}'

        except Exception as e:
            print(f"✗ Animation generation error: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

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
    global df, visualizer

    data_path = 'data/CardsDataset.csv'
    if not os.path.exists(data_path):
        return False

    # Load CSV
    df = pd.read_csv(data_path)

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


def load_blank_patterns_data():
    """
    Load and preprocess the Excel file for blank pattern analysis.
    """
    global blank_patterns_df

    excel_path = os.path.join(
        "data", "task1_B_condition_positioned_blank_cards.xlsx")
    sheet_name = "B_condition_blank_cards"

    if not os.path.exists(excel_path):
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


@app.route('/api/generate-animation/<int:participant>/<int:trial_n>')
def generate_animation_deprecated(participant, trial_n):
    """
    DEPRECATED: This endpoint caused WORKER TIMEOUT on Render.
    Use frame-based animation instead for production deployment.
    """
    return jsonify({
        'error': 'This endpoint is deprecated due to memory issues',
        'message': 'Use frame-based animation endpoints instead',
        'new_endpoints': {
            'metadata': f'/api/animation-info/{participant}/{trial_n}',
            'frame_example': f'/api/animation-frame/{participant}/{trial_n}/0'
        },
        'reason': 'Full animation generation exceeds Render free tier limits (512MB RAM, 30s timeout)',
        'migration': 'See documentation for AnimationPlayer JavaScript class'
    }), 410  # 410 Gone


@app.route('/api/trial-image/<int:participant>/<int:trial_n>')
def trial_image(participant, trial_n):
    """Get static image of trial's final state."""
    img_bytes = visualizer.generate_static_image(participant, trial_n)

    if img_bytes is None:
        return "Image not found", 404

    return send_file(img_bytes, mimetype='image/png')


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


@app.route('/api/test-animation')
def test_animation():
    """Diagnostic endpoint to test animation generation."""
    import sys
    import matplotlib

    diagnostics = {
        'python_version': sys.version,
        'matplotlib_version': matplotlib.__version__,
        'matplotlib_backend': matplotlib.get_backend(),
        'data_loaded': df is not None,
        'visualizer_exists': visualizer is not None,
    }

    if df is not None:
        diagnostics['total_trials'] = len(df)
        diagnostics['sample_participant'] = int(df['participant'].iloc[0])
        diagnostics['sample_trial'] = int(df['trialN'].iloc[0])

    # Try to generate a simple test animation
    try:
        if visualizer is not None and df is not None:
            participant = int(df['participant'].iloc[0])
            trial = int(df['trialN'].iloc[0])

            print(
                f"[TEST] Attempting to generate animation for participant {participant}, trial {trial}")

            # Test generation
            result = visualizer.generate_animation_html(participant, trial)

            diagnostics['test_generation'] = 'Success' if result else 'Failed (returned None)'
            diagnostics['result_path'] = result

            print(f"[TEST] Result: {diagnostics['test_generation']}")
        else:
            diagnostics['test_generation'] = 'Skipped (no data)'
    except Exception as e:
        diagnostics['test_generation'] = f'Error: {str(e)}'
        import traceback
        diagnostics['traceback'] = traceback.format_exc()
        print(f"[TEST] Exception: {str(e)}")
        traceback.print_exc()

    return jsonify(diagnostics)

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
    return render_template('powerbi.html')


# ============================================================================
# APPLICATION STARTUP - Load data when module is imported
# ============================================================================
# Load data at module level (works with gunicorn)
print("=" * 60)
print("Initializing Card Placement Analysis Application")
print("=" * 60)
print("\nLoading dataset...")

if not load_data():
    print("\n✗ WARNING: Could not load dataset")
    print("Application will start but show error page")
    print("Please ensure CardsDataset.csv is in the data/ folder")
else:
    print(f"✓ Dataset loaded successfully")
    print(f"  - Total trials: {len(df)}")
    print(f"  - Participants: {df['participant'].nunique()}")
    print(
        f"  - Success rate: {(df['overall_correct'] == 1).mean() * 100:.1f}%")

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
        print("\n🌐 Application running at: http://0.0.0.0:5001")
        print("\nPress Ctrl+C to stop the server\n")

        # Get port from environment (for deployment) or use 5001 for local
        port = int(os.environ.get('PORT', 5001))

        # Disable debug in production
        debug_mode = os.environ.get('FLASK_ENV') != 'production'

        app.run(debug=debug_mode, host='0.0.0.0', port=port)
    else:
        print("\n✗ Error: Could not load dataset")
        print("\nPlease ensure:")
        print("  1. CardsDataset.csv is in the data/ folder")
        print("  2. The CSV file has the correct format")
        print("  3. File permissions allow reading")
        print("\n" + "=" * 60)
