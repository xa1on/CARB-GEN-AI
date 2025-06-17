"""
MUNICODE SCRAPER

Scrapes library.municode.com for municipality codes

Authors: Chenghao Li, 
Org: University of Toronto: School of Cities
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
        self.browser = webdriver.Chrome(options = chrome_options)
        self.browser.set_window_size(1024, 768)
        self.url = self.home_url

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
        Takes a html snapshot of current page

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
        buffer_secondary_xpath = """/html/body/div[2]/div[2]/div/div/span/i""" # secondary loading thing
        loading_complete_xpath = """/html/body/div[2]/div[2]/div/div/span""" # find hidden loading complete item
        wait = WebDriverWait(self.browser, 7.5)
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_main_xpath)))
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_secondary_xpath)))
        wait.until(EC.presence_of_element_located((By.XPATH, loading_complete_xpath)))
        self.browser.implicitly_wait(1)
        # self.take_snapshot() # for debugging purposes
    
    def scrape_index_link(self):
        """
        Scrapes any items with index-link class from page, with name tied to the link.
        Selecting states and selecting municipalities uses index-link

        :param self:
        :return: dictionary in the format {[item_name]: [link to item]}
        """
        soup = BeautifulSoup(self.browser.page_source, "html.parser")
        items = soup.select("a[class=index-link]")
        return {item.text.lower(): item["href"] for item in items}

    def scrape_codes(self, depth=0):
        """
        Scrapes codes from municpality page with depth

        :param self:
        :param title: whether or not we are looking for the titles
        :param depth: depth of item. (title: 0, chapter: 2, article/section: 3)
        :return: dictionary in the format {[code_name]: [link to code]}
        """
        result = {}
        soup = BeautifulSoup(self.browser.page_source, "html.parser")
        codes = soup.find_all("li", {'depth': depth})
        for code in codes:
            code = code.select("a[class=toc-item-heading]")[0]
            code_text = code.text.replace('\n', '')
            if depth != 0 or code_text[:5].lower() == "title":
                result[code_text] = code["href"]
        return result
    

def main():
    bob = MuniCodeCrawler()
    bob.go()
    states = bob.scrape_index_link()
    print(states)
    bob.go(states['california'])
    muni = bob.scrape_index_link()
    print(muni)
    bob.go(muni['tracy'])
    titles = bob.scrape_codes()
    print(titles)
    bob.go(titles['Title 4 - PUBLIC WELFARE, MORALS AND CONDUCT'])
    chapters = bob.scrape_codes(2)
    print(chapters)
    bob.go(titles['Title 5 - SANITATION AND HEALTH'])
    more_chapters = bob.scrape_codes(2)
    print(more_chapters)


if __name__ == '__main__':
    main()