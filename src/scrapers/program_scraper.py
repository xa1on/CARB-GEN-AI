import time
import json
from googlesearch import search
import requests
from bs4 import BeautifulSoup


import os
import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()
GEMINI_PAID_API_KEY = os.getenv('GEMINI_PAID') # google cloud api key
GEMINI_FREE_API_KEY = os.getenv('GEMINI_FREE')

def make_prompts(client: genai.Client, data: dict, names: list[str], query: str) -> list[str]:
    prompts: list[str] = []
    vectors = [
        np.array(e.values) for e in client.models.embed_content(
            model="gemini-embedding-001",
            contents=[query]+names,
            config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY")).embeddings
    ]
    embeddings_matrix = np.array(vectors)
    similarity_matrix = cosine_similarity(embeddings_matrix)
    for i in range(1,len(names)+1):
        similarity = similarity_matrix[0,i]
        if similarity<0.8:
            continue
        #print(data[names[i-1]])
        prompts.append(data[names[i-1]]['search_terms'][0]+" programs site:.gov")

    return prompts

def google_top3(prompt):
    try:
        results = list(search(prompt,num_results=3))
        print(111,results)
        return results
    except Exception as e:
        print(f"Error during search: {e}")
        return []
    
def scrape_text(url):
    response = requests.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "noscript", "iframe", "header", "footer", "nav"]):
        tag.extract()

    text = soup.get_text(separator=" ", strip=True)
    text = " ".join(text.split())

    return text

def main():
    free_client = genai.Client(api_key=GEMINI_FREE_API_KEY)
    paid_client = genai.Client(api_key=GEMINI_PAID_API_KEY)
    query = "Is there any mention of the implementation or use of a Just Cause Eviction policy? These policies may also be called or mention Retaliatory Evictions. This typically involves requiring landlords or property owners to have a valid reason to evict a tenant. They may also be called good cause eviction or for cause eviction, etc. True/False?"
    with open("queries.json", "r") as f:
        data = json.load(f)
    prompts = make_prompts(free_client,data,list(data.keys()),query)
    print(prompts)
    search_results = []
    for p in prompts:
        urls = google_top3(p)
        print(urls)
        for u in urls:
            text = scrape_text(u)
            print(text)
            search_results.append(text)
    print(search_results)


if __name__ == "__main__":
    main()
