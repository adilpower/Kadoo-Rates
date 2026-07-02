import requests
from bs4 import BeautifulSoup

def get_rates():
    url = "https://kadoobdc.co.tz/market-rates"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Kadoo's structure stores data in a table
        rates_table = soup.find('table') 
        
        # Full list of Dar es Salaam branches as found in the dropdown
        dar_branches = [
            'HEAD OFFICE', 'MLIMANI CITY 2ND BRANCH', 'SINZA BRANCH', 
            'UBUNGO BRANCH', 'SAMORA BRANCH', 'SAMORA 2ND BRANCH', 
            'IPS BUILDING BRANCH', 'JAMHURI BRANCH', 'UHURU BRANCH', 
            'SIKUKUU BRANCH', 'MSIMBAZI CATE HOTEL BRANCH', 'MKUNGUNI BRANCH', 
            'NAMANGA BRANCH', 'MASAKI BRANCH', 'DAR VILLAGE BRANCH', 
            'MOROCCO BRANCH', 'KUNDUCHI BRANCH'
        ]
        
        extracted_rates = []
        
        rows = rates_table.find_all('tr') if rates_table else []
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 2:
                branch = cols[0].text.strip().upper()
                if branch in dar_branches:
                    try:
                        # Assuming the table structure is [Branch Name, Buying, Selling]
                        buy_rate = float(cols[1].text.strip())
                        extracted_rates.append({'branch': branch, 'buy': buy_rate})
                    except ValueError:
                        continue
    except Exception:
        extracted_rates = []
    
    # Ensure a file is always created to prevent deployment errors
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
            f.write("<h2>No rates found, checking again soon...</h2>")
        f.write("</body></html>")

if __name__ == "__main__":
    get_rates()
