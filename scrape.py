import requests
from bs4 import BeautifulSoup

def get_rates():
    # Use a more complete header set to bypass potential bot detection
    url = "https://kadoobdc.co.tz/market-rates"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://kadoobdc.co.tz/"
    }
    
    extracted_rates = []
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        # Check if the request was successful
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find all tables in the page to debug
            tables = soup.find_all('table')
            
            # Look for the table that actually contains branch data
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        branch_name = cols[0].text.strip().upper()
                        # Verify if it looks like a branch name or contains rate data
                        try:
                            buy_rate = float(cols[1].text.strip())
                            extracted_rates.append({'branch': branch_name, 'buy': buy_rate})
                        except (ValueError, IndexError):
                            continue
    except Exception as e:
        print(f"Error fetching data: {e}")

    # Always generate the file so the workflow doesn't crash
    with open("index.html", "w") as f:
        f.write("<html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head><body>")
        if extracted_rates:
            # Sort by rate and pick the best
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
    get_rates()
