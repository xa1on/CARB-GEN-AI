"""
MUNICODE SCRAPER

Scrapes library.municode.com for municipality codes

Notes: something really similar should be done with codelibrary.amlegal.com and generalcode.com/library

Authors: Chenghao Li
Org: University of Toronto - School of Cities
"""

import time
import json
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

SNAPSHOTS_DIR = "snapshots"
LOADING_CSS_SELECTOR = ".fa-2x"
TIMEOUT = 7.5
INDEX_CSS = "a[class=index-link]"
CODE_CSS = "a[class=toc-item-heading]"
TEXT_CSS = "ul.chunks.list-unstyled.small-padding"

DEPTH = {
    "Titles": 0,
    "Chapters": 1,
    "Articles": 2
}

class MuniCodeCrawler:
    home_url = "https://library.municode.com"
    def __init__(self, starting_url=home_url):
        """
        Create new MuniCodeCrawler Object

        :param self:
        :return: returns new object
        """
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--log-level=1")
        self.browser = webdriver.Chrome(options = chrome_options)
        self.wait = WebDriverWait(self.browser, TIMEOUT)
        self.browser.set_window_size(1024, 1024)
        self.go(starting_url)

    def get_url(self):
        """
        Get current url

        :param self:
        :return: current url value
        """
        return self.url
    
    def set_url(self, url):
        """
        Set current url to url

        :param self:
        :param url: url to set object to
        :return:
        """
        self.url = url
    
    def take_snapshot(self):
        """
        Takes a .html snapshot file of current page

        :param self:
        :return:
        """
        with open(f"{SNAPSHOTS_DIR}\\{str(time.asctime(time.localtime(time.time()))).replace(":", "-")}-snap.html", "w", encoding="utf-8") as f:
            f.write(str(self.soup))#self.browser.page_source)
    
    def go(self, url=None):
        """
        Goes to specified url and waits for loading to finish 

        :param self:
        :return:
        """
        if url: # if url is specified, go to url specified
            self.set_url(url)
        self.browser.get(self.url)
        self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, LOADING_CSS_SELECTOR)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
    
    def wait_muni(self):
        self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, INDEX_CSS)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")

    def wait_codes(self):
        self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, CODE_CSS)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")

    def wait_text(self):
        self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, TEXT_CSS)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")

    def contains_child(self):
        """
        Checks if current page contains any children pages. (ex: a chapter contains sub-chapers/articles/sections)

        :param self:
        :return: True/False depending on whether or not child page exists
        """
        return len(self.soup.select("ul.codes-toc-list.list-unstyled")) > 0
    
    def scrape_index_link(self, requires_code=False):
        """
        Scrapes any items with index-link class from page, with name tied to the link.
        Selecting states and selecting municipalities uses index-link

        :param self:
        :param requires_code: whether or not what needs to be scraped requires it link to the code of ordinances
        :return: dictionary in the format {[item_name]: [link to item]}
        """
        self.wait_muni()
        items = self.soup.select(INDEX_CSS)
        result = {item.text.lower(): item["href"] + ("/codes/code_of_ordinances" if requires_code and "/codes/code_of_ordinances" not in item["href"] else "") for item in items}
        return result
    
    def scrape_codes(self, depth=0):
        """
        Scrapes codes from municipality page with depth

        :param self:
        :param title: whether or not we are looking for the titles
        :param depth: depth of item. (title: 0, chapter: 1, article/section: 2)
        :return: dictionary in the format {[code_name]: [link to code]}
        """
        if depth: # because municode is stupid, it works like this: (title: 0, chapter: 2, article/section: 3), therefore, we need to index up
            depth += 1
        self.wait_codes()
        result = {}
        codes = self.soup.find_all("li", {"depth": depth})
        for code in codes:
            code = code.select_one("a[class=toc-item-heading]")
            code_text = code.find("span", {"data-ng-bind": "::node.Heading"}).text.replace('\n', '')
            result[code_text] = code["href"]
        return result
    
    """
    scrape states and cities are redundant but good for readability
    """

    def scrape_states(self):
        return self.scrape_index_link()
    
    def scrape_munis(self):
        return self.scrape_index_link(True)

    def scrape_titles(self):
        return self.scrape_codes(DEPTH["Titles"])

    def scrape_chapters(self):
        return self.scrape_codes(DEPTH["Chapters"])

    def scrape_articles(self):
        return self.scrape_codes(DEPTH["Articles"])

    def scrape_text(self):
        """
        Scrapes text from code on page

        Notes: Incredibly messy. need to redo (CL)

        :param self:
        :return: string of the output in markdown format
        """
        self.wait_text()
        def text_selector(tag):
            # only select text with h2, h3, p, table tags
            return tag.name == "h2" or tag.name == "h3" or tag.name == "p" or tag.name == "table"
        
        def get_row_num(table):
            # get the total number of rows in a table
            body = table.select_one("tbody")
            row = body.select_one("tr")
            items = row.select("td")
            total = 0
            for item in items:
                if item.has_attr("colspan"):
                    total += int(item.get("colspan"))
                else:
                    total += 1
            return total

        def rows_text(parent, text_type):
            # converts table rows into markdown text
            rows = parent.select("tr")
            result = ""
            for row in rows:
                items = row.select(text_type)
                for item in items:
                    text = ""
                    splits = item.text.split('\n')
                    for line in splits:
                        stripped = line.strip()
                        if stripped:
                            text += ", " + stripped
                    text = text[2:]
                    result += f"|{text}{("|" * (int(item.get("colspan")[0]) - 1)) if item.has_attr("colspan") else ""}"
                result += "|\n"
            return result
        
        result = ""
        text_block = self.soup.select_one("ul.chunks.list-unstyled.small-padding") # contains the text
        previous_line_incr = 0
        text = text_block.find_all(text_selector)
        previous_line_type = None
        for line in text:
            name = line.name
            if name == "h2" or name == "h3":
                result += "#" * int(name[1]) + ' ' + line.select_one("div[class=chunk-title]").text + '\n'
            elif name == "table":
                num_rows = get_row_num(line)
                heads = line.select("thead")
                result += '\n'
                if not len(heads):
                    result += '|' * (num_rows + 1) + '\n'
                else:
                    for head in heads:
                        result += rows_text(head, "th")
                result += '|' + "-|" * num_rows + '\n'
                bodies = line.select("tbody")
                if not len(bodies):
                    result += '|' * (num_rows + 1) + '\n'
                else:
                    for body in bodies:
                        result += rows_text(body, "td")
            elif name == "p":
                stripped = line.text.strip()
                if not stripped:
                    name = previous_line_type
                    continue
                # splitting to remove all trailing white-space
                split = line.text.split('\n')
                insert_text = ""
                for text_line in split:
                    if text_line:
                        insert_text += ' ' + text_line.strip()
                insert_text = insert_text[1:]
                # i know everything below looks really messy, might need a rerewrite
                if line.has_attr("class") and "incr" in line.get("class")[0]:
                    current_line_incr = int(line.get("class")[0][-1:]) + 1
                    insert_text = ((' ' * 4 * (current_line_incr - 1)) if previous_line_type != "table" else "\n\n") + '>' * current_line_incr + insert_text
                    if current_line_incr == 1 or previous_line_incr == 0:
                        insert_text = ">\n" + insert_text
                    elif previous_line_incr > current_line_incr:
                        insert_text = '>' * current_line_incr + '\n' + insert_text
                    previous_line_incr = current_line_incr + 1
                elif "content" in line.get("class")[0]:
                    insert_text = ("\n" if previous_line_type == "table" else '') + insert_text + "<br>" + '\n'
                else:
                    if line.has_attr("class"):
                        if (line.get("class")[0] == "p0" or "indent" in line.get("class")[0]):
                            insert_text = ("\n\n" if previous_line_type == "table" else '\n') + '* ' + insert_text + '\n'
                        elif line.get("class")[0] == "b0":
                            insert_text = ("\n\n" if previous_line_type == "table" else '\n') + '* * ' + insert_text + '\n\n'
                        elif line.get("class")[0] == "bc0":
                            insert_text = ('\n' if previous_line_type == "table" else '') + "\n**" + insert_text + "**\n"
                        else:
                            insert_text = ("\n\n" if previous_line_type == "table" else '\n') + insert_text + '\n'
                    else:
                        insert_text = ("\n\n" if previous_line_type == "table" else '\n') + insert_text + '\n'
                    if previous_line_incr:
                        previous_line_incr = 0
                result += insert_text
            previous_line_type = name
        with open("test.md", "w", encoding="utf-8") as f: # for testing purposes
            f.write(result)
        return result

