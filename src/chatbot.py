"""
MUNICODE POLICY CHATBOT

Gets answer to policy questions based on municode using gemini.

Authors: Chenghao Li
Org: University of Toronto - School of Cities
"""

import scrapers.municode_scraper as municode
import os
import random
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE')
MAX_BATCH_AMOUNT = 4 # maximum number of articles/titles or whatever you want ordered
LOGGING = True
LOG_PROMPTS = False

# grounding with google search
GROUNDING = types.Tool(
    google_search=types.GoogleSearch()
)

# gemini configs
CONFIGS = {
    "thinking": types.GenerateContentConfig(
        system_instruction=[
            "You are a helpful municipality policy analyst bot.",
            """Try to use the links the user provides as much as possible and to not stray from the chapter/article/section unless for verification/grounding purposes. Extract relevant information, then ground your answer based on the extraced data. Only use search to check your work or ground your answer. Make sure you check your work. Use and make sure to cite specific sources when coming up with your reponse. Your sources must come from official government websites or from a municipal code website like municode.""",
            """Quotes must be exact content from inside the provided link with no modification.
Whenever you provide a quote, double check that the quote is within the link you specified. You must be able to specify one quote from within the provided links.
Try to keep the quotes short, only containing the most relevant and important points.""",
            """Please follow the formating tips below for the answer section:

1. Numeric question:
    - Answer should only contain the number and the units.
    - Example: 5 eggs

2. Binary question:
    - Answer should only contain "(YES)" for yes or "(NO)" for no and "(NONE)" for no answer found.
    - If you find no answers and encounter nothing of use, don't respond with "(NO)" and instead respond with "(NONE)"
    - DO NOT GIVE FULL RESPONSES
    - Example: Q: Are ADUs required to provide parking space? A: "(YES)"

3. Categorical Questions:
    - Provide ONLY the title or name requested.
    - Example: Q: Which entity acts as the special permit granting authority for multi-family housing? A: "Zoning Board of Appeals"

4. Ambiguous or multi-answer questions:
    - If the answer based on the information from the link is ambiguous or if there is no single answer, provide all the answers you find and the cases where each answer would apply.
    - Example: Q: how many eggs should I use to feed my family? A: "4 people: 6 eggs, 5 people: 7 eggs, 6+ people: 10 eggs"

5. No answer/answers found
    - Reply ONLY with the word “(NONE)” and nothing else.

Please provide one or more quotes from which you derived your answer in the format shown below:

"(ANSWER): 'answer'

(QUOTE): ```'exact quote from url 1'``` [(LINK)]('url 1')

(QUOTE): ```'exact quote from url 2'``` [(LINK)]('url 2')

..."

Examples: 
    Question: When/Where is it unlawful to solicit someone?
    Response: (ANSWER): Within 30 feet of entrance/exit of bank/credit union, check cashig business, automated teller machine, Parking lots or parking structures after dark, Public transportation vehicle

    (QUOTES): ```4.12.1230 - Prohibited solicitation at specific locations.

    (a) It shall be unlawful for any person to solicit within thirty (30) feet of any entrance or exit of a bank, credit union, check cashing business or within thirty (30) feet of an automated teller machine.

    (b) It shall be unlawful for any person to solicit in any public transportation vehicle.

    (c) Parking lots. It shall be unlawful for any person to solicit in any parking lot or parking structure any time after dark. "After dark" means any time for one-half hour after sunset to one-half hour before sunrise.``` [(LINK)](https://library.municode.com/ca/tracy/codes/code_of_ordinances?nodeId=TIT4PUWEMOCO_CH4.12MIRE_ART14SOAGSO)

    Question: Can I direct traffic if I'm not police?
    Response: (ANSWER): (NO)
    (QUOTES): ``` 3.08.050 - Direction of traffic.

No person, other than an officer of the Police Department or a person deputized or authorized by the Chief of Police or other person acting in any official capacity, or by authority of law shall direct or attempt to direct traffic by voice, hand or other signal.

(Prior code § 3-2.203)``` [(LINK)](https://library.municode.com/ca/tracy/codes/code_of_ordinances?nodeId=TIT3PUSA_CH3.08TRRE)
""",
            "Keep your responses clear and concise."
        ],
        thinking_config=types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=-1
        ),
        tools=[GROUNDING],
        temperature=0.05,
        topP=0.15
    ),
    "sorter": types.GenerateContentConfig(
        system_instruction=[
            """You are a helpful municipality policy analyst bot.""",
            """You will be asked to sort names of titles/chapters/articles/sections in terms of most relevant to least relevant from a list of names.""",
            """Terms like "inclusionary zoning", "density bonus", "commercial linkage fees", etc. typically belong in housing chapters."""
        ],
        temperature=0.05,
        topP=0.15
    )
}

