import scrapers.municode_scraper as municode
import os
import random
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE')
MAX_BATCH_AMOUNT = 4 # maximum number of articles/titles or whatever you want ordered

def log(text):
    with open("log.txt", "a", encoding="utf-8") as f: # for testing purposes
        f.write(text)

def thinking_query(client, prompt, thinking_budget=-1):
    log(f"Prompt:\n{prompt}\n-------------------\n")
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
    if len(result["think"]):
        log(f"Thinking:\n{result["think"]}\n")
    log(f"Response:\n{result["response"]}\n-------------------\n")
    return result

def key_list(dict, seperator=", "):
    result = ""
    for key in dict:
        result += key + seperator
    result = result[:-(len(seperator))]
    return result

def random_keys(dict, num):
    return key_list(random.sample(list(dict.keys()), num), '\n')

def answer_from_chapters(muni_nav, client, query, title, depth=2, definitions=""):
    muni_chapters = muni_nav.scrape_codes(depth)
    definitions_chapter_link = definitions
    for name in muni_chapters:
        if "definition" in name.lower():
            definitions_chapter_link = muni_chapters[name]
            muni_chapters.pop(name)
            break

    chapter_list = key_list(muni_chapters)
    batch_size = min(len(muni_chapters), MAX_BATCH_AMOUNT)
    random_chapters = random_keys(muni_chapters, batch_size)

    prompt = f"""You are a helpful city policy analyst. Select {batch_size} of the following chapters/articles/sections that best match this query: "{query}". Reply only with the {batch_size} chapters/articles/sections names with no extra spaces, punctuation, only the exact names of the chapters/articles/sections in a new-line separated list order from best to worst (most relevant to least relevant) with no modification. If none are relevant, reply with ONLY the word "[NONE]" in all caps with square brackets.\nHere is the list of chapters/articles/sections for {title}:\n{chapter_list}.\n\nExample: {random_chapters}"""

    response = thinking_query(client, prompt, 0)
    if "[NONE]" in response["response"]:
        return "[NONE]"
    selected_chapters = response["response"].split('\n')
    for attempt in range(len(selected_chapters)):
        current_chapter = selected_chapters[attempt]
        if muni_chapters[current_chapter]:
            muni_nav.go(muni_chapters[current_chapter])
            response = ""
            if muni_nav.contains_child():
                response = answer_from_chapters(muni_nav, client, query, title, depth + 1, definitions_chapter_link)
            else:
                prompt = f"""You are a helpful city policy analyst. Answer the following question from the link {muni_chapters[current_chapter]}{f", with the definitions of terms stored here:{definitions_chapter_link}" if definitions_chapter_link else ""}. If the answer based on the information from the link is ambiguous, explain your findings. If there is no single answer, provide all the answers you find and the cases where each answer would apply. If the link does not contain any answer/answers, return ONLY the word “[NONE]”. Question: {query}"""
                response = thinking_query(client, prompt, -1)["response"]
            if not "[NONE]" in response:
                return response

def answer(muni_nav, client, query):
    muni_titles = muni_nav.scrape_titles()
    titles_list = key_list(muni_titles)
    batch_size = min(len(muni_titles), MAX_BATCH_AMOUNT)
    random_titles = random_keys(muni_titles, batch_size)

    prompt = f"""You are a helpful city policy analyst. Select {batch_size} of the following titles/chapters that best match this query: "{query}". Reply only with the {batch_size} title/chapter names with no extra spaces, punctuation, only the exact names of the titles/chapters in a new-line separated list order from best to worst (most relevant to least relevant) with no modification. DO NOT INCLUDE SECTIONS THAT ARE NOT RELEVANT. For example, don't include the "summary history table", "dispostion table" or the "city municipal code" sections.\nHere is the list of sections for the municipality:\n\n{titles_list}.\n\nExample of response format: {random_titles}"""

    response = thinking_query(client, prompt, 0)
    selected_titles = response["response"].split('\n')
    for attempt in range(len(selected_titles)):
        current_title = selected_titles[attempt]
        if muni_titles[current_title]:
            muni_nav.go(muni_titles[current_title])
            get_answer = answer_from_chapters(muni_nav, client, query, current_title)
            if not "[NONE]" in get_answer:
                return get_answer

def main():
    state = "california"
    muni = "tracy"
    query = "What's the minimum lot size for a residential care facility?"
    client = genai.Client(api_key=GOOGLE_API_KEY)
    municode_nav = municode.MuniCodeCrawler() # open crawler
    muni_states = municode_nav.scrape_states() # grab states
    state = state or input("State: ").lower()
    with open("log.txt", "w", encoding="utf-8") as f: # for testing purposes
        f.write(f"State: {state}\n\n")
    municode_nav.go(muni_states[state]) # go to selected state
    muni_cities = municode_nav.scrape_cities() # grab cities
    for city in muni_cities:
        print(city)
    muni = muni or input("Municipality: ").lower()
    log(f"Municipality: {muni}\n\n")
    municode_nav.go(muni_cities[muni]) # go to selected city
    query = query or input("Question: ")
    log(f"Question: {query}\n-------------------\n")
    answer(municode_nav, client, query)
    


if __name__ == "__main__":
    main()