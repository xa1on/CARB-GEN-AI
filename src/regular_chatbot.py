import scrapers.municode_scraper as municode
import os
import random
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE')
MAX_BATCH_AMOUNT = 4 # maximum number of articles/titles or whatever you want ordered

def thinking_query(client, prompt):
    result = {
        "think": "",
        "response": "" 
    }
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True
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

def main():
    client = genai.Client(api_key=GOOGLE_API_KEY)
    while(True):
        print("-------------------------")
        prompt = input("prompt: ")
        response = thinking_query(client, prompt)
        print("-------------------------")
        print("think:")
        print(response["think"])
        print("-------------------------")
        print("reponse:")
        print(response["response"])


if __name__ == "__main__":
    main()