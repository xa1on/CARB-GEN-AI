"""
GENERAL CODE SCRAPER

Scrapes generalcode.com/library for municipality codes
Handles the multi-domain architecture where cities redirect to municipal.codes subdomains

Authors: Allen Lopez & Ariana Based on Chenghao Li's Municode scraper
Org: University of Toronto - School of Cities
"""

import time
import json
from bs4 import BeautifulSoup, Tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.action_chains import ActionChains
from urllib.parse import urlparse, urljoin  # Fixed typo
import re

# loading Detection & basic config
SNAPSHOTS_DIR = "snapshots"
LOADING_CSS_SELECTOR = "#mapwrapper"        # wait for map container instead of spinner like municode
TIMEOUT = 120

# Navigation selectors
STATE_CSS = "text[id^='state_']"     # SVG text elements for state selection on map but not needed for dropdown
CITY_CSS = "a.codeLink"              # HTML links for city selection
CODE_CSS = "a.has-preview"                          # Clickable chapters/sections

# Content Structure
BODY_CSS = "main.site-main"                       # Main content container
TEXT_CSS = "title"                                # title extraction

# search functionality
SEARCH_CSS = "input.wp-block-search__input"          # Search input box
SEARCH_RESULT_CSS = "a.title"                          # Search results links
SEARCH_RESULT_COUNT_CSS = "b"                        # "60&nbsp;items" count


# CSS selectors for eCode360
ECODE360_TITLE_CSS = "span.titleTitle"           # Titles and chapters
ECODE360_SECTION_CSS = "span.titleNumbers"       # Section ranges
ECODE360_TEXT_CSS = "div.para"                   # Ordinance text content
ECODE360_NAV_ARROW = "span[class*='arrow']"      # Expandable arrows (if needed)


ECODE360_DEPTH = {
    "Titles": 0,          # Title 1 "General Provisions", Title 2 "Administration"
    "Chapters": 1,        # Chapter 1.01, Chapter 1.02, etc.
    "Sections": 2         # § 1.01.010, § 1.01.020, etc.
}


# Helper function - should be outside class
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



