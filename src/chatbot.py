import scrapers.municode_scraper
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE')


