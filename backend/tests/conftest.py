"""测试环境：在任何 app.* 导入之前把库指到临时 SQLite 文件。"""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(prefix="stallspan_test_", suffix=".sqlite", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["SEED_ON_EMPTY"] = "false"
