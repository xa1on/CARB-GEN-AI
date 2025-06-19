import scrapers.municode_scraper as municode
import os
import random
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE')
MAX_BATCH_AMOUNT = 4 # maximum number of articles/titles or whatever you want ordered

def thinking_query(client, prompt, thinking_budget=-1):
    result = {
        "think": "",
        "response": "" 
    }
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=thinking_budget
            )
        )
    )
    for part in response.candidates[0].content.parts:
        if not part.text:
            continue
        if part.thought:
            result["think"] += part.text
        else:
            result["response"] += part.text
    return result

def key_list(dict, seperator=", "):
    result = ""
    for key in dict:
        result += key + seperator
    result = result[:-(len(seperator))]
    return result

def random_keys(dict, num):
    return key_list(random.sample(list(dict.keys()), num), '\n')

def log(text):
    with open("log.txt", "a", encoding="utf-8") as f: # for testing purposes
        f.write(text)


def main():
    client = genai.Client(api_key=GOOGLE_API_KEY)
    municode_nav = municode.MuniCodeCrawler() # open crawler
    muni_states = municode_nav.scrape_states() # grab states
    state = input("State: ").lower()
    with open("log.txt", "w", encoding="utf-8") as f: # for testing purposes
        f.write(f"State: {state}\n\n")
    municode_nav.go(muni_states[state]) # go to selected state
    muni_cities = municode_nav.scrape_cities() # grab cities
    for city in muni_cities:
        print(city)
    muni = input("Municipality: ").lower()
    log(f"Municipality: {muni}\n\n")
    municode_nav.go(muni_cities[muni]) # go to selected city
    query = input("Question: ")
    log(f"Question: {query}\n-------------------\n")
    #municode_nav.go("https://library.municode.com/ca/yreka/codes/code_of_ordinances")
    muni_titles = municode_nav.scrape_titles() # grab titles
    titles_list = key_list(muni_titles)
    batch_size = min(len(muni_titles), MAX_BATCH_AMOUNT)
    random_titles = random_keys(muni_titles, batch_size) # random 3 titles to be used as example in the prompt
    #query = "What is the minimum lot size for a residential care facility?"
    prompt = f"""You are a helpful city policy analyst. Select {batch_size} of the following titles/chapters that best match this query: "{query}". Reply only with the {batch_size} title/chapter names with no extra spaces, punctuation, only the exact names of the titles/chapters in a new-line separated list order from best to worst (most relevant to least relevant) with no modification. DO NOT INCLUDE SECTIONS THAT ARE NOT RELEVANT. For example, don't include the "summary history table", "dispostion table" or the "city municipal code" sections.\nHere is the list of sections for the municipality:\n\n{titles_list}.\n\nExample of response format: {random_titles}"""
    log(f"{prompt}\n-------------------\n")

    response = thinking_query(client, prompt, 0)
    log(f"{response["think"]}\n")
    log(f"{response["response"]}\n-------------------\n")

    selected_titles = response["response"].split('\n')
    municode_nav.go(muni_titles[selected_titles[0]])
    muni_chapters = municode_nav.scrape_chapters()
    chapter_list = key_list(muni_chapters)
    batch_size = min(len(muni_chapters), MAX_BATCH_AMOUNT)
    random_chapters = random_keys(muni_chapters, batch_size)

    prompt = f"""You are a helpful city policy analyst. Select {batch_size} of the following chapters/articles that best match this query: "{query}". Reply only with the {batch_size} chapters/articles names with no extra spaces, punctuation, only the exact names of the chapters/articles in a new-line separated list order from best to worst (most relevant to least relevant) with no modification. If none are relevant, reply with ONLY the word "NONE" in all caps.\nHere is the list of chapters/articles for {selected_titles[0]}:\n{chapter_list}.\n\nExample: {random_chapters}"""
    log(f"{prompt}\n-------------------\n")

    response = thinking_query(client, prompt, 0)
    log(f"{response["think"]}\n")
    log(f"{response["response"]}\n-------------------\n")


if __name__ == "__main__":
    main()