import asyncio
import json
from datetime import datetime
from playwright.async_api import async_playwright

URL = "https://kadoobdc.co.tz/market-rates"

# Dar es Salaam branches, matched by KEYWORD rather than exact dropdown text.
# Exact-string matching turned out to be fragile — e.g. the real dropdown
# entry for "Msimbazi" includes a hotel suffix ("MSIMBAZI CATE HOTEL BRANCH")
# that wasn't in the original exact-match list, so that branch silently
# dropped out of the results with no error. Keyword matching is robust to
# that kind of wording variation. Each entry is:
#   (display_label, [required substrings, ALL must be present],
#                    [excluded substrings, NONE may be present])
DAR_ES_SALAAM_BRANCH_MATCHERS = [
    ("HEAD OFFICE",              ["HEAD OFFICE"], []),
    ("MASAKI BRANCH",            ["MASAKI"], []),
    ("KUNDUCHI BRANCH",          ["KUNDUCHI"], []),
    ("IPS BUILDING BRANCH",      ["IPS"], []),
    ("MLIMANI CITY 2ND BRANCH",  ["MLIMANI"], []),
    ("SAMORA 2ND BRANCH",        ["SAMORA", "2ND"], []),
    ("SAMORA BRANCH",            ["SAMORA"], ["2ND"]),
    ("SINZA BRANCH",             ["SINZA"], []),
    ("JAMHURI BRANCH",           ["JAMHURI"], []),
    ("NAMANGA BRANCH",           ["NAMANGA"], []),
    ("UHURU BRANCH",             ["UHURU"], []),
    ("MOROCCO BRANCH",           ["MOROCCO"], []),
    ("SIKUKUU BRANCH",           ["SIKUKUU"], []),
    ("MSIMBAZI CATE HOTEL BRANCH", ["MSIMBAZI"], []),
    ("MKUNGUNI BRANCH",          ["MKUNGUNI"], []),
    ("DAR VILLAGE BRANCH",       ["DAR VILLAGE"], []),
]


def match_dar_branch(option_text: str):
    """Return the canonical display label for a dropdown option if it's a
    Dar es Salaam branch, else None. Checked in the order defined above so
    more specific rules (e.g. SAMORA 2ND) are tried before general ones."""
    text_upper = option_text.upper()
    for label, required, excluded in DAR_ES_SALAAM_BRANCH_MATCHERS:
        if all(r in text_upper for r in required) and not any(e in text_upper for e in excluded):
            return label
    return None


# Preserves the intended display order regardless of dict insertion order.
DAR_ES_SALAAM_BRANCHES = [label for label, _, _ in DAR_ES_SALAAM_BRANCH_MATCHERS]


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
        # rates), fall back to a longer fixed wait so slow AJAX still lands
        # before we read the table.
        await page.wait_for_timeout(2500)

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
    results = {}  # branch_name -> {"currencies": [...], "usd_buy": float|None, "raw_options": [...]}
    debug_options = []
    duplicate_warnings = []

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
            if not opt["value"]:
                continue  # skip placeholder option

            branch_label = match_dar_branch(opt["text"])
            if branch_label is None:
                continue  # not a Dar es Salaam branch

            print(f"Selecting branch: {opt['text']} (value={opt['value']}) -> matched as {branch_label}")

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

            if branch_label in results:
                # Two different dropdown options both matched the same keyword.
                # Rather than silently overwrite the earlier one (which is how
                # the Msimbazi rate mismatch happened), keep BOTH under
                # distinct keys so nothing is lost and the duplicate is visible
                # on the page itself.
                dup_key = f"{branch_label} [dropdown text: {opt['text']}]"
                msg = (
                    f"DUPLICATE MATCH: '{opt['text']}' also matched '{branch_label}', "
                    f"which was already filled by a different dropdown option. "
                    f"Keeping both — see '{dup_key}' on the page."
                )
                print(f"  WARNING: {msg}")
                duplicate_warnings.append(msg)
                results[dup_key] = {"currencies": currencies, "usd_buy": usd_buy}
            else:
                results[branch_label] = {"currencies": currencies, "usd_buy": usd_buy}

        await browser.close()

    return results, debug_options, duplicate_warnings


