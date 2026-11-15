"""Tripwire: the suite must not run against the real data folder.

Some tests post fake players and text to a display they start. If the runtime
folder is the real one, that overwrites a live display's saved state. Run the
suite with `bash scripts/run-tests.sh`, which uses a throwaway folder.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "dnd" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "dnd" / "display"))


class SandboxGuard(unittest.TestCase):
    def test_the_runtime_folder_is_not_a_real_one_with_campaigns(self):
        import runtime_paths
        real_root = Path.home() / ".claude" / "dnd"
        campaigns = real_root / "campaigns"
        has_real_data = campaigns.is_dir() and any(campaigns.iterdir())
        rt = Path(runtime_paths.RT).resolve()
        inside_real = real_root.resolve() == rt or real_root.resolve() in rt.parents
        if has_real_data and inside_real:
            self.fail(
                f"The tests are about to use your real data folder ({rt}). "
                "Run them with: bash scripts/run-tests.sh")


if __name__ == "__main__":
    unittest.main()