class GeneralCodeCrawler:
    home_url: str = "https://www.generalcode.com/library/"
    def __init__(self, starting_url: str=home_url):
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
        "Wait for element to be visible and update soup "

        self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, CSS)))
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self
    
    def go(self, url):
        """
        Goes to specified url and waits for loading to finish 

        :param url: URL to navigate to 
        :return: self for method chaining
        """
        self.browser.get(url)
        self.wait_visibility(LOADING_CSS_SELECTOR)
        return self
    

    
    def scrape_title(self) -> str:
        """ Extract the title of the current page 
        : return: page title as string
        """
        time.sleep(1)  
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        title = self.soup.select_one("title")
        return title.text.strip() if title else "No title found"
    
    
    def scrape_states_fixed(self) -> dict[str, str]:
        """
        Scrape states from the dropdown menu - FIXED VERSION
        """
        self.wait_visibility(LOADING_CSS_SELECTOR)
        
        # Look for the dropdown content container
        dropdown_links = self.soup.select("div.dropdown-content a[href*='/text-library/#']")
        
        states = {}
        for link in dropdown_links:
            state_name = link.text.strip()
            state_href = link.get('href', '')
            
            # Extract state abbreviation from href (e.g., "/text-library/#CA" -> "CA")
            if state_href and '#' in state_href:
                state_abbrev = state_href.split('#')[1].upper()
                if state_name and state_abbrev:
                    states[state_abbrev.lower()] = state_href
        
        return states

        
    def scrape_munis_with_scroll(self, state_abbrev: str) -> dict[str, str]:
        """
        Scrape municipalities using dropdown - FIXED hover management for scrolling
        """
        try:
            # Step 1: Find the dropdown button
            ecodes_button = self.browser.find_element(By.CSS_SELECTOR, "button.dropbtn")
            
            # Step 2: Scroll just enough to make dropdown visible
            print("Scrolling dropdown into view...")
            self.browser.execute_script("window.scrollTo(0, 400);")  # Scroll down just a bit
            time.sleep(1)
            
            # Step 3: Hover over button to reveal dropdown
            print("Hovering over eCodes by State button...")
            ActionChains(self.browser).move_to_element(ecodes_button).perform()
            time.sleep(2)
            
            # Step 4: CRITICAL - Move hover to dropdown list to maintain it during scrolling
            print("Moving hover to dropdown list...")
            dropdown = self.browser.find_element(By.CSS_SELECTOR, "div.dropdown-content")
            ActionChains(self.browser).move_to_element(dropdown).perform()
            time.sleep(1)
            
            # Step 5: Map state abbreviation to full name
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
            
            # Step 6: Find and click state link - with maintained hover
            print(f"Looking for {state_name} link...")
            
            state_link = None
            try:
                # First try to find state in current view
                state_link = self.browser.find_element(By.XPATH, f"//div[@class='dropdown-content']//a[text()='{state_name}']")
                print(f"Found {state_name} in current view")
                
            except:
                # State not visible, scroll while maintaining hover on dropdown
                print(f"{state_name} not in current view, scrolling...")
                
                for scroll_attempt in range(5):
                    # Maintain hover on dropdown while scrolling
                    ActionChains(self.browser).move_to_element(dropdown).perform()
                    
                    # Scroll within dropdown
                    self.browser.execute_script("arguments[0].scrollTop += 150;", dropdown)
                    time.sleep(1)
                    
                    # Try to find state after scrolling
                    try:
                        state_link = self.browser.find_element(By.XPATH, f"//div[@class='dropdown-content']//a[text()='{state_name}']")
                        print(f"Found {state_name} after scroll attempt {scroll_attempt + 1}")
                        break
                    except:
                        continue
                
                if not state_link:
                    raise Exception(f"Could not find {state_name} after scrolling")
            
            print(f"Found {state_name} link: '{state_link.text}'")
            print(f"Is displayed: {state_link.is_displayed()}")
            
            # Step 7: Click the state link
            state_link.click()
            print(f"Clicked {state_name} link")
            
            # Step 8: Handle modal/iframe (same as before)
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
        
   ### ecode 360 platform
    # ========== FIXED eCODE360 METHODS ==========

    def wait_for_ecode360_load(self):
        """Wait for eCode360 page to fully load"""
        try:
            # Wait for loading spinner to disappear
            WebDriverWait(self.browser, 3).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.loading"))
            )
        except:
            pass  # No loading spinner present
        
        time.sleep(2)  # Additional wait for dynamic content
        self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        return self

    def go_to_city(self, url):
        """Navigate to city code page (works for any platform)"""
        print(f"Navigating to: {url}")
        self.browser.get(url)
        
        # Detect platform and wait accordingly
        if "ecode360.com" in url:
            self.wait_for_ecode360_load()
        else:
            time.sleep(3)
            self.soup = BeautifulSoup(self.browser.page_source, "html.parser")
        
        return self

    def scrape_ecode360_items(self) -> dict[str, str]:
        """
        Generic method to scrape all clickable items from eCode360 main content area
        Works for titles, chapters, and sections
        Returns: {item_name: item_url}
        """
        self.wait_for_ecode360_load()
        
        items = {}
        
        # Target the main content area
        content_area = self.soup.select_one("div#codeContent")
        
        if not content_area:
            print("Warning: Could not find main content area (div#codeContent)")
            return items
        
        # Find all clickable title links in the content area
        title_links = content_area.select("a.titleLink")
        
        print(f"Found {len(title_links)} clickable items in content area")
        
        for link in title_links:
            # Get the titleTitle span which contains the name
            title_span = link.select_one("span.titleTitle")
            
            if not title_span:
                continue
            
            # Get the data-guid attribute which we'll use to build the URL
            data_guid = title_span.get('data-guid', '')
            
            if not data_guid:
                continue
            
            # Extract the text (remove the section numbers part)
            title_text = title_span.get_text(strip=True)
            
            # Clean up the text - remove the section number range if present
            # e.g., "General Provisions (§ 1-2.1 – § 1-12.1)" -> "General Provisions"
            if '(' in title_text:
                title_text = title_text.split('(')[0].strip()
            
           # Build the URL using the data-guid
            # eCode360 URLs follow the pattern: https://ecode360.com/data-guid#data-guid
            item_url = f"https://ecode360.com/{data_guid}#{data_guid}"


            items[title_text] = item_url
            
        
        return items

    def scrape_ecode360_titles(self) -> dict[str, str]:
        """
        Scrape top-level titles from eCode360 page
        Returns: {title_name: title_url}
        """
        print("Scraping eCode360 titles/chapters...")
        return self.scrape_ecode360_items()

    def scrape_ecode360_chapters(self) -> dict[str, str]:
        """
        Scrape chapters from current eCode360 page
        Returns: {chapter_name: chapter_url}
        """
        print("Scraping eCode360 chapters/sections...")
        return self.scrape_ecode360_items()

    def scrape_ecode360_sections(self) -> dict[str, str]:
        """
        Scrape sections from current eCode360 chapter page
        Returns: {section_name: section_url}
        """
        print("Scraping eCode360 sections...")
        return self.scrape_ecode360_items()

    def scrape_ecode360_text(self) -> str:
        """
        Scrape ordinance text from current eCode360 section
        Returns: Text content
        """
        print("Scraping eCode360 text content...")
        self.wait_for_ecode360_load()
        
        result = ""
        
        # Target the main content area
        content_area = self.soup.select_one("div#codeContent")
        
        if not content_area:
            print("Warning: No content area found")
            return ""
        
        # Try multiple content selectors in order
        # 1. Definition text (for sections with term definitions)
        deftexts = content_area.select("div.deftext")
        if deftexts:
            print(f"Found {len(deftexts)} definition blocks")
            
            # REPLACE THIS SECTION:
            for deftext in deftexts:
                # Try to find the term name (it might be in various locations)
                parent = deftext.parent
                if parent:
                    term_link = parent.find("a", class_="termLink")
                    if term_link:
                        result += f"**{term_link.get_text(strip=True)}**\n"
                # Get the definition text
                text = stripped_splitter(deftext.get_text())
                if text:
                    result += text + "\n\n"
            # END REPLACEMENT
            
            return result.strip()
        
        # 2. Regular paragraphs
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
                text = stripped_splitter(para.get_text())
                if text:
                    result += text + "\n\n"
            print(f"Extracted {len(result)} characters of text")
            return result.strip()
        
        # 3. Fallback: get all text from content area
        text = content_area.get_text(separator="\n\n", strip=True)
        return text



    def contains_child_ecode360(self) -> bool:
        """
        Check if current eCode360 page has child elements
        """
        items = self.scrape_ecode360_items()
        return len(items) > 0
    

    def analyze_california_platforms(self) -> dict:
        """Analyze all California cities to determine platform distribution"""
        print("Analyzing platform distribution for California cities...")
        
        ca_cities = self.scrape_munis_with_scroll('ca')
        
        platforms = {
            "codepublishing": [],
            "municipal_codes": [],
            "ecode360": [],
            "generalcode": [],
            "other": []
        }
        
        print(f"Analyzing {len(ca_cities)} California cities...")
        
        for city_name, city_url in ca_cities.items():
            if "codepublishing.com" in city_url:
                platforms["codepublishing"].append({"name": city_name, "url": city_url})
            elif "municipal.codes" in city_url:
                platforms["municipal_codes"].append({"name": city_name, "url": city_url})
            elif "ecode360.com" in city_url:
                platforms["ecode360"].append({"name": city_name, "url": city_url})
            elif "generalcode.com" in city_url:
                platforms["generalcode"].append({"name": city_name, "url": city_url})
            else:
                platforms["other"].append({"name": city_name, "url": city_url})
        
        total = len(ca_cities)
        print(f"\n=== CALIFORNIA PLATFORM DISTRIBUTION ({total} cities) ===")
        
        for platform, cities in platforms.items():
            count = len(cities)
            percentage = (count / total) * 100
            print(f"{platform.upper()}: {count} cities ({percentage:.1f}%)")
            
            if cities:
                examples = [city['name'] for city in cities[:5]]
                print(f"  Examples: {examples}")
                if len(cities) > 5:
                    print(f"  ... and {len(cities) - 5} more")
        
        return platforms
        

    
    
        
    