MODELS = {
    "thinking": "gemini-2.5-flash",
    "fast": "gemini-2.0-flash-lite"
}

def log(text):
    """
    Log text into log.txt file for debugging/testing purposes

    :param text: input text
    :return:
    """
    if not LOGGING:
        return
    with open("log.md", "a", encoding="utf-8") as f:
        f.write(text)

def gemini_query(client, prompt, config, model):
    """
    Send thinking query to gemini

    :param client: gemini client
    :param prompt: prompt for gemini
    :param config: gemini config
    :return: dictionary containing "think" and "response"
    """

    try:
        result = {
            "think": "",
            "response": "" 
        }
        if LOG_PROMPTS:
            log(f"### Prompt:\n\n{prompt}\n\n-------------------\n\n")

        # gemini config

        # incremental response
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=prompt,
            config=config
        ):
            if chunk.candidates and chunk.candidates[0].content.parts:
                for part in chunk.candidates[0].content.parts:
                    if not part or not part.text:
                        continue
                    if part.thought:
                        if not result["think"]:
                            log(f"### Thinking:\n\n")
                        result["think"] += part.text
                        log(part.text)
                    else:
                        if not result["response"]:
                            log(f"### Response:\n\n")
                        result["response"] += part.text
                        log(part.text)
        log("\n\n-------------------\n\n")
        return result
    except ServerError as e:
        log(f"\n\n#### ERROR OCCURED ({e}). RETRYING IN 10 SECONDS\n\n")
        time.sleep(10)
        log(f"#### RETRYING...\n\n")
        return gemini_query(client, prompt, config, model)

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

def answer_from_chapters(muni_nav, client, muni, url, query, title, depth=2, definitions=""):
    """
    Accesses the chapter/article/section recursively until answer is found to query

    :param muni_nav: municode scraper object
    :param client: gemini client
    :param muni: current municipality
    :param url: url to page to scrape from
    :param query: input question
    :param title: title chapter is child of
    :param depth: depth of chapter. (2-usually the chapter, 3-usually the article/section)
    :param definitions: link to the most relevant definitions section (usually empty)
    :return: prompt, answer to query
    """

    muni_chapters = None
    while not muni_chapters:
        muni_nav.go(url)
        muni_chapters = muni_nav.scrape_codes(depth) # get chapters/articles
        if not muni_chapters:
            log(f"#### FAILED. RETRYING...\n\n")
    definitions_chapter_link = definitions
    for name in muni_chapters: # find definitions, if exists.
        if "definition" in name.lower():
            definitions_chapter_link = muni_chapters[name]
            muni_chapters.pop(name)
            break

    chapter_list = key_list(muni_chapters)
    batch_size = min(len(muni_chapters), MAX_BATCH_AMOUNT) # number of "relevant" chapters/articles we want
    random_chapters = random_keys(muni_chapters, batch_size)

    log(f"## Selecting chapter/article under {title}...\n\n")

    prompt = f"""Select {batch_size} of the following chapters/articles/sections that best match this query: "{query}". Reply only with the {batch_size} chapters/articles/sections names with no extra spaces, punctuation, only the exact names of the chapters/articles/sections in a new-line separated list order from best to worst (most relevant to least relevant) with no modification. Avoid anything that is repealed or obsolete. If none are relevant, reply with ONLY the word "(NONE)" in all caps with square brackets.
Here is the list of chapters/articles/sections for {title}:
{chapter_list}.
    
Example: {random_chapters}"""

    response = gemini_query(client, prompt, CONFIGS["sorter"], MODELS["fast"]) # prompt for relevant chapters/articles

    if not response["response"] or "(NONE)" in response["response"]: # if theres no relevant chapters/articles, go back so we can check a different title
        return prompt, {"response": "(NONE)"}

    selected_chapters = response["response"].split('\n') # seperate into list so we can loop through from from first to last
    for attempt in range(len(selected_chapters)):
        current_chapter = selected_chapters[attempt]
        if current_chapter in muni_chapters:
            log(f"### Navigating to [{current_chapter}]({muni_chapters[current_chapter]})\n\n")
            response = {}
            if muni_nav.contains_child(): # if theres child entries, we'll use those instead to get our answer
                prompt, response = answer_from_chapters(muni_nav, client, muni, muni_chapters[current_chapter], query, title, depth + 1, definitions_chapter_link)
            else:
                log("## ANSWERING\n\n")
                prompt = f"""Answer the following question from the link {muni_chapters[current_chapter]}{f", with the definitions of terms stored here:{definitions_chapter_link}" if definitions_chapter_link else ""} for the municipality of {muni}.

Question: {query}"""
                response = gemini_query(client, prompt, CONFIGS["thinking"], MODELS["thinking"]) # prompt for answer to query
            if not "(NONE)" in response["response"]: # If no answer, continue, else, continue and look at next chapter/article
                return prompt, response
    return

