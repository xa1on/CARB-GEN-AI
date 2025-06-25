"""
MUNICODE SCRAPER

Scrapes library.municode.com for municipality codes

Notes: something really similar should be done with codelibrary.amlegal.com and generalcode.com/library

Authors: Chenghao Li
Org: University of Toronto - School of Cities
"""

import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

SNAPSHOTS_DIR = "snapshots"

class MuniCodeCrawler:
    home_url = "https://library.municode.com"
    def __init__(self):
        """
        Create new MuniCodeCrawler Object

        :param self:
        :return: returns new object
        """
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--log-level=1")
        self.browser = webdriver.Chrome(options = chrome_options)
        self.browser.set_window_size(1024, 1024)
        self.go(self.home_url)

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
            f.write(self.browser.page_source)
    
    def go(self, url=None):
        """
        Goes to specified url and waits for loading to finish 

        :param self:
        :return:
        """
        if url: # if url is specified, go to url specified
            self.set_url(url)
        self.browser.get(self.url)
        buffer_main_xpath = """/html/body/div[2]/div[2]/ui-view/div/div/div/p/span/i""" # main initializing application spinning thing
        buffer_secondary_xpath = """/html/body/div[2]/div[2]/ui-view/div/div/div/p/span/i""" # secondary loading thing
        loading_complete_xpath = """/html/body/div[3]/div[2]/div/div/span""" # find hidden loading complete item
        google_translate_xpath = """/html/body/header/div/div/div[3]/div/ul/li[3]/div/div/span/a""" # path for google translate widget. usually a good indicator that it has fully loaded in
        wait = WebDriverWait(self.browser, 7.5)
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_main_xpath)))
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_secondary_xpath)))
        wait.until(EC.visibility_of_element_located((By.XPATH, loading_complete_xpath)))
        wait.until(EC.visibility_of_element_located((By.XPATH, google_translate_xpath)))
        self.browser.implicitly_wait(0.5) # just to make 100% sure no errors occur. not ideal, but I can't seem to find whats not allowing it to fully load
        # self.take_snapshot() # for debugging purposes
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
        items = self.soup.select("a[class=index-link]")
        return {item.text.lower(): item["href"] + ("/codes/code_of_ordinances" if requires_code and "/codes/code_of_ordinances" not in item["href"] else "") for item in items}
    
    def scrape_codes(self, depth=0):
        """
        Scrapes codes from municipality page with depth

        :param self:
        :param title: whether or not we are looking for the titles
        :param depth: depth of item. (title: 0, chapter: 2, article/section: 3)
        :return: dictionary in the format {[code_name]: [link to code]}
        """
        result = {}
        codes = self.soup.find_all("li", {"depth": depth})
        for code in codes:
            code = code.select_one("a[class=toc-item-heading]")
            code_text = code.find("span", {"data-ng-bind": "::node.Heading"}).text.replace('\n', '')
            #if depth != 0 or "title" in code_text.lower(): # unfortunately, not all cities call them titles.
            result[code_text] = code["href"]
        return result
    
    """
    scrape states and cities are redundant but good for readability
    """

    def scrape_states(self):
        return self.scrape_index_link()
    
    def scrape_cities(self):
        return self.scrape_index_link(True)

    def scrape_titles(self):
        return self.scrape_codes(0)

    def scrape_chapters(self):
        return self.scrape_codes(2)

    def scrape_articles(self):
        return self.scrape_codes(3)
    
    def scrape_text(self):
        """
        Scrapes text from code on page

        :param self:
        :return: string of the output in markdown format
        """
        
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

        def rows_text(parent, type):
            # converts table rows into markdown text
            rows = parent.select("tr")
            result = ""
            for row in rows:
                items = row.select(type)
                for item in items:
                    text = ""
                    splits = item.text.split('\n')
                    for line in splits:
                        text += line.strip() + " "
                    result += f"|{text}{("|" * (int(item.get("colspan")[0]) - 1)) if item.has_attr("colspan") else ""}"
                result += "|\n"
            return result
        
        result = ""
        text_block = self.soup.select_one("ul.chunks.list-unstyled.small-padding") # contains the text
        previous_line_incr = 0
        text = text_block.find_all(text_selector)

        for line in text:
            if line.name == "h2" or line.name == "h3":
                result += "#" * int(line.name[1]) + ' ' + line.select_one("div[class=chunk-title]").text + '\n'
            elif line.name == "table":
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
            elif line.name == "p":
                # splitting to remove all trailing white-space
                split = line.text.split('\n')
                insert_text = ""
                for text_line in split:
                    insert_text += text_line.strip() + (' ' if len(text_line) else '')
                if line.has_attr("class") and "incr" in line.get("class")[0]:
                    current_line_incr = int(line.get("class")[0][-1:]) + 1
                    insert_text = ' ' * 4 * (current_line_incr - 1) + insert_text
                    previous_line_incr = current_line_incr + 1
                else:
                    insert_text += '\n'
                    if previous_line_incr:
                        previous_line_incr = 0
                result += insert_text
        #with open("test.md", "w", encoding="utf-8") as f: # for testing purposes
            #f.write(result)
        return result
    

def main():
    muni_scraper = MuniCodeCrawler()
    states = muni_scraper.scrape_states() # gets a dict of states
    print(states)
    muni_scraper.go(states["california"]) # goes to california via the results of states
    muni = muni_scraper.scrape_cities() # gets a dict of municipalities in the state of california
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
    main()