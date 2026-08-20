from __future__ import annotations

from unittest.mock import patch

import pytest

from deploy.verify_demo import _assert_no_plain_model_secret, verify_model_configuration


def test_secret_check_accepts_masks_but_rejects_plain_keys() -> None:
    _assert_no_plain_model_secret({"api_key_mask": "sk-••••1234", "fingerprint": "abcd1234"})

    with pytest.raises(RuntimeError, match="secret field"):
        _assert_no_plain_model_secret({"api_key": "hidden"})

    with pytest.raises(RuntimeError, match="key-looking value"):
        _assert_no_plain_model_secret({"message": "sk-abcdefghijklmnopqrstuvwxyz123456"})


def test_model_configuration_recognizes_list_shaped_agent_bindings() -> None:
    agents = [
        {
            "agent_id": "hanhai-course-agent",
            "active_version": {
                "manifest": {
                    "capabilities": ["platform-model-gateway"],
                    "model_runtime": {"mode": "platform_optional"},
                }
            },
        }
    ]
    responses = {
        "http://hub/api/model-profiles": {
            "profiles": [{"profile_id": "profile-1"}],
        },
        "http://hub/api/model-profiles/profile-1": {
            "profile_id": "profile-1",
            "models": [{"id": "gpt-test", "chat_eligible": True}],
        },
        "http://hub/api/model-bindings": {
            "global": {
                "binding": {"profile_id": "profile-1", "model_id": "gpt-test"},
            },
            "agents": [
                {
                    "agent_id": "hanhai-course-agent",
                    "binding": {"profile_id": "profile-1", "model_id": "gpt-test"},
                }
            ],
        },
    }

    with patch("deploy.verify_demo.request_json", side_effect=lambda url, **_: responses[url]):
        result = verify_model_configuration("http://hub", user="demo-c", agents=agents)

    assert result["global_binding"] is True
    assert result["hanhai_binding"] is True
    assert result["chat_eligible_models"] == 1
