"""操作画面の JavaScript に構文エラーがないか（Node.js があるときだけ確かめる）。"""

import re
import shutil
import subprocess

import pytest

from cattoy import webpage


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js がない")
def test_control_page_script_parses(tmp_path):
    scripts = re.findall(r"<script>(.*?)</script>", webpage.CONTROL_PAGE, re.S)
    assert scripts
    js = tmp_path / "page.js"
    js.write_text("\n".join(scripts), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
