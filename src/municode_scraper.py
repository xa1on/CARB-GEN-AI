"""
MUNICODE SCRAPER

Scrapes library.municode.com for municipality codes

Notes: something really similar should be done with codelibrary.amlegal.com

Authors: Chenghao Li, 
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
        buffer_secondary_xpath = """/html/body/div[2]/div[2]/div/div/span/i""" # secondary loading thing
        loading_complete_xpath = """/html/body/div[2]/div[2]/div/div/span""" # find hidden loading complete item
        wait = WebDriverWait(self.browser, 7.5)
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_main_xpath)))
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_secondary_xpath)))
        wait.until(EC.presence_of_element_located((By.XPATH, loading_complete_xpath)))
        self.browser.implicitly_wait(1) # this is not good. we should try and figure out a more foolproof way of ensuring the page loads.
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
        Scrapes codes from municipality page with depth

        :param self:
        :param title: whether or not we are looking for the titles
        :param depth: depth of item. (title: 0, chapter: 2, article/section: 3)
        :return: dictionary in the format {[code_name]: [link to code]}
        """
        result = {}
        soup = BeautifulSoup(self.browser.page_source, "html.parser")
        codes = soup.find_all("li", {"depth": depth})
        for code in codes:
            code = code.select("a[class=toc-item-heading]")[0]
            code_text = code.text.replace('\n', '')
            if depth != 0 or "title" in code_text.lower():
                result[code_text] = code["href"]
        return result
    
    """
    scrape states and cities are redundant but good for readability
    """

    def scrape_states(self):
        return self.scrape_index_link()
    
    def scrape_cities(self):
        return self.scrape_index_link()

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
            # only select text with h2, h3, or p tags
            return tag.name == "h2" or tag.name == "h3" or tag.name == "p"

        result = ""
        soup = BeautifulSoup(self.browser.page_source, "html.parser")
        text_block = soup.select_one("ul.chunks.list-unstyled.small-padding") # contains the text
        text = text_block.find_all(text_selector)
        previous_line_incr = 0
        for line in text:
            if line.name == "h2" or line.name == "h3":
                result += "#" * int(line.name[1]) + ' ' + line.select_one("div[class=chunk-title]").text + '\n'
            else:
                # splitting to remove all trailing white-space
                split = line.text.split('\n')
                insert_text = ""
                for text_line in split:
                    insert_text += text_line.strip() + (' ' if len(text_line) > 0 else '')
                if line.has_attr("class") and "incr" in line.get("class")[0]:
                    current_line_incr = int(line.get("class")[0][-1:]) + 1
                    insert_text = ' ' * 4 * (current_line_incr - 1) + insert_text
                    previous_line_incr = current_line_incr + 1
                else:
                    insert_text += '\n'
                    if previous_line_incr:
                        previous_line_incr = 0
                result += insert_text
        #with open("test.md", "w", encoding="utf-8") as f:
            #f.write(result) # for testing purposes
        return result
    

def main():
    muni_scraper = MuniCodeCrawler()
    muni_scraper.go() # goes to home page of municode
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
    articles = muni_scraper.scrape_articles() # scrapes the articles
    print(articles)
    muni_scraper.go(articles["Article 14. - Soliciting and Aggressive Solicitation"]) # access chapter's article
    print(muni_scraper.scrape_text()) # scrapes all text from article



if __name__ == "__main__":
    main()