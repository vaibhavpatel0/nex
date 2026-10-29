"""garak integration status (optional).

Running garak from the platform is NOT implemented in this prototype. We only detect
whether it is installed and document how to run it manually against the same Ollama
model (see docs/USER_GUIDE.md). The UI marks in-app execution as unavailable.
"""
from __future__ import annotations

import importlib.util
import shutil
from typing import Any, Dict


def garak_status() -> Dict[str, Any]:
    installed = importlib.util.find_spec("garak") is not None or shutil.which("garak") is not None
    return {
        "installed": installed,
        "in_app_execution": False,
        "note": "In-app garak runs are not implemented. Run garak manually; see the user guide.",
        "manual_command": "python -m garak --model_type ollama --model_name qwen3:8b --probes promptinject",
    }
