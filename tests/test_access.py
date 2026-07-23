import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import access


class AccessTokenTest(unittest.TestCase):
    def test_main_updates_access_token_without_printing_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "KITE_API_KEY=test_key\n"
                "KITE_API_SECRET=test_secret\n"
                "KITE_ACCESS_TOKEN=old_token\n",
                encoding="utf-8",
            )
            output = StringIO()

            with (
                patch.object(access, "__file__", str(Path(temp_dir) / "access.py")),
                patch.dict(os.environ, {
                    "KITE_API_KEY": "test_key",
                    "KITE_API_SECRET": "test_secret",
                }),
                patch.object(access, "getpass", return_value="fresh_request_token"),
                patch.object(access.KiteConnect, "generate_session",
                             return_value={"access_token": "new_access_token"}),
                redirect_stdout(output),
            ):
                access.main()

            contents = env_path.read_text(encoding="utf-8")
            self.assertIn("KITE_ACCESS_TOKEN=new_access_token", contents)
            self.assertNotIn("new_access_token", output.getvalue())
            self.assertIn("updated in .env", output.getvalue())


if __name__ == "__main__":
    unittest.main()