def export_munis():
    """
    Exports all available municipalities in municode into a json file

    format: {[state]: {link: [url], municipalities: {[muni name]: [url]}}}
    """
    muni_scraper = MuniCodeCrawler()
    result = {}
    for state, state_url in muni_scraper.scrape_states().items():
        result[state] = {
            "link": state_url,
            "municipalities": {}
        }
        muni_scraper.go(state_url)
        for muni, muni_url in muni_scraper.scrape_munis().items():
            result[state]["municipalities"][muni] = muni_url        
    with open("municode_munis.json", "w") as f:
        json.dump(result, f)

def test_text_scrape():
    muni_scraper = MuniCodeCrawler("https://library.municode.com/ca/milpitas/codes/code_of_ordinances?nodeId=TITXIZOPLAN_CH10ZO_S11SPPLAR_XI-10-11.01PUIN")
    muni_scraper.scrape_text()

def main():
    muni_scraper = MuniCodeCrawler()
    states = muni_scraper.scrape_states() # gets a dict of states
    print(states)
    muni_scraper.go(states["california"]) # goes to california via the results of states
    muni = muni_scraper.scrape_munis() # gets a dict of municipalities in the state of california
    print(muni)
    muni_scraper.go(muni["tracy"]) # goes to tracy
    titles = muni_scraper.scrape_titles() # grabs all the titles for tracy
    print(titles)
    muni_scraper.go(titles["Title 5 - SANITATION AND HEALTH"]) # scrapes the chapters in title 5
    more_chapters = muni_scraper.scrape_chapters()
    print(more_chapters)
    muni_scraper.go(titles["Title 4 - PUBLIC WELFARE, MORALS AND CONDUCT"]) # access title 4 for tracy
    chapters = muni_scraper.scrape_chapters() # scrapes the chapters in title 4
    print(chapters)
    muni_scraper.go(chapters["Chapter 4.12 - MISCELLANEOUS REGULATIONS"]) # access chapter
    if (muni_scraper.contains_child()): # checks if current page has any children
        articles = muni_scraper.scrape_articles() # scrapes the articles
        print(articles)
        muni_scraper.go(articles["Article 14. - Soliciting and Aggressive Solicitation"]) # access chapter's article
        print(muni_scraper.scrape_text()) # scrapes all text from article



if __name__ == "__main__":
    test_text_scrape()