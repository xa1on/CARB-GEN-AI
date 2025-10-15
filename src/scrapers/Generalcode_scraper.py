"""
GENERAL CODE SCRAPER

Scrapes generalcode.com/library for municipality codes
Handles the multi-domain architecture where cities redirect to municipal.codes subdomains

- With Municipality Caching

Authors: Ariana Siordia & Allen Lopez Based on Chenghao Li's Municode scraper
Org: University of Toronto - School of Cities
"""


import time
import json
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager

# Loading Detection & basic config
TIMEOUT = 120

# Helper function
def stripped_splitter(text: str, separator=' ') -> str:
    """Split by newline and strip leading and trailing spaces"""
    result = ""
    split = text.split('\n')
    for text_line in split:
        stripped = text_line.strip()
        if stripped:
            result += separator + stripped
    return result[len(separator):] if result else ""


class GeneralCodeCrawler:
    home_url: str = "https://www.generalcode.com/library/"

    def __init__(self, starting_url: str = home_url):
        """
        Create new GeneralCodeCrawler Object
        :param starting_url: URL to start scraping from
        :returns new object
        """
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--log-level=1")

        service = Service(ChromeDriverManager().install())
        self.browser = webdriver.Chrome(service=service, options=chrome_options)
        self.wait = WebDriverWait(self.browser, TIMEOUT)

        self.browser.set_window_size(1024, 1024)
        self.go(starting_url)

    def wait_visibility(self, CSS):
        """Wait for element to be visible and update soup"""
        self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, CSS)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self
    
    def wait_invisibility(self, CSS):
        """Wait for element to become invisible (e.g., loading spinners)"""
        try:
            self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, CSS)))
        except:
            pass  # Element may not exist, that's okay
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self

    def go(self, url):
        """
        Goes to specified url and waits for loading to finish
        :param url: URL to navigate to 
        :return: self for method chaining
        """
        self.browser.get(url)
        self.wait_visibility("#mapwrapper")
        return self

    def scrape_states_fixed(self) -> dict[str, str]:
        """Scrape states from the dropdown menu"""
        self.wait_visibility("#mapwrapper")
        dropdown_links = self.soup.select("div.dropdown-content a[href*='/text-library/#']")

        states = {}
        for link in dropdown_links:
            state_name = link.text.strip()
            state_href = link.get('href', '')
            if state_href and '#' in state_href:
                state_abbrev = state_href.split('#')[1].upper()
                if state_name and state_abbrev:
                    states[state_abbrev.lower()] = state_href
        return states

    def scrape_munis_with_scroll(self, state_abbrev: str) -> dict[str, str]:
        """Scrape municipalities using dropdown with scroll management"""
        try:
            ecodes_button = self.browser.find_element(By.CSS_SELECTOR, "button.dropbtn")

            print("Scrolling dropdown into view...")
            self.browser.execute_script("window.scrollTo(0, 400);")
            time.sleep(1)

            print("Hovering over eCodes by State button...")
            ActionChains(self.browser).move_to_element(ecodes_button).perform()
            time.sleep(2)

            print("Moving hover to dropdown list...")
            dropdown = self.browser.find_element(By.CSS_SELECTOR, "div.dropdown-content")
            ActionChains(self.browser).move_to_element(dropdown).perform()
            time.sleep(1)

            state_names = {
                'al': 'Alabama', 'ak': 'Alaska', 'az': 'Arizona', 'ar': 'Arkansas',
                'ca': 'California', 'co': 'Colorado', 'ct': 'Connecticut', 'de': 'Delaware',
                'fl': 'Florida', 'hi': 'Hawaii', 'id': 'Idaho', 'il': 'Illinois',
                'in': 'Indiana', 'ia': 'Iowa', 'ks': 'Kansas', 'ky': 'Kentucky',
                'me': 'Maine', 'md': 'Maryland', 'ma': 'Massachusetts', 'mi': 'Michigan',
                'mn': 'Minnesota', 'mo': 'Missouri', 'mt': 'Montana', 'ne': 'Nebraska',
                'nv': 'Nevada', 'nh': 'New Hampshire', 'nj': 'New Jersey', 'nm': 'New Mexico',
                'ny': 'New York', 'nd': 'North Dakota', 'ok': 'Oklahoma', 'or': 'Oregon',
                'pa': 'Pennsylvania', 'ri': 'Rhode Island', 'sd': 'South Dakota',
                'tx': 'Texas', 'ut': 'Utah', 'vt': 'Vermont', 'va': 'Virginia',
                'wa': 'Washington', 'wi': 'Wisconsin', 'wy': 'Wyoming'
            }

            state_name = state_names.get(state_abbrev.lower(), state_abbrev.title())

            print(f"Looking for {state_name} link...")
            state_link = None

            try:
                state_link = self.browser.find_element(By.XPATH, f"//div[@class='dropdown-content']//a[text()='{state_name}']")
                print(f"Found {state_name} in current view")
            except:
                print(f"{state_name} not in current view, scrolling...")
                for scroll_attempt in range(5):
                    ActionChains(self.browser).move_to_element(dropdown).perform()
                    self.browser.execute_script("arguments[0].scrollTop += 150;", dropdown)
                    time.sleep(1)
                    try:
                        state_link = self.browser.find_element(By.XPATH, f"//div[@class='dropdown-content']//a[text()='{state_name}']")
                        print(f"Found {state_name} after scroll attempt {scroll_attempt + 1}")
                        break
                    except:
                        continue
                if not state_link:
                    raise Exception(f"Could not find {state_name} after scrolling")

            state_link.click()
            print(f"Clicked {state_name} link")

            wait = WebDriverWait(self.browser, 10)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".bod-block-popup-wrap.active")))

            iframe = wait.until(EC.presence_of_element_located((By.ID, "codestate")))
            self.browser.switch_to.frame(iframe)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.codeLink")))

            iframe_soup = BeautifulSoup(self.browser.page_source, "html.parser")
            city_links = iframe_soup.select("a.codeLink")

            cities = {}
            for link in city_links:
                city_name = link.text.strip()
                city_url = link.get('href', '')
                if city_name and city_url:
                    clean_name = city_name.lower().replace(' ', '_').replace('city_of_', '')
                    cities[clean_name] = city_url

            self.browser.switch_to.default_content()
            print(f"Successfully extracted {len(cities)} cities")
            return cities

        except Exception as e:
            print(f"Failed: {e}")
            self.browser.switch_to.default_content()
            return {}

    def export_munis(self, states_to_scrape: list[str] = None) -> None:
        """Export all municipalities to JSON file"""
        print("Starting export...")
        result = {}

        all_states = self.scrape_states_fixed()

        if states_to_scrape:
            states = {k: v for k, v in all_states.items() if k in states_to_scrape}
            print(f"Scraping {len(states)} selected states: {', '.join([s.upper() for s in states.keys()])}\n")
        else:
            states = all_states
            print(f"Found {len(states)} states\n")

        for state_abbrev, state_href in states.items():
            print(f"Scraping {state_abbrev.upper()}...")

            result[state_abbrev] = {
                "link": state_href,
                "municipalities": {}
            }

            cities = self.scrape_munis_with_scroll(state_abbrev)
            result[state_abbrev]["municipalities"] = cities

            print(f"  Got {len(cities)} cities\n")

            with open("generalcode_munis.json", "w") as f:
                json.dump(result, f, indent=2)

        print(f"✅ Done! Saved {len(result)} states")

    # ============================================================================
    # PLATFORM DETECTION & NAVIGATION
    # ============================================================================

    def detect_platform(self, url: str = None) -> str:
        """Detect which platform a city uses"""
        if url is None:
            url = self.browser.current_url

        if "ecode360.com" in url:
            return "ecode360"
        elif "municipal.codes" in url:
            return "municipal_codes"
        elif "codepublishing.com" in url:
            return "codepublishing"
        elif "generalcode.com" in url:
            return "generalcode"
        else:
            return "unknown"

    def go_to_city(self, url):
        """Navigate to city code page (works for any platform)"""
        print(f"Navigating to: {url}")
        self.browser.get(url)

        platform = self.detect_platform(url)
        print(f"  Platform: {platform}")

        if platform == "ecode360":
            self.wait_for_ecode360_load()
            time.sleep(2)
        elif platform == "codepublishing":
            self.wait_for_codepublishing_load()
        elif platform == "municipal_codes":
            self.wait_for_municipal_codes_load()
        else:
            time.sleep(3)
            self.soup = BeautifulSoup(self.browser.page_source, "html.parser")

        return self

    # ============================================================================
    # ECODE360 PLATFORM
    # ============================================================================

    def wait_for_ecode360_load(self):
        """Wait for eCode360 page to fully load"""
        try:
            WebDriverWait(self.browser, 3).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.loading"))
            )
        except:
            pass
        
        try:
            WebDriverWait(self.browser, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#codeContent, div.mainContent, div[class*='content']"))
            )
        except:
            print("Warning: Standard content selectors not found")
        
        time.sleep(2)
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self

    def scrape_ecode360_items(self) -> dict[str, str]:
        """Generic method to scrape all clickable items from eCode360"""
        self.wait_for_ecode360_load()
        
        items = {}
        
        content_area = None
        content_selectors = [
            "div#codeContent",
            "div.mainContent", 
            "div[id*='content']",
            "div[class*='content']"
        ]
        
        for selector in content_selectors:
            content_area = self.soup.select_one(selector)
            if content_area:
                print(f"✓ Found content area using: {selector}")
                break
        
        if not content_area:
            print("❌ Could not find any content area")
            content_area = self.soup.select_one("body")
            if not content_area:
                return items
        
        title_links = []
        
        title_links = content_area.select("a.titleLink")
        
        if not title_links:
            title_links = content_area.select("a:has(span.titleTitle)")
        
        if not title_links:
            title_links = content_area.select("a[onclick*='navigateTo']")
        
        if not title_links:
            all_links = content_area.select("a[href]")
            title_links = [link for link in all_links if link.select_one("span[class*='title']")]
        
        print(f"Found {len(title_links)} clickable items in content area")
        
        for link in title_links:
            title_span = link.select_one("span.titleTitle, span[class*='title']")
            if not title_span:
                title_text = link.get_text(strip=True)
                if not title_text or len(title_text) < 3:
                    continue
            else:
                title_text = title_span.get_text(strip=True)
                if '(' in title_text:
                    title_text = title_text.split('(')[0].strip()
            
            item_url = None
            
            data_guid = title_span.get('data-guid', '') if title_span else ''
            if data_guid:
                item_url = f"https://ecode360.com/{data_guid}#{data_guid}"
            
            if not item_url:
                href = link.get('href', '')
                if href and href.startswith('http'):
                    item_url = href
                elif href:
                    item_url = f"https://ecode360.com{href}"
            
            if not item_url:
                onclick = link.get('onclick', '')
                if 'navigateTo' in onclick:
                    import re
                    match = re.search(r"navigateTo\('([^']+)'\)", onclick)
                    if match:
                        guid = match.group(1)
                        item_url = f"https://ecode360.com/{guid}#{guid}"
            
            if title_text and item_url:
                items[title_text] = item_url
        
        return items

    def scrape_ecode360_text(self) -> str:
        """Scrape ordinance text from current eCode360 section in Markdown format"""
        print("Scraping eCode360 text content...")
        self.wait_for_ecode360_load()
        
        result = ""
        content_area = self.soup.select_one("div#codeContent")
        
        if not content_area:
            print("Warning: No content area found")
            return ""
        
        # Extract title/heading if present
        title = content_area.select_one("span.titleTitle, h1, h2")
        if title:
            result += f"# {title.get_text(strip=True)}\n\n"
        
        # Handle definition lists
        deftexts = content_area.select("div.deftext")
        if deftexts:
            print(f"Found {len(deftexts)} definition blocks")
            for deftext in deftexts:
                parent = deftext.parent
                if parent:
                    term_link = parent.find("a", class_="termLink")
                    if term_link:
                        result += f"**{term_link.get_text(strip=True)}**\n\n"
                text = stripped_splitter(deftext.get_text())
                if text:
                    result += text + "\n\n"
            return result.strip()
        
        # Handle regular content with formatting
        paragraph_selectors = ["p.para", "div.para", "div.section-content", "p"]
        paragraphs = []
        for selector in paragraph_selectors:
            paras = content_area.select(selector)
            if paras:
                paragraphs = paras
                print(f"Found {len(paras)} content blocks using selector: {selector}")
                break
        
        if paragraphs:
            for para in paragraphs:
                # Handle bold text
                for bold in para.find_all(['b', 'strong']):
                    bold_text = bold.get_text(strip=True)
                    bold.replace_with(f"**{bold_text}**")
                
                # Handle italic text
                for italic in para.find_all(['i', 'em']):
                    italic_text = italic.get_text(strip=True)
                    italic.replace_with(f"*{italic_text}*")
                
                # Handle lists
                if para.name == 'li':
                    text = stripped_splitter(para.get_text())
                    if text:
                        result += f"- {text}\n"
                else:
                    text = stripped_splitter(para.get_text())
                    if text:
                        result += text + "\n\n"
            
            print(f"Extracted {len(result)} characters of text")
            return result.strip()
        
        text = content_area.get_text(separator="\n\n", strip=True)
        return text

    # ============================================================================
    # CODE PUBLISHING PLATFORM
    # ============================================================================

    def wait_for_codepublishing_load(self):
        """Wait for Code Publishing page to load with proper AJAX handling"""
        
        try:
            WebDriverWait(self.browser, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#mainContent, div#browseCode"))
            )
        except:
            pass
        
        time.sleep(4)
        
        try:
            WebDriverWait(self.browser, 5).until(
                lambda driver: (
                    driver.find_elements(By.CSS_SELECTOR, "div#mainContent p.CHTOC a") or
                    driver.find_elements(By.CSS_SELECTOR, "div#mainContent p.CiteTOC a") or
                    driver.find_elements(By.CSS_SELECTOR, "div#mainContent h1")
                )
            )
        except:
            pass
        
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self

    def scrape_codepublishing_items(self) -> dict[str, str]:
        """
        Scrape items from Code Publishing
        ALWAYS checks mainContent first (where chapters/sections actually are)
        """
        self.wait_for_codepublishing_load()
        
        items = {}
        current_url = self.browser.current_url
        
        main_content = self.soup.select_one("div#mainContent")
        
        if main_content:
            chapter_links = main_content.select("p.CHTOC a")
            section_links = main_content.select("p.CiteTOC a")
            
            if chapter_links or section_links:
                links_to_process = chapter_links if chapter_links else section_links
                
                for link in links_to_process:
                    title_text = link.get_text(strip=True)
                    href = link.get('href', '')
                    
                    if not title_text or not href:
                        continue
                    
                    if href.startswith('http'):
                        full_url = href
                    elif href.startswith('/CA/'):
                        full_url = f"https://www.codepublishing.com{href}"
                    elif href.startswith('#!/'):
                        base_url = current_url.split('#!/')[0]
                        full_url = base_url + href
                    else:
                        base_url = current_url.split('#!/')[0]
                        full_url = base_url + '#!/' + href
                    
                    items[title_text] = full_url
                
                if items:
                    return items
        
        browse_area = self.soup.select_one("div#browseCode")
        
        if browse_area:
            links = browse_area.select("a[href*='#!/']")
            
            for link in links:
                title_text = link.get_text(strip=True)
                href = link.get('href', '')
                
                skip_terms = ['preface', 'how to amend', 'clear all', 'uncodified', 'history', 'tables']
                if any(term in title_text.lower() for term in skip_terms):
                    continue
                
                if not title_text or not href:
                    continue
                
                if href.startswith('/'):
                    full_url = f"https://www.codepublishing.com{href}"
                elif href.startswith('#!/'):
                    base_url = current_url.split('#!/')[0] if '#!/' in current_url else current_url
                    full_url = base_url + href
                else:
                    full_url = href
                
                items[title_text] = full_url
        
        return items

    def scrape_codepublishing_text(self) -> str:
        """Scrape ordinance text from Code Publishing"""
        print("Scraping Code Publishing text...")
        self.wait_for_codepublishing_load()
        
        result = ""
        paragraphs = self.soup.select("p.P1")
        
        if paragraphs:
            print(f"Found {len(paragraphs)} paragraphs")
            for para in paragraphs:
                text = stripped_splitter(para.get_text())
                if text:
                    result += text + "\n\n"
            return result.strip()
        
        main_body = self.soup.select_one("div#mainBody")
        if main_body:
            return main_body.get_text(separator="\n\n", strip=True)
        
        return ""

    # ============================================================================
    # MUNICIPAL.CODES PLATFORM
    # ============================================================================

    def wait_for_municipal_codes_load(self):
        """Wait for municipal.codes page to fully load"""
        try:
            WebDriverWait(self.browser, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.product-content, div#content, main"))
            )
        except:
            pass
        
        time.sleep(2)
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self

    def click_municipal_code_tab(self):
        """Navigate from a city's homepage to its Municipal Code content"""
        print("Looking for 'Municipal Code' tab...")
        self.wait_for_municipal_codes_load()
        
        try:
            municipal_code_link = None
            
            shortcuts = self.soup.select("a.homepage-shortcuts-primary")
            for shortcut in shortcuts:
                if "Municipal Code" in shortcut.get_text():
                    municipal_code_link = shortcut
                    print("Found Municipal Code in homepage shortcuts")
                    break
            
            if not municipal_code_link:
                for link in self.soup.select("a[href]"):
                    link_text = link.get_text(strip=True)
                    if "Municipal Code" in link_text and "Charter" not in link_text:
                        href = link.get('href', '')
                        if href and ('CVMC' in href or 'CMC' in href or 'municipal' in href.lower()):
                            municipal_code_link = link
                            print(f"Found Municipal Code link: {link_text}")
                            break
            
            if municipal_code_link:
                href = municipal_code_link.get('href', '')
                
                if href.startswith('#'):
                    full_url = self.browser.current_url.split('#')[0] + href
                elif href.startswith('/'):
                    parsed = urlparse(self.browser.current_url)
                    full_url = f"{parsed.scheme}://{parsed.netloc}{href}"
                else:
                    full_url = href
                
                print(f"Navigating to: {full_url}")
                self.browser.get(full_url)
                self.wait_for_municipal_codes_load()
            else:
                print("Warning: Could not find Municipal Code tab")
            
            return self
            
        except Exception as e:
            print(f"Error clicking Municipal Code tab: {e}")
            return self

    def scrape_municipal_codes_titles(self) -> dict[str, str]:
        """Extract top-level titles from a municipal code"""
        print("Scraping municipal.codes titles...")
        self.wait_for_municipal_codes_load()
        
        titles = {}
        
        homepage_list = self.soup.select_one("div.homepage-product-list")
        
        if homepage_list:
            print("Detected homepage-style layout")
            list_items = homepage_list.select("a.homepage-product-list-item")
            
            for item in list_items:
                num_span = item.select_one("span.homepage-product-num")
                name_span = item.select_one("span.homepage-product-name")
                
                if not name_span:
                    continue
                
                num_text = num_span.get_text(strip=True) if num_span else ""
                name_text = name_span.get_text(strip=True)
                
                skip_terms = ['tables', 'view all', 'legislative history']
                if any(term in name_text.lower() for term in skip_terms):
                    continue
                
                title_text = f"{num_text} {name_text}" if num_text else name_text
                href = item.get('href', '')
                
                if not href:
                    continue
                
                if href.startswith('/'):
                    parsed = urlparse(self.browser.current_url)
                    full_url = f"{parsed.scheme}://{parsed.netloc}{href}"
                else:
                    full_url = urljoin(self.browser.current_url, href)
                
                titles[title_text] = full_url
            
            print(f"Extracted {len(titles)} titles from homepage layout")
        
        else:
            print("Checking for TOC-style layout")
            
            toc_list = None
            for selector in ["ul.toc.from-product", "ul.toc", "ul.from-product"]:
                toc_list = self.soup.select_one(selector)
                if toc_list:
                    break
            
            if not toc_list:
                print("Error: No TOC or homepage layout found")
                return titles
            
            title_items = toc_list.select("li.tocItem.level2.node-operational")
            if not title_items:
                title_items = toc_list.select("li.tocitem.level2.node-operational")
            
            for item in title_items:
                link = item.select_one("a")
                if not link:
                    continue
                
                num_span = link.select_one("span.num")
                name_span = link.select_one("span.name")
                
                if num_span and name_span:
                    title_text = f"{num_span.get_text(strip=True)} {name_span.get_text(strip=True)}"
                else:
                    title_text = link.get_text(strip=True)
                
                href = link.get('href', '')
                
                if 'node-reserved' in item.get('class', []):
                    continue
                
                skip_terms = ['tables', 'city officers', 'ordinance']
                if any(term in title_text.lower() for term in skip_terms):
                    continue
                
                if href.startswith('/'):
                    parsed = urlparse(self.browser.current_url)
                    full_url = f"{parsed.scheme}://{parsed.netloc}{href}"
                else:
                    full_url = urljoin(self.browser.current_url, href)
                
                titles[title_text] = full_url
            
            print(f"Extracted {len(titles)} titles from TOC layout")
        
        return titles

    def scrape_municipal_codes_chapters(self) -> dict[str, str]:
        """Extract chapters from a title page"""
        print("Scraping municipal.codes chapters...")
        self.wait_for_municipal_codes_load()
        
        chapters = {}
        
        inner_toc = None
        for selector in ["ul.toc.from-level2", "ul.toc.from-level3", "article ul.toc"]:
            inner_toc = self.soup.select_one(selector)
            if inner_toc:
                break
        
        if inner_toc:
            section_items = inner_toc.select("li.tocItem, li.tocitem")
            
            for item in section_items:
                link = item.select_one("a") if item.name != 'a' else item
                if not link:
                    continue
                
                chapter_text = link.get_text(strip=True)
                href = link.get('href', '')
                
                if not chapter_text or not href or len(chapter_text) < 2:
                    continue
                
                if href.startswith('/'):
                    parsed = urlparse(self.browser.current_url)
                    full_url = f"{parsed.scheme}://{parsed.netloc}{href}"
                else:
                    full_url = urljoin(self.browser.current_url, href)
                
                chapters[chapter_text] = full_url
        
        print(f"Extracted {len(chapters)} chapters")
        return chapters

    def scrape_municipal_codes_sections(self) -> dict[str, str]:
        """Extract sections from a chapter page"""
        print("Scraping municipal.codes sections...")
        self.wait_for_municipal_codes_load()
        sections = {}
        
        toc_list = None
        for selector in ["ul.toc.from-level4.to-level6", "ul.toc.from-level4", "ul.toc"]:
            toc_list = self.soup.select_one(selector)
            if toc_list:
                break
        
        if not toc_list:
            print("No sections TOC found - this city may not have section-level structure")
            return sections
        
        section_items = toc_list.select("li.tocItem.level6, li.tocitem.level6")
        
        for item in section_items:
            link = item.select_one("a")
            if not link or not link.get("href"):
                continue
            
            num_span = link.select_one("span.num")
            name_span = link.select_one("span.name")
            
            if num_span and name_span:
                section_text = f"{num_span.get_text(strip=True)} {name_span.get_text(strip=True)}"
            else:
                section_text = link.get_text(strip=True)
            
            if len(section_text) < 3:
                continue
            
            href = link.get("href")
            section_url = href if href.startswith("http") else urljoin(self.browser.current_url, href)
            
            sections[section_text] = section_url
        
        print(f"Extracted {len(sections)} sections")
        return sections

    def scrape_municipal_codes_text(self) -> str:
        """Extract actual ordinance text from the current page"""
        print("Scraping municipal.codes text content...")
        self.wait_for_municipal_codes_load()
        
        current_url = self.browser.current_url
        section_id = current_url.rstrip('/').split('/')[-1]
        
        print(f"Looking for article with id='{section_id}'")
        
        article = self.soup.find('article', id=section_id)
        
        if not article:
            article = self.soup.find('article', class_='level6')
        if not article:
            article = self.soup.find('article', class_='level4')
        if not article:
            article = self.soup.find('article', class_='level2')
        
        if not article:
            print("No article content found")
            return ""
        
        paragraphs = []
        for p in article.find_all('p'):
            if p.find_parent('ul', class_='toc') or 'tocHeading' in p.get('class', []):
                continue
            
            text = p.get_text(strip=True)
            if text:
                paragraphs.append(text)
        
        text = ' '.join(paragraphs)
        print(f"Extracted {len(text)} characters from {len(paragraphs)} paragraphs")
        
        return text

    # ============================================================================
    # UNIFIED SCRAPING METHODS (PLATFORM-AGNOSTIC)
    # ============================================================================

    def scrape_titles(self) -> dict[str, str]:
        """
        Scrape titles from current page (platform-agnostic)
        Automatically detects platform and calls appropriate method
        """
        platform = self.detect_platform()
        print(f"Scraping titles using {platform} platform...")
        
        if platform == "ecode360":
            return self.scrape_ecode360_items()
        elif platform == "codepublishing":
            return self.scrape_codepublishing_items()
        elif platform == "municipal_codes":
            return self.scrape_municipal_codes_titles()
        else:
            print(f"Warning: Unknown platform {platform}")
            return {}

    def scrape_chapters(self) -> dict[str, str]:
        """
        Scrape chapters from current page (platform-agnostic)
        Automatically detects platform and calls appropriate method
        """
        platform = self.detect_platform()
        print(f"Scraping chapters using {platform} platform...")
        
        if platform == "ecode360":
            return self.scrape_ecode360_items()
        elif platform == "codepublishing":
            return self.scrape_codepublishing_items()
        elif platform == "municipal_codes":
            return self.scrape_municipal_codes_chapters()
        else:
            print(f"Warning: Unknown platform {platform}")
            return {}

    def scrape_sections(self) -> dict[str, str]:
        """
        Scrape sections from current page (platform-agnostic)
        Automatically detects platform and calls appropriate method
        """
        platform = self.detect_platform()
        print(f"Scraping sections using {platform} platform...")
        
        if platform == "ecode360":
            return self.scrape_ecode360_items()
        elif platform == "codepublishing":
            return self.scrape_codepublishing_items()
        elif platform == "municipal_codes":
            return self.scrape_municipal_codes_sections()
        else:
            print(f"Warning: Unknown platform {platform}")
            return {}

    def scrape_text(self) -> str:
        """
        Scrape text content from current page (platform-agnostic)
        Automatically detects platform and calls appropriate method
        """
        platform = self.detect_platform()
        print(f"Scraping text using {platform} platform...")
        
        if platform == "ecode360":
            return self.scrape_ecode360_text()
        elif platform == "codepublishing":
            return self.scrape_codepublishing_text()
        elif platform == "municipal_codes":
            return self.scrape_municipal_codes_text()
        else:
            print(f"Warning: Unknown platform {platform}")
            return ""

    def contains_children(self) -> bool:
        """
        Check if current page has child elements (platform-agnostic)
        Useful for conditional navigation
        """
        platform = self.detect_platform()
        
        if platform == "ecode360":
            items = self.scrape_ecode360_items()
            return len(items) > 0
        elif platform == "codepublishing":
            # Check mainContent for TOC links
            main_content = self.soup.select_one("div#mainContent")
            if main_content:
                links = main_content.select("p.CHTOC a, p.CiteTOC a")
                if links:
                    return True
            # Fallback to browseCode
            browse = self.soup.select_one("div#browseCode")
            if browse:
                links = browse.select("a[href*='#!/']")
                return len(links) > 0
            return False
        elif platform == "municipal_codes":
            toc = self.soup.select_one("ul.toc")
            if toc:
                items = toc.select("li.tocItem, li.tocitem")
                return len(items) > 0
            return False
        else:
            return False

    def close(self):
        """Close the browser and clean up resources"""
        if self.browser:
            self.browser.quit()
            print("Browser closed")


