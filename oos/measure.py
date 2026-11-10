"""Measurement pass: switch every zone, verify the switch, read units/prices for carried SKUs.

Talks to Rohlik's own MCP server (the same tools the Claude connector uses), authenticated with
the documented legacy `rhl-email` / `rhl-pass` headers. Listing status is never derived here:
the footprint in the config decides which SKUs are even requested per zone.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = logging.getLogger("oos.measure")
CTX = "Automated daily stock-availability measurement for a retail media report."


class ToolError(RuntimeError):
    pass


class Rohlik:
    def __init__(self, session: ClientSession):
        self.s = session

    async def call(self, name: str, args: dict, retries: int = 3) -> dict:
        args = {**args, "context": CTX}
        last = None
        for attempt in range(1, retries + 1):
            try:
                res = await self.s.call_tool(name, args)
                text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                if res.isError:
                    raise ToolError(f"{name} returned an error: {text[:300]}")
                if getattr(res, "structuredContent", None) and not text:
                    return res.structuredContent
                return json.loads(text)
            except Exception as e:  # network blips, transient 5xx, malformed JSON
                last = e
                log.warning("%s attempt %d/%d failed: %s", name, attempt, retries, e)
                await asyncio.sleep(2 * attempt)
        raise ToolError(f"{name} failed after {retries} attempts: {last}")

    async def select(self, address_id: int) -> dict:
        return await self.call("select_delivery_address_checkout", {"address_id": address_id})

    async def selected_address_id(self) -> int | None:
        """Address the checkout actually has selected. Only addressId is kept; the rest of the
        payload (contact, card data) is discarded and never logged or stored."""
        data = await self.call("get_checkout", {})
        try:
            return int(data["sections"]["addressSection"]["addressId"])
        except (KeyError, TypeError, ValueError):
            return None

    async def details(self, ids: list[int]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            data = await self.call("get_product_details",
                                   {"product_ids": chunk, "include_description": False})
            for k, v in data.items():
                out[int(k)] = v
        missing = set(ids) - set(out)
        if missing:
            raise ToolError(f"get_product_details returned no data for {sorted(missing)}")
        return out


@asynccontextmanager
async def connect(url: str, email: str | None, password: str | None):
    headers = {}
    if email and password:
        headers = {"rhl-email": email, "rhl-pass": password}
    async with streamablehttp_client(url, headers=headers, timeout=60) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            need = {"select_delivery_address_checkout", "get_checkout", "get_product_details"}
            if not need <= tools:
                raise ToolError(f"Rohlik MCP is missing tools: {sorted(need - tools)} "
                                f"(check credentials / login confirmation e-mail)")
            yield Rohlik(session)


def parse(p: dict) -> dict:
    prices, stock = p.get("prices") or {}, p.get("stock") or {}
    return {
        "units": int(stock.get("maxAvailableAmount") or 0),
        "price": prices.get("originalPrice"),
        "sale_price": prices.get("salePrice"),
        "sale_id": prices.get("saleId"),
        "reason": stock.get("availabilityReason") or stock.get("unavailabilityReason"),
        "restock_date": stock.get("expectedReplenishmentDate"),
    }


async def measure(cfg: dict, url: str, email: str | None, password: str | None) -> dict:
    zones = cfg["zones"]
    canary = int(cfg["canary_sku"])
    carried = {z["key"]: [] for z in zones}
    for b in cfg["brands"]:
        for s in b["skus"]:
            excluded = set(s.get("not_listed_in", [])) | set(s.get("unverified_in", []))
            for z in zones:
                if z["key"] not in excluded:
                    carried[z["key"]].append(int(s["id"]))

    result = {"zones": {}, "restored": False}
    async with connect(url, email, password) as r:
        try:
            prev = parse((await r.details([canary]))[canary])
            prev_sig = (prev["units"], prev["sale_id"])
            for z in zones:
                key, aid = z["key"], int(z["address_id"])
                zr = {"status": "FAILED", "verification": None, "canary": None, "products": {}}
                for attempt in (1, 2):
                    await r.select(aid)
                    confirmed = await r.selected_address_id()
                    ids = sorted(set(carried[key]) | {canary})
                    data = await r.details(ids)
                    can = parse(data[canary])
                    sig = (can["units"], can["sale_id"])
                    canary_changed = sig != prev_sig
                    if confirmed is not None:
                        ok = confirmed == aid
                        method = "checkout_address"
                    else:  # checkout unreadable: fall back to the skill's canary rule
                        ok = canary_changed
                        method = "canary_only"
                    if ok:
                        zr.update(status="OK", canary={"units": sig[0], "sale_id": sig[1],
                                                       "changed_vs_previous": canary_changed},
                                  verification={"method": method, "attempt": attempt},
                                  products={pid: parse(data[pid]) for pid in carried[key]})
                        prev_sig = sig
                        break
                    log.warning("zone %s not confirmed on attempt %d (checkout=%s)", key, attempt, confirmed)
                result["zones"][key] = zr
                log.info("zone %s: %s", key, zr["status"])
        finally:
            restore = int(cfg["restore_address_id"])
            try:
                await r.select(restore)
                result["restored"] = (await r.selected_address_id()) == restore
            except Exception as e:  # never leave the run without trying
                log.error("restore failed: %s", e)
    return result


def run_measure(cfg: dict) -> dict:
    url = os.environ.get("ROHLIK_MCP_URL", "https://mcp.rohlik.cz/mcp")
    return asyncio.run(measure(cfg, url, os.environ.get("RHL_EMAIL"), os.environ.get("RHL_PASS")))
