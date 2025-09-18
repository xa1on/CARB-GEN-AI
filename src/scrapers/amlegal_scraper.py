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
SEARCH_CSS = "#headerSearch"
SEARCH_IN_SEARCH_CSS = "#searchBox"
SEARCH_RESULT_CSS = "div[class=search-result-body]"
SEARCH_RESULT_COUNT_CSS = "h3[class=text-light]"

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
    home_url: str = "https://codelibrary.amlegal.com/regions/ca" #updated to amlegal california
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
    
    def go(self, url):
        """
        Goes to specified url and waits for loading to finish 

        :param self:
        :return:
        """
        self.browser.get(url)
        self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, LOADING_CSS_SELECTOR)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self
    
    def wait_visibility(self, CSS):
        self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, CSS)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self

    def search(self, search_term):
        """
        Searches specified search term in municode

        :param search_term: search term to use for search
        """
        search_bar = None
        try:
            search_bar = self.browser.find_element(By.CSS_SELECTOR, SEARCH_CSS)
        except:
            search_bar = self.browser.find_element(By.CSS_SELECTOR, SEARCH_IN_SEARCH_CSS)
        search_bar.clear()
        search_bar.send_keys(search_term, Keys.RETURN)
        self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, LOADING_CSS_SELECTOR)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self
    '''
    This function is not used
    
    def contains_child(self) -> bool:
        """
        Checks if current page contains any children pages. (ex: a chapter contains sub-chapers/articles/sections)

        :param self:
        :return: True/False depending on whether or not child page exists
        """
        self.wait_visibility(BODY_CSS)
        return len(self.soup.select("ul.codes-toc-list.list-unstyled")) > 0
    '''
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
        self.wait_visibility(SEARCH_RESULT_COUNT_CSS)
        search_results = self.soup.select(SEARCH_RESULT_CSS)
        result: dict[str: SearchResult] = {}
        for search_result in search_results:
            link = search_result.select_one("a.text-lg")
            name = link.text.replace('\n', '').replace('*', '')
            related_text = stripped_splitter(search_result.find("div", {"ng-bind-html": "::hit.ContentFragment"}).text)
            directories = search_result.select_one("ol.breadcrumb").select("a")
            result[name] = SearchResult(href=link["href"], name=name, chapter_name=stripped_splitter(directories[-1].text).replace('*', ''), related_text=related_text)
        return result

    def scrape_index_link(self) -> dict[str: str]:
        """
        Scrapes any items with index-link class from page, with name tied to the link.
        Selecting states and selecting municipalities uses index-link

        :param self:
        :return: dictionary in the format {[item_name]: [link to item]}
        """
        self.wait_visibility(INDEX_CSS)
        items = self.soup.select(INDEX_CSS)
        return {item.text.lower(): 'https://codelibrary.amlegal.com'+item["href"] for item in items} #{item.text.lower(): item["href"] + ("/codes/code_of_ordinances" if "/codes/code_of_ordinances" not in item["href"] else "") for item in items}
    
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
            result[code_text] = "https://codelibrary.amlegal.com"+code["href"]
        return result
    
    """
    scrape states and cities are redundant but good for readability
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
        self.wait_visibility(BODY_CSS)
        self.wait_visibility(TEXT_CSS)
        def text_selector(tag):
            # only select text with h2, h3, p, table tags
            return tag.name == "h2" or tag.name == "h3" or tag.name == "p" or tag.name == "table" or (tag.name == "div" and tag.has_attr("class") and tag.get("class")[0] == "footnote-content")

        def table_item_selector(tag):
            # select tags of table items
            return tag.name == "td" or tag.name == "th"

        def bold_text_selector(tag):
            return tag.name == "b" or (tag.name == "span" and tag.has_attr("class") and tag.get("class")[0] == "bold")

        result: str = ""

        text_block = self.soup.select_one("ul.chunks.list-unstyled.small-padding") # contains the text
        bolds = text_block.find_all(bold_text_selector)
        for bold in bolds:
            bold.replace_with(f"**{stripped_splitter(bold.text)}**")
        sups = text_block.find_all("sup")
        for sup in sups:
            sup.replace_with(f"<sup>{stripped_splitter(sup.text)}</sup>")
        subs = text_block.find_all("sub")
        for sub in subs:
            sub.replace_with(f"<sub>{stripped_splitter(sub.text)}</sub>")
        # using text_selector to retain order
        text = text_block.find_all(text_selector)
        for line in text:
            tag = line.name
            if tag == "div":
                for chunk in line.contents:
                    result += "> " + stripped_splitter(chunk.text) + "\n>\n"
            elif tag == "h2" or tag == "h3":
                result += "#" * int(tag[1:]) + ' ' + line.select_one("div[class=chunk-title]").text + "\n\n"
            elif tag == "table":
                head = line.select_one("thead")
                body = line.select_one("tbody")
                head_rows = []
                head_row_exists = False
                if head:
                    head_rows = head.select("tr")
                    head_row_exists = True
                body_rows = body.select("tr")
                rows = head_rows + body_rows
                width = len(body_rows[0].find_all(table_item_selector))
                height = len(rows)
                filled = [['' for _ in range(width)] for _ in range(height)] # matrix representing filled spots
                current_row = 0 # index
                current_col = 0
                for row in rows: # iterate through and fill up matrix
                    items = row.find_all(table_item_selector)
                    current_col = 0
                    for item in items:
                        for col_index in range(current_col, len(filled[current_row])): # go to first open col in row
                            if filled[current_row][col_index]:
                                current_col += 1
                            else:
                                break
                        item_w = 1
                        item_h = 1
                        if item.has_attr("colspan"):
                            item_w = int(item.get("colspan")[0])
                        if item.has_attr("rowspan"):
                            item_h = int(item.get("rowspan")[0])
                        if current_col + item_w > width: # resizing matrix to ensure enough space
                            for row in filled:
                                for _ in range(current_col + item_w - width):
                                    row.append('')
                            width = current_col + item_w
                        if current_row + item_h > height:
                            for _ in range(current_row + item_h - height):
                                filled.append([False for _ in width])
                        for x in range(current_col, current_col + item_w):
                            for y in range(current_row, current_row + item_h):
                                filled[y][x] = '|' + (stripped_splitter(item.text, "<br>") if x == current_col and y == current_row else '') # filling all spots where item sits
                        current_col += item_w
                    current_row += 1
                seperator = "|-" * width + "|\n"
                if not head_row_exists:
                    result += '|' * width + "|\n" + seperator # inserting seperator if header doesn't exist
                for row in filled:
                    for item in row:
                        result += item
                    result += "|\n"
                    if head_row_exists:
                        result += seperator
                        head_row_exists = False
                result += '\n'
            elif tag == "p":
                line_class = line.get("class")[0] if line.has_attr("class") else ''
                if line_class == "b0":
                    # indented text
                    result += "\t\t" + stripped_splitter(line.text) + "\n\n"
                elif "incr" in line_class:
                    # start of indented text
                    indent = int(line_class[-1]) + 1
                    result += '\t' * indent + ' ' + line.text.strip() + ' '
                elif not "refmanual" in line_class:
                    result += stripped_splitter(line.text) + "\n\n"
        with open("test.md", "w", encoding="utf-8") as f: # for testing purposes
            f.write(result)
        return result

def export_munis() -> None: #Needs to be modified
    """
    Exports all available municipalities in municode into a json file

    format: {[state]: {link: [url], municipalities: {[muni name]: [url]}}}
    """
    muni_scraper = AmlegalCrawler()
    result: dict[str: dict[str: str|dict[str: str]]] = {}
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
    aml_scraper = AmlegalCrawler("https://library.municode.com/ca/milpitas/codes/code_of_ordinances?nodeId=TITXIZOPLAN_CH10ZO_S11SPPLAR_XI-10-11.01PUIN")
    aml_scraper.scrape_text()

def test_search():
    muni_scraper = AmlegalCrawler("https://library.municode.com/ca/campbell/codes/code_of_ordinances")
    muni_scraper.search("Eviction")
    print(muni_scraper.scrape_search())


def main():
    aml_scraper = AmlegalCrawler()
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
