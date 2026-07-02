import asyncio
import json
from datetime import datetime
from playwright.async_api import async_playwright

URL = "https://kadoobdc.co.tz/market-rates"

# Exact branch names as they appear in the LIVE dropdown on /market-rates
# (verified by hand by the site owner — this is the source of truth, not the
# separate /branch content page, which uses different text for the same branches).
DAR_ES_SALAAM_BRANCHES = [
    "HEAD OFFICE",
    "MASAKI BRANCH",
    "KUNDUCHI BRANCH",
    "IPS BUILDING BRANCH",
    "MLIMANI CITY 2ND BRANCH",
    "SAMORA BRANCH",
    "SAMORA 2ND BRANCH",
    "SINZA BRANCH",
    "JAMHURI BRANCH",
    "NAMANGA BRANCH",
    "UHURU BRANCH",
    "MOROCCO BRANCH",
    "SIKUKUU BRANCH",
    "MSIMBAZI BRANCH",
    "MKUNGUNI BRANCH",
]


def normalize(name: str) -> str:
    """Loose match: uppercase, collapse whitespace, drop the word BRANCH."""
    return " ".join(name.upper().replace("BRANCH", "").split())


DAR_NORMALIZED = {normalize(b): b for b in DAR_ES_SALAAM_BRANCHES}


async def get_branch_options(page):
    """Return [{value, text}] for every <option> in the branch <select>."""
    select = page.locator("select").first
    await select.wait_for(state="attached", timeout=15000)
    options = await select.evaluate(
        """(el) => Array.from(el.options).map(o => ({value: o.value, text: o.textContent.trim()}))"""
    )
    return options


async def get_table_signature(page):
    """A cheap fingerprint of the table's current content, used to detect
    when the AJAX update for a newly-selected branch has actually landed."""
    return await page.evaluate(
        """() => {
            const table = document.querySelector('table');
            return table ? table.innerText : '';
        }"""
    )


async def scrape_branch_table(page, previous_signature: str):
    """
    After a branch is selected, read the rates table.
    Expected columns on this site: Currency | Code | Buying | Selling
    Returns a list of dicts, and also the USD buying rate if found.

    Critically, this waits for the table's content to actually CHANGE from
    whatever it showed for the previously-selected branch, rather than just
    checking "is there more than one row" — the old table already has rows
    from the prior branch, so that check alone passes instantly and scrapes
    stale data before the AJAX response for the new branch arrives.
    """
    try:
        await page.wait_for_function(
            """(prevSig) => {
                const table = document.querySelector('table');
                if (!table) return false;
                const rows = table.querySelectorAll('tbody tr, tr');
                if (rows.length <= 1) return false; // only header, still loading
                return table.innerText !== prevSig;
            }""",
            arg=previous_signature,
            timeout=10000,
        )
        # Small settle delay in case the DOM updates in more than one paint.
        await page.wait_for_timeout(300)
    except Exception:
        # If content never changed (e.g. two branches genuinely have identical
        # rates), fall back to a fixed wait so we still capture *something*.
        await page.wait_for_timeout(1500)

    rows_data = await page.evaluate(
        """() => {
            const table = document.querySelector('table');
            if (!table) return [];
            const rows = Array.from(table.querySelectorAll('tr'));
            return rows.map(r =>
                Array.from(r.querySelectorAll('td,th')).map(c => {
                    const img = c.querySelector('img');
                    return {
                        text: c.textContent.trim(),
                        alt: img ? (img.getAttribute('alt') || '').trim() : ''
                    };
                })
            );
        }"""
    )

    currencies = []
    usd_candidates = []  # every USD-family row (US DOLLARS, $100 notes, $1, $5/$10/$20 notes, ...)
    for row in rows_data:
        if len(row) < 3:
            continue

        # Column layout on this site: [0] Currency = flag image, empty text
        # (alt attribute may hold the name); [1] "Code" = actually the
        # readable currency name text, not an ISO code; [2] Buying; [3] Selling.
        cell0 = row[0] if len(row) > 0 else {"text": "", "alt": ""}
        cell1 = row[1] if len(row) > 1 else {"text": "", "alt": ""}
        buying_raw = row[2]["text"] if len(row) > 2 else ""
        selling_raw = row[3]["text"] if len(row) > 3 else ""

        buying = parse_number(buying_raw)
        selling = parse_number(selling_raw)
        if buying is None and selling is None:
            continue  # header row or junk

        # Prefer the Code column's text (the real name), fall back to the
        # flag's alt text, then to the Currency cell's own text as a last resort.
        name = cell1["text"] or cell0["alt"] or cell0["text"]

        currencies.append(
            {
                "currency": name,
                "buying": buying,
                "selling": selling,
            }
        )

        name_upper = name.strip().upper()
        if name_upper == "US DOLLARS" or name_upper.startswith("USD"):
            usd_candidates.append({"currency": name_upper, "buying": buying})

    # Several USD rows exist (main board rate + denomination-specific notes
    # like "USD ($100 - 2006)", "USD ($1)", "USD ($5,$10,$20)"). The
    # comparable board rate is the plain "US DOLLARS" row.
    usd_buy = None
    exact = [c for c in usd_candidates if c["currency"] == "US DOLLARS"]
    if exact:
        usd_buy = exact[0]["buying"]
    elif usd_candidates:
        usd_buy = usd_candidates[0]["buying"]

    return currencies, usd_buy


