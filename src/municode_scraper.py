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
        self.url = self.home_url
    
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
        with open((SNAPSHOTS_DIR + "\\" + str(time.asctime(time.localtime(time.time()))).replace(":", "-") + "-snap.html"), "w", encoding="utf-8") as f:
            f.write(self.browser.page_source)
    
    def go(self, url=None):
        """
        Goes to specified url and waits for loading to finish 

        :param self:
        :return:
        """
        if url:
            self.set_url(url)
        self.browser.get(self.url)
        buffer_main_xpath = """/html/body/div[2]/div[2]/ui-view/div/div/div/p/span/i""" # main initializing application spinning thing
        buffer_secondary_xpath = """/html/body/div[2]/div[2]/div/div/span/i""" # secondary loading thing
        loading_complete_xpath = """/html/body/div[2]/div[2]/div/div/span""" # find hidden loading complete item
        wait = WebDriverWait(self.browser, 2)
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_main_xpath)))
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_secondary_xpath)))
        wait.until(EC.presence_of_element_located((By.XPATH, loading_complete_xpath)))
        self.take_snapshot()
    
    def scrape_index_link(self):
        """
        Scrapes any items with index-link class from page, with name tied to the link.
        Selecting states and selecting municipalities uses index-link

        :param self:
        :return: dictionary in the format {[state_name]: [link to state muni]}
        """
        soup = BeautifulSoup(self.browser.page_source, "html.parser")
        states = soup.select("a[class=index-link]")
        return {state.text.lower(): state["href"] for state in states}
    

def main():
    bob = MuniCodeCrawler()
    bob.go()
    states = bob.scrape_index_link()
    print(states)
    bob.go(states['california'])
    muni = bob.scrape_index_link()
    print(muni)


if __name__ == '__main__':
    main()