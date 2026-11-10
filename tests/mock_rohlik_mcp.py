"""Local stand-in for mcp.rohlik.cz: same tool names, same JSON shape, data recorded live
on 2026-11-10 via the Claude Rohlik connector. Used by CI tests; never talks to Rohlik.

MOCK_BREAK_ZONE=<address_id> makes get_checkout report the wrong address for that zone."""
import json, os
from pathlib import Path
from mcp.server.fastmcp import FastMCP

FX = Path(__file__).parent / "fixtures"
ZONES = {6011285: "chrastany", 5495707: "praha_liboc", 5698003: "horni_pocernice",
         5530325: "brno", 10947259: "ostrava"}
DATA = {k: json.loads((FX / f"{v}.json").read_text(encoding="utf-8")) for k, v in ZONES.items()}
state = {"address": 11759356}
BREAK = int(os.environ.get("MOCK_BREAK_ZONE", "0"))
mcp = FastMCP("rohlik-mock", host="127.0.0.1", port=int(os.environ.get("MOCK_PORT", "8765")))


@mcp.tool()
def select_delivery_address_checkout(address_id: int, context: str = "") -> dict:
    state["address"] = address_id
    return {"success": True, "address_id": address_id, "full_address": "", "is_delivered_to": None}


@mcp.tool()
def get_checkout(context: str = "") -> dict:
    a = state["address"]
    shown = 99999 if a == BREAK else a
    return {"sections": {"addressSection": {"is_valid": True, "addressId": shown,
                                            "delivery_address": "redacted"},
                         "contactSection": {"email": "redacted"}}, "checkout_ready": False}


@mcp.tool()
def get_product_details(product_ids: list[int], include_description: bool = True, context: str = "") -> dict:
    zone = DATA.get(state["address"], {})
    out = {}
    for pid in product_ids:
        rec = zone.get(str(pid), [0, 99.9, "Vyprodáno.", None])   # not carried -> looks sold out
        units, price = rec[0], rec[1]
        reason = rec[2] if len(rec) > 2 else None
        date = rec[3] if len(rec) > 3 else None
        stock = {"maxAvailableAmount": units,
                 "availabilityStatus": "AVAILABLE" if units else "UNAVAILABLE",
                 "availabilityReason": reason, "maxBasketAmountReason": "AVAILABLE"}
        if date:
            stock["expectedReplenishmentDate"] = date
        out[str(pid)] = {"productId": pid, "name": f"product {pid}", "brand": None,
                         "prices": {"originalPrice": price, "salePrice": None, "saleId": None, "currency": "CZK"},
                         "stock": stock}
    return out


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
