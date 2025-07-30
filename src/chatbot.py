"""
MUNICODE POLICY CHATBOT

Gets answer to policy questions based on municode using gemini.

Authors: Chenghao Li
Org: University of Toronto - School of Cities
"""

import scrapers.municode_scraper as municode
import config.instruction as inst
import config.general as config
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

class QueryResponse:
    def __init__(self, prompt: str|list[types.Content]):
        if isinstance(prompt, str):
            self.prompt: str = prompt
        else:
            self.prompt: str = prompt[-1].parts[0].text
        self.think: str = ""
        self.response: str = ""

def start_logging() -> None:
    with open(config.LOG_PATH, "w", encoding="utf-8") as f: # for testing purposes
        f.write(f"# LOG\n\n")

def log(text: str) -> None:
    """
    Log text (markdown format) into log file for debugging/testing purposes

    :param text: input text
    :return:
    """
    with open(config.LOG_PATH, "a", encoding="utf-8") as f:
        f.write(text)

def llm_query(client: genai.Client, prompt: str|list[types.Content], config: types.GenerateContentConfig, model: str) -> QueryResponse:
    try:
        result: QueryResponse = QueryResponse(prompt=prompt)
        if config.LOG_PROMPTS:
            if isinstance(prompt, str):
                log(f"### Prompt: \n\n<details>\n\n<summary>Prompt</summary>\n\n{prompt}\n\n</details>\n\n-------------------\n\n")
            else:
                log(f"### Prompt: \n\n<details>\n\n<summary>Prompt</summary>\n\n{prompt[-1].parts[0].text}\n\n</details>\n\n-------------------\n\n")

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
                            if not result.think:
                                log(f"### Thinking:\n\n<details>\n\n<summary>Thinking...</summary>\n\n")
                            result.think += part.text
                            log(part.text)
                        else:
                            if not result.response:
                                log(f"</details>\n\n### Response:\n\n")
                            result.response += part.text
                            log(part.text)
        log("\n\n-------------------\n\n")
        return result
    except ServerError as e:
        log(f"\n\n#### ERROR OCCURED ({e}). RETRYING IN 10 SECONDS\n\n")
        time.sleep(10)
        log(f"#### RETRYING...\n\n")
        return llm_query(client=client, prompt=prompt, config=config, model=model)
    
def key_list(dict: dict[str: str], seperator: str=", ") -> str:
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

