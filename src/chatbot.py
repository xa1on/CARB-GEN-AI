"""
MUNICODE POLICY CHATBOT

Gets answer to policy questions based on municode using gemini.

Authors: Chenghao Li, Ariana Siordia
Org: Urban Displacement Project: UC Berkeley / University of Toronto
"""

import scrapers.scraper as scraper
import scrapers.municode_scraper as municode
import config.instruction as inst
import config.general as general_args
import config.prompts as prompts


import os
import time
import json
import numpy as np

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError
from sklearn.metrics.pairwise import cosine_similarity


load_dotenv()
GEMINI_PAID_API_KEY = os.getenv('GEMINI_PAID') # google cloud api key
GEMINI_FREE_API_KEY = os.getenv('GEMINI_FREE')

class RelevanceItem:
    """
    Contains a name and it's relevance rating
    """
    def __init__(self, name: str, relevance_rating: float):
        self.name: str = name
        self.relevance_rating: float = relevance_rating
    
    def __str__(self) -> str:
        return f"name:{self.name}, relevance_rating:{self.relevance_rating}"
    
    def __repr__(self) -> str:
        return str(self)


class ResponseItem:
    """
    LLM response item
    """
    def __init__(self, response: str, thoughts: str=""):
        self.response: str = response
        self.thoughts: str = thoughts


class SourceResponse:
    """
    Query Response sources based on source schema

    MUST MIRROR SOURCE_RESPONSE_SCHEMA FROM instruction.py
    """

    def __init__(self, source_url: str, page_name: str, relevant_quotation_from_source: str):
        self.source_url: str = source_url
        self.page_name: str = page_name
        self.relevant_quotation_from_source: str = relevant_quotation_from_source
    
    @classmethod
    def from_dict(cls, source: dict[str:str]):
        return cls(**source)


class ConditionalResponse:
    """
    Query Response types based on conditional responses

    MUST MIRROR CONDITION_SCHEMA FROM instruction.py
    """

    def __init__(self, condition: str, conditioned_response: str):
        self.condition: str = condition
        self.conditioned_reponse: str = conditioned_response
    
    @classmethod
    def from_dict(cls, source: dict[str:str]):
        return cls(**source)

class QueryResponse:
    """
    Query Response to store response answers based on response schema

    MUST MIRROR RESPONSE_SCHEMA FROM instruction.py
    """

    def __init__(self, sources: list[SourceResponse], response_confidence: float|None=None, binary_response: bool|None=None, numeric_response: float|None=None, categorical_response: str|None=None, conditional_response: list[ConditionalResponse]|None=None, none_found: bool=False):
        self.none_found: bool = none_found
        self.sources: list[SourceResponse] = sources
        self.response_confidence: float = response_confidence
        if binary_response != None:
            self.binary_response: bool = binary_response
        if numeric_response != None:
            self.numeric_response: float = numeric_response
        if categorical_response != None:
            self.categorical_response: str = categorical_response
        if conditional_response != None:
            self.conditional_response: list[ConditionalResponse] = conditional_response
    
    @classmethod
    def from_dict(cls, source: dict[str:]):
        params: dict[str:] = source.copy()

        if "conditional_response" in source:
            params["conditional_response"] = []

            for response_case in source["conditional_response"]:
                params["conditional_response"].append(ConditionalResponse.from_dict(response_case))
        
        params["sources"] = []
        for ind_source in source["sources"]:
            params["sources"].append(SourceResponse.from_dict(ind_source))
        return cls(**params)

def start_logging() -> None:
    """
    Clears log and begins logging in log file
    """
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
    """
    Clears log.md file

    :return:
    """
    open(general_args.LOG_PATH, "w", encoding="utf-8").close()

def get_latest_response(contents: list[types.Content]) -> ResponseItem:
    """
    Returns a ResponseItem for the latest response

    :param contents: list of llm response contents
    :return: response item with response and thoughts
    """
    thoughts = ""
    response = ""
    for content in contents[-1].parts:
        if content.thought:
            thoughts += content.text
        else:
            response += content.text
    return ResponseItem(response, thoughts)


