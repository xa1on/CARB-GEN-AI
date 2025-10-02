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
import os
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
SEARCH_CSS = 'input[class="search__input form-control"]'
SEARCH_RESULT_CSS = 'a[class="select-search"]'
SEARCH_RESULT2_CSS = 'span[class="search-badge search-badge--title badge badge-secondary"]'
SEARCH_RESULT3_CSS = 'em[class="mark"]'
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

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
    search_url: str = "https://codelibrary.amlegal.com/search"
    def __init__(self, search=False, starting_url: str=home_url, searching_url: str=search_url): 
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
        if search == True:
            self.go(searching_url)
        else:
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
        self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, LOADING_CSS_SELECTOR)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self
    

    def search(self, search_term):
        """
        Searches specified search term in amlegal

        :param search_term: search term to use for search
        """
        search_bar = self.browser.find_element(By.CSS_SELECTOR, SEARCH_CSS)
        search_bar.clear()
        search_bar.send_keys(search_term, Keys.RETURN)
        self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, LOADING_CSS_SELECTOR)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self
    
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
        self.wait_visibility(SEARCH_RESULT_CSS)
        search_results = self.soup.select(SEARCH_RESULT_CSS)
        search_results2 = self.soup.select(SEARCH_RESULT2_CSS)
        search_results3 = self.soup.select(SEARCH_RESULT3_CSS)
        result: dict[str: SearchResult] = {}
        resholder = []
        resholder2 = []
        resholder3 = []
        resholder3_pieces = []
        for res in search_results:
            name = res.text
            href = self.home_url + res["href"]
            #result[name] = SearchResult(href,name)
            resholder.append([href,name])
        for res2 in search_results2:
            chapter_name = res2.text
            resholder2.append(chapter_name)
        for res3 in search_results3:
            text1 = res3.text
            text2 = res3.next_sibling.strip() if res3.next_sibling else ""
            related_text = text1 + ' ' + text2
            resholder3_pieces.append(related_text)
        for i in range(len(resholder3_pieces)):
            if i%2 == 0:
                if i!= 0:
                    resholder3.append(piece)
                piece = ""
            piece += resholder3_pieces[i]+' '
        for i in range(len(resholder)):
            name = resholder[i][1]
            href = resholder[i][0]
            chapter_name = resholder2[i]
            related_text = resholder3[i]
            result[name] = SearchResult(href,name,chapter_name,related_text)
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
        return {item.text.lower(): self.home_url + item["href"] for item in items}
    
    def scrape_codes(self, depth: int=0) -> dict[str: str]:
        """
        Scrapes codes from amlegal page with depth (depth is now used to help me with caseworks)

        :param self:
        :param title: whether or not we are looking for the titles
        :param depth: depth of item. (title: 0, chapter: 1, article/section: 2)
        :return: dictionary in the format {[code_name]: [link to code]}
        """
        #if depth: # because /Amelgal/ is weird, it works like this: (title: 0, chapter: 2, article/section: 3), therefore, we need to index up when depth isn't 0 so we start at depth 0.
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
        for code in codes:
            code_text = code.text.strip()+(' '+code.next_sibling.strip() if code.next_sibling else "")
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

    def save_full_page(aml_scraper, name):
        path = os.path.join(SNAPSHOTS_DIR, f"{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(aml_scraper.browser.page_source)
        print("WROTE:", path)
        return path

    def save_codes_content(aml_scraper, name):
        aml_scraper.wait_visibility(BODY_CSS)
        el = aml_scraper.browser.find_element(By.CSS_SELECTOR, BODY_CSS)
        html = el.get_attribuet("outerHTML")
        path = os.path.join(SNAPSHOTS_DIR, f"{name}.codes.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print("WROTE:", path)
        return path

    def scrape_text(self) -> str: #Needs to be modified
        """
        Scrapes text from code on page

        :param self:
        :return: string of the output in markdown format
        """
        try:
            self.wait_visibility(BODY_CSS)
        except Exception:
            pass

        container = self.soup.select_one(BODY_CSS)
        raw_html = container.decode_contents() if container else ""

        def clean_text(s: str) -> str:
            return re.sub(r'\s+\n', '\n', re.sub(r'\s{2,}', ' ', s.strip()))

        '''added an edge case to take care of tables format in potential scraping'''
        def table_to_markdown(table):
            rows =[]
            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                rows.append("|" + " | ".join(cells) + " |")
            if table.find("th"):
                header_sep = "| " + " | ".join("---" for _ in table.find_all("th")) + " |"
                rows.insert(1, header_sep)
            return "\n".join(rows)

        
        def element_to_markdown(el):
            if el.name in ('g', 'div', 'section', 'article'):
                return clean_text(el.get_text(separator=" ", strip=True))
            if el.name in ('li',):
                return "- " + clean_text(el.egt_text(separator=" ", strip=True))
            if el.name == "table":
                return table_to_markdown(el)
            if el.name in ('ul', 'ol'):
                items = []
                for li in el.find_all('li', recursive=False):
                    items.append(elem_to_markdown(li))
                return "\n".join(items)
            return clean_text(el.get_text(separator=" ", strip=True))

        sections = []
        # so my first straightforward strategy is just to iterate through the lis if there are explicit chunks
        chunks = container.select(TEXT_CSS + " > li") if container and container .select(TEXT_CSS) else []
        if chunks:
            for li in chunks:
                # here I took for heading (strong/bold) or header tag of some sort
                heading = None
                for htag in ('h1', 'h2', 'h3', 'h4', 'h5', 'strong', 'b'):
                    h = li.find(htag)
                    if h:
                        heading = h.get_text(strip=True)
                        break
                    # or I just extract a somewhat leading section
                    if not heading:
                        text_preview_smth = li.get_text(" ", strip=True)[:120]
                        m = re.match(r'^\s*([0-9][\d\.\(\)a-zA-Z\- ]{1,40})', text_preview_smth)
                        if m:
                            heading = m.group(1).strip()
                    body_text = elem_to_markdown(li)
                    sections.append({"heading": heading, "html": str(li), "text": body_text})

                    page_title = ""
                    try:
                        t = self.soup.select_one("title")
                        page_title = t.get_text(strip=True) if t else ""
                    except Exception:
                        page_title = ""

                    md = f"# {page_title}\n\n" if page_title else ""
                    for sec in sections:
                        if sec.get("heading"):
                            md += f"## {sec['heading']}\n\n"
                        if sec.get("text"):
                            md += sec["text"].strip() + "\n\n"

        return {
            "url": getattr(self, "browser", None) and self.browser.current_url or "", 
            "title": page_title,
            "markdown": md.strip(),
            "sections": sections,
            "raw_html": raw_html
        }

    ''' (you're right just fixed some obvious ones)
    '''


def export_munis() -> None:
    pass

def test_text_scrape():
    pass

def test_search(term):
    aml_scraper = AmlegalCrawler(True)
    aml_scraper.search(term)
    return aml_scraper.scrape_search()


def main():
    aml_scraper = AmlegalCrawler()
    states = aml_scraper.scrape_states()
    del states['view google map of online clients']
    print(states)
    aml_scraper.go(states['california'])
    muni = aml_scraper.scrape_munis() # gets a dict of municipalities in the state of california
    print(muni)
    aml_scraper.go(muni["adelanto"]) # goes to adelanto
    titles = aml_scraper.scrape_titles() # grabs all the titles for adelanto
    print(titles)
    aml_scraper.go(titles["TITLE 1 GENERAL PROVISIONS"]) # scrapes the chapters in title 1
    chapters = aml_scraper.scrape_chapters()
    print(chapters)
    aml_scraper.go(chapters["1.01 Code Adopted"]) # access chapter
    # if (muni_scraper.contains_child()): All chapters seems to have articles, thus this line is omitted
    articles = aml_scraper.scrape_articles() # scrapes the articles
    print(articles)
    aml_scraper.go(articles["1.01.010 Declaration of Purpose"]) # access chapter's article
    print(aml_scraper.scrape_text()) # scrapes all text from article

if __name__ == "__main__":
    results = test_search("adelanto")
    for key in results:
        print(key+'\n'+results[key].href+'\n'+results[key].chapter_name+'\n'+results[key].related_text)
