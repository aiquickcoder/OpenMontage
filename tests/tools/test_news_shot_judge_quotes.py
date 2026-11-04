"""White quotation cards must not collide just because their margins match."""
from pathlib import Path

import unittest
import tempfile

import cv2
import numpy as np

from addons.news.shot_judge import hard_rules, same_paper_quote


def card(tmp_path: Path, name: str, text: str) -> dict:
    im = np.full((760, 1080, 3), 255, np.uint8)
    cv2.putText(im, text, (70, 380), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2)
    path = tmp_path / f"{name}.jpg"
    cv2.imwrite(str(path), im)
    return {"path": path, "still": True, "role": "quote", "src_key": name}


class QuoteDedupTests(unittest.TestCase):
    def test_different_text_with_same_coarse_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            a = card(tmp_path, "a", "We are going to crash into the ground")
            b = card(tmp_path, "b", "Investigation remains ongoing")
            assert not same_paper_quote(a, b)
            assert not any("looks the same" in p for v in hard_rules([a, b], [0, 0]).values() for p in v)


    def test_compressed_repost_still_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            a = card(tmp_path, "a", "We are going to crash into the ground")
            path = tmp_path / "repost.jpg"
            cv2.imwrite(str(path), cv2.imread(str(a["path"])), [cv2.IMWRITE_JPEG_QUALITY, 55])
            b = {**a, "src_key": "repost", "path": path}
            assert same_paper_quote(a, b)
            assert "looks the same as shot 1" in hard_rules([a, b], [0, 0])[1]


    def test_same_file_rule_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            a = card(tmp_path, "a", "Source quotation")
            assert "repeats shot 1 (a)" in hard_rules([a, a], [0, 2**32])[1]


    def test_normal_pictures_keep_coarse_hash_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            a = card(tmp_path, "a", "First")
            b = card(tmp_path, "b", "Second")
            a["role"] = "key"
            assert hard_rules([a, b], [0, 0])[1] == ["looks the same as shot 1"]

if __name__ == "__main__":
    unittest.main()
