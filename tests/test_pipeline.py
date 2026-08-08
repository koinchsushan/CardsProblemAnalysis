"""
Regression test for the /behavioural-analysis dataset upload pipeline.

Pins the contract between:
  - the source CSV at data/CardsDataset.csv
  - the canonical JSON at data/card_analysis_data.json
  - the converter script at process_dataset.py

If `process_dataset.process_csv_to_json` is changed (formula change, column
rename, schema drift, blank-card detection bug, etc.) or `data/CardsDataset.csv`
gains/loses columns, this test will fail before the change can ship.

Run directly:
    python tests/test_pipeline.py

Or via unittest discovery:
    python -m unittest tests.test_pipeline -v

Or with pytest (no plugin required — unittest.TestCase is pytest-discoverable):
    pytest tests/test_pipeline.py -v
"""

import json
import sys
import unittest
from pathlib import Path

# Make the project root importable so `from process_dataset import ...` works
# regardless of where the test is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from process_dataset import process_csv_to_json  # noqa: E402

CSV_PATH = ROOT / "data" / "CardsDataset.csv"
SHIPPED_JSON_PATH = ROOT / "data" / "card_analysis_data.json"


def _trial_id(trial: dict) -> tuple:
    """
    Stable identity for a trial across slot orderings.

    We use (participant, trial_number) — both of which the source CSV has as
    columns and both of which process_dataset.py carries through unchanged.
    Sorting the pair gives stable set-equality comparisons regardless of the
    test environment's list ordering.
    """
    return (str(trial["participant"]), trial["trial_number"])


class PipelineRegressionTests(unittest.TestCase):
    """Pin the upload pipeline's output against the shipped canonical JSON."""

    @classmethod
    def setUpClass(cls):
        if not CSV_PATH.exists():
            raise unittest.SkipTest(
                f"Source CSV not found at {CSV_PATH} — pipeline test cannot run."
            )
        if not SHIPPED_JSON_PATH.exists():
            raise unittest.SkipTest(
                f"Canonical JSON not found at {SHIPPED_JSON_PATH} "
                "— pipeline test cannot run."
            )
        # Run the pipeline fresh from the CSV so the test reflects the
        # current code, not a cached re-import.
        cls.new = process_csv_to_json(str(CSV_PATH))
        cls.shipped = json.loads(SHIPPED_JSON_PATH.read_text(encoding="utf-8"))
        cls.new_stats = cls.new["statistics"]
        cls.shipped_stats = cls.shipped["statistics"]

    # ------------------------------------------------------------------ #
    # Pinned scalar values                                                #
    # ------------------------------------------------------------------ #

    def test_total_trials_pinned(self):
        self.assertEqual(
            self.new_stats["total_trials"], 845,
            msg=(
                f"Pipeline produced {self.new_stats['total_trials']} trials; "
                "expected 845. A column was likely added or dropped, or rows were "
                "filtered unexpectedly by `_derive_trial`."
            ),
        )

    def test_success_count_pinned(self):
        self.assertEqual(
            self.new_stats["success_count"], 107,
            msg=(
                f"Pipeline produced {self.new_stats['success_count']} successes; "
                "expected 107. Inspect the `outcome == 'success'` branch in "
                "_derive_trial — likely the overall_correct coerter changed."
            ),
        )

    def test_success_rate_pinned(self):
        self.assertEqual(
            self.new_stats["success_rate"], 12.66,
            msg=(
                f"Pipeline produced {self.new_stats['success_rate']}% success rate; "
                "expected 12.66. Either success_count or total_trials drifted."
            ),
        )

    def test_blank_card_count_pinned(self):
        self.assertEqual(
            self.new_stats["trials_with_blank_cards"], 146,
            msg=(
                f"Pipeline produced {self.new_stats['trials_with_blank_cards']} "
                "blank-card trials; expected 146. Check `_blank_card_count` and "
                "`_parse_move_token` — they must treat `value == 'B'` and "
                "`is_blank == True` as a blank-card move, including numbered "
                "blanks (blank2/blank3/blank4)."
            ),
        )

    # ------------------------------------------------------------------ #
    # Slot-level contracts                                                #
    # ------------------------------------------------------------------ #

    def test_slot6_trial_identity_matches_shipped(self):
        """
        Slot 6 is the Blueprint's source of truth — the entire behavioural
        Blueprint re-derives analyses #1–#5 and #7–#9 from slot 6 on every
        request. If slot 6's trial identity drifts, every visualisation tab
        silently shows the wrong trials.

        We assert set equality on (participant, trial_number) pairs — order-
        independent and stable across reorderings of the slot list.
        """
        new_ids = {_trial_id(t) for t in self.new["analysis_types"][5]["trials"]}
        shipped_ids = {
            _trial_id(t) for t in self.shipped["analysis_types"][5]["trials"]
        }
        missing = shipped_ids - new_ids  # in shipped, not in new (drift!)
        extra = new_ids - shipped_ids  # in new, not in shipped (drift!)

        self.assertFalse(
            missing,
            msg=(
                f"{len(missing)} trials present in the shipped JSON's slot 6 are "
                f"MISSING from the new pipeline output. Slot 6 is the source of "
                f"truth for every behavioural analysis tab. "
                f"First 5 missing: {sorted(missing)[:5]}"
            ),
        )
        self.assertFalse(
            extra,
            msg=(
                f"{len(extra)} trials produced by the new pipeline are NOT in the "
                f"shipped JSON's slot 6 — likely a duplicate row is leaking through. "
                f"First 5 extra: {sorted(extra)[:5]}"
            ),
        )
        # Identity set means counts must match too.
        self.assertEqual(
            len(new_ids),
            len(shipped_ids),
            msg=(
                f"Slot 6 trial-count mismatch: new pipeline = {len(new_ids)}, "
                f"shipped JSON = {len(shipped_ids)}. Both depicted shapes must keep "
                f"the same number of unique (participant, trial_number) pairs."
            ),
        )

    def test_statistics_block_byte_match_shipped(self):
        """
        Belt-and-suspenders: every key in `statistics` should match the shipped
        JSON bit-for-bit. This catches drift in avg_moves / blank_success_rate /
        no_blank_success_rate that the pinned-value tests above don't check.
        """
        self.assertEqual(
            self.new_stats,
            self.shipped_stats,
            msg=(
                f"Statistics block drifted.\n"
                f"New    : {self.new_stats}\n"
                f"Shipped: {self.shipped_stats}"
            ),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
