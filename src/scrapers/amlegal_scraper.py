""" 
AMLEGAL SCRAPER

Scrapes codelibrary.amlegal.com/regions/ca for municipality codes

Notes: something really similar should be done with codelibrary.amlegal.com and generalcode.com/library

Authors: Chenghao Li, Zack Yu, Bo Wang
Org: University of Toronto - School of Cities
"""

import time
import json
import re
from bs4 import BeautifulSoup, Tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait




SNAPSHOTS_DIR = "snapshots"
LOADING_CSS_SELECTOR = ".fa-2x"
TIMEOUT = 120
INDEX_CSS = 'a[class="browse-link roboto"]'
CODE_CSS = "a[class=toc-link]"
BODY_CSS = "#codesContent"
TEXT_CSS = "ul.chunks.list-unstyled.small-padding"

DEPTH: dict[str: int] = {
    "Titles": 0,
    "Chapters": 1,
    "Articles": 2
}

def stripped_splitter(text: str, separator=' ') -> str:
    # split by newline and strip leading and tailing spaces
    result = ""
    split = text.split('\n')
    for text_line in split:
        stripped = text_line.strip()
        if stripped:
            result += separator + stripped
    return result[len(separator):]

class SearchResult:
    def __init__(self, href: str, name: str, chapter_name: str, related_text: str):
        self.href = href
        self.name = name
        self.chapter_name = chapter_name
        self.related_text = related_text
    
    def __repr__(self) -> str:
        return f"href={self.href}, name={self.name}, chapter_name={self.chapter_name}, related_text={self.related_text}"

class AmlegalCrawler:
    home_url: str = "https://codelibrary.amlegal.com/"
    def __init__(self, starting_url: str=home_url): 
        """
        Create new AmlegalCrawler Object

        :param self:
        :return: returns new object
        """
        chrome_options = webdriver.ChromeOptions()

        chrome_options.add_argument("--log-level=1")

        self.browser = webdriver.Chrome(options = chrome_options)
        self.wait= WebDriverWait(self.browser, TIMEOUT)


        self.browser.set_window_size(1024, 1024)
        self.go(starting_url)

    def wait_visibility(self, CSS):
        self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, CSS)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self
    
    def go(self, url):
        """
        Goes to specified url and waits for loading to finish 

        :param self:
        :return:
        """
        self.browser.get(url)
        self.wait_visibility(LOADING_CSS_SELECTOR)
        return self
    

    def search(self, search_term):
        """
        Searches specified search term in municode

        :param search_term: search term to use for search
        """
        pass
    
    def contains_child(self) -> bool:
        """
        Checks if current page contains any children pages. (ex: a chapter contains sub-chapers/articles/sections)

        :param self:
        :return: True/False depending on whether or not child page exists
        """
        pass

    def scrape_title(self) -> str:
        self.wait_visibility(BODY_CSS)
        self.wait_visibility(TEXT_CSS)
        title = self.soup.select_one("title")
        return title.text.strip()

    def scrape_search(self) -> dict[str: SearchResult]:
        """
        Scrapes search results from search page

        :param self:
        :return: list of SearchResults
        """
        pass

    def scrape_index_link(self) -> dict[str: str]:
        """
        Scrapes any items with index-link class from page, with name tied to the link.
        Selecting states and selecting municipalities uses index-link

        :param self:
        :return: dictionary in the format {[item_name]: [link to item]}
        """
        self.wait_visibility(INDEX_CSS)
        items = self.soup.select(INDEX_CSS)
        return {item.text.lower(): self.home_url + item["href"] for item in items}
    
    def scrape_codes(self, depth: int=0) -> dict[str: str]:
        """
        Scrapes codes from amlegal page with depth (depth is now used to help me with caseworks)

        :param self:
        :param title: whether or not we are looking for the titles
        :param depth: depth of item. (title: 0, chapter: 1, article/section: 2)
        :return: dictionary in the format {[code_name]: [link to code]}
        """
        #if depth: # because municode is weird, it works like this: (title: 0, chapter: 2, article/section: 3), therefore, we need to index up when depth isn't 0 so we start at depth 0.
            #depth += 1
        self.wait_visibility(CODE_CSS)
        result: dict[str: str] = {}
        if depth == 0:
            codes = self.soup.find_all("a", {"class": "toc-link"})
            new_codes = []
            for code in codes:
                if "TITLE" in code.text:
                    new_codes.append(code)
            codes = list(new_codes)
        elif depth == 1:
            codes = self.soup.find_all("a", {"class": "Jump"})
            new_codes = []
            pattern = r"\d\.\d\d"
            for code in codes:
                if re.search(pattern,code.text):
                    new_codes.append(code)
            codes = list(new_codes)
        elif depth == 2:
            codes = self.soup.find_all("a", {"class": "Jump"})
            new_codes = []
            pattern = r"\d\.\d\d.\d\d\d"
            for code in codes:
                if re.search(pattern,code.text):
                    new_codes.append(code)
            codes = list(new_codes)
        print(codes)
        for code in codes:
            code_text = code.text.strip()
            result[code_text] = "https://codelibrary.amlegal.com" + code["href"]
        return result
    
    """
    scrape states and cities are redundant but good for readability. (and in case other websites have different methods of scraping the states and municipalities)
    """

    def scrape_states(self) -> dict[str: str]:
        return self.scrape_index_link()
    
    def scrape_munis(self) -> dict[str: str]:
        return self.scrape_index_link()

    def scrape_titles(self) -> dict[str: str]:
        return self.scrape_codes(DEPTH["Titles"])

    def scrape_chapters(self) -> dict[str: str]:
        return self.scrape_codes(DEPTH["Chapters"])

    def scrape_articles(self) -> dict[str: str]:
        return self.scrape_codes(DEPTH["Articles"])

    def scrape_text(self) -> str: #Needs to be modified
        """
        Scrapes text from code on page

        :param self:
        :return: string of the output in markdown format
        """
        pass

def test_text_scrape():
    pass

def test_search():
    pass


def main():
    aml_scraper = AmlegalCrawler()
    
    # showcase state scraping! the scraper currently doesn't work since 

    aml = aml_scraper.scrape_munis() # gets a dict of municipalities in the state of california
    print(aml)
    aml_scraper.go(aml["adelanto"]) # goes to adelanto
    titles = aml_scraper.scrape_titles() # grabs all the titles for adelanto
    print(titles)
    aml_scraper.go(titles["TITLE 1 GENERAL PROVISIONS"]) # scrapes the chapters in title 1
    chapters = aml_scraper.scrape_chapters()
    print(chapters)
    aml_scraper.go(chapters["1.01"]) # access chapter
    # if (muni_scraper.contains_child()): All chapters seems to have articles, thus this line is omitted
    articles = aml_scraper.scrape_articles() # scrapes the articles
    print(articles)
    aml_scraper.go(articles["1.01.010"]) # access chapter's article
    print(aml_scraper.scrape_text()) # scrapes all text from article

if __name__ == "__main__":
    main()
