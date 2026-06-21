import os
import tempfile
import threading
import unittest
from pathlib import Path

from birka.infrastructure.scan_cache import ScanCache


class ScanCacheTests(unittest.TestCase):
    def _open_cache(self, tmp: str) -> ScanCache:
        return ScanCache(Path(tmp) / "cache.sqlite")

    def test_miss_returns_none_for_unknown_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            self.assertIsNone(cache.get("/nope.wav", 1.0, 100))
            cache.close()

    def test_put_then_get_round_trips_wav_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            meta = {
                "duration_seconds": 12.5,
                "sample_rate_hz": 48000,
                "channels": 2,
                "bpm": 128.0,
                "key": "F#m",
            }
            cache.put("/a.wav", 100.0, 4096, "wav", meta)
            got = cache.get("/a.wav", 100.0, 4096)
            self.assertIsNotNone(got)
            self.assertEqual(got["kind"], "wav")
            self.assertEqual(got["sample_rate_hz"], 48000)
            self.assertEqual(got["bpm"], 128.0)
            self.assertEqual(got["key"], "F#m")
            cache.close()

    def test_get_invalidates_on_mtime_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            cache.put("/a.wav", 100.0, 4096, "wav", {"bpm": 90.0})
            # Same size, newer mtime → file was edited → cache must miss.
            self.assertIsNone(cache.get("/a.wav", 101.0, 4096))
            cache.close()

    def test_get_invalidates_on_size_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            cache.put("/a.wav", 100.0, 4096, "wav", {"bpm": 90.0})
            # Same mtime, different size → file truncated/grown → miss.
            self.assertIsNone(cache.get("/a.wav", 100.0, 8192))
            cache.close()

    def test_put_upserts_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            cache.put("/a.wav", 100.0, 4096, "wav", {"bpm": 90.0})
            cache.put("/a.wav", 100.0, 4096, "wav", {"bpm": 120.0})
            got = cache.get("/a.wav", 100.0, 4096)
            self.assertEqual(got["bpm"], 120.0)
            cache.close()

    def test_prune_removes_paths_not_in_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            cache.put("/keep.wav", 1.0, 10, "wav", {})
            cache.put("/gone.wav", 1.0, 10, "wav", {})
            deleted = cache.prune({"/keep.wav"})
            self.assertEqual(deleted, 1)
            self.assertIsNotNone(cache.get("/keep.wav", 1.0, 10))
            self.assertIsNone(cache.get("/gone.wav", 1.0, 10))
            cache.close()

    def test_prune_empty_set_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            cache.put("/a.wav", 1.0, 10, "wav", {})
            # Must NOT delete everything when the existing-set is empty.
            self.assertEqual(cache.prune(set()), 0)
            self.assertIsNotNone(cache.get("/a.wav", 1.0, 10))
            cache.close()

    def test_cache_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            cache.put("/a.wav", 5.0, 256, "wav", {"bpm": 140.0})
            cache.close()
            # Reopen the same file path — metadata must persist.
            cache2 = self._open_cache(tmp)
            got = cache2.get("/a.wav", 5.0, 256)
            self.assertIsNotNone(got)
            self.assertEqual(got["bpm"], 140.0)
            cache2.close()

    def test_concurrent_reads_and_writes_are_thread_safe(self) -> None:
        # The scanner dispatches reads via a ThreadPoolExecutor while a
        # previous batch may still be flushing writes. Verify no deadlock /
        # corruption under interleaved get/put from many threads.
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._open_cache(tmp)
            errors = []

            def worker(n: int) -> None:
                try:
                    for i in range(50):
                        path = f"/t{n}_{i}.wav"
                        cache.put(path, float(i), i * 10, "wav", {"bpm": float(i)})
                        got = cache.get(path, float(i), i * 10)
                        if got is None or got["bpm"] != float(i):
                            errors.append((path, got))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [], f"concurrent cache errors: {errors[:3]}")
            cache.close()


if __name__ == "__main__":
    unittest.main()
