"""Run gflow with Flow's client-side availability gate neutralised.

Flow decides "unavailable" in the browser: RPC KV2T2d (/AiSandbox.CheckToolAvailability)
returns a status code and the route guard redirects on 4/5/6/8. This wrapper adds a
Playwright route to every browser context gflow opens and rewrites that status to an
empty message of the same byte length, so batchexecute framing stays valid.

Usage (same arguments as gflow):
    python -m tools.flow.gflow_bridge image t2i ...

Unofficial and against Flow's intended regional rollout: the server may still refuse
generation, and Google can change the RPC at any time. Opt-in via FLOW_BRIDGE=1.
"""

from __future__ import annotations

import re
import sys
from typing import Any

GATE_URL = re.compile(r".*/batchexecute\?rpcids=KV2T2d.*")
GATE_RESULT = re.compile(r'(\["wrb\.fr","KV2T2d",)"\[\d\]"')


async def _patch_gate(route: Any) -> None:
    resp = await route.fetch()
    body = GATE_RESULT.sub(r'\1"[ ]"', await resp.text())
    await route.fulfill(response=resp, body=body)


def install() -> None:
    from playwright.async_api import BrowserType

    original = BrowserType.launch_persistent_context

    async def launch_persistent_context(self, *args, **kwargs):
        ctx = await original(self, *args, **kwargs)
        await ctx.route(GATE_URL, _patch_gate)
        return ctx

    BrowserType.launch_persistent_context = launch_persistent_context


def main() -> int:
    install()
    from gflow_cli.cli import main as gflow_main

    sys.argv[0] = "gflow"
    return gflow_main()


if __name__ == "__main__":
    sys.exit(main())
