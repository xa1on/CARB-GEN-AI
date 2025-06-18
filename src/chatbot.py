import scrapers.municode_scraper as municode
import os
import random
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE')

def main():
    client = genai.Client(api_key=GOOGLE_API_KEY)
    municode_nav = municode.MuniCodeCrawler() # open crawler
    muni_states = municode_nav.scrape_states() # grab states
    state = input("State: ").lower()
    municode_nav.go(muni_states[state]) # go to selected state
    muni_cities = municode_nav.scrape_cities() # grab cities
    for city in muni_cities:
        print(city)
    muni = input("Municipality: ").lower()
    municode_nav.go(muni_cities[muni]) # go to selected city
    #municode_nav.go("https://library.municode.com/ca/yreka/codes/code_of_ordinances")
    muni_titles = municode_nav.scrape_titles() # grab titles
    titles_list = ""
    for title in muni_titles:
        titles_list += title + ", " # creating a string list of titles to tell mr.gemini
    titles_list = titles_list[:-2] # remove extra comma created by for loop
    random_titles = random.sample(list(muni_titles.keys()), 3) # random 3 titles to be used as example in the prompt
    query = input("Question: ")
    #query = "What is the minimum lot size for a residential care facility?"
    prompt = f"""You are a helpful city policy analyst. Select 3 of the following titles/chapters that best match this query: "{query}". Reply only with the 3 title/chapter names with no extra spaces, punctuation, only the exact names of the titles/chapters in a new-line separated list order from best to worst with no modification. DO NOT INCLUDE SECTIONS THAT ARE NOT RELEVANT. For example, don't include the "summary history table", "dispostion table" or the "city municipal code" sections. Here is the list of sections for the municipality:\n{titles_list}.\nExample: {random_titles[0]}\n{random_titles[1]}\n{random_titles[2]}"""
    print(prompt)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True
            )
        )
    )
    
    for part in response.candidates[0].content.parts:
        if not part.text:
            continue
        if part.thought:
            print(f"think: {part.text}\n")
        else:
            print(f"answer: {part.text}\n")

if __name__ == "__main__":
    main()