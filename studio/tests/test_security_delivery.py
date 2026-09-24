import tempfile
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from defusedxml.common import DTDForbidden
from plugins.tools._reader_core import _guard_office_archive
from plugins.tools._reader_xlsx import _ET, _x_chart_lines
from scripts.security_scan import scan_files


class DeliverySecurityTests(unittest.TestCase):
    def test_chart_xml_rejects_entity_declarations(self):
        xml = '<!DOCTYPE root [<!ENTITY internal "expanded">]><root>&internal;</root>'
        with self.assertRaises(DTDForbidden):
            _ET.fromstring(xml, forbid_dtd=True)

    def test_oversized_chart_is_not_decompressed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chart.xlsx"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("xl/charts/chart1.xml", "x" * 2_000_001)
            with patch.object(zipfile.ZipFile, "read", side_effect=AssertionError("must not decompress")):
                result = _x_chart_lines(path)
            self.assertIn("safety limit", str(result))

    def test_office_zip_bomb_is_rejected_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bomb.xlsx"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("xl/sharedStrings.xml", b"x" * 25_000_001)
            with self.assertRaisesRegex(ValueError, "25 MB"):
                _guard_office_archive(path)
            from plugins.tools._doc_reader import reader_src
            result = subprocess.run([sys.executable, "-", str(path)], input=reader_src(), text=True, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("Office archive rejected", result.stderr)

    def test_small_real_spreadsheet_still_loads(self):
        from openpyxl import Workbook, load_workbook
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe.xlsx"
            book = Workbook(); book.active["A1"] = "OK"; book.save(path); book.close()
            _guard_office_archive(path)
            loaded = load_workbook(path)
            self.assertEqual(loaded.active["A1"].value, "OK")
            loaded.close()

    def test_publication_scan_detects_credentials_without_printing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = "gh" + "p_" + "a" * 36
            (root / "config.txt").write_text(value)
            (root / ".ENV").write_text("local configuration")
            (root / "alias").symlink_to(root / "config.txt")
            findings = scan_files(root, ["config.txt", ".ENV", "alias"])
            self.assertEqual({f["kind"] for f in findings}, {"GitHub token", "private runtime path", "tracked symlink"})
            self.assertNotIn(value, str(findings))
