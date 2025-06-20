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
    """
    Log text into log.txt file for debugging/testing purposes

    :param text: input text
    :return:
    """

    with open("log.txt", "a", encoding="utf-8") as f:
        f.write(text)

def thinking_query(client, prompt, thinking_budget=-1, grounding=False):
    """
    Send thinking query to gemini

    :param client: gemini client
    :param prompt: prompt for gemini
    :param thinking_budget: number of tokens allocated for thinking (0: no thinking, -1: dynamic thinking)
    :return: dictionary containing "think" and "response"
    """

    log(f"Prompt:\n{prompt}\n-------------------\n")
    result = {
        "think": "",
        "response": "" 
    }
    grounding_tool = types.Tool(
        google_search=types.GoogleSearch()
    )
    generate_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=thinking_budget
        ),
        tools=[grounding_tool] if grounding else [],
        temperature=0.05,
        topP=0.15
    )
    for chunk in client.models.generate_content_stream(
        model="gemini-2.5-flash",
        contents=prompt,
        config=generate_config
    ):
        for part in chunk.candidates[0].content.parts:
            if not part.text:
                continue
            if part.thought:
                if not result["think"]:
                    log(f"Thinking:\n")
                result["think"] += part.text
                log(part.text)
            else:
                if not result["response"]:
                    log(f"Response:\n")
                result["response"] += part.text
                log(part.text)
    log("\n-------------------\n")
    return result

def key_list(dict, seperator=", "):
    """
    Takes dictionary/list and creates a string containing all the keys/elements in a list seperated with the seperator

    :param dict: input dictionary/list
    :param seperator: string to seperate each element
    :return: list as a string
    """

    result = ""
    for key in dict:
        result += key + seperator
    result = result[:-(len(seperator))]
    return result

def random_keys(dict, num):
    """
    Get a list of random keys from a dictionary.

    :param dict: input dictionary
    :param num: number of keys to sample
    :return: string containing the list of sampled keys each on a seperate line
    """

    return key_list(random.sample(list(dict.keys()), num), '\n')

def answer_from_chapters(muni_nav, client, query, title, depth=2, definitions=""):
    """
    Accesses the chapter/article/section recursively until answer is found to query

    :param muni_nav: municode scraper object
    :param client: gemini client
    :param query: input question
    :param title: title chapter is child of
    :param depth: depth of chapter. (2-usually the chapter, 3-usually the article/section)
    :param definitions: link to the most relevant definitions section (usually empty)
    :return: answer to query
    """

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

    prompt = f"""You are a helpful city policy analyst. Select {batch_size} of the following chapters/articles/sections that best match this query: "{query}". Reply only with the {batch_size} chapters/articles/sections names with no extra spaces, punctuation, only the exact names of the chapters/articles/sections in a new-line separated list order from best to worst (most relevant to least relevant) with no modification. Avoid anything that is repealed or obsolete. If none are relevant, reply with ONLY the word "[NONE]" in all caps with square brackets.\nHere is the list of chapters/articles/sections for {title}:\n{chapter_list}.\n\nExample: {random_chapters}"""

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
                prompt = f"""You are a helpful city policy analyst. Answer the following question from the link {muni_chapters[current_chapter]}{f", with the definitions of terms stored here:{definitions_chapter_link}" if definitions_chapter_link else ""}. Try to use the links provided as much as possible and to not stray from the chapter/article/section unless for verification/grounding purposes. Extract relevant information, then ground your answer based on the extraced data. Only use search to check your work or ground your answer. Make sure you check your work. Use and make sure to cite specific sources when coming up with your reponse.
                \nPlease follow the formating tips below:\nIf a numeric answer is asked for, reply ONLY with the number and units. If a yes/no question is asked, reply ONLY with "[YES]" for yes or "[NO]" for no and nothing else. If the answer based on the information from the link is ambiguous or if there is no single answer, provide all the answers you find and the cases where each answer would apply. For example: (Question: how many eggs should I use to feed my family? Answer: "4 people: 6 eggs, 5 people: 7 eggs, 6+ people: 10 eggs").If the link does not contain the answer/answers, reply ONLY with the word “[NONE]” and nothing else. Do not reply with anything not explicitly asked for. Keep your response short and within one line.

                \nQuestion: {query}"""
                response = thinking_query(client, prompt, 1024, True)["response"]
            if not "[NONE]" in response:
                return response

def answer(muni_nav, client, query):
    """
    Accesses the titles recursively until answer is found to query

    :param muni_nav: municode scraper object
    :param client: gemini client
    :param query: input question
    :return: answer to query
    """

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
    muni = "milpitas"
    query = "How many hamsters are you allowed to keep?"
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