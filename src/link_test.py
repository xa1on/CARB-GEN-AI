"""
Checks for broken links in a CSV (404, dead link, etc.)

Authors: Chenghao Li
Org: Urban Displacement Project: UC Berkeley / University of Toronto
"""

import csv
import urllib.request
import json
from selenium import webdriver

CSV_FILE = "data/ord tables/2025 ARB Policy Map Ordinance Table - Copy of Master (Auto-Updates).csv"
LINK_COLUMN = "Source"
LOG_FILE = "logs/broken_links.csv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0",
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

CSV_HEADER = ["Row", "Municipality", "County", "Policy Type", "Link", "Reason"]

def log(text):
    print(text)

def log_csv(row: list) -> None:
    with open(LOG_FILE, "a", encoding="utf-8", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)


def get_status_code(driver, url):
    """
    Returns status code of website

    From stack overflow

    https://stackoverflow.com/a/69758112

    :param driver: 
    """
    driver.get(url)
    for entry in driver.get_log('performance'):
        for k, v in entry.items():
            if k == 'message' and 'status' in v:
                msg = json.loads(v)['message']['params']
                for mk, mv in msg.items():
                    if mk == 'response':
                        response_url = mv['url']
                        response_status = mv['status']
                        if response_url == url:
                            return response_status


def main():
    options = webdriver.ChromeOptions()
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'}) # required to get response status through selenium
    driver = webdriver.Chrome(options=options)
    
    with open(LOG_FILE, 'w', encoding="utf-8", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)

    with open(CSV_FILE, 'r', encoding="utf8") as file:
        reader = csv.reader(file)
        link_index = -1
        row_count = sum(1 for _ in reader)
        file.seek(0)
        for row_index, row in enumerate(reader):
            print(f"{row_index + 1}/{row_count}: {(row_index + 1) / row_count}")
            if not row_index:
                for item_index, item in enumerate(row):
                    if item == LINK_COLUMN:
                        link_index = item_index
                        print(link_index)
                        break
            else:
                city = row[0].strip()
                county = row[1].strip()
                policy_type = row[2].strip()
                links = row[link_index].strip()
                if links:
                    if ' ' in links or '\n' in links:
                        if "http" in links[4:]:
                            reason = "likely contains multiple links"
                            log(f"""{row_index + 1}[{city}|{county}|{policy_type}] {reason}.""")
                            log_csv([row_index + 1, city, county, policy_type, links, reason])
                        else:
                            reason = "is malformed. It contains a space/newline character"
                            log(f"""{row_index + 1}[{city}|{county}|{policy_type}] {reason}.""")
                            log_csv([row_index + 1, city, county, policy_type, links, reason])
                    elif links[:4] != "http":
                        reason = "is likely missing \"https://\" or is a malformed link"
                        log(f"""{row_index + 1}[{city}|{county}|{policy_type}] {reason}.""")
                        log_csv([row_index + 1, city, county, policy_type, links, reason])
                    else:
                        req = urllib.request.Request(links)
                        for header_type, value in HEADERS.items():
                            req.add_header(header_type, value)
                        try:
                            status_code = urllib.request.urlopen(req).getcode()
                            if status_code != 200:
                                reason = f"is invalid/down :{status_code}"
                                log(f"""{row_index + 1}[{city}|{county}|{policy_type}] {reason}.""")
                                log_csv([row_index + 1, city, county, policy_type, links, reason])
                        except:
                            try:
                                status_code = get_status_code(driver, links)
                                if not (status_code == 200 or status_code == "200"):
                                    reason = f"is invalid/down ::{status_code}"
                                    log(f"""{row_index + 1}[{city}|{county}|{policy_type}] {reason}.""")
                                    log_csv([row_index + 1, city, county, policy_type, links, reason])
                            except:
                                reason = "is invalid/down :::"
                                log(f"""{row_index + 1}[{city}|{county}|{policy_type}] {reason}.""")
                                log_csv([row_index + 1, city, county, policy_type, links, reason])
                                
                

if __name__ == "__main__":
    main()