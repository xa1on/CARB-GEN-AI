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
    "thinker": "gemini-2.5-flash",
    "fast": "gemini-2.0-flash-lite"
}

SORTER_QUERY_TEMPLATE = """Query: "{query}".
Here is the list of title/chapters/articles/sections:

{name_list}"""

RESPONSE_QUERY_TEMPLATE = """Answer the following question on the city/municipality of {muni} from the documents provided below for the muni/city of {muni}:

Below is the document in markdown format from the following link {muni_code_url}:

{text}



Question: {query}\n Response: """

GROUNDER_QUERY_TEMPLATE = """Is this answer accurate for the query "{query}" in regard to the city or municipality of {muni}?

Response:
"""

RELEVANCE_THRESHOLD = 4 # completely arbitrary

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

def answer(muni_nav: municode.MuniCodeCrawler, client, muni, query, depth=0):
    """
    Accesses the title/chapter/article/section names recursively until answer is found to query

    :param muni_nav: municode scraper object
    :param client: gemini client
    :param muni: current municipality
    :param query: input question
    :param depth: depth of item. (0-title, 2-chapter, 3-article/section)
    :return: prompt, answer to query, structured response in tuple
    """

    code_names = muni_nav.scrape_codes(depth)
    log(f"## Selecting title/chapters/articles/sections...\n\n")

    name_list = key_list(code_names)

    prompt = SORTER_QUERY_TEMPLATE.format(
        query=query,
        name_list=name_list
    )

    response = gemini_query(client, prompt, inst.CONFIGS["sorter"], MODELS["fast"]) # prompt for relevant title/chapters/articles/section

    response_json = json.loads(response["response"])
    response_json.sort(key=lambda x: x['relevance_rating'], reverse=True) # sorts the list based on relevance_rating
    for index, response in enumerate(response_json):
        if response["relevance_rating"] < RELEVANCE_THRESHOLD:
            response_json = response_json[:index]
            if response_json:
                log("### Filtered Response:\n\n")
                log(str(response_json) + "\n\n")
            break

    if not response_json: # return None if none of the reponses are relevant enough
        return None, None, None

    if not depth or muni_nav.contains_child():
        log(f"### Going deeper ...\n\n")
        for page in response_json:
            log(f"### Navigating to [{page["name"]}]({code_names[page["name"]]})\n\n")
            muni_nav.go(code_names[page["name"]])
            response = answer(muni_nav, client, muni, query, depth + 1)
            if response[0]:
                return response
            else:
                log(f"### Backtracking ...\n\n")
    else:
        log("## ANSWERING\n\n")
        prompt = RESPONSE_QUERY_TEMPLATE.format(
            muni=muni,
            muni_code_url=muni_nav.url,
            text=muni_nav.scrape_text(),
            #additional_links=(f"Definitions: {definitions_link}\n\n" if definitions_link else ""),
            query=query
        )
        response = gemini_query(client, prompt, inst.CONFIGS["thinker"], MODELS["thinker"]) # prompt for answer to query
        if "(NONE)" in response["response"]:
            return None, None, None
        else:
            log("## VERIFYING\n\n")
            contents = [
                types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(text=response["think"]),
                        types.Part.from_text(text=response["response"])
                    ]
                ),
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=GROUNDER_QUERY_TEMPLATE.format(
                                muni=muni,
                                query=query,
                                answer=response["response"]
                            )
                        )
                    ]
                )
            ]
            grounding_response = gemini_query(client, contents, inst.CONFIGS["grounder"], MODELS["thinker"])
            if "(YES)" in grounding_response["response"]:
                structured_response = gemini_query(client, response["response"], inst.CONFIGS["structurer"], MODELS["fast"])
            else:
                return None, None, None
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

    municode_nav.go(munis[state]["municipalities"][muni])

    return answer(municode_nav, client, muni, query) # find relevant chapter/article and get answer

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
            response = gemini_query(client, contents, inst.CONFIGS["thinker"], MODELS["thinker"])
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
    query = "How many chickens can I keep in my backyard? They are kept in an enclosed pen." 
    
    # manual input
    state = state or input("State: ").lower()
    muni = muni or input("Municipality: ").lower()
    query = query or input("Question: ")

    response = init(state, muni, query, client)
    if response[1]:
        start_chat(response, client)
    else:
        log("# No results found. Answer is likely No")
    

    


if __name__ == "__main__":
    main()