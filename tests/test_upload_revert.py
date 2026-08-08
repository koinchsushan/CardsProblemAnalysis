"""
Round-trip test for the /behavioural-analysis upload → revert endpoints.

Verifies that:
  1. A valid CSV upload returns ok with the correct pinned statistics.
  2. After a successful upload the in-process global `df` is reloaded.
  3. The revert endpoint restores the original dataset and returns ok.
  4. A bad upload (e.g., .txt file) returns 400 without corrupting state.
  5. Excel is regenerated from the uploaded CSV (B-condition subset).
  6. The /dataset-status endpoint reports custom/original correctly.

Each test is isolated: the CSV/JSON/Excel are backed up before the first test and
restored after the last one completes, so the suite leaves the project in its
original state even if it fails midway.

Run directly:
    python tests/test_upload_revert.py

Or via unittest discovery:
    python -m unittest tests.test_upload_revert -v
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import unittest
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The app's module-level code runs load_data() on import, so this must
# happen AFTER the project root is on sys.path AND data/CardsDataset.csv
# must exist.
CSV_PATH  = ROOT / "data" / "CardsDataset.csv"
JSON_PATH = ROOT / "data" / "card_analysis_data.json"
EXCEL_PATH = ROOT / "data" / "task1_B_condition_positioned_blank_cards.xlsx"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _backup_file(path: Path) -> Path:
    """Copy a file to <name>.test_bak for later restoration."""
    backup = path.with_suffix(path.suffix + ".test_bak")
    if path.exists():
        shutil.copy2(path, backup)
    return backup


def _restore_file(backup: Path, target: Path) -> None:
    """Restore a file from its .test_bak backup and clean up."""
    if backup.exists():
        shutil.copy2(backup, target)
        backup.unlink()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@unittest.skipIf(not CSV_PATH.exists(), f"Source CSV not found at {CSV_PATH}")
class UploadRevertRoundTripTests(unittest.TestCase):
    """Test the full upload → revert lifecycle via Flask's test client."""

    _csv_backup:   Path | None = None
    _json_backup:  Path | None = None
    _excel_backup: Path | None = None
    _orig_csv_bytes: bytes | None = None  # cached before any upload modifies disk

    @classmethod
    def setUpClass(cls):
        # Self-heal: if a previous test run left the live CSV corrupted
        # (e.g. test_z1 leaked test_small.csv and teardown failed), reseed
        # from data/.shipped/ BEFORE we cache bytes or back up files. Without
        # this, suite state silently cascades across runs.
        shipped_dir = ROOT / "data" / ".shipped"
        shipped_csv = shipped_dir / "CardsDataset.csv"
        if (CSV_PATH.exists() and shipped_csv.exists()
                and CSV_PATH.stat().st_size < 50_000
                and shipped_csv.stat().st_size >= 50_000):
            print(f"[setUpClass] Live CSV is {CSV_PATH.stat().st_size} bytes — "
                  f"restoring from data/.shipped/")
            shutil.copy2(shipped_csv, CSV_PATH)
            for stem in ("card_analysis_data.json",
                         "task1_B_condition_positioned_blank_cards.xlsx"):
                src = shipped_dir / stem
                dst = ROOT / "data" / stem
                if src.exists() and dst.exists():
                    shutil.copy2(src, dst)
            try:
                from app import load_data
                load_data()
            except Exception:
                pass

        # Cache the original CSV bytes so later tests (e.g. test_z2) can
        # re-upload the real dataset even after an earlier test overwrote
        # the on-disk CSV with a small test fixture.
        cls._orig_csv_bytes = CSV_PATH.read_bytes()

        # Back up all three data files BEFORE importing the app.  Also
        # delete any stale .orig.bak files from previous test runs so the
        # startup code creates fresh ones from the current originals.
        cls._csv_backup   = _backup_file(CSV_PATH)
        cls._json_backup  = _backup_file(JSON_PATH)
        cls._excel_backup = _backup_file(EXCEL_PATH)

        base = ROOT / "data"
        for stem in ("CardsDataset.csv", "card_analysis_data.json",
                     "task1_B_condition_positioned_blank_cards.xlsx"):
            bak = base / (stem + ".orig.bak")
            if bak.exists():
                bak.unlink()

        from app import app as flask_app
        cls.app = flask_app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

        import app as app_module
        cls.app_module = app_module

    @classmethod
    def tearDownClass(cls):
        if cls._csv_backup is not None:
            _restore_file(cls._csv_backup, CSV_PATH)
        if cls._json_backup is not None:
            _restore_file(cls._json_backup, JSON_PATH)
        if cls._excel_backup is not None:
            _restore_file(cls._excel_backup, EXCEL_PATH)
        try:
            cls.app_module.load_data()
            cls.app_module.load_blank_patterns_data()
        except Exception:
            pass

    def _read_upload_csv(self) -> BytesIO:
        """Read the real CardsDataset.csv into a BytesIO for multipart upload.

        Uses the cached original bytes (setUpClass) so later tests still get
        the original CSV even after an earlier test overwrote the on-disk file.
        """
        return BytesIO(self._orig_csv_bytes)

    # ------------------------------------------------------------------ #
    # Phase 1 — original-dataset checks (MUST run before any upload)     #
    # ------------------------------------------------------------------ #

    def test_0_dataset_status_original(self):
        """GET /dataset-status reports is_custom: false on the shipped dataset."""
        resp = self.client.get("/behavioural-analysis/dataset-status")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self.assertFalse(payload.get("is_custom"),
                         msg=f"Expected is_custom=false on original, got: {payload}")
        self.assertEqual(payload["files"]["CardsDataset.csv"], "original")

    # ------------------------------------------------------------------ #
    # Phase 2 — upload endpoint                                          #
    # ------------------------------------------------------------------ #

    def test_upload_valid_csv_returns_ok(self):
        """A valid CardsDataset.csv upload returns 200 with ok:True and
        the expected pinned statistics."""
        data = {"dataset": (self._read_upload_csv(), "CardsDataset.csv")}
        resp = self.client.post(
            "/behavioural-analysis/upload-dataset",
            data=data, content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200, msg=f"Upload failed: {resp.json}")
        payload = resp.get_json()
        self.assertTrue(payload.get("ok"), msg=f"Expected ok=True: {payload}")

        stats = payload.get("statistics", {})
        self.assertEqual(stats.get("total_trials"), 845)
        self.assertEqual(stats.get("success_count"), 107)
        self.assertEqual(stats.get("success_rate"), 12.66)
        self.assertEqual(stats.get("trials_with_blank_cards"), 146)

    def test_upload_invalid_extension_returns_400(self):
        """A file with a non-.csv extension is rejected before processing."""
        data = {"dataset": (BytesIO(b"not,csv,data\n"), "badfile.txt")}
        resp = self.client.post(
            "/behavioural-analysis/upload-dataset",
            data=data, content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Only .csv or .xlsx files are accepted.", resp.get_json().get("error", ""))

    def test_upload_no_file_returns_400(self):
        """POST without a 'dataset' field returns 400."""
        resp = self.client.post(
            "/behavioural-analysis/upload-dataset",
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)

    def test_upload_reloads_in_memory_df(self):
        """After a successful upload, the global df reflects the uploaded data."""
        self.client.post(
            "/behavioural-analysis/upload-dataset",
            data={"dataset": (self._read_upload_csv(), "CardsDataset.csv")},
            content_type="multipart/form-data",
        )
        self.assertIsNotNone(self.app_module.df)
        self.assertEqual(len(self.app_module.df), 845)

    # ------------------------------------------------------------------ #
    # Phase 3 — revert endpoint (re-uploads first to establish state)     #
    # ------------------------------------------------------------------ #

    def test_revert_restores_original_dataset(self):
        """After an upload, the revert endpoint restores all 3 files
        and reloads the in-memory df."""
        self.client.post(
            "/behavioural-analysis/upload-dataset",
            data={"dataset": (self._read_upload_csv(), "CardsDataset.csv")},
            content_type="multipart/form-data",
        )
        resp = self.client.post("/behavioural-analysis/revert-dataset")
        self.assertEqual(resp.status_code, 200, msg=f"Revert failed: {resp.json}")
        payload = resp.get_json()
        self.assertTrue(payload.get("ok"))
        for stem in ("CardsDataset.csv", "card_analysis_data.json",
                     "task1_B_condition_positioned_blank_cards.xlsx"):
            self.assertIn(stem, payload.get("restored", []))

    def test_upload_pandas_module_error_returns_diagnostics(self):
        """A ModuleNotFoundError during processing returns exception_type,
        traceback, diagnostics, and a remediation hint."""
        original = self.app_module.process_csv_to_json

        def fake_process(_tmp_path):
            raise ModuleNotFoundError("No module named 'pandas.core.methods.to_dict'")

        self.app_module.process_csv_to_json = fake_process
        try:
            resp = self.client.post(
                "/behavioural-analysis/upload-dataset",
                data={"dataset": (self._read_upload_csv(), "CardsDataset.csv")},
                content_type="multipart/form-data",
            )
            self.assertEqual(resp.status_code, 500)
            payload = resp.get_json() or {}
            self.assertEqual(payload.get("exception_type"), "ModuleNotFoundError")
            self.assertIn("pandas.core.methods.to_dict", payload.get("exception_message", ""))
            self.assertIsInstance(payload.get("traceback"), list)
            self.assertGreater(len(payload["traceback"]), 0)
            diag = payload.get("diagnostics", {}) or {}
            self.assertIn("pandas_version", diag)
            self.assertEqual(diag.get("python_version"),
                             f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
            self.assertIn("pip install --force-reinstall", payload.get("hint", ""))
        finally:
            self.app_module.process_csv_to_json = original

    # ------------------------------------------------------------------ #
    # Phase 4 — Excel round-trip & post-upload status (alphabetically     #
    #           AFTER phase-3 so state has already been modified by       #
    #           earlier upload tests — each test re-uploads as needed).   #
    # ------------------------------------------------------------------ #

    def test_z1_dataset_status_custom_after_upload(self):
        """After uploading a different CSV, /dataset-status reports is_custom: true.

        Only CSV + JSON are checked (Excel is a derived file).  Uploading the
        SHIPPED CSV back produces byte-identical copies so is_custom stays
        false; we use one of the test-datasets instead to force a real change.
        """
        test_csv = ROOT / "test_datasets" / "test_small.csv"
        if not test_csv.exists():
            self.skipTest("test_small.csv not found in test_datasets/")
        data = BytesIO(test_csv.read_bytes())
        self.client.post(
            "/behavioural-analysis/upload-dataset",
            data={"dataset": (data, "test_small.csv")},
            content_type="multipart/form-data",
        )
        resp = self.client.get("/behavioural-analysis/dataset-status")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("is_custom"),
                        msg="Uploading a different CSV should mark is_custom=true")

    def test_z2_upload_regenerates_excel_with_b_conditions(self):
        """After uploading the real CSV, the regenerated Excel contains
        only KQJB + KQB rows (the count depends on the current CSV and may
        differ from the shipped hand-curated 152-row original)."""
        import pandas as pd

        self.client.post(
            "/behavioural-analysis/upload-dataset",
            data={"dataset": (self._read_upload_csv(), "CardsDataset.csv")},
            content_type="multipart/form-data",
        )
        self.assertTrue(EXCEL_PATH.exists(),
                        msg="Excel was not regenerated after upload")
        xl = pd.read_excel(EXCEL_PATH, sheet_name="B_condition_blank_cards")
        conditions = xl["condition"].dropna().unique().tolist()
        self.assertEqual(sorted(conditions), ["KQB", "KQJB"],
                         msg=f"Unexpected conditions: {conditions}")
        self.assertGreater(len(xl), 50, msg=f"Excel has too few rows ({len(xl)}) — regeneration may be broken")

    def test_revert_without_backup_returns_404(self):
        """If neither .shipped/ nor .orig.bak files exist, revert returns 404."""
        base = ROOT / "data"
        shipped_dir = base / ".shipped"
        moved_shipped = []
        moved_bak = []

        # Temporarily hide all backups — both .shipped/ and .orig.bak
        for stem in ("CardsDataset.csv", "card_analysis_data.json",
                     "task1_B_condition_positioned_blank_cards.xlsx"):
            # .shipped/ files
            sp = shipped_dir / stem
            stmp = shipped_dir / (stem + ".test_moved")
            if sp.exists():
                shutil.move(str(sp), str(stmp))
                moved_shipped.append((sp, stmp))

            # .orig.bak files
            bak = base / (stem + ".orig.bak")
            tmp = base / (stem + ".orig.bak.test_moved")
            if bak.exists():
                shutil.move(str(bak), str(tmp))
                moved_bak.append((bak, tmp))

        try:
            resp = self.client.post("/behavioural-analysis/revert-dataset")
            self.assertEqual(resp.status_code, 404)
            self.assertIn("No backups", resp.get_json().get("error", ""))
        finally:
            for bak, tmp in moved_bak:
                if tmp.exists():
                    shutil.move(str(tmp), str(bak))
            for sp, stmp in moved_shipped:
                if stmp.exists():
                    shutil.move(str(stmp), str(sp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