def parse_number(raw: str):
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


async def get_rates():
    results = {}  # branch_name -> {"currencies": [...], "usd_buy": float|None}
    debug_options = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(URL, wait_until="networkidle")

        options = await get_branch_options(page)
        debug_options = options
        print("--- Dropdown options found on page ---")
        for o in options:
            print(f"  value={o['value']!r}  text={o['text']!r}")
        print("---------------------------------------")

        prev_signature = await get_table_signature(page)

        for opt in options:
            text_norm = normalize(opt["text"])
            if text_norm not in DAR_NORMALIZED or not opt["value"]:
                continue  # skip placeholder + non-Dar-es-Salaam branches

            branch_label = DAR_NORMALIZED[text_norm]
            print(f"Selecting branch: {opt['text']} (value={opt['value']})")

            try:
                await page.locator("select").first.select_option(value=opt["value"])
                # Trigger any JS listeners bound to 'change' explicitly, in case
                # select_option's native event isn't enough for this site's JS.
                await page.locator("select").first.dispatch_event("change")
            except Exception as e:
                print(f"  Could not select {opt['text']}: {e}")
                continue

            currencies, usd_buy = await scrape_branch_table(page, prev_signature)
            print(f"  -> {len(currencies)} currency rows, USD buy = {usd_buy}")

            # This branch's table content becomes the baseline the next
            # branch's update must differ from.
            prev_signature = await get_table_signature(page)

            results[branch_label] = {
                "currencies": currencies,
                "usd_buy": usd_buy,
            }

        await browser.close()

    return results, debug_options


def build_html(results: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Determine highest USD buying rate among branches that actually returned a rate
    valid = {b: d for b, d in results.items() if d["usd_buy"] is not None}
    best_branch, best_rate = (None, None)
    if valid:
        best_branch = max(valid, key=lambda b: valid[b]["usd_buy"])
        best_rate = valid[best_branch]["usd_buy"]

    html = [
        "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<style>",
        "body{font-family:Arial,sans-serif;margin:16px;background:#f7f7f7;}",
        "h1{font-size:20px;} h2{font-size:16px;margin-top:28px;}",
        ".best{background:#fff3cd;padding:12px;border-radius:8px;border:1px solid #e0c46c;}",
        "table{border-collapse:collapse;width:100%;margin-bottom:8px;background:#fff;}",
        "td,th{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:14px;}",
        "th{background:#c0392b;color:#fff;}",
        ".updated{color:#666;font-size:12px;margin-bottom:16px;}",
        "</style></head><body>",
        "<h1>Kadoo Bureau De Change — Dar es Salaam Branch Rates</h1>",
        f"<div class='updated'>Last updated: {now}</div>",
    ]

    if best_branch:
        html.append(
            f"<div class='best'><strong>Highest USD Buying Rate:</strong> "
            f"{valid[best_branch]['usd_buy']:.2f} TZS at <strong>{best_branch}</strong></div>"
        )
    else:
        html.append(
            "<div class='best'>Could not determine USD buying rate for any "
            "Dar es Salaam branch — see debug log in the Action run.</div>"
        )

    if not results:
        html.append(
            "<h2>Rates are currently unavailable.</h2>"
            "<p>The scraper could not read any branch data. Check the Action "
            "logs for the list of dropdown options that were actually found — "
            "the branch names may not match DAR_ES_SALAAM_BRANCHES exactly.</p>"
        )
    else:
        for branch in DAR_ES_SALAAM_BRANCHES:
            if branch not in results:
                continue
            data = results[branch]
            html.append(f"<h2>{branch}</h2>")
            if not data["currencies"]:
                html.append("<p>No rates returned for this branch.</p>")
                continue
            html.append("<table><tr><th>Currency</th><th>Buying</th><th>Selling</th></tr>")
            for c in data["currencies"]:
                b = f"{c['buying']:.2f}" if c["buying"] is not None else "-"
                s = f"{c['selling']:.2f}" if c["selling"] is not None else "-"
                html.append(f"<tr><td>{c['currency']}</td><td>{b}</td><td>{s}</td></tr>")
            html.append("</table>")

    html.append("</body></html>")
    return "\n".join(html)


async def main():
    results, debug_options = await get_rates()

    # Save raw debug data so a failed run is easy to diagnose from Action logs/artifacts
    with open("debug_options.json", "w") as f:
        json.dump(debug_options, f, indent=2)

    html = build_html(results)
    with open("index.html", "w") as f:
        f.write(html)

    print(f"Done. Captured {len(results)} Dar es Salaam branches.")


if __name__ == "__main__":
    asyncio.run(main())
