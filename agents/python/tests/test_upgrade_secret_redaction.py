"""新提供方凭据不出现在配置 repr 或校验异常文本中。"""

import pytest
from pydantic import ValidationError

from shared.config import Settings


def test_credentials_are_hidden_from_configuration_diagnostics():
    credential = "synthetic-credential-must-not-appear"
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY=credential, ENVIRONMENT="test")
    assert credential not in repr(settings)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, DEEPSEEK_API_KEY=credential, MAF_STREAM_QUEUE_SIZE="bad-number")
    assert credential not in str(error.value)
    assert "input_value" not in str(error.value)
