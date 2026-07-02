import requests
from bs4 import BeautifulSoup

def get_rates():
    url = "https://kadoobdc.co.tz/market-rates"
    # Using a header to mimic a real browser request
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Kadoo's structure as seen in Capture.JPG
    # We look for the table containing rate data
    rates_table = soup.find('table') 
    
    # Define Dar branches to filter
    dar_branches = [
        'MLIMANI CITY', 'SINZA BRANCH', 'UBUNGO BRANCH', 'SAMORA BRANCH', 
        'IPS BUILDING BRANCH', 'JAMHURI BRANCH', 'UHURU BRANCH', 
        'SIKUKUU BRANCH', 'MSIMBAZI CATE HOTEL BRANCH', 'MKUNGUNI BRANCH', 
        'NAMANGA BRANCH', 'MASAKI BRANCH', 'DAR VILLAGE BRANCH', 
        'MOROCCO BRANCH', 'KUNDUCHI BRANCH'
    ]
    
    extracted_rates = []
    
    # Logic to iterate rows and extract branch name and buying rate
    # Adjusting table logic based on standard site structure
    rows = rates_table.find_all('tr') if rates_table else []
    for row in rows:
        cols = row.find_all('td')
        if len(cols) >= 2:
            branch = cols[0].text.strip().upper()
            if branch in dar_branches:
                try:
                    buy_rate = float(cols[1].text.strip())
                    extracted_rates.append({'branch': branch, 'buy': buy_rate})
                except ValueError:
                    continue
    
    if extracted_rates:
        best = max(extracted_rates, key=lambda x: x['buy'])
        
        # Write to index.html
        with open("index.html", "w") as f:
            f.write(f"<h1>Highest USD Buy Rate: {best['buy']} at {best['branch']}</h1>")
            f.write("<ul>")
            for r in extracted_rates:
                f.write(f"<li>{r['branch']}: {r['buy']}</li>")
            f.write("</ul>")

if __name__ == "__main__":
    get_rates()