# ============================================================================
# DEMO METHODS 
# ============================================================================

def demo_export_ca_cities():
    """
    DEMO 1: Export all California municipalities to JSON (run this first!)
    Creates: generalcode_munis.json
    """
    print("\n=== DEMO 1: Export CA Municipalities ===")
    crawler = GeneralCodeCrawler()
    crawler.export_munis(states_to_scrape=['ca'])
    crawler.close()


def demo_navigate_berkeley():
    """
    DEMO 2: Navigate Berkeley's code structure
    Shows: How to navigate titles → chapters → text
    """
    print("\n=== DEMO 2: Navigate Berkeley ===")
    
    # Load cities from JSON
    with open('generalcode_munis.json', 'r') as f:
        cities = json.load(f)['ca']['municipalities']
    
    crawler = GeneralCodeCrawler()
    crawler.go_to_city(cities['berkeley'])
    
    # Get titles
    titles = crawler.scrape_titles()
    print(f"✓ Found {len(titles)} titles")
    
    # Navigate to first title
    first_title = list(titles.keys())[0]
    print(f"✓ Navigating to: {first_title}")
    crawler.go_to_city(titles[first_title])
    
    # Check for children
    if crawler.contains_children():
        chapters = crawler.scrape_chapters()
        print(f"✓ Found {len(chapters)} chapters")
    else:
        text = crawler.scrape_text()
        print(f"✓ Extracted {len(text)} characters of text")
    
    crawler.close()


