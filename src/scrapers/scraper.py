"""
MUNICODE SCRAPER

Scrapes library.municode.com for municipality codes

Notes: something really similar should be done with codelibrary.amlegal.com and generalcode.com/library

Authors: Chenghao Li
Org: Urban Displacement Project: UC Berkeley / University of Toronto
"""
import re
import json
from bs4 import BeautifulSoup, Tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

SNAPSHOTS_DIR = "snapshots"
TIMEOUT = 120

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

class Scraper:
    home_url: str = "https://library.municode.com"
    def __init__(self, starting_url: str=home_url):
        """
        Create new MuniCodeScraper Object

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
    
    def wait_invisibility(self, CSS):
        self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, CSS)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self
    
    def go(self, url):
        """
        Goes to specified url and waits for loading to finish 

        :param self:
        :return:
        """
        self.browser.get(url)
        #self.wait_invisibility()
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
        """
        Returns title of the page

        :param self:
        :return: html title for page
        """
        #self.wait_visibility()
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
        pass
    
    def scrape_codes(self, depth: int=0) -> dict[str: str]:
        """
        Scrapes codes from municipality page with depth

        :param self:
        :param title: whether or not we are looking for the titles
        :param depth: depth of item. (title: 0, chapter: 1, article/section: 2)
        :return: dictionary in the format {[code_name]: [link to code]}
        """
        pass

    def scrape_states(self) -> dict[str: str]:
        """
        Scrapes states from page, with name tied to the link.

        :param self:
        :return: dictionary in the format {[item_name]: [link to item]}
        """
        pass
    
    def scrape_munis(self) -> dict[str: str]:
        """
        Scrapes municipailities from page, with name tied to the link.

        :param self:
        :return: dictionary in the format {[item_name]: [link to item]}
        """
        pass

    def scrape_text(self) -> str:
        """
        Scrapes text from code on page

        :param self:
        :return: string of the output in markdown format
        """
        pass

def export_munis() -> None:
    """
    Exports all available municipalities into a json file

    format: {[state]: {link: [url], municipalities: {[muni name]: [url]}}}
    """
    pass


def main():
    pass



if __name__ == "__main__":
    main()