# SPDX-License-Identifier: MPL-2.0
import json

from reviewer_codex.host_config import DIRECT_TOOL_NAMESPACE, ensure_direct_namespace


class _Input:
    def __init__(self):
        self.messages = []

    def write(self, value):
        self.messages.append(json.loads(value))

    def flush(self):
        pass

    def close(self):
        pass


class _Process:
    def __init__(self, responses):
        self.stdin = _Input()
        self.stdout = iter(json.dumps(response) + "\n" for response in responses)

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


def _response(request_id, result):
    return {"id": request_id, "result": result}


def test_direct_namespace_is_merged_through_atomic_codex_config_rpc(tmp_path):
    process = _Process(
        [
            _response(1, {"userAgent": "test"}),
            _response(
                2,
                {
                    "config": {
                        "features": {
                            "code_mode": {
                                "direct_only_tool_namespaces": ["mcp__history"]
                            }
                        }
                    }
                },
            ),
            _response(3, {"status": "ok"}),
        ]
    )

    changed = ensure_direct_namespace(
        home=tmp_path,
        popen=lambda *args, **kwargs: process,
    )

    assert changed
    assert process.stdin.messages[-1] == {
        "method": "config/value/write",
        "id": 3,
        "params": {
            "keyPath": "features.code_mode.direct_only_tool_namespaces",
            "value": ["mcp__history", DIRECT_TOOL_NAMESPACE],
            "mergeStrategy": "replace",
        },
    }


def test_direct_namespace_configuration_is_idempotent(tmp_path):
    process = _Process(
        [
            _response(1, {"userAgent": "test"}),
            _response(
                2,
                {
                    "config": {
                        "features": {
                            "code_mode": {
                                "direct_only_tool_namespaces": [
                                    "mcp__history",
                                    DIRECT_TOOL_NAMESPACE,
                                ]
                            }
                        }
                    }
                },
            ),
        ]
    )

    changed = ensure_direct_namespace(
        home=tmp_path,
        popen=lambda *args, **kwargs: process,
    )

    assert not changed
    assert [item["method"] for item in process.stdin.messages] == [
        "initialize",
        "initialized",
        "config/read",
    ]
