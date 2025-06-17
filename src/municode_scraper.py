from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class MuniCodeCrawler:
    home_url = "https://library.municode.com"
    def __init__(self):
        """
        Create new MuniCodeCrawler Object

        :param self:
        """
        chrome_options = webdriver.ChromeOptions()
        self.browser = webdriver.Chrome(options = chrome_options)
        self.url = self.home_url
    
    def set_url(self, url):
        """
        Set current url to url

        :param self:
        :param url: url to set object to
        """
        self.url = url
    
    def take_snapshot(self):
        with open("snapshot.html", "w", encoding="utf-8") as f:
            f.write(self.browser.page_source)
    
    def retrieve_html(self):
        self.browser.get(self.url)
        buffer_xpath = """/html/body/div[2]/div[2]/ui-view/div/div/div/p/span/i"""
        wait = WebDriverWait(self.browser, 2)
        wait.until(EC.invisibility_of_element_located((By.XPATH, buffer_xpath)))
    
    def scrape_states(self):
        soup = BeautifulSoup(self.browser.page_source, "html.parser")
        states = soup.select("a[class=index-link]")
        return {state.text.lower():state["href"] for state in states}
    

def main():
    bob = MuniCodeCrawler()
    bob.retrieve_html()
    print(bob.scrape_states())

if __name__ == '__main__':
    main()