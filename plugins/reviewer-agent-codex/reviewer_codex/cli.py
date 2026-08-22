# SPDX-License-Identifier: MPL-2.0
"""Deterministic toggle commands used by bundled skills."""

import sys
from pathlib import Path

from . import controls, state


def _usage():
    print(
        "usage: toggle.py reviewer|stop on|off|status | "
        "model [<model> [<effort>] | clear | effort <level>|clear]",
        file=sys.stderr,
    )
    return 2


def _show_model():
    model, model_source = state.reviewer_model_detail()
    effort, effort_source = state.reviewer_effort_detail()
    print(
        "reviewerModel: {} {} (model from {}; effort from {})".format(
            model, effort, model_source, effort_source
        )
    )
    return 0


def _model_command(args):
    if not args or args == ["status"]:
        return _show_model()
    if args[0] == "effort":
        if len(args) != 2:
            return _usage()
        if args[1] == "clear":
            state.clear_reviewer_effort()
            return _show_model()
        if not state.is_valid_reviewer_effort(args[1]):
            print(
                "reviewerModel: ERROR - invalid reasoning effort {!r}; expected one of: {}".format(
                    args[1], ", ".join(state.REVIEWER_EFFORTS)
                ),
                file=sys.stderr,
            )
            return 2
        state.set_reviewer_effort(args[1])
        return _show_model()
    if args[0] == "clear":
        if len(args) != 1:
            return _usage()
        state.clear_reviewer_model()
        return _show_model()
    if len(args) > 2:
        return _usage()
    model = args[0]
    effort = args[1] if len(args) == 2 else None
    if not state.is_valid_reviewer_model(model):
        print(
            "reviewerModel: ERROR - invalid model {!r}; expected one token using "
            "letters, digits, dot, underscore, colon, slash, or hyphen".format(model),
            file=sys.stderr,
        )
        return 2
    if effort is not None and not state.is_valid_reviewer_effort(effort):
        print(
            "reviewerModel: ERROR - invalid reasoning effort {!r}; expected one of: {}".format(
                effort, ", ".join(state.REVIEWER_EFFORTS)
            ),
            file=sys.stderr,
        )
        return 2
    state.set_reviewer_model(model)
    if effort is not None:
        state.set_reviewer_effort(effort)
    return _show_model()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "model":
        return _model_command(args[1:])
    if len(args) != 2 or args[0] not in ("reviewer", "stop") or args[1] not in ("on", "off", "status"):
        return _usage()
    feature, action = args
    code, message = controls.apply_feature_action(Path.cwd(), feature, action)
    print(message, file=sys.stderr if code else sys.stdout)
    return code