def demo_scrape_text():
    """
    DEMO 3: Extract ordinance text from a specific section
    Shows: How to navigate deep and extract text
    """
    print("\n=== DEMO 3: Scrape Ordinance Text ===")
    
    with open('generalcode_munis.json', 'r') as f:
        cities = json.load(f)['ca']['municipalities']
    
    crawler = GeneralCodeCrawler()
    crawler.go_to_city(cities['berkeley'])
    
    # Navigate through structure
    titles = crawler.scrape_titles()
    first_title_url = list(titles.values())[0]
    crawler.go_to_city(first_title_url)
    
    if crawler.contains_children():
        chapters = crawler.scrape_chapters()
        first_chapter_url = list(chapters.values())[0]
        crawler.go_to_city(first_chapter_url)
    
    # Extract text
    text = crawler.scrape_text()
    print(f"✓ Extracted {len(text)} characters")
    print(f"✓ Preview: {text[:200]}...")
    
    # Save to file
    with open('demo_output.txt', 'w', encoding='utf-8') as f:
        f.write(text)
    print("✓ Saved to demo_output.txt")
    
    crawler.close()


def demo_batch_cities():
    """
    DEMO 4: Process multiple cities quickly
    Shows: Efficient batch processing
    """
    print("\n=== DEMO 4: Batch Scrape Multiple Cities ===")
    
    with open('generalcode_munis.json', 'r') as f:
        all_cities = json.load(f)['ca']['municipalities']
    
    # Process first 3 cities
    test_cities = list(all_cities.items())[:3]
    results = {}
    
    crawler = GeneralCodeCrawler()
    
    for city_name, city_url in test_cities:
        print(f"\n  Processing {city_name}...")
        crawler.go_to_city(city_url)
        titles = crawler.scrape_titles()
        results[city_name] = {
            'platform': crawler.detect_platform(),
            'title_count': len(titles)
        }
        print(f"  ✓ {len(titles)} titles on {results[city_name]['platform']}")
    
    crawler.close()
    
    # Summary
    print("\n=== SUMMARY ===")
    for city, data in results.items():
        print(f"{city}: {data['title_count']} titles ({data['platform']})")


def demo_platform_detection():
    """
    DEMO 5: Test multi-platform support
    Shows: Automatic platform detection
    """
    print("\n=== DEMO 5: Platform Detection ===")
    
    with open('generalcode_munis.json', 'r') as f:
        all_cities = json.load(f)['ca']['municipalities']
    
    # Test a few cities
    crawler = GeneralCodeCrawler()
    
    for city_name, city_url in list(all_cities.items())[:5]:
        platform = crawler.detect_platform(city_url)
        print(f"{city_name:20} → {platform}")
    
    crawler.close()


# ============================================================================
# MAIN - Run Demos
# ============================================================================

if __name__ == "__main__":
    # FIRST TIME? Run this to create the cache:
    demo_export_ca_cities()
    
    # Then uncomment any of these to test:
    # demo_navigate_berkeley()
    # demo_scrape_text()
    # demo_batch_cities()
    # demo_platform_detection()