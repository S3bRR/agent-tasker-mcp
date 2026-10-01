"""Official SDK peer; only the optional interoperability tests depend on mcp."""

import asyncio
from mcp.server.fastmcp import FastMCP

server = FastMCP("official-sdk-peer", log_level="ERROR")
counts, events = {}, {}


@server.tool()
async def echo(value: int, parties: int = 1, delay: float = 0) -> dict[str, int]:
    """Echo a value, optionally rendezvousing with simultaneous calls."""
    if parties > 1:
        event = events.setdefault(parties, asyncio.Event())
        counts[parties] = counts.get(parties, 0) + 1
        if counts[parties] >= parties:
            event.set()
        await asyncio.wait_for(event.wait(), timeout=5)
    await asyncio.sleep(delay)
    return {"value": value}


if __name__ == "__main__":
    server.run(transport="stdio")