def llm_query(client: genai.Client, contents: str|list[types.Content], config: types.GenerateContentConfig, model: str, attempt: int=1) -> list[types.Content]:
    """
    Prompts LLM

    :param client: genai client
    :param contents: prompt/content history
    :param config: gemini config
    :param model: llm model
    :param attempt: current attempt number
    :return: content history as a content list
    """
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
            thinking_part = types.Part.from_text(text=thinking)
            thinking_part.thought = True
            parts.append(thinking_part)
        parts.append(types.Part.from_text(text=response))

        result.append(
            types.Content(
                role="model",
                parts=parts
            )
        )

        return result
    except ServerError as e:
        if attempt < general_args.LLM_ATTEMPT_LIMIT:
            log(f"\n\n#### ERROR OCCURED ON ATTEMPT ({attempt}) ERROR: ({e}). RETRYING IN {general_args.LLM_ATTEMPT_DELAY} SECONDS\n\n")
            time.sleep(general_args.LLM_ATTEMPT_DELAY)
            log(f"#### RETRYING...\n\n")
            return llm_query(client=client, contents=contents, config=config, model=model, attempt=attempt + 1)
        else:
            log(f"\n\n#### ERROR OCCURED ({e}). ATTEMPT LIMIT REACHED ({attempt}).\n\n")
            exit()


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
    """
    LLM based sorting (super arbitrary)

    :param client: genai client
    :param names: names to sort
    :param query: query to base relevancy sort off of
    :return: list of RelevanceItems sorted from most relevant to least relevant
    """

    result: list[RelevanceItem] = []
    names = list(names)
    vectors = [
        np.array(e.values) for e in client.models.embed_content(
            model="gemini-embedding-001",
            contents=[query]+names,
            config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY")).embeddings
    ]
    embeddings_matrix = np.array(vectors)
    similarity_matrix = cosine_similarity(embeddings_matrix)

    log("### Relevance_Ratings:\n\n")

    for i in range(1, len(names) + 1):
        similarity = similarity_matrix[0,i]
        if similarity < general_args.RELEVANCE_THRESHOLD:
            continue
        result.append(RelevanceItem(name=names[i-1],relevance_rating=similarity))
    result.sort(key=lambda x: x.relevance_rating, reverse=True)

    log(f"{result}\n\n")
    return result

def answer(client: genai.Client, query: str, muni_name: str, muni_url: str, context: str) -> list[types.Content]:
    """
    Use answerer prompt to generate response based on context

    :param client: genai client
    :param query: general query
    :param muni_name: name of municipality (required to ensure llm stays within the specified municipality)
    :param context: text contexr in markdown format
    :return: returns 
    """
    log("## ANSWERING\n\n")
    prompt: str = prompts.RESPONSE_QUERY_TEMPLATE.format(
        muni_name=muni_name,
        muni_url=muni_url,
        text=context,
        query=query
    )
    return llm_query(
        client=client,
        contents=prompt,
        config=inst.THINKER_CONFIG,
        model=inst.THINKING_MODEL
    )

def structure(client: genai.Client, response: str) -> dict:
    log("## Structuring answer\n\n")
    return json.loads(get_latest_response(llm_query(
        client=client,
        contents=response,
        config=inst.STRUCTURER_CONFIG,
        model=inst.THINKING_MODEL
    )).response)

def search_term_generator(client: genai.Client, query: str) -> list[str]:
    """
    Gets a list of relevant search terms based on a query

    :param client: llm client
    :param query: string query
    :return: list of available search terms
    """
    terms = json.loads(
        get_latest_response(llm_query(
            client=client, 
            contents=prompts.SEARCHER_QUERY_TEMPLATE.format(query=query, n=general_args.SEARCH_TERM_LIMIT), 
            config=inst.SEARCHER_CONFIG, 
            model=inst.FAST_MODEL
        )).response
    )
    terms.sort(key=lambda x: x['relevance_rating'], reverse=True)
    search_terms: list[str] = [term["name"] for term in terms][:general_args.SEARCH_TERM_LIMIT]
    return search_terms

def closest(client: genai.Client, query: str, items: list[str]):
    """
    Get closest string to match query

    :param client: llm client
    :param query: query to match
    :query items: string items
    """
    items = list(items)
    vectors = [
        np.array(e.values) for e in client.models.embed_content(
            model="gemini-embedding-001",
            contents=[query]+items,
            config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY")).embeddings
    ]

    
    embeddings_matrix = np.array(vectors)
    similarity_matrix = cosine_similarity(embeddings_matrix)

    max = float('-inf')
    closest_item = items[0]
    for i in range(1,len(items)+1):
        similarity = similarity_matrix[0,i]
        if similarity>max:
            max = similarity
            closest_item = items[i-1]
    return closest_item

def search_answerer(client: genai.Client, scraper: scraper.Scraper, muni_name: str, query: str, free_client: genai.Client|None=None, search_terms: list[str]|None=None, visited: set[str]|None=None):
    """
    Utilize scraper search to answer query

    :param client: llm client
    :param scraper: webscraper crawler
    :param muni_name: municipality name
    :param query: string query
    :param free_client: optional free client to minimize api credit usage
    :param search_terms: optional search terms to find context
    :param visited: set containing the names of visited pages
    :return: answer to query
    """
    if not free_client:
        print("FREE CLIENT NOT FOUND. USING PAID CLIENT")
        free_client = client
    search_terms = (search_terms or search_term_generator(client, query))[:general_args.SEARCH_TERM_LIMIT]
    visited = visited or set()
    for term in search_terms:
        log(f"""## Searching "{term}"...\n\n""")
        scraper.search(term)
        search_results = scraper.scrape_search()
        if search_results:
            if len(search_results) == 1: # no need to run sorter if there is only 1 search result
                section_names = [RelevanceItem(list(search_results.keys())[0], relevance_rating=10)] # jank
            else:
                section_names = run_sorter(client=client, names=search_results.keys(), query=query)
            for section in section_names:
                muni_url = search_results[section.name].href
                log(f"""## Navigating to [{section.name}]({muni_url})\n\n""")
                scraper.go(muni_url)
                title = scraper.scrape_title()
                if not title in visited:
                    visited.add(title)
                    context = scraper.scrape_text()
                    response = get_latest_response(answer(client=free_client, query=query, muni_name=muni_name, muni_url=muni_url, context=context))
                    if not "(NONE)" in response.response:
                        structured = structure(free_client, response.response)
                        
                        if "sources" in structured and structured["sources"]:
                            quotes_verified, failed_quotes = verify_quotes_exist(context, structured["sources"])
            
                            if not quotes_verified:
                                log(f"Answer rejected - {len(failed_quotes)} quote(s) could not be verified\n\n")
                                log("Continuing search for better evidence\n\n")
                                continue

                            llm_verified = llm_verify_answer(client=free_client, query=query, muni_name=muni_name, context=context, original_response=response.response)
                            
                            if llm_verified:
                                log("Answer passed both verification steps\n\n")
                                return QueryResponse.from_dict(structured)
                            else:
                                log("Answer rejected, LLM could not verify its own answer\n\n")
                                log("Continuing search for better evidence\n\n")
                else:
                    log(f"""## Already visited, going back...\n\n""")
    # no answer found. need to move to named tuple or something b/c none_found is not in the schema
    return QueryResponse(none_found=True, binary_response=False, sources=[], response_confidence=1)
    

def traversal_answerer(client: genai.Client, scraper: scraper.Scraper, query: str):
    """
    Iteratively recurse through municipal page to find relevant document, then query llm.

    TODO: not finished yet :/
    """
    titles = scraper.scrape_titles()
    scraper.go(titles[closest(client,query,titles.keys())])
    chapters = scraper.scrape_chapters()
    scraper.go(chapters[closest(client,query,chapters.keys())])
    if (scraper.contains_child()):
        articles = scraper.scrape_articles()
        scraper.go(articles[closest(client,query,articles.keys())])
        return scraper.scrape_text()
    return QueryResponse(none_found=True, binary_response=False, sources=[], response_confidence=1)

    

def chatbot_query(client: genai.Client, scraper: scraper.Scraper, state_name: str, muni_name: str, query: str, free_client: genai.Client|None=None, search_terms: list[str]|None=None):
    munis = {}
    with open(general_args.MUNICODE_MUNIS, 'r') as file:
        munis = json.load(file)
    scraper.go(munis[state_name]["municipalities"][muni_name])
    search_answer = search_answerer(client, scraper, muni_name, query, free_client, search_terms)
    if search_answer and not search_answer.none_found:
        return search_answer
    traversal_answer = traversal_answerer(client, scraper, muni_name, query, free_client)
    if traversal_answer and not search_answer.none_found:
        return traversal_answer
    return QueryResponse(none_found=True, binary_response=False, sources=[], response_confidence=1)
    

# Funcction that checks that the llm did not invent the quote
def verify_quotes_exist(context: str, sources: list[dict]) -> tuple[bool, list[str]]:
    """
    Verify that all quoted text actually appears in the context
    
    :param context: The original scraped text
    :param sources: List of source objects with relevant_quotation_from_source
    :return: Tuple of (all_verified: bool, failed_quotes: list[str])
    """
    log("### Verifying quotes:\n\n")
    
    failed_quotes = []
    
    for i, source in enumerate(sources):
        quote = source.get("relevant_quotation_from_source", "")
        
        if not quote:
            log(f"Source {i+1} has no quote\n\n")
            continue
        
        # Normalize text
        normalized_quote = " ".join(quote.split())
        normalized_context = " ".join(context.split())
        
        # Check if quote exists in context
        if not normalized_quote.lower() in normalized_context.lower():
            log(f"Quote {i+1} not found in source text\n\n")
            log(f"Missing quote: {quote[:200]}{'...' if len(quote) > 200 else ''}\n\n")
            failed_quotes.append(quote)
    
    if failed_quotes:
        log(f"Verification failed: {len(failed_quotes)} quote(s) could not be verified\n\n")
        return False, failed_quotes
    else:
        log(f"All {len(sources)} quote(s) verified\n\n")
        return True, []
    
# Function asking LLM to double check its answer
def llm_verify_answer(client: genai.Client, query: str, muni_name: str, context: str, original_response: str) -> bool:
    """
    Ask the LLM to verify its own answer
    """
    log("## LLM SELF-VERIFICATION\n\n")
    
    verification_prompt = prompts.VERIFICATION_QUERY_TEMPLATE.format(muni_name=muni_name, query=query, original_response=original_response, context=context)
    
    verification = get_latest_response(llm_query(client, verification_prompt, inst.THINKER_CONFIG, inst.THINKING_MODEL))
    
    if "(VERIFIED)" in verification.response:
        log("LLM confirmed its answer\n\n")
        return True
    
    log("LLM rejected its own answer\n\n" if "(REJECTED)" in verification.response
        else "LLM gave unclear verification response\n\n")
    return False
    
def main():
    municode_nav = municode.MuniCodeScraper() # open crawler
    clear_log()
    free_client = genai.Client(api_key=GEMINI_FREE_API_KEY)
    paid_client = genai.Client(api_key=GEMINI_PAID_API_KEY)
    state = "california"
    muni = "campbell"
    query = "Is there any mention of the implementation or use of a Just Cause Eviction policy? These policies may also mention Retaliatory Evictions or be called a Retaliatory Eviction policy. They may also be called good cause eviction or for cause eviction, etc. This typically involves requiring landlords or property owners to have a valid reason to evict a tenant. A landlord must have a legitimate, legally recognized reason to evict a tenant, especially if they have lived in the unit for a certain amount of time (usually at least 12 months). The ordinance may include relocation assistance for tenants evicted without cause. Ensure the policy applies to most types of residential properties, not just mobile homes/parks. If you are given a document specific to mobile homes/parks, the answer is not given in that document, so return none/ignore. Answer: Yes/No/None?"
    search_terms = ["Eviction", "Just cause eviction", "retaliatory evictions", "good cause eviction", "for cause eviction"]
    
    # manual input
    state = state or input("State: ").lower()
    muni = muni or input("Municipality: ").lower()
    query = query or input("Question: ")

    chatbot_query(client=paid_client, scraper=municode_nav, state_name=state, muni_name=muni, query=query, free_client=free_client, search_terms=search_terms)
    


if __name__ == "__main__":
    main()
