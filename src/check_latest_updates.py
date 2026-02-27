# check for updates on selected city using csv input

from .scrapers import municode_scraper

import csv

INPUT_CSV = "data/datasets/2025 ARB Policy Map Ordinance Table - Copy of Master (Auto-Updates).csv"
OUTPUT_FILE = "logs/latest_updates.txt"
VERBOSE = False # false means it only outputs the names of the city-policy pairs that require updates, true means it logs every city policy pair it checks in addition to a requires update tag next to the ones that require updates.
RUN_ALL = False # false means it inputs the user for a specific city to check while true starts checking every city in the database

def clear():
    open(OUTPUT_FILE, "w", encoding="utf-8").close()

def log(text: str) -> None:
    print(text)
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(text)


def check(agent: municode_scraper.MuniCodeScraper, city, county, policy_type, exists, number, header1, header2, header3, description, source, notes, date_checked):
    link = source.split(' ')[0]
    if "library.municode.com" not in link:
        return False
    agent.go(link)
    if VERBOSE:
        log(f"Checking: {city}: {policy_type} - {number}\n")
    result = agent.scrape_changes(max_dates=1)
    if len(result):
        return result
    return False


def check_muni(csv_inp: str, agent: municode_scraper.MuniCodeScraper, muni: str):
    with open(csv_inp, mode='r', encoding="utf8") as file:
        csv_read = csv.reader(file)
        for line in csv_read:
            if line[0].upper() == muni.upper() and line[3] == 'Y':
                checking = check(agent, *line)
                if checking:
                    log(f"{"Newest update: " if VERBOSE else ""}{line[0]}: {line[2]} - {line[4]} ({checking[0]})\n")

def check_all(csv_inp: str, agent: municode_scraper.MuniCodeScraper):
    with open(csv_inp, mode='r', encoding="utf8") as file:
        csv_read = csv.reader(file)
        for line in csv_read:
            if line[3] == 'Y':
                checking = check(agent, *line)
                if checking:
                    log(f"{"Newest update: " if VERBOSE else ""}{line[0]}: {line[2]} - {line[4]} ({checking[0]})\n")

def run_all():
    agent = municode_scraper.MuniCodeScraper()

    check_all(INPUT_CSV, agent)

def run_muni():
    agent = municode_scraper.MuniCodeScraper()

    municipality = input("CITY TO CHECK: ")
    check_muni(INPUT_CSV, agent, municipality)

if __name__ == "__main__":
    clear()
    if RUN_ALL:
        run_all()
    else:
        run_muni()
