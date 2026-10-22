import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from contractedge.dataset.archive import ArchiveError, ExtractionLimits, safe_extract
from contractedge.dataset.archive import inspect_archive
from contractedge.source.snapshot import compute_snapshot


def make_tar(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for name, kind, content in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = content if isinstance(content, bytes) else content.encode()
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = content
                archive.addfile(info)
            else:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)


class SafeExtractionTests(unittest.TestCase):
    def test_extracts_regular_files_and_hashes_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "src.tar.gz"
            dest = Path(tmp) / "out"
            make_tar(archive, [("repo/a.txt", "file", "hello")])
            tree = safe_extract(archive, dest)
            self.assertEqual((dest / "repo/a.txt").read_text(), "hello")
            self.assertEqual(tree.source_root, str(dest / "repo"))
            self.assertIn("a.txt", tree.file_digests)
            self.assertEqual(tree.snapshot_id, compute_snapshot(dest / "repo").snapshot_id)
            inspection = inspect_archive(archive)
            self.assertEqual(inspection.source_root, "repo")
            self.assertEqual(inspection.snapshot_id, tree.snapshot_id)

    def test_accepts_archive_root_directory_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "src.tar.gz"
            dest = Path(tmp) / "out"
            make_tar(archive, [(".", "dir", ""), ("repo/a.txt", "file", "hello")])
            tree = safe_extract(archive, dest)
            self.assertEqual((dest / "repo/a.txt").read_text(), "hello")
            self.assertEqual(tree.file_digests.keys(), {"a.txt"})

    def test_rejects_parent_traversal_and_absolute_paths(self):
        for name in ("../escape", "/tmp/escape"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / "src.tar.gz"
                make_tar(archive, [(name, "file", "x")])
                with self.assertRaises(ArchiveError):
                    safe_extract(archive, Path(tmp) / "out")

    def test_rejects_escaping_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "src.tar.gz"
            make_tar(archive, [("repo/link", "symlink", "../../outside")])
            with self.assertRaises(ArchiveError):
                safe_extract(archive, Path(tmp) / "out")

    def test_allows_internal_symlink_without_following_it_for_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "src.tar.gz"
            dest = Path(tmp) / "out"
            make_tar(archive, [("repo/real.txt", "file", "ok"), ("repo/link.txt", "symlink", "real.txt")])
            tree = safe_extract(archive, dest)
            self.assertTrue((dest / "repo/link.txt").is_symlink())
            self.assertEqual((dest / "repo/link.txt").read_text(), "ok")
            self.assertNotIn("repo/link.txt", tree.file_digests)

    def test_rejects_expanded_size_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "src.tar.gz"
            make_tar(archive, [("repo/a.txt", "file", "0123456789")])
            with self.assertRaises(ArchiveError):
                safe_extract(archive, Path(tmp) / "out", ExtractionLimits(max_expanded_bytes=5))


if __name__ == "__main__":
    unittest.main()
