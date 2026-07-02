import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

async def get_rates():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Go to the URL and wait for content
        await page.goto("https://kadoobdc.co.tz/market-rates", wait_until="networkidle")
        
        # Get the full page content
        content = await page.content()
        await browser.close()
        
        soup = BeautifulSoup(content, 'html.parser')
        
        # Debug: Print a snippet of the page to the logs to see what we are dealing with
        print("--- Page Content Snippet ---")
        print(soup.prettify()[:1000])
        print("----------------------------")
        
        rates_table = soup.find('table')
        extracted_rates = []
        
        if rates_table:
            rows = rates_table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    try:
                        branch = cols[0].text.strip().upper()
                        # Clean the string to handle potential non-numeric characters in rates
                        raw_rate = cols[1].text.strip().replace(',', '')
                        buy_rate = float(raw_rate)
                        extracted_rates.append({'branch': branch, 'buy': buy_rate})
                    except (ValueError, IndexError):
                        continue

        with open("index.html", "w") as f:
            f.write("<html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head><body>")
            if extracted_rates:
                best = max(extracted_rates, key=lambda x: x['buy'])
                f.write(f"<h2>Highest USD Buy Rate: {best['buy']} at {best['branch']}</h2>")
                f.write("<table border='1'><tr><th>Branch</th><th>Buy Rate</th></tr>")
                for r in extracted_rates:
                    f.write(f"<tr><td>{r['branch']}</td><td>{r['buy']}</td></tr>")
                f.write("</table>")
            else:
                f.write("<h2>Rates are currently unavailable. The scraper is unable to read the table structure.</h2>")
            f.write("</body></html>")

if __name__ == "__main__":
    asyncio.run(get_rates())
