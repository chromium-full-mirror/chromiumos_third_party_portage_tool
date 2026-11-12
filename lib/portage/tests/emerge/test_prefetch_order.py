# Copyright 2026 The ChromiumOS Authors
# Distributed under the terms of the GNU General Public License v2

from types import SimpleNamespace
from unittest import mock
import weakref

from _emerge.Package import Package
from _emerge.Scheduler import Scheduler
from portage.tests import TestCase
from portage.versions import _pkg_str


def _pkg(cpv_str, size="", operation="merge"):
	# _pkg_str parses metadata["SIZE"] into cpv.file_size (or None when empty).
	cpv = _pkg_str(cpv_str, metadata={"SIZE": size})
	return mock.Mock(spec=Package, cpv=cpv, operation=operation)


class PrefetchOrderTestCase(TestCase):

	def _run(self, mergelist, max_jobs):
		# Build a minimal Scheduler stub to record the order in which
		# _add_prefetchers enqueues prefetchers onto the fetch queue.
		queue = []
		sched = SimpleNamespace(
			_parallel_fetch=True,
			_prefetchers=weakref.WeakValueDictionary(),
			_mergelist=mergelist,
			_max_jobs=max_jobs,
			_task_queues=SimpleNamespace(fetch=SimpleNamespace(add=queue.append)),
			_create_prefetcher=lambda pkg: mock.Mock(pkg=pkg),
		)
		Scheduler._add_prefetchers(sched)
		return [str(p.pkg.cpv) for p in queue]

	def testPrefetchOrder(self):
		pkgs = [
			_pkg("a/p1-1", "100"),
			_pkg("a/p2-1", "200"),
			_pkg("a/p3-1", "300"),
			# Source builds have no binary SIZE metadata (file_size is None).
			_pkg("a/src-1"),
			_pkg("a/big-1", "50000000"),
			_pkg("a/huge-1", "6716903262"),
			# Solved Blockers and uninstalls must be skipped regardless of size.
			object(),
			_pkg("a/old-1", "999999", operation="uninstall"),
		]
		# With parallel jobs (_max_jobs > 1), keep the first _max_jobs
		# entries in merge order, then interleave merge order and descending
		# file_size (treating missing file_size as 0).
		self.assertEqual(
			self._run(pkgs, max_jobs=2),
			["a/p1-1", "a/p2-1", "a/p3-1", "a/huge-1", "a/src-1", "a/big-1"],
		)
		merge_order = [
			"a/p1-1", "a/p2-1", "a/p3-1", "a/src-1", "a/big-1", "a/huge-1",
		]
		# When all entries fit within _max_jobs, merge order is preserved.
		self.assertEqual(self._run(pkgs, max_jobs=16), merge_order)
		# With --jobs=1 or bare --jobs (_max_jobs is True), ready packages
		# share the single-slot fetch queue, so preserve merge order.
		self.assertEqual(self._run(pkgs, max_jobs=1), merge_order)
		self.assertEqual(self._run(pkgs, max_jobs=True), merge_order)
