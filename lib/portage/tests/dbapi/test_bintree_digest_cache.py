# Copyright 2026 The ChromiumOS Authors
# Distributed under the terms of the GNU General Public License v2

import os
import tempfile
from types import SimpleNamespace
from unittest import mock

from _emerge.BinpkgVerifier import BinpkgVerifier
from portage.checksum import perform_multiple_checksums
from portage.dbapi.bintree import binarytree
from portage.tests import TestCase


class BintreeDigestCacheTestCase(TestCase):

	def _make_bintree(self):
		# Allocate a minimal binarytree without loading full portage config.
		bt = binarytree.__new__(binarytree)
		bt._digests_cache = {}
		return bt

	def testCacheHitAcrossRenameAndInvalidation(self):
		# Verify cache hits survive rename/chmod, partial hits compute only
		# missing hashes, and modifying the file invalidates the cache entry.
		bt = self._make_bintree()
		with tempfile.TemporaryDirectory() as tmpdir:
			partial = os.path.join(tmpdir, "foo-1.tbz2.partial")
			final = os.path.join(tmpdir, "foo-1.tbz2")
			with open(partial, "wb") as f:
				f.write(b"binary-package-data")
			expected = perform_multiple_checksums(partial, hashes=("MD5", "SHA1"))

			# Cached digests survive rename (.partial -> .tbz2) and chmod.
			bt._cache_digests(partial, {"MD5": expected["MD5"]})
			os.rename(partial, final)
			os.chmod(final, 0o644)

			# Partial hit computes only missing SHA1 and reuses cached MD5.
			with mock.patch(
				"portage.dbapi.bintree.perform_multiple_checksums",
				wraps=perform_multiple_checksums,
			) as checksum_mock:
				got = bt._get_cached_digests(final, hashes=("MD5", "SHA1"))
				self.assertEqual(got, expected)
				checksum_mock.assert_called_once_with(final, hashes=["SHA1"])

				# Subsequent lookup for MD5 is a pure cache hit.
				self.assertEqual(
					bt._get_cached_digests(final, hashes=("MD5",)),
					{"MD5": expected["MD5"]},
				)
				checksum_mock.assert_called_once()

				# Replacing the file invalidates the cache key and recomputes.
				with open(final, "wb") as f:
					f.write(b"updated-binary-package-data")
				updated = bt._get_cached_digests(final, hashes=("MD5",))
				self.assertNotEqual(updated["MD5"], expected["MD5"])
				self.assertEqual(checksum_mock.call_count, 2)

	def testBinpkgVerifierCachesOnMatchOnly(self):
		# Verify BinpkgVerifier caches digests on bintree only when all
		# computed hashes match the expected digests.
		bt = self._make_bintree()
		with tempfile.TemporaryDirectory() as tmpdir:
			pkg_path = os.path.join(tmpdir, "foo-1.tbz2")
			open(pkg_path, "wb").close()
			pkg = SimpleNamespace(
				root_config=SimpleNamespace(
					trees={"bintree": bt}, settings={"PORTAGE_QUIET": "1"}
				)
			)

			def run_verifier(expected, actual):
				v = BinpkgVerifier(pkg=pkg, _pkg_path=pkg_path)
				v._digests = expected
				dig = SimpleNamespace(
					returncode=0, cancelled=False,
					hash_names=tuple(actual), digests=actual,
				)
				v._current_task = dig
				with mock.patch.object(BinpkgVerifier, "_digest_exception"), \
					mock.patch.object(BinpkgVerifier, "wait"):
					v._digester_exit(dig)

			# Mismatch must not populate the cache; verified match must populate it.
			run_verifier({"MD5": "expected"}, {"MD5": "wrong"})
			self.assertEqual(bt._digests_cache, {})
			run_verifier({"MD5": "abc", "SHA1": "def"}, {"MD5": "abc", "SHA1": "def"})
			self.assertEqual(
				bt._get_cached_digests(pkg_path, hashes=("MD5", "SHA1")),
				{"MD5": "abc", "SHA1": "def"},
			)
