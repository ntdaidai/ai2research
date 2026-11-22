import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from harness import service


class DocumentTools(unittest.TestCase):
    def test_failed_export_does_not_report_stale_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'diagram.drawio'
            source.write_text('<mxfile/>')
            target = source.with_suffix('.svg')
            target.write_text('<svg>old</svg>')
            with patch.object(service.shutil, 'which', return_value=sys.executable), patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
                result = service.export_diagram(str(source))
            self.assertEqual(result['status'], 'failed')
            self.assertIsNone(result['output'])
            self.assertEqual(target.read_text(), '<svg>old</svg>')
            self.assertEqual(list(root.glob('.drawio-export-*')), [])

    def test_successful_pdf_export_validates_and_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'diagram.drawio'
            source.write_text('<mxfile/>')
            def export(cmd, **kwargs):
                Path(cmd[cmd.index('-o')+1]).write_bytes(b'%PDF-1.4\nverified')
                return subprocess.CompletedProcess(cmd, 0)
            with patch.object(service.shutil, 'which', return_value=sys.executable), patch.object(service.subprocess, 'run', side_effect=export):
                result = service.export_diagram(str(source), 'pdf')
            self.assertEqual(result['status'], 'completed')
            self.assertTrue(Path(result['output']).read_bytes().startswith(b'%PDF-'))
            self.assertEqual(list(Path(tmp).glob('.drawio-export-*')), [])

    def test_tectonic_fallback_uses_untrusted_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'paper.tex'
            source.write_text('test')
            def compiler(name):
                return '/usr/bin/tectonic' if name == 'tectonic' else None
            def compile(cmd, **kwargs):
                self.assertIn('--untrusted', cmd)
                out = Path(cmd[cmd.index('--outdir')+1])
                (out/'paper.pdf').write_bytes(b'%PDF-1.4\n')
                return subprocess.CompletedProcess(cmd, 0, b'compiled', b'')
            with patch.dict(os.environ, {'RESEARCH_STATE_DIR':tmp}), patch.object(service.shutil, 'which', side_effect=compiler), patch.object(service.subprocess, 'run', side_effect=compile):
                self.assertEqual(service.compile_paper(str(source))['status'], 'completed')
