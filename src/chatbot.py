"""
MUNICODE POLICY CHATBOT

Gets answer to policy questions based on municode using gemini.

Authors: Chenghao Li
Org: University of Toronto - School of Cities
"""

import scrapers.municode_scraper as municode
import config.instruction as inst
import config.general as general_args
import config.prompts as prompts


import os
import time
import json

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError

load_dotenv()
GEMINI_PAID_API_KEY = os.getenv('GEMINI_PAID') # google cloud api key
GEMINI_FREE_API_KEY = os.getenv('GEMINI_FREE')

class RelevanceItem:
    def __init__(self, name: str, relevance_rating: float):
        self.name: str = name
        self.relevance_rating: float = relevance_rating
    
    def __str__(self) -> str:
        return f"name:{self.name}, relevance_rating:{self.relevance_rating}"


class ResponseItem:
    def __init__(self, response: str, thoughts: str=""):
        self.response: str = response
        self.thoughts: str = thoughts


def start_logging() -> None:
    with open(general_args.LOG_PATH, "w", encoding="utf-8") as f: # for testing purposes
        f.write(f"# LOG\n\n")

def log(text: str) -> None:
    """
    Log text (markdown format) into log file for debugging/testing purposes

    :param text: input text
    :return:
    """
    with open(general_args.LOG_PATH, "a", encoding="utf-8") as f:
        f.write(text)

def clear_log() -> None:
    open(general_args.LOG_PATH, "w", encoding="utf-8").close()

def get_latest_response(contents: list[types.Content]) -> ResponseItem:
    thoughts = ""
    response = ""
    for content in contents[-1].parts:
        if content.thought:
            thoughts += content.text
        else:
            response += content.text
    return ResponseItem(response, thoughts)


def llm_query(client: genai.Client, contents: str|list[types.Content], config: types.GenerateContentConfig, model: str) -> list[types.Content]:
    try:
        result: list[types.Content] = []
        if general_args.LOG_PROMPTS:
            if isinstance(contents, str):
                log(f"### Prompt: \n\n<details>\n\n<summary>Prompt</summary>\n\n{contents}\n\n</details>\n\n-------------------\n\n")
                result.append(
                    types.Content(
                        role="user",
                        parts = [
                            types.Part.from_text(text=contents)
                        ]
                    )
                )
            else:
                log(f"### Prompt: \n\n<details>\n\n<summary>Prompt</summary>\n\n{contents[-1].parts[0].text}\n\n</details>\n\n-------------------\n\n")
                result += contents

        thinking: str = ""
        response: str = ""

        # incremental response
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config
        ):
            if chunk.candidates:
                if chunk.candidates[0].content.parts:
                    for part in chunk.candidates[0].content.parts:
                        if not part or not part.text:
                            continue
                        if part.thought:
                            if not thinking:
                                log(f"### Thinking:\n\n<details>\n\n<summary>Thinking...</summary>\n\n")
                            thinking += part.text
                            log(part.text)
                        else:
                            if not response:
                                log(f"</details>\n\n### Response:\n\n")
                            response += part.text
                            log(part.text)
        log("\n\n-------------------\n\n")

        parts = []

        if thinking:
            parts.append(types.Part.from_text(thought=True, text=thinking))
        parts.append(types.Part.from_text(text=response))

        result.append(
            types.Content(
                role="model",
                parts=parts
            )
        )

        return result
    except ServerError as e:
        log(f"\n\n#### ERROR OCCURED ({e}). RETRYING IN 10 SECONDS\n\n")
        time.sleep(10)
        log(f"#### RETRYING...\n\n")
        return llm_query(client=client, contents=contents, config=config, model=model)

def join_list(element: list[str]|dict[str: str], seperator: str=", ") -> str:
    """
    Takes list/dictionary and creates a string containing all the keys/elements in a list seperated with the seperator

    :param element: input dictionary/list
    :param seperator: string to seperate each element
    :return: list as a string
    """

    result = ""
    for key in element:
        result += key + seperator
    result = result[:-(len(seperator))]
    return result

def run_sorter(client: genai.Client, names: list[str], query: str) -> list[RelevanceItem]:
    prompt: str = prompts.SORTER_QUERY_TEMPLATE.format(
        query=query,
        name_list=join_list(names)
    )

    response: list[types.Content] = llm_query(
        client=client,
        contents=prompt,
        config=inst.SORTER_CONFIG,
        model=general_args.FAST_MODEL
    )

    response_json: list[dict[str: str]] = json.loads(response[-1].parts[0].text)
    result: list[RelevanceItem] = []

    response_json.sort(key=lambda x: x['relevance_rating'], reverse=True) # sorts the list based on relevance_rating
    for index, option in enumerate(response_json):
        if option["relevance_rating"] < general_args.RELEVANCE_THRESHOLD: # filter based on relevance threshold
            ################### REDUNDANT CODE.
            response_json = response_json[:index]
            if response_json:
                log("### Filtered Response:\n\n")
                log(str(response_json) + "\n\n")
            ###################
            break
        result.append(RelevanceItem(name=option["name"], relevance_rating=option["relevance_rating"]))
    return result

def answer(client: genai.Client, query: str, municipality: str, municipality_url: str, context: str) -> list[types.Content]:
    log("## ANSWERING\n\n")
    prompt: str = inst.RESPONSE_QUERY_TEMPLATE.format(
        muni=municipality,
        muni_code_url=municipality_url,
        text=context,
        query=query
    )
    return llm_query(
        client=client,
        contents=prompt,
        config=inst.THINKER_CONFIG,
        model=general_args.THINKING_MODEL
    )

def chatbot_query(client: genai.Client, scraper: municode.MuniCodeCrawler, muni_name: str, query: str, free_client: genai.Client|None=None, search_terms: list[str]|None=None):
    munis = {}
    with open(general_args.MUNICODE_MUNIS, 'r') as file:
        munis = json.load(file)
    

def main():
    #municode_nav = municode.MuniCodeCrawler() # open crawler
    clear_log()
    free_client = genai.Client(api_key=GEMINI_FREE_API_KEY)
    paid_client = genai.Client(api_key=GEMINI_PAID_API_KEY)
    state = "california"
    muni = "campbell"
    query = "Is there any mention of the implementation or use of a Just Cause Eviction policy? These policies may also be called or mention Retaliatory Evictions. This typically involves requiring landlords or property owners to have a valid reason to evict a tenant. They may also be called good cause eviction or for cause eviction, etc. True/False?"
    search_terms = ["Just Cause", "Eviction Policy", "Retaliatory Eviction"]
    
    # manual input
    state = state or input("State: ").lower()
    muni = muni or input("Municipality: ").lower()
    query = query or input("Question: ")

    
    

    

    


if __name__ == "__main__":
    main()