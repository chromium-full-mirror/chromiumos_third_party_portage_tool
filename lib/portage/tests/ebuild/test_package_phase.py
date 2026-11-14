# Copyright 2026 The ChromiumOS Authors
# Distributed under the terms of the GNU General Public License v2

import subprocess
import tempfile

from _emerge.PackagePhase import PackagePhase
from _emerge.SpawnProcess import SpawnProcess

from portage import os
from portage import shutil
from portage.tests import TestCase
from portage.util._async.SchedulerInterface import SchedulerInterface
from portage.util._eventloop.global_event_loop import global_event_loop


class _FakeSettings(dict):
	def environ(self):
		env = os.environ.copy()
		env.update(self)
		return env


class _RecordingPackagePhase(PackagePhase):
	__slots__ = ("proot_state",)

	def _start_package_phase(self):
		self.proot_state = (
			os.path.exists(os.path.join(self._proot, "masked")),
			os.stat(os.path.join(self._proot, "keep_a")).st_ino,
			os.stat(os.path.join(self._proot, "keep_b")).st_ino,
		)
		self._start_task(
			SpawnProcess(
				args=["true"],
				background=self.background,
				env=self.settings.environ(),
				scheduler=self.scheduler,
				logfile=self.logfile,
			),
			self._pkg_install_mask_cleanup,
		)


class PackagePhaseTestCase(TestCase):

	def testPkgInstallMaskPreservesHardlinksWithTerm(self):
		"""
		Verify PackagePhase preserves hardlinks in ${T}/packaging even when
		TERM is set (GNU coreutils 9.0+ emits OSC-8/SGR escapes in cp --help
		whenever TERM is non-empty and != "dumb").
		"""
		tmp_dir = tempfile.mkdtemp()
		try:
			builddir = os.path.join(tmp_dir, "builddir")
			build_info = os.path.join(builddir, "build-info")
			d_dir = os.path.join(builddir, "image")
			t_dir = os.path.join(builddir, "temp")
			for path in (build_info, d_dir, t_dir):
				os.makedirs(path)

			with open(os.path.join(build_info, "PKG_INSTALL_MASK"), "w") as f:
				f.write("/masked\n")

			keep_a = os.path.join(d_dir, "keep_a")
			keep_b = os.path.join(d_dir, "keep_b")
			with open(keep_a, "w") as f:
				f.write("payload\n")
			os.link(keep_a, keep_b)
			with open(os.path.join(d_dir, "masked"), "w") as f:
				f.write("drop\n")

			probe_dst = os.path.join(tmp_dir, "probe_dst")
			cp_supports_hardlink = (
				subprocess.call(
					["cp", "-l", keep_a, probe_dst],
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL,
				)
				== 0
			)

			settings = _FakeSettings(
				PORTAGE_BUILDDIR=builddir,
				D=d_dir,
				T=t_dir,
				EPREFIX="",
				TERM="xterm",
			)
			phase = _RecordingPackagePhase(
				background=False,
				scheduler=SchedulerInterface(global_event_loop()),
				settings=settings,
			)
			phase.start()
			phase.wait()

			self.assertEqual(phase.returncode, os.EX_OK)
			self.assertFalse(os.path.exists(phase._proot))
			masked_exists, ino_a, ino_b = phase.proot_state
			self.assertFalse(masked_exists)
			if cp_supports_hardlink:
				self.assertEqual(ino_a, ino_b)
		finally:
			shutil.rmtree(tmp_dir)
