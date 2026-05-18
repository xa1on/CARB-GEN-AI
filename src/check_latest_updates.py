# check for updates on selected city using csv input

from .scrapers import municode_scraper

import csv
import os

INPUT_CSV = "data/ord tables/2025 ARB Policy Map Ordinance Table - Copy of Master (Auto-Updates).csv"
OUTPUT_FILE = "logs/latest_updates.csv"
VERBOSE = True # false means it only outputs the names of the city-policy pairs that require updates, true means it logs every city policy pair it checks in addition to a requires update tag next to the ones that require updates.
RUN_ALL = True # false means it inputs the user for a specific city to check while true starts checking every city in the database
RESUME = True # true means it will skip already checked rows from a previous run

CSV_HEADER = ["Municipality", "County", "Policy Type", "Number", "Newest Update", "Link"]

def clear():
    os.makedirs("logs", exist_ok=True)
    if RESUME and os.path.exists(OUTPUT_FILE):
        log(f"Resuming from previous run. '{OUTPUT_FILE}' will be preserved.\n")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)

def get_checked() -> set:
    if not os.path.exists(OUTPUT_FILE):
        return set()
    checked = set()
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None) # skip header
        for row in reader:
            if len(row) >= 4:
                # Key: City|County|PolicyType|Number
                checked.add(f"{row[0]}|{row[1]}|{row[2]}|{row[3]}")
    return checked

def log(text: str) -> None:
    print(text, end="")

def log_csv(row: list) -> None:
    with open(OUTPUT_FILE, "a", encoding="utf-8", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)


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
    checked = get_checked()
    with open(csv_inp, mode='r', encoding="utf8") as file:
        csv_read = csv.reader(file)
        for line in csv_read:
            if line[0].upper() == muni.upper() and line[3] == 'Y':
                key = f"{line[0]}|{line[1]}|{line[2]}|{line[4]}"
                if key in checked:
                    continue
                checking = check(agent, *line)
                if checking:
                    update_date = checking[0].to_string() if hasattr(checking[0], "to_string") else str(checking[0])
                    log(f"{"Newest update: " if VERBOSE else ""}{line[0]}: {line[2]} - {line[4]} ({update_date})\n")
                    log_csv([line[0], line[1], line[2], line[4], update_date, line[9]])
                else:
                    log_csv([line[0], line[1], line[2], line[4], "False", line[9]])

def check_all(csv_inp: str, agent: municode_scraper.MuniCodeScraper):
    checked = get_checked()
    with open(csv_inp, mode='r', encoding="utf8") as file:
        csv_read = csv.reader(file)
        for line in csv_read:
            if line[3] == 'Y':
                key = f"{line[0]}|{line[1]}|{line[2]}|{line[4]}"
                if key in checked:
                    continue
                checking = check(agent, *line)
                if checking:
                    update_date = checking[0].to_string() if hasattr(checking[0], "to_string") else str(checking[0])
                    log(f"{"Newest update: " if VERBOSE else ""}{line[0]}: {line[2]} - {line[4]} ({update_date})\n")
                    log_csv([line[0], line[1], line[2], line[4], update_date, line[9]])
                else:
                    log_csv([line[0], line[1], line[2], line[4], "False", line[9]])

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
