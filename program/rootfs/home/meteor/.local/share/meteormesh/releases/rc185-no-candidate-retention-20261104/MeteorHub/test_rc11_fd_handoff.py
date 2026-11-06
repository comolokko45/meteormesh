"""Regression: verified HDD input FDs must survive only intended child processes."""
import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from mm01_bridge import _ACTIVE, guarded_ffprobe_run, guarded_detector_run


class FakePinnedGuard:
    """Only for isolated fd-handoff tests; never production guard replacement."""
    def __init__(self, root):
        self.root = root
        self.policy = SimpleNamespace(root=str(root))
        self._fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        self._initial_dev = os.fstat(self._fd).st_dev
        self._mm01_pins = {}
    def verify(self):
        return None
    def _open_parent(self, parts, *, create=False):
        return os.open(self.root.joinpath(*parts), os.O_RDONLY | os.O_DIRECTORY)
    def close(self):
        os.close(self._fd)


class FDHandoff(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rc11-fd-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.guard = FakePinnedGuard(self.root)
        self.addCleanup(self.guard.close)
        self.token = _ACTIVE.set(self.guard)
        self.addCleanup(lambda: _ACTIVE.reset(self.token))
        self.camera = self.root / 'cam-viva'
        self.camera.mkdir()
        self.results = self.root / 'results'
        self.results.mkdir()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg not installed')
    def test_real_ffprobe_reads_inherited_pinned_video(self):
        video = self.camera / 'valid.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=1:d=1',
                        '-c:v', 'mpeg4', '-y', str(video)], check=True,
                       capture_output=True, timeout=15)
        command = ['ffprobe', '-v', 'error', '-show_entries',
                   'format=duration:stream=codec_type,width,height', '-of', 'json', str(video)]
        result = guarded_ffprobe_run(subprocess.run, command, video)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertGreater(float(data['format']['duration']), 0)

    def test_real_child_reads_video_and_writes_pending_via_fd(self):
        video = self.camera / 'input.mp4'
        video.write_bytes(b'METEORMESH_VIDEO_CONTENT')
        script = self.root / 'child.py'
        script.write_text('import sys,pathlib\n'
                          'data=pathlib.Path(sys.argv[1]).read_bytes()\n'
                          'pathlib.Path(sys.argv[3]).write_text(str(len(data)))\n')
        out = self.results / 'result.pending.json'
        argv = [sys.executable, str(script), str(video), '--save-path', str(out)]
        result = guarded_detector_run(subprocess.run, argv, out, self.root, 15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.read_text(), str(len(video.read_bytes())))

    def test_ffprobe_rejects_video_symlink(self):
        original = self.camera / 'existing.mp4'
        original.write_bytes(b'abc')
        video = self.camera / 'linked.mp4'
        video.symlink_to(original)
        with self.assertRaises(OSError):
            guarded_ffprobe_run(subprocess.run, ['ffprobe', str(video)], video)


if __name__ == '__main__':
    unittest.main()
