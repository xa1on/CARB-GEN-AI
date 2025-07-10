"""
MUNICODE POLICY CHATBOT

Gets answer to policy questions based on municode using gemini.

Authors: Chenghao Li
Org: University of Toronto - School of Cities
"""

import scrapers.municode_scraper as municode
import os
import time
import json
import config.instruction as inst
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE') # google cloud api key
LOGGING = True # whether or not log.md is generated
LOG_PROMPTS = True # logs prompts generated

MUNICODE_MUNIS = "src/config/municode_munis.json"


# model options
MODELS = {
    "thinking": "gemini-2.5-flash",
    "fast": "gemini-2.0-flash-lite"
}

SORTER_QUERY_TEMPLATE = """Query: "{query}".
Here is the list of title/chapters/articles/sections:

{name_list}"""

RESPONSE_QUERY_TEMPLATE = """Answer the following question on the city/municipality of {muni} from the links provided below for the muni/city of {muni}:

{muni} Code {current_name}: {muni_code_url}

{additional_links}



Question: {query}\n Response: """

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
            "response": "",
        }
        if LOG_PROMPTS:
            if isinstance(prompt, str):
                log(f"### Prompt:\n\n{prompt}\n\n-------------------\n\n")
            else:
                log(f"### Prompt: \n\n{prompt[-1].parts[0].text}\n\n-------------------\n\n")

        # gemini config

        # incremental response
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=prompt,
            config=config
        ):
            if chunk.candidates:
                if chunk.candidates[0].content.parts:
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

def answer(muni_nav, client, muni, url, query, depth=0, definitions=""):
    """
    Accesses the title/chapter/article/section names recursively until answer is found to query

    :param muni_nav: municode scraper object
    :param client: gemini client
    :param muni: current municipality
    :param url: url to page to scrape from
    :param query: input question
    :param depth: depth of item. (0-title, 2-chapter, 3-article/section)
    :param definitions: link to the most relevant definitions section (usually empty)
    :return: prompt, answer to query, structured response
    """

    muni_names = None
    while not muni_names:
        muni_nav.go(url)
        muni_names = muni_nav.scrape_codes(depth) # get title/chapters/articles/section
        if not muni_names:
            log(f"#### FAILED. RETRYING...\n\n")
    definitions_link = definitions
    for name in muni_names: # find definitions, if exists.
        if "definition" in name.lower():
            definitions_link = muni_names[name]
            muni_names.pop(name)
            break

    name_list = key_list(muni_names)

    log(f"## Selecting title/chapters/articles/sections...\n\n")

    prompt = SORTER_QUERY_TEMPLATE.format(
        query=query,
        name_list=name_list
    )

    response = gemini_query(client, prompt, inst.CONFIGS["sorter"], MODELS["fast"]) # prompt for relevant title/chapters/articles/section

    response_json = json.loads(response["response"])
    response_json.sort(key=lambda x: x['relevance_rating'], reverse=True) # sorts the list based on relevance_rating

    if not len(response_json) or response_json[0]["relevance_rating"] < 4: # return None if none of the reponses are relevant enough
        return None, None, None

    for attempt in response_json:
        current_name = attempt["name"]
        if current_name in muni_names:
            log(f"### Navigating to [{current_name}]({muni_names[current_name]})\n\n")
            response = {}
            if not depth or muni_nav.contains_child(): # if theres child entries, we'll use those instead to get our answer
                if not depth:
                    depth += 1
                prompt, response, structured_response = answer(muni_nav, client, muni, muni_names[current_name], query, depth + 1, definitions_link)
            else:
                log("## ANSWERING\n\n")
                prompt = RESPONSE_QUERY_TEMPLATE.format(
                    muni=muni,
                    current_name=current_name,
                    muni_code_url=muni_names[current_name],
                    additional_links=(f"Definitions: {definitions_link}\n\n" if definitions_link else ""),
                    query=query
                )
                response = gemini_query(client, prompt, inst.CONFIGS["thinking"], MODELS["thinking"]) # prompt for answer to query
                if "(NONE)" in response["response"]:
                    response = None
                else:
                    structured_response = gemini_query(client, response["response"], inst.CONFIGS["structurer"], MODELS["fast"])
            if response: # If no answer, continue, else, continue and look at next title/chapter/article/section
                return prompt, response, structured_response
    return None, None, None

def init(state, muni, query, client):
    municode_nav = municode.MuniCodeCrawler() # open crawler
    if LOGGING:
        with open("log.md", "w", encoding="utf-8") as f: # for testing purposes
            f.write(f"# LOG\n\n")
    
    with open(MUNICODE_MUNIS, 'r') as file:
        munis = json.load(file)

    log(f"#### Municipality: {muni}\n\n")

    log(f"#### Question: {query}\n\n-------------------\n\n")

    return answer(municode_nav, client, muni, munis[state]["municipalities"][muni], query) # find relevant chapter/article and get answer

def start_chat(response, client):
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
    response = response[1]
    # allow user to respond and converse with model
    while (True):
        prompt = input("Respond: ")
        if prompt == "/structure":
            print(json.loads(gemini_query(client, response["response"], inst.CONFIGS["structurer"], MODELS["fast"])))
        else:
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=prompt)
                    ]
                )
            )
            response = gemini_query(client, contents, inst.CONFIGS["thinking"], MODELS["thinking"])
            contents.append(
                types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(text=response["think"]),
                        types.Part.from_text(text=response["response"])
                    ]
                )
            )

def main():
    client = genai.Client(api_key=GOOGLE_API_KEY)
    state = "california"
    muni = "milpitas"
    query = "When it comes to affordable housing, are there Inclusionary Zoning rules?" 
    
    # manual input
    state = state or input("State: ").lower()
    muni = muni or input("Municipality: ").lower()
    query = query or input("Question: ")

    response = init(state, muni, query, client)
    start_chat(response, client)
    

    


if __name__ == "__main__":
    main()