# Mutliple different tests 

#distribution of platforms for generalcode
def test_platform_analysis():
    """Test platform distribution analysis for California"""
    scraper = GeneralCodeCrawler()
    platforms = scraper.analyze_california_platforms()
    return platforms

#scrape info from ecode360 platform
def test_ecode360_city(city_name: str):
    """Test eCode360 scraping for any city"""
    scraper = GeneralCodeCrawler()
    
    ca_cities = scraper.scrape_munis_with_scroll('ca')
    city_url = ca_cities.get(city_name)
    
    if not city_url:
        print(f"City '{city_name}' not found")
        return
    
    print(f"{city_name.title()} URL: {city_url}\n")
    scraper.go_to_city(city_url)
    
    print("=== EXTRACTING CHAPTERS/TITLES ===")
    chapters = scraper.scrape_ecode360_chapters()
    print(f"Found {len(chapters)} items\n")
    
    if chapters:
        regular_chapters = {k: v for k, v in chapters.items() 
                   if 'Charter' not in k and 'Municipal Code' not in k}
        
        if regular_chapters:
            first_chapter = list(regular_chapters.keys())[0]
            first_chapter_url = regular_chapters[first_chapter]
            print(f"=== NAVIGATING TO: {first_chapter} ===")
            scraper.go_to_city(first_chapter_url)
            
            print("=== EXTRACTING SECTIONS ===")
            sections = scraper.scrape_ecode360_sections()
            print(f"Found {len(sections)} sections\n")
            
            if sections:
                section_list = list(sections.items())
                if len(section_list) > 1:
                    second_section = section_list[1][0]
                    second_section_url = section_list[1][1]
                    print(f"=== NAVIGATING TO: {second_section} ===")
                    scraper.go_to_city(second_section_url)
                    
                    print("=== EXTRACTING TEXT ===")
                    text = scraper.scrape_ecode360_text()
                    print(f"Extracted {len(text)} characters")
                    if text:
                        print(f"\nPreview (first 500 chars):\n{text[:500]}...")

if __name__ == "__main__":
    test_platform_analysis()
