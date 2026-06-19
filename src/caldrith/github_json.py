"""Raw-JSON helpers for githubkit responses.

githubkit eagerly validates response bodies against its full typed models when you
touch ``response.parsed_data``. For Caldrith's diff/reconcile path we only need a plain
``dict`` of the fields the API returned, and we deliberately want to be *tolerant* of
GitHub adding new fields (the diff engine already ignores keys the desired config does
not mention). Reading the raw JSON body avoids brittle coupling to githubkit's schema
snapshot and keeps tests from having to construct fully-populated model payloads.
"""

from __future__ import annotations

import json
from typing import Any

from githubkit.response import Response


def response_json(response: Response[Any]) -> Any:
    """Return the decoded JSON body of a githubkit response (no model validation)."""
    return json.loads(response.text)