def build_html(results: dict, duplicate_warnings=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    duplicate_warnings = duplicate_warnings or []

    # Determine highest USD buying rate among branches that actually returned a rate.
    # Several branches can share the same top rate, so find the max value first,
    # then list every branch that matches it — not just one.
    valid = {b: d for b, d in results.items() if d["usd_buy"] is not None}
    best_rate = max((d["usd_buy"] for d in valid.values()), default=None)
    best_branches = []
    if best_rate is not None:
        # Preserve the display order used elsewhere in the page where possible;
        # any duplicate-suffixed keys just sort alphabetically at the end.
        ordered = [b for b in DAR_ES_SALAAM_BRANCHES if b in valid]
        ordered += [b for b in valid if b not in DAR_ES_SALAAM_BRANCHES]
        best_branches = [b for b in ordered if valid[b]["usd_buy"] == best_rate]

    html = [
        "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<style>",
        "body{font-family:Arial,sans-serif;margin:16px;background:#f7f7f7;}",
        "h1{font-size:20px;} h2{font-size:16px;margin-top:28px;}",
        ".best{background:#fff3cd;padding:12px;border-radius:8px;border:1px solid #e0c46c;}",
        ".disclaimer{background:#f4f4f4;padding:12px;border-radius:8px;border:1px solid #ccc;"
        "font-size:13px;color:#444;margin-top:14px;}",
        ".warning{background:#f8d7da;padding:12px;border-radius:8px;border:1px solid #f1aeb5;"
        "font-size:13px;color:#58151c;margin-top:14px;}",
        "table{border-collapse:collapse;width:100%;margin-bottom:8px;background:#fff;}",
        "td,th{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:14px;}",
        "th{background:#c0392b;color:#fff;}",
        ".updated{color:#666;font-size:12px;margin-bottom:16px;}",
        "</style></head><body>",
        "<h1>Kadoo Bureau De Change — Dar es Salaam Branch Rates</h1>",
        f"<div class='updated'>Last updated: {now}</div>",
    ]

    if best_branches:
        if len(best_branches) == 1:
            branch_text = f"<strong>{best_branches[0]}</strong>"
        else:
            branch_text = ", ".join(f"<strong>{b}</strong>" for b in best_branches)
        html.append(
            f"<div class='best'><strong>Highest USD Buying Rate:</strong> "
            f"{best_rate:.2f} TZS — available at: {branch_text}</div>"
        )
    else:
        html.append(
            "<div class='best'>Could not determine USD buying rate for any "
            "Dar es Salaam branch — see debug log in the Action run.</div>"
        )

    html.append(
        "<div class='disclaimer'><strong>Please note:</strong> the extraction of these "
        "rates has been manually verified personally, but do not make any financial "
        "decisions based on the information on this page. This is intended only as "
        "guidance on the highest USD buying rate and which branch offers it. For any "
        "financial decision, kindly call the bureau to confirm the current rates.</div>"
    )

    if duplicate_warnings:
        html.append("<div class='warning'><strong>Data check needed:</strong> more than one dropdown "
                     "entry on Kadoo's site matched the same branch name below — both are shown "
                     "separately so no data was silently dropped. Please verify manually which is "
                     "current:<ul>")
        for w in duplicate_warnings:
            html.append(f"<li>{w}</li>")
        html.append("</ul></div>")

    if not results:
        html.append(
            "<h2>Rates are currently unavailable.</h2>"
            "<p>The scraper could not read any branch data. Check the Action "
            "logs for the list of dropdown options that were actually found — "
            "the branch names may not match DAR_ES_SALAAM_BRANCHES exactly.</p>"
        )
    else:
        ordered = [b for b in DAR_ES_SALAAM_BRANCHES if b in results]
        ordered += [b for b in results if b not in DAR_ES_SALAAM_BRANCHES]
        for branch in ordered:
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
    results, debug_options, duplicate_warnings = await get_rates()

    # Save raw debug data so a failed run is easy to diagnose from Action logs/artifacts
    with open("debug_options.json", "w") as f:
        json.dump(debug_options, f, indent=2)

    html = build_html(results, duplicate_warnings)
    with open("index.html", "w") as f:
        f.write(html)

    print(f"Done. Captured {len(results)} Dar es Salaam branches.")
    if duplicate_warnings:
        print(f"NOTE: {len(duplicate_warnings)} duplicate branch match(es) — see index.html for details.")


if __name__ == "__main__":
    asyncio.run(main())