def answer(muni_nav, client, muni, url, query):
    """
    Accesses the titles recursively until answer is found to query

    :param muni_nav: municode scraper object
    :param client: gemini client
    :param muni: current municipality
    :param url: url to the municode website
    :param query: input question
    :return: answer to query
    """
    muni_titles = None
    while not muni_titles:
        muni_nav.go(url)
        muni_titles = muni_nav.scrape_titles() # all titles
        if not muni_titles:
            log(f"#### FAILED, RETRYING ...\n\n")
    titles_list = key_list(muni_titles)
    batch_size = min(len(muni_titles), MAX_BATCH_AMOUNT) # number of "relevant" titles we want
    random_titles = random_keys(muni_titles, batch_size)

    log("## Selecting title...\n\n")

    prompt = f"""Select {batch_size} of the following titles/chapters that best match this query: "{query}". Reply only with the {batch_size} title/chapter names with no extra spaces, punctuation, only the exact names of the titles/chapters in a new-line separated list order from best to worst (most relevant to least relevant) with no modification. DO NOT INCLUDE SECTIONS THAT ARE NOT RELEVANT. For example, don't include the "summary history table", "dispostion table" or the "city municipal code" sections.
Here is the list of sections for the municipality:
{titles_list}.
    
    Example of response format: {random_titles}"""

    response = gemini_query(client, prompt, CONFIGS["sorter"], MODELS["fast"]) # prompting to get relevant titles

    selected_titles = response["response"].split('\n') # seperate into list so we can loop through from from first to last
    for attempt in range(len(selected_titles)):
        current_title = selected_titles[attempt]
        if muni_titles[current_title]:
            log(f"### Navigating to [{current_title}]({muni_titles[current_title]})\n\n")
            get_answer = answer_from_chapters(muni_nav, client, muni, muni_titles[current_title], query, current_title) # get relevant chapter/article, then retrieve answer
            print(get_answer)
            if not "(NONE)" in get_answer[1]["response"]: # if answer is none, continue, otherwise, return answer.
                return get_answer
    return

def main():
    state = "california"
    muni = "milpitas"
    query = "When it comes to affordable housing, are there Inclusionary Zoning rules?" 
    client = genai.Client(api_key=GOOGLE_API_KEY)
    municode_nav = municode.MuniCodeCrawler() # open crawler
    if LOGGING:
        with open("log.md", "w", encoding="utf-8") as f: # for testing purposes
            f.write(f"# LOG\n\n")

    muni_states = None
    while not muni_states:
        muni_states = municode_nav.scrape_states() # grab states
        if not muni_states:
            log(f"#### FAILED TO GET STATES, RETRYING.\n\n")
            municode_nav.go()
    
    state = state or input("State: ").lower()
    log(f"#### State: {state}\n\n")
    
    muni_cities = None
    while not muni_cities:
        municode_nav.go(muni_states[state]) # go to selected state
        muni_cities = municode_nav.scrape_cities() # grab cities
        if not muni_cities:
            log(f"#### FAILED TO GET CITIES, RETRYING ...\n\n")
    #for city in muni_cities:
        #print(city)
    
    muni = muni or input("Municipality: ").lower()
    log(f"#### Municipality: {muni}\n\n")

    query = query or input("Question: ")
    log(f"#### Question: {query}\n\n-------------------\n\n")
    response = answer(municode_nav, client, muni, muni_cities[muni], query) # find relevant chapter/article and get answer
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=response[0])
            ]
        ),
        types.Content(
            role="model",
            parts=[
                types.Part.from_text(text=response[1]["think"]),
                types.Part.from_text(text=response[1]["response"])
            ]
        )
    ]
    while (True):
        prompt = input("Respond: ")
        log(f"### User asks:\n\n{prompt}\n\n-------------------\n\n")
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt)
                ]
            )
        )
        response = gemini_query(client, contents, CONFIGS["thinking"], MODELS["thinking"])
        contents.append(
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text=response["think"]),
                    types.Part.from_text(text=response["response"])
                ]
            )
        )

    


if __name__ == "__main__":
    main()