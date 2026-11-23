# Copyright 2026 The ChromiumOS Authors
# Distributed under the terms of the GNU General Public License v2

import os as _os
import tempfile

try:
	from unittest import mock
except ImportError:
	try:
		import mock
	except ImportError:
		mock = None

import portage
from portage import os
from portage import shutil
from portage.dbapi.vartree import vartree
from portage.package.ebuild.config import config
from portage.tests import TestCase
from _emerge.BlockerCache import BlockerCache


class VartreeFlushCacheTestCase(TestCase):

	def _create_repo_env(self, base_dir):
		test_repo = os.path.join(base_dir, "var", "repositories", "test_repo")
		os.makedirs(os.path.join(test_repo, "profiles"))
		with open(os.path.join(test_repo, "profiles", "repo_name"), "w") as f:
			f.write("test_repo\n")
		return {
			"PORTAGE_REPOSITORIES": (
				"[DEFAULT]\nmain-repo = test_repo\n"
				"[test_repo]\nlocation = %s\n" % test_repo
			),
		}

	def _make_config(self, root_dir, env):
		return config(
			config_root=root_dir,
			config_profile_path="",
			target_root=root_dir,
			sysroot=root_dir,
			eprefix="",
			env=env,
		)

	def _safe_rmtree(self, path):
		for root, dirs, files in os.walk(path):
			for d in dirs:
				os.chmod(os.path.join(root, d), 0o755)
			for fn in files:
				p = os.path.join(root, fn)
				if not os.path.islink(p):
					os.chmod(p, 0o644)
		os.chmod(path, 0o755)
		shutil.rmtree(path)

	def testFlushCacheWritableAndReadOnlyTrees(self):
		if os.getuid() == 0:
			self.skipTest("chmod 0o555 does not block writes as root")

		tempdir = tempfile.mkdtemp()
		try:
			os.chmod(tempdir, 0o755)
			env = self._create_repo_env(tempdir)

			rw_root = os.path.join(tempdir, "rw_root")
			os.makedirs(rw_root)
			os.chmod(rw_root, 0o755)

			ro_root = os.path.join(tempdir, "ro_root")
			ro_vdb = os.path.join(ro_root, "var", "db", "pkg")
			ro_edb = os.path.join(ro_root, "var", "cache", "edb")
			os.makedirs(ro_vdb)
			os.makedirs(ro_edb)
			os.chmod(ro_vdb, 0o555)
			os.chmod(ro_edb, 0o555)
			os.chmod(os.path.join(ro_root, "var"), 0o555)
			os.chmod(ro_root, 0o555)

			# Create the writable tree first so global secpass would be latched
			# to 2 (unprivileged mode), matching cross-root `emerge` startup.
			rw_settings = self._make_config(rw_root, env)
			ro_settings = self._make_config(ro_root, env)

			self.assertIn("unprivileged", rw_settings.features)
			self.assertNotIn("unprivileged", ro_settings.features)

			rw_tree = vartree(settings=rw_settings)
			ro_tree = vartree(settings=ro_settings)

			rw_tree.dbapi.flush_cache()
			self.assertTrue(os.path.exists(rw_tree.dbapi._aux_cache_filename))
			self.assertTrue(os.path.exists(rw_tree.dbapi._cache_delta_filename))

			# Must be a safe no-op rather than raising PermissionError.
			ro_tree.dbapi.flush_cache()
			self.assertEqual(os.listdir(ro_edb), [])
		finally:
			self._safe_rmtree(tempdir)

	def testFlushCacheReadOnlyCacheOrVdbDir(self):
		if os.getuid() == 0:
			self.skipTest("chmod 0o555 does not block writes as root")

		tempdir = tempfile.mkdtemp()
		try:
			os.chmod(tempdir, 0o755)
			env = self._create_repo_env(tempdir)

			# Unprivileged tree whose cache dir is read-only.
			root_ro_cache = os.path.join(tempdir, "root_ro_cache")
			vdb_rw = os.path.join(root_ro_cache, "var", "db", "pkg")
			edb_ro = os.path.join(root_ro_cache, "var", "cache", "edb")
			os.makedirs(vdb_rw)
			os.makedirs(edb_ro)
			os.chmod(root_ro_cache, 0o755)
			os.chmod(vdb_rw, 0o755)
			os.chmod(edb_ro, 0o555)

			t1 = vartree(settings=self._make_config(root_ro_cache, env))
			self.assertIn("unprivileged", t1.settings.features)
			t1.dbapi.flush_cache()
			self.assertEqual(os.listdir(edb_ro), [])

			# Unprivileged tree whose VDB dir is read-only (cannot be locked).
			root_ro_vdb = os.path.join(tempdir, "root_ro_vdb")
			vdb_ro = os.path.join(root_ro_vdb, "var", "db", "pkg")
			edb_rw = os.path.join(root_ro_vdb, "var", "cache", "edb")
			os.makedirs(vdb_ro)
			os.makedirs(edb_rw)
			os.chmod(root_ro_vdb, 0o755)
			os.chmod(vdb_ro, 0o555)
			os.chmod(edb_rw, 0o755)

			t2 = vartree(settings=self._make_config(root_ro_vdb, env))
			self.assertIn("unprivileged", t2.settings.features)
			t2.dbapi.flush_cache()
			self.assertEqual(os.listdir(edb_rw), [])
		finally:
			self._safe_rmtree(tempdir)

	def testFlushCacheWorldWritableRootSkipped(self):
		if os.getuid() == 0:
			self.skipTest("unprivileged check only runs as non-root")

		tempdir = tempfile.mkdtemp()
		try:
			os.chmod(tempdir, 0o755)
			env = self._create_repo_env(tempdir)

			ww_root = os.path.join(tempdir, "ww_root")
			edb = os.path.join(ww_root, "var", "cache", "edb")
			os.makedirs(edb)
			os.chmod(ww_root, 0o777)

			ww_settings = self._make_config(ww_root, env)
			self.assertNotIn("unprivileged", ww_settings.features)

			ww_tree = vartree(settings=ww_settings)
			ww_tree.dbapi.flush_cache()
			self.assertEqual(os.listdir(edb), [])
		finally:
			self._safe_rmtree(tempdir)

	def testFlushCacheIgnoresGlobalSecpassAndReadOnlyRootAsUid0(self):
		if mock is None:
			self.skipTest("need mock for testing")
		if _os.getuid() == 0:
			self.skipTest("chmod 0o555 does not block os.access as root")

		tempdir = tempfile.mkdtemp()
		try:
			os.chmod(tempdir, 0o755)
			env = self._create_repo_env(tempdir)

			ro_root = os.path.join(tempdir, "ro_root")
			ro_vdb = os.path.join(ro_root, "var", "db", "pkg")
			ro_edb = os.path.join(ro_root, "var", "cache", "edb")
			os.makedirs(ro_vdb)
			os.makedirs(ro_edb)
			os.chmod(ro_vdb, 0o555)
			os.chmod(ro_edb, 0o555)
			os.chmod(ro_root, 0o555)
			ro_tree = vartree(settings=self._make_config(ro_root, env))

			# Even if uid == 0 (e.g. root with a read-only mount), flush_cache
			# must skip when var/db/pkg and var/cache/edb are not writable.
			with mock.patch.object(_os, "getuid", return_value=0) as mock_getuid:
				self.assertFalse(ro_tree.dbapi._aux_cache_writable())
				ro_tree.dbapi.flush_cache()
				self.assertGreaterEqual(mock_getuid.call_count, 2)
			self.assertEqual(os.listdir(ro_edb), [])

			# Even if global secpass is 0, a writable unprivileged tree flushes.
			rw_root = os.path.join(tempdir, "rw_root")
			os.makedirs(rw_root)
			os.chmod(rw_root, 0o755)
			rw_tree = vartree(settings=self._make_config(rw_root, env))
			with mock.patch.object(portage.data, "secpass", 0):
				rw_tree.dbapi.flush_cache()
			self.assertTrue(os.path.exists(rw_tree.dbapi._aux_cache_filename))
			self.assertTrue(os.path.exists(rw_tree.dbapi._cache_delta_filename))

			# And if a root lacks "unprivileged" (e.g. world-writable dir),
			# uid == 0 still permits flushing when the tree is writable.
			root_no_unpriv = os.path.join(tempdir, "root_no_unpriv")
			os.makedirs(root_no_unpriv)
			os.chmod(root_no_unpriv, 0o777)
			root_tree = vartree(settings=self._make_config(root_no_unpriv, env))
			self.assertNotIn("unprivileged", root_tree.settings.features)
			with mock.patch.object(_os, "getuid", return_value=0):
				root_tree.dbapi.flush_cache()
			self.assertTrue(os.path.exists(root_tree.dbapi._aux_cache_filename))
		finally:
			self._safe_rmtree(tempdir)

	def testBlockerCacheFlushRespectsPerTreePrivilege(self):
		if os.getuid() == 0:
			self.skipTest("unprivileged check only runs as non-root")

		tempdir = tempfile.mkdtemp()
		try:
			os.chmod(tempdir, 0o755)
			env = self._create_repo_env(tempdir)

			# World-writable root has "unprivileged" disabled even though
			# var/cache/edb is writable.
			ww_root = os.path.join(tempdir, "ww_root")
			os.makedirs(os.path.join(ww_root, "var", "cache", "edb"))
			os.chmod(ww_root, 0o777)

			rw_root = os.path.join(tempdir, "rw_root")
			os.makedirs(os.path.join(rw_root, "var", "cache", "edb"))
			os.chmod(rw_root, 0o755)

			rw_tree = vartree(settings=self._make_config(rw_root, env))
			ww_tree = vartree(settings=self._make_config(ww_root, env))

			bc_ww = BlockerCache(None, ww_tree.dbapi)
			bc_rw = BlockerCache(None, rw_tree.dbapi)
			for i in range(BlockerCache._cache_threshold):
				data = BlockerCache.BlockerData(i, ())
				cpv = "cat/pkg-%d" % i
				bc_ww[cpv] = data
				bc_rw[cpv] = data

			bc_ww.flush()
			self.assertFalse(os.path.exists(bc_ww._cache_filename))

			bc_rw.flush()
			self.assertTrue(os.path.exists(bc_rw._cache_filename))
		finally:
			self._safe_rmtree(tempdir)